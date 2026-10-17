"""Rent the cheapest suitable vast.ai GPU, train Stage 2 on it, bring back the
metrics, and destroy the box. One command.

    python scripts/vast_run.py search                 # read-only, no key needed
    python scripts/vast_run.py run --dry-run          # show what would be rented
    python scripts/vast_run.py run --yes              # rent, train, destroy

Why it is shaped like this: the session that drives it runs in a container
whose only route out is an HTTPS proxy -- no ssh, no raw TCP -- so it cannot
log in to the box it rents. Everything the box does is therefore decided up
front in its ``onstart`` script, and the only thing that comes back is a git
branch, ``gpu-results/<run>``, holding the metrics and the logs. This script
watches for that branch and destroys the instance the moment it appears.

Money guards, because a rented GPU bills by the minute whether or not it is
doing anything useful:

* nothing is rented without ``--yes``;
* ``--max-price`` caps the hourly rate, and nothing above it is considered;
* ``--max-hours`` is enforced twice -- the box wraps the job in ``timeout``,
  and this script destroys the instance when the deadline passes;
* the instance is destroyed in a ``finally``, so an exception or Ctrl-C here
  still releases it;
* if this script itself dies, the box tries to destroy itself when the job
  ends, using the per-instance key vast.ai gives every container. That last
  path is best-effort and unverified -- if this session is lost mid-run,
  check https://cloud.vast.ai/instances/ and destroy the instance by hand.

Secrets, read from the environment and never written into anything:

* ``VAST_API_KEY`` -- your vast.ai key, used only by this script.
* ``GH_RESULTS_TOKEN`` -- a fine-grained GitHub token for
  ``Sourish-Plovane/Plovane`` only, Contents read and write, short expiry. It
  is handed to the box as an environment variable so the box can clone the
  repository and push the results branch. A rented host's operator can read
  its containers' environment, which is why the token should be scoped to one
  repository and revoked after the run.

The trained weights (~500 MB) stay on the box and are destroyed with it: this
run exists to answer whether Stage 2 helps, and that answer is the metrics.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://console.vast.ai/api/v0"
REPO_PATH = "github.com/Sourish-Plovane/Plovane.git"
BRANCH = "Sourish"

# CUDA 12.4 build: covers Ampere (sm_80/86) and Ada (sm_89). Blackwell
# (sm_120, the RTX 50 series) needs a 12.8+ build, so compute_cap is capped
# below it rather than renting a card this image cannot drive.
IMAGE = "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime"

# What the job needs, nothing more. Per-group backward keeps GPU memory to one
# group's activations, so 16 GB is ample. Ampere or newer for bf16 and for
# current PyTorch builds, which have dropped Pascal and Volta.
OFFER_FILTER = {
    "verified": {"eq": True},
    "rentable": {"eq": True},
    "external": {"eq": False},
    "num_gpus": {"eq": 1},
    "gpu_ram": {"gte": 16000},
    "disk_space": {"gte": 40},
    "reliability2": {"gte": 0.95},
    "inet_down": {"gte": 200},
    "compute_cap": {"gte": 800, "lt": 1000},
    "cuda_max_good": {"gte": 12.4},
    "type": "on-demand",
    "order": [["dph_total", "asc"]],
    "limit": 50,
}


# Input: name (str)
# Output: str | None
# Processing: An environment secret by name, ignoring case.
# Changed by: Claude Code
# Date: 2026-10-16
def secret(name: str) -> str | None:
    """An environment secret by name, ignoring case.

    Environment variables are case-sensitive, and a secret saved in the
    environment settings as ``Vast_Api_Key`` is not ``VAST_API_KEY``. Matching
    without case means the spelling someone chose in a settings form does not
    silently look like a missing key. An exact match still wins.
    """
    if os.environ.get(name):
        return os.environ[name]
    for key, value in os.environ.items():
        if key.lower() == name.lower() and value:
            return value
    return None


class ApiError(RuntimeError):
    """A vast.ai API call that came back with an HTTP error status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# Input: method (str), path (str), key (str | None), body (dict | None)
# Output: dict
# Processing: One call to the vast.ai REST API.
# Changed by: Claude Code
# Date: 2026-10-16
def api(method: str, path: str, *, key: str | None = None, body: dict | None = None) -> dict:
    """One call to the vast.ai REST API."""
    request = urllib.request.Request(
        API + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # The body names the reason (bad key, offer gone, out of credit); a
        # bare status code would not.
        raise ApiError(exc.code, f"{method} {path} -> {exc.code}: {exc.read()[:400]!r}") from None
    return json.loads(raw) if raw else {}


# Input: instance (int), key (str)
# Output: str
# Processing: Destroy an instance; a 404 means it is already gone, not billing.
# Changed by: Claude Code
# Date: 2026-10-17
def destroy(instance: int, key: str) -> str:
    """Destroy an instance and say what happened.

    The box destroys itself after pushing its results, so by the time this
    runs vast.ai usually answers 404. That instance is gone and not billing;
    reporting it as a failure sent the owner to the console for nothing.
    """
    try:
        api("DELETE", f"/instances/{instance}/", key=key)
    except ApiError as exc:
        if exc.status == 404:
            return f"instance {instance} already gone (the box destroyed itself)"
        return (
            f"!! could not destroy instance {instance}: {exc}\n"
            f"!! it is still billing -- destroy it at https://cloud.vast.ai/instances/"
        )
    return f"destroyed instance {instance}"


# Input: none
# Output: list[dict]
# Processing: Offers that can run the job, cheapest first.
# Changed by: Claude Code
# Date: 2026-10-16
def search_offers() -> list[dict]:
    """Offers that can run the job, cheapest first."""
    query = urllib.parse.quote(json.dumps(OFFER_FILTER))
    return api("GET", f"/bundles/?q={query}").get("offers", [])


# Input: offers (list[dict]), max_price (float)
# Output: dict
# Processing: The cheapest offer at or under the cap; refuses rather than overspend.
# Changed by: Claude Code
# Date: 2026-10-16
def choose_offer(offers: list[dict], *, max_price: float) -> dict:
    """The cheapest offer at or under the cap; refuses rather than overspend."""
    affordable = [o for o in offers if o.get("dph_total", float("inf")) <= max_price]
    if not affordable:
        cheapest = min((o.get("dph_total", float("inf")) for o in offers), default=None)
        raise RuntimeError(
            f"no offer at or under ${max_price:.3f}/hr"
            + (f" (cheapest matching is ${cheapest:.3f}/hr)" if cheapest is not None else "")
        )
    return min(affordable, key=lambda o: o["dph_total"])


# Input: run_id (str), max_hours (float)
# Output: str
# Processing: The script the box runs on boot.
# Changed by: Claude Code
# Date: 2026-10-16
def build_onstart(run_id: str, *, max_hours: float) -> str:
    """The script the box runs on boot.

    The token is referenced as ``$GH_TOKEN`` and never interpolated here, so
    the text of this script -- which vast.ai stores and shows in its console --
    carries no secret. Results are pushed whatever happens, failure included:
    a log of a crash is the thing most worth having back.
    """
    result_branch = f"gpu-results/{run_id}"
    seconds = int(max_hours * 3600)
    return f"""#!/bin/bash
exec > /root/onstart.log 2>&1
# No `set -x`: it would echo $REPO with the token expanded into the log.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq git curl >/dev/null
export REPO="https://x-access-token:${{GH_TOKEN}}@{REPO_PATH}"
export WORKDIR=/root/plovane BRANCH={BRANCH}
git clone --branch {BRANCH} --depth 50 "$REPO" "$WORKDIR"
cd "$WORKDIR"
timeout {seconds} bash scripts/gpu_box_setup.sh > /root/run.log 2>&1
echo $? > /root/exit_status

# Report back on a fresh orphan branch: metrics, logs, exit status.
OUT=$(mktemp -d)
cp /root/run.log /root/onstart.log /root/exit_status "$OUT"/ 2>/dev/null
cp artifacts/cross_encoder_metrics.json "$OUT"/ 2>/dev/null
cp artifacts/cross_encoder_scores.jsonl "$OUT"/ 2>/dev/null
cp artifacts/smoke/cross_encoder_metrics.json "$OUT"/smoke_metrics.json 2>/dev/null
nvidia-smi > "$OUT"/nvidia-smi.txt 2>&1
sed -i "s#${{GH_TOKEN}}#<redacted>#g" "$OUT"/*.log 2>/dev/null
cd "$OUT" && git init -q -b {result_branch} \\
  && git -c user.name=gpu-box -c user.email=gpu-box@users.noreply.github.com add -A \\
  && git -c user.name=gpu-box -c user.email=gpu-box@users.noreply.github.com \\
       commit -qm "GPU run {run_id}: exit $(cat /root/exit_status)" \\
  && git push -q "$REPO" HEAD:refs/heads/{result_branch}

# Best effort: release the box even if the session that rented it is gone.
curl -s -X DELETE -H "Authorization: Bearer ${{CONTAINER_API_KEY}}" \\
  "{API}/instances/${{CONTAINER_ID}}/" || true
"""


# Input: branch (str)
# Output: bool
# Processing: Whether the results branch has been pushed yet.
# Changed by: Claude Code
# Date: 2026-10-16
def branch_exists(branch: str) -> bool:
    """Whether the results branch has been pushed yet."""
    out = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        capture_output=True, text=True, timeout=60,
    )
    return bool(out.stdout.strip())


# Input: offers (list[dict])
# Output: None
# Processing: Print a short table of offers.
# Changed by: Claude Code
# Date: 2026-10-16
def print_offers(offers: list[dict]) -> None:
    print(f"{'gpu':14} {'$/hr':>6} {'vram':>5} {'reliab':>7} {'down':>6}  {'where':16} id")
    for o in offers[:10]:
        print(
            f"{o.get('gpu_name', '?'):14} {o['dph_total']:6.3f} {o.get('gpu_ram', 0) / 1024:4.0f}G "
            f"{o.get('reliability2', 0):7.3f} {o.get('inet_down', 0):6.0f}  "
            f"{str(o.get('geolocation', '?'))[:16]:16} {o.get('id')}"
        )


# Input: args (argparse.Namespace)
# Output: int
# Processing: Rent, wait for results, always destroy.
# Changed by: Claude Code
# Date: 2026-10-16
def run(args: argparse.Namespace) -> int:
    offers = search_offers()
    offer = choose_offer(offers, max_price=args.max_price)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_branch = f"gpu-results/{run_id}"
    print(
        f"cheapest suitable: {offer['gpu_name']} at ${offer['dph_total']:.3f}/hr "
        f"(offer {offer['id']}, {offer.get('geolocation', '?')})"
    )
    print(f"worst case at the time cap: ${offer['dph_total'] * args.max_hours:.2f}")

    body = {
        "client_id": "me",
        "image": IMAGE,
        "disk": 40,
        "label": f"plovane-stage2-{run_id}",
        "runtype": "ssh",
        "onstart": build_onstart(run_id, max_hours=args.max_hours),
        "env": {"GH_TOKEN": secret("GH_RESULTS_TOKEN") or "<GH_RESULTS_TOKEN>"},
    }
    if args.dry_run:
        shown = dict(body, env={"GH_TOKEN": "<redacted>"})
        print("\n-- dry run: this would be sent, nothing rented --")
        print(json.dumps({k: v for k, v in shown.items() if k != "onstart"}, indent=2))
        print("\n-- onstart --\n" + body["onstart"])
        return 0

    if not args.yes:
        print("\nnot renting without --yes")
        return 2
    key = secret("VAST_API_KEY")
    missing = [n for n in ("VAST_API_KEY", "GH_RESULTS_TOKEN") if not secret(n)]
    if missing:
        # Names only, never values. A secret stored as a network secret may be
        # injected by the proxy rather than exported, in which case it will
        # not appear here at all.
        print(f"not set in this session's environment (any capitalisation): {', '.join(missing)}")
        return 2

    created = api("PUT", f"/asks/{offer['id']}/", key=key, body=body)
    instance = created.get("new_contract")
    if not created.get("success") or not instance:
        print(f"rent failed: {created}")
        return 1
    print(f"rented instance {instance}; results will arrive on branch {result_branch}")

    # Python's default SIGTERM and SIGHUP handlers exit without running
    # ``finally`` blocks, so a tool timeout or a closed terminal would leave
    # the instance billing. Turning them into an exception routes them
    # through the destroy below. SIGKILL cannot be caught; the box's own
    # self-destroy is the backstop for that.
    def _stop(signum, _frame):
        raise SystemExit(f"received signal {signum}; destroying instance before exit")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)

    deadline = time.time() + args.max_hours * 3600 + 600  # box's own timeout, plus push time
    try:
        while time.time() < deadline:
            if branch_exists(result_branch):
                print("results pushed")
                break
            try:
                info = api("GET", f"/instances/{instance}/", key=key).get("instances") or {}
                status = info.get("actual_status") or info.get("cur_state") or "?"
            except RuntimeError as exc:
                status = f"status check failed: {exc}"
            print(f"{datetime.now(timezone.utc):%H:%M:%S}  instance {instance}: {status}", flush=True)
            time.sleep(args.poll_seconds)
        else:
            print(f"time cap of {args.max_hours}h reached without results")
    finally:
        print(destroy(instance, key))

    if not branch_exists(result_branch):
        return 1
    subprocess.run(["git", "fetch", "-q", "origin", result_branch], check=True)
    for name in ("exit_status", "cross_encoder_metrics.json"):
        shown = subprocess.run(
            ["git", "show", f"FETCH_HEAD:{name}"], capture_output=True, text=True
        )
        print(f"\n== {name} ==\n{shown.stdout or shown.stderr}")
    return 0


# Input: none
# Output: int
# Processing: CLI entry point.
# Changed by: Claude Code
# Date: 2026-10-16
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("search", help="list the cheapest suitable offers (read-only)")
    run_parser = sub.add_parser("run", help="rent, train, bring back metrics, destroy")
    run_parser.add_argument("--yes", action="store_true", help="actually rent (costs money)")
    run_parser.add_argument("--dry-run", action="store_true", help="show the request, rent nothing")
    run_parser.add_argument("--max-price", type=float, default=0.20, help="$/hr cap (default 0.20)")
    run_parser.add_argument("--max-hours", type=float, default=5.0, help="time cap (default 5)")
    run_parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()

    if args.command == "search":
        print_offers(search_offers())
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

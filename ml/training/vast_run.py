"""Rent the cheapest suitable vast.ai GPU, train Stage 2 on it, bring back the
metrics, and destroy the box. One command.

    python ml/training/vast_run.py search                 # read-only, no key needed
    python ml/training/vast_run.py run --dry-run          # show what would be rented
    python ml/training/vast_run.py run --yes              # rent, train, destroy

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
import tempfile
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://console.vast.ai/api/v0"
REPO_PATH = "github.com/Sourish-Plovane/Plovane.git"
REPO_SLUG = "Sourish-Plovane/Plovane"
BRANCH = "Sourish"

# CUDA 12.4 build: covers Ampere (sm_80/86) and Ada (sm_89). Blackwell
# (sm_120, the RTX 50 series) needs a 12.8+ build, so compute_cap is capped
# below it rather than renting a card this image cannot drive.
IMAGE = "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime"

# What the job needs, nothing more. Per-group backward keeps GPU memory to one
# group's activations, so 16 GB is ample. Ampere or newer for bf16 and for
# current PyTorch builds, which have dropped Pascal and Volta.
# Country codes whose hosts cannot reliably reach GitHub.
NO_GITHUB = {"CN"}

OFFER_FILTER = {
    "verified": {"eq": True},
    "rentable": {"eq": True},
    "external": {"eq": False},
    "num_gpus": {"eq": 1},
    "gpu_ram": {"gte": 16000},
    "disk_space": {"gte": 40},
    "reliability2": {"gte": 0.98},
    "inet_down": {"gte": 200},
    "compute_cap": {"gte": 800, "lt": 1000},
    "cuda_max_good": {"gte": 12.4},
    "type": "on-demand",
    "order": [["dph_total", "asc"]],
    "limit": 50,
}


JOBS = {
    "cross-encoder": {"script": "ml/training/gpu_box_setup.sh", "gpu_ram": 16000, "disk": 40, "label": "plovane-stage2"},
    # Fine-tuning a ~1.5B model (or a 7B in 4-bit) with LoRA wants a 24 GB card and room for the base weights.
    "whiteboard": {"script": "ml/training/whiteboard_box_setup.sh", "gpu_ram": 24000, "disk": 60, "label": "plovane-whiteboard"},
}
"""What each job runs on the box and needs from it."""


# Input: name (str)
# Output: str | None
# Processing: An environment secret by name, ignoring case.
# Changed by: Sourish
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

    # Input: status (int), message (str)
    # Output: None
    # Processing: Keep the HTTP status next to the error message.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# Input: method (str), path (str), key (str | None), body (dict | None)
# Output: dict
# Processing: One call to the vast.ai REST API.
# Changed by: Sourish
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
# Changed by: Sourish
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
# Changed by: Sourish
# Date: 2026-10-16
def search_offers(job: str = "cross-encoder") -> list[dict]:
    """Offers that can run the job, cheapest first."""
    query = urllib.parse.quote(json.dumps({**OFFER_FILTER, "gpu_ram": {"gte": JOBS[job]["gpu_ram"]},
                                           "disk_space": {"gte": JOBS[job]["disk"]}}))
    return api("GET", f"/bundles/?q={query}").get("offers", [])


# Input: offers (list[dict]), max_price (float)
# Output: dict
# Processing: The cheapest offer at or under the cap; refuses rather than overspend.
# Changed by: Sourish
# Date: 2026-10-16
def choose_offer(offers: list[dict], *, max_price: float) -> dict:
    """The cheapest offer at or under the cap; refuses rather than overspend."""
    # The box clones from and pushes to GitHub; hosts in these countries could
    # not reach it (two runs in China died silently with nothing pushed).
    offers = [o for o in offers if str(o.get("geolocation", "")).rsplit(",", 1)[-1].strip() not in NO_GITHUB]
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
# Changed by: Sourish
# Date: 2026-10-16
def build_onstart(run_id: str, *, max_hours: float, job: str = "cross-encoder") -> str:
    """The script the box runs on boot.

    The token is referenced as ``$GH_TOKEN`` and never interpolated here, so
    the text of this script -- which vast.ai stores and shows in its console --
    carries no secret. Results are pushed whatever happens, failure included:
    a log of a crash is the thing most worth having back.
    """
    result_branch = f"gpu-results/{run_id}"
    progress_branch = f"gpu-progress/{run_id}"
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

# Every 5 minutes, copy the logs (token removed) to gpu-progress/<run>, so a
# box that dies or never pushes its results still leaves a trail to read.
progress() {{
  while true; do
    sleep 300
    P=$(mktemp -d)
    cp /root/run.log /root/onstart.log "$P"/ 2>/dev/null
    sed -i "s#${{GH_TOKEN}}#<redacted>#g" "$P"/*.log 2>/dev/null
    (cd "$P" && git init -q -b progress \
      && git -c user.name=gpu-box -c user.email=gpu-box@users.noreply.github.com add -A \
      && git -c user.name=gpu-box -c user.email=gpu-box@users.noreply.github.com commit -qm "progress $(date -u +%H:%M)" \
      && git push -qf "$REPO" HEAD:refs/heads/{progress_branch}) >/dev/null 2>&1
    rm -rf "$P"
  done
}}
progress &
PROGRESS_PID=$!

timeout {seconds} bash {JOBS[job]["script"]} > /root/run.log 2>&1
echo $? > /root/exit_status
kill $PROGRESS_PID 2>/dev/null

# Report back on a fresh orphan branch: metrics, logs, exit status.
OUT=$(mktemp -d)

# The final model is ~480 MB, over git's 100 MB file limit, so it goes up as
# a release asset. Curl is never run verbose: -v would print the token.
if [ -s ml/artifacts/cross_encoder_final.pt ]; then
  curl -s -X POST -H "Authorization: Bearer ${{GH_TOKEN}}" \\
    -H "Accept: application/vnd.github+json" \\
    "https://api.github.com/repos/{REPO_SLUG}/releases" \\
    -d '{{"tag_name":"cross-encoder-{run_id}","target_commitish":"{BRANCH}","name":"Cross-encoder {run_id}","prerelease":true,"body":"Final Stage 2 cross-encoder from GPU run {run_id}. sha256 and settings are in cross_encoder_final.json on branch {result_branch}."}}' \\
    > "$OUT"/release.json
  RELEASE_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$OUT"/release.json)
  if [ -n "$RELEASE_ID" ]; then
    curl -s -X POST -H "Authorization: Bearer ${{GH_TOKEN}}" \\
      -H "Content-Type: application/octet-stream" \\
      --data-binary @ml/artifacts/cross_encoder_final.pt \\
      "https://uploads.github.com/repos/{REPO_SLUG}/releases/$RELEASE_ID/assets?name=cross_encoder_final.pt" \\
      > "$OUT"/release_asset.json
  fi
fi
# The whiteboard job's LoRA adapter (tens of MB) goes up as a release asset too.
if [ -d ml/artifacts/whiteboard_lora/adapter ]; then
  tar -czf /root/whiteboard_lora_adapter.tar.gz -C ml/artifacts/whiteboard_lora adapter
  curl -s -X POST -H "Authorization: Bearer ${{GH_TOKEN}}" \\
    -H "Accept: application/vnd.github+json" \\
    "https://api.github.com/repos/{REPO_SLUG}/releases" \\
    -d '{{"tag_name":"whiteboard-lora-{run_id}","target_commitish":"{BRANCH}","name":"Whiteboard LoRA {run_id}","prerelease":true,"body":"LoRA adapter from GPU run {run_id}. Settings and held-out scores are in metrics.json on branch {result_branch}."}}' \\
    > "$OUT"/release_wb.json
  WB_RELEASE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$OUT"/release_wb.json)
  if [ -n "$WB_RELEASE" ]; then
    # Retried, and the HTTP status and curl's own error kept: the first run's upload failed without a word.
    ls -l /root/whiteboard_lora_adapter.tar.gz > "$OUT"/release_wb_upload.log 2>&1
    for attempt in 1 2 3; do
      CODE=$(curl -sS --retry 3 --retry-delay 10 --max-time 900 -o "$OUT"/release_wb_asset.json -w '%{{http_code}}' \\
        -X POST -H "Authorization: Bearer ${{GH_TOKEN}}" -H "Content-Type: application/gzip" \\
        --data-binary @/root/whiteboard_lora_adapter.tar.gz \\
        "https://uploads.github.com/repos/{REPO_SLUG}/releases/$WB_RELEASE/assets?name=whiteboard_lora_adapter.tar.gz" \\
        2>> "$OUT"/release_wb_upload.log)
      echo "attempt $attempt: HTTP $CODE" >> "$OUT"/release_wb_upload.log
      [ "$CODE" = "201" ] && break
      sleep 20
    done
  fi
fi
cp ml/artifacts/whiteboard_lora/metrics.json ml/artifacts/whiteboard_lora/predictions.jsonl "$OUT"/ 2>/dev/null
cp /root/run.log /root/onstart.log /root/exit_status "$OUT"/ 2>/dev/null
cp ml/artifacts/cross_encoder_metrics.json "$OUT"/ 2>/dev/null
cp ml/artifacts/cross_encoder_scores.jsonl "$OUT"/ 2>/dev/null
cp ml/artifacts/cross_encoder_final.json ml/artifacts/final.log "$OUT"/ 2>/dev/null
cp ml/artifacts/smoke/cross_encoder_metrics.json "$OUT"/smoke_metrics.json 2>/dev/null
nvidia-smi > "$OUT"/nvidia-smi.txt 2>&1
sed -i "s#${{GH_TOKEN}}#<redacted>#g" "$OUT"/*.log 2>/dev/null
cd "$OUT" && git init -q -b {result_branch} \\
  && git -c user.name=gpu-box -c user.email=gpu-box@users.noreply.github.com add -A \\
  && git -c user.name=gpu-box -c user.email=gpu-box@users.noreply.github.com \\
       commit -qm "GPU run {run_id}: exit $(cat /root/exit_status)" \\
  && for attempt in 1 2 3; do git push -q "$REPO" HEAD:refs/heads/{result_branch} && break; sleep 20; done

# Best effort: release the box even if the session that rented it is gone.
curl -s -X DELETE -H "Authorization: Bearer ${{CONTAINER_API_KEY}}" \\
  "{API}/instances/${{CONTAINER_ID}}/" || true
"""


# Input: branch (str)
# Output: bool
# Processing: Whether the results branch has been pushed yet.
# Changed by: Sourish
# Date: 2026-10-16
def branch_exists(branch: str) -> bool:
    """Whether the results branch has been pushed yet."""
    out = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        capture_output=True, text=True, timeout=60,
    )
    return bool(out.stdout.strip())


# Input: run_id (str)
# Output: str
# Processing: The tail of the logs the box last copied to its progress branch, or a note that it copied none.
# Changed by: Claude Code
# Date: 2026-11-26
def last_progress(run_id: str) -> str:
    """The end of the box's last progress logs, to show why a run brought nothing back."""
    branch = f"gpu-progress/{run_id}"
    if not branch_exists(branch):
        return f"no progress logs on {branch}: the box died within 5 minutes or could not reach GitHub"
    subprocess.run(["git", "fetch", "-q", "origin", branch], check=True)
    parts = []
    for name in ("onstart.log", "run.log"):
        shown = subprocess.run(["git", "show", f"FETCH_HEAD:{name}"], capture_output=True, text=True)
        tail = "\n".join((shown.stdout or shown.stderr).splitlines()[-30:])
        parts.append(f"== last lines of {name} ({branch}) ==\n{tail}")
    return "\n".join(parts)


# Input: token (str)
# Output: str | None
# Processing: Why the box could not push its results with this token, or None when it can; checked before renting.
# Changed by: Claude Code
# Date: 2026-11-26
def token_can_push(token: str) -> str | None:
    """A dry-run push still asks GitHub for write access, and sends nothing.

    Three runs were lost to a token that could read the repository (so the box
    cloned and trained) but not push (so nothing came back).
    """
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        git = ["git", "-c", "user.name=check", "-c", "user.email=check@users.noreply.github.com"]
        subprocess.run(["git", "init", "-q", scratch], check=True, env=env)
        subprocess.run([*git, "-C", scratch, "commit", "-q", "--allow-empty", "-m", "check"], check=True, env=env)
        out = subprocess.run(
            ["git", "-C", scratch, "push", "--dry-run", "-q", f"https://x-access-token:{token}@{REPO_PATH}",
             "HEAD:refs/heads/gpu-progress/permission-check"],
            capture_output=True, text=True, timeout=60, env=env)
    if out.returncode == 0:
        return None
    said = (out.stderr or out.stdout).replace(token, "<redacted>").strip().splitlines()
    return ("GH_RESULTS_TOKEN cannot push to " + REPO_SLUG + " (" + (said[0] if said else "no reason given") + "). "
            "Edit the token at https://github.com/settings/personal-access-tokens: Repository permissions, "
            "Contents: Read and write.")


# Input: offers (list[dict])
# Output: None
# Processing: Print a short table of offers.
# Changed by: Sourish
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
# Changed by: Sourish
# Date: 2026-10-16
def run(args: argparse.Namespace) -> int:
    job = getattr(args, "job", "cross-encoder")
    offers = search_offers(job)
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
        "disk": JOBS[job]["disk"],
        "label": f"{JOBS[job]['label']}-{run_id}",
        "runtype": "ssh",
        "onstart": build_onstart(run_id, max_hours=args.max_hours, job=job),
        "env": {"GH_TOKEN": secret("GH_RESULTS_TOKEN") or "<GH_RESULTS_TOKEN>",
                **{k: v for k, v in (("WB_MODEL", getattr(args, "model", None)), ("WB_EPOCHS", getattr(args, "epochs", None)),
                                     ("WB_FLAGS", getattr(args, "flags", None)))
                   if v}},
    }
    if args.dry_run:
        shown = dict(body, env={**body["env"], "GH_TOKEN": "<redacted>"})
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
    problem = token_can_push(secret("GH_RESULTS_TOKEN"))
    if problem:
        print(f"not renting: {problem}")
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
    # Input: signum, _frame
    # Output: None (raises SystemExit)
    # Processing: Turn a termination signal into an exit so the instance is destroyed.
    # Changed by: Sourish
    # Date: 2026-10-22
    def _stop(signum, _frame):
        raise SystemExit(f"received signal {signum}; destroying instance before exit")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)

    deadline = time.time() + args.max_hours * 3600 + 600  # box's own timeout, plus push time
    missing = 0
    try:
        while time.time() < deadline:
            if branch_exists(result_branch):
                print("results pushed")
                break
            try:
                info = api("GET", f"/instances/{instance}/", key=key).get("instances") or {}
                status = info.get("actual_status") or info.get("cur_state") or "gone"
            except RuntimeError as exc:
                info, status = None, f"status check failed: {exc}"
            print(f"{datetime.now(timezone.utc):%H:%M:%S}  instance {instance}: {status}", flush=True)
            # vast.ai returns nothing for an instance that no longer exists.
            # Three empty answers in a row: the box is gone, and so is any
            # chance of results beyond what it already pushed.
            missing = missing + 1 if info == {} else 0
            if missing >= 3 and not branch_exists(result_branch):
                print("the instance disappeared without pushing results")
                break
            time.sleep(args.poll_seconds)
        else:
            print(f"time cap of {args.max_hours}h reached without results")
    finally:
        print(destroy(instance, key))

    if not branch_exists(result_branch):
        print(last_progress(run_id))
        return 1
    subprocess.run(["git", "fetch", "-q", "origin", result_branch], check=True)
    for name in ("exit_status", "cross_encoder_metrics.json"):
        shown = subprocess.run(
            ["git", "show", f"FETCH_HEAD:{name}"], capture_output=True, text=True
        )
        print(f"\n== {name} ==\n{shown.stdout or shown.stderr}")
    return 0


SERVE_IMAGE = "vllm/vllm-openai:latest"
"""The OpenAI-compatible model server; the whiteboard talks to it like any provider."""
SERVED_NAME = "plovane-whiteboard"
MODEL_FILE = Path(os.path.expanduser("~")) / ".config" / "plovane" / "model.json"
"""Where the whiteboard finds the running model (backend/whiteboard/llm_agent.py reads it). Mode 600: it holds the key."""


# Input: tag (str | None)
# Output: tuple[str, str]
# Processing: The release tag and download URL of the newest (or the named) whiteboard LoRA adapter on GitHub.
# Changed by: Claude Code
# Date: 2026-11-27
def adapter_release(tag: str | None = None) -> tuple[str, str]:
    path = f"releases/tags/{tag}" if tag else "releases?per_page=50"
    with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{REPO_SLUG}/{path}",
                                                       headers={"Accept": "application/vnd.github+json"}), timeout=30) as reply:
        found = json.loads(reply.read())
    for release in [found] if tag else found:
        for asset in release.get("assets") or []:
            if release.get("tag_name", "").startswith("whiteboard-lora-") and asset["name"] == "whiteboard_lora_adapter.tar.gz":
                return release["tag_name"], asset["browser_download_url"]
    raise RuntimeError("no whiteboard LoRA release with an adapter attached yet: train one first (run --job whiteboard)")


# Input: adapter_url (str), idle_minutes (int), max_hours (float)
# Output: str
# Processing: The box's start-up script for serving: fetch the adapter, start vLLM with it under SERVED_NAME behind the key in VLLM_KEY, and destroy the box after idle_minutes without a request, when the server dies, or at max_hours.
# Changed by: Claude Code
# Date: 2026-11-27
def build_serve_onstart(adapter_url: str, *, idle_minutes: int, max_hours: float) -> str:
    return f"""#!/bin/bash
exec > /root/onstart.log 2>&1
cd /root
curl -sSL -o adapter.tar.gz "{adapter_url}" && tar xzf adapter.tar.gz || echo "adapter download failed"
BASE=$(python3 -c 'import json; print(json.load(open("/root/adapter/adapter_config.json"))["base_model_name_or_path"])')
echo "base model: $BASE"
timeout {int(max_hours * 3600)} python3 -m vllm.entrypoints.openai.api_server --model "$BASE" \\
  --enable-lora --lora-modules {SERVED_NAME}=/root/adapter --max-lora-rank 64 --max-model-len 8192 \\
  --api-key "$VLLM_KEY" --host 0.0.0.0 --port 8000 > /root/vllm.log 2>&1 &
SERVER=$!
# Idle watchdog: every answered request resets the clock; nothing for {idle_minutes} minutes ends the box.
last=$(date +%s); seen=0
while sleep 60; do
  now_seen=$(grep -c 'POST /v1/chat/completions' /root/vllm.log 2>/dev/null || echo 0)
  [ "$now_seen" != "$seen" ] && seen=$now_seen && last=$(date +%s)
  kill -0 $SERVER 2>/dev/null || {{ echo "model server stopped"; break; }}
  [ $(( $(date +%s) - last )) -gt {idle_minutes * 60} ] && {{ echo "idle for {idle_minutes} minutes"; break; }}
done
curl -s -X DELETE -H "Authorization: Bearer ${{CONTAINER_API_KEY}}" "{API}/instances/${{CONTAINER_ID}}/" || true
"""


# Input: args (argparse.Namespace)
# Output: int
# Processing: Rent a GPU, serve the trained whiteboard model on it, wait until it answers, and write MODEL_FILE so the whiteboard uses it; destroys the box if it never comes up.
# Changed by: Claude Code
# Date: 2026-11-27
def serve(args: argparse.Namespace) -> int:
    import secrets

    if MODEL_FILE.exists():
        print(f"a model is already being served ({MODEL_FILE}); stop it first with: vast_run.py stop")
        return 2
    try:
        tag, adapter_url = adapter_release(args.release)
    except (RuntimeError, urllib.error.URLError) as exc:
        print(f"not serving: {exc}")
        return 2
    offer = choose_offer(search_offers("whiteboard"), max_price=args.max_price)
    print(f"adapter {tag}; cheapest suitable: {offer['gpu_name']} at ${offer['dph_total']:.3f}/hr "
          f"(offer {offer['id']}, {offer.get('geolocation', '?')}); stops itself after {args.idle_minutes} idle minutes "
          f"or {args.max_hours} h (at most ${offer['dph_total'] * args.max_hours:.2f})")
    key_for_model = secrets.token_urlsafe(24)
    body = {"client_id": "me", "image": SERVE_IMAGE, "disk": 50, "label": f"plovane-serve-{tag}", "runtype": "ssh",
            "onstart": build_serve_onstart(adapter_url, idle_minutes=args.idle_minutes, max_hours=args.max_hours),
            "env": {"-p 8000:8000": "1", "VLLM_KEY": key_for_model}}
    if args.dry_run:
        print(json.dumps({k: v for k, v in dict(body, env={**body["env"], "VLLM_KEY": "<redacted>"}).items() if k != "onstart"}, indent=2))
        print("\n-- onstart --\n" + body["onstart"])
        return 0
    if not args.yes:
        print("\nnot renting without --yes")
        return 2
    key = secret("VAST_API_KEY")
    if not key:
        print("not set in this session's environment: VAST_API_KEY")
        return 2
    created = api("PUT", f"/asks/{offer['id']}/", key=key, body=body)
    instance = created.get("new_contract")
    if not created.get("success") or not instance:
        print(f"rent failed: {created}")
        return 1
    print(f"rented instance {instance}; loading the model (usually 5-10 minutes)", flush=True)
    deadline, url = time.time() + args.wait_minutes * 60, None
    try:
        while time.time() < deadline:
            info = api("GET", f"/instances/{instance}/", key=key).get("instances") or {}
            port = ((info.get("ports") or {}).get("8000/tcp") or [{}])[0].get("HostPort")
            if info.get("public_ipaddr") and port:
                url = f"http://{info['public_ipaddr'].strip()}:{port}/v1"
                try:
                    with urllib.request.urlopen(urllib.request.Request(f"{url}/models", headers={"Authorization": f"Bearer {key_for_model}"}), timeout=10) as reply:
                        if SERVED_NAME in reply.read().decode():
                            break
                except (urllib.error.URLError, OSError):
                    pass
            print(f"{datetime.now(timezone.utc):%H:%M:%S}  instance {instance}: {info.get('actual_status') or 'starting'}", flush=True)
            time.sleep(30)
        else:
            raise RuntimeError(f"the model did not answer within {args.wait_minutes} minutes")
    except BaseException as exc:
        print(f"not serving: {exc}")
        print(destroy(instance, key))
        return 1
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(MODEL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # it holds the model's key
    with os.fdopen(handle, "w") as out:
        json.dump({"url": url, "key": key_for_model, "model": SERVED_NAME, "instance": str(instance), "adapter": tag,
                   "price_per_hour": offer["dph_total"], "started": datetime.now(timezone.utc).isoformat()}, out, indent=1)
    print(f"your model is answering at {url} (saved to {MODEL_FILE}). Builds now draw with it.")
    print("stop it with: python ml/training/vast_run.py stop  (it also stops itself when idle)")
    return 0


# Input: none
# Output: int
# Processing: Destroy the box serving the model and forget it, so the whiteboard stops using it.
# Changed by: Claude Code
# Date: 2026-11-27
def stop_serving() -> int:
    if not MODEL_FILE.exists():
        print("no model is being served")
        return 0
    served = json.loads(MODEL_FILE.read_text())
    key = secret("VAST_API_KEY")
    if not key:
        print("not set in this session's environment: VAST_API_KEY")
        return 2
    print(destroy(int(served["instance"]), key))
    MODEL_FILE.unlink()
    return 0


# Input: none
# Output: int
# Processing: Say whether the served model's box is still there and what it costs (never the key).
# Changed by: Claude Code
# Date: 2026-11-27
def serve_status() -> int:
    if not MODEL_FILE.exists():
        print("no model is being served")
        return 0
    served = json.loads(MODEL_FILE.read_text())
    key = secret("VAST_API_KEY")
    info = api("GET", f"/instances/{served['instance']}/", key=key).get("instances") or {} if key else {}
    print(json.dumps({"url": served["url"], "adapter": served.get("adapter"), "instance": served["instance"],
                      "status": info.get("actual_status") or ("gone" if key else "unknown (no VAST_API_KEY)"),
                      "price_per_hour": served.get("price_per_hour"), "started": served.get("started")}, indent=1))
    if key and not info:
        MODEL_FILE.unlink()  # the box stopped itself (idle): forget it so builds fall back cleanly
        print("the box is gone (it stops itself when idle); forgot it")
    return 0


# Input: none
# Output: int
# Processing: CLI entry point.
# Changed by: Sourish
# Date: 2026-10-16
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    search_parser = sub.add_parser("search", help="list the cheapest suitable offers (read-only)")
    search_parser.add_argument("--job", choices=sorted(JOBS), default="cross-encoder")
    run_parser = sub.add_parser("run", help="rent, train, bring back metrics, destroy")
    run_parser.add_argument("--job", choices=sorted(JOBS), default="cross-encoder",
                            help="cross-encoder (Stage 2 ranking) or whiteboard (LoRA fine-tune of the whiteboard model)")
    run_parser.add_argument("--model", default=None, help="whiteboard job: base model (default Qwen2.5-Coder-1.5B-Instruct)")
    run_parser.add_argument("--epochs", default=None, help="whiteboard job: training epochs (default 3)")
    run_parser.add_argument("--flags", default=None, help="whiteboard job: extra training flags, e.g. \"--four-bit\" for a 7B model")
    run_parser.add_argument("--yes", action="store_true", help="actually rent (costs money)")
    run_parser.add_argument("--dry-run", action="store_true", help="show the request, rent nothing")
    run_parser.add_argument("--max-price", type=float, default=0.20, help="$/hr cap (default 0.20)")
    run_parser.add_argument("--max-hours", type=float, default=5.0, help="time cap (default 5)")
    run_parser.add_argument("--poll-seconds", type=int, default=60)
    serve_parser = sub.add_parser("serve", help="rent a GPU and serve the trained whiteboard model for the whiteboard")
    serve_parser.add_argument("--release", default=None, help="adapter release tag (default: the newest)")
    serve_parser.add_argument("--idle-minutes", type=int, default=30, help="stop after this long without a request (default 30)")
    serve_parser.add_argument("--max-hours", type=float, default=4.0, help="stop after this long in any case (default 4)")
    serve_parser.add_argument("--max-price", type=float, default=0.25, help="$/hr cap (default 0.25)")
    serve_parser.add_argument("--wait-minutes", type=int, default=25, help="give up if it is not answering by then")
    serve_parser.add_argument("--yes", action="store_true", help="actually rent (costs money)")
    serve_parser.add_argument("--dry-run", action="store_true", help="show the request, rent nothing")
    sub.add_parser("stop", help="stop serving the whiteboard model")
    sub.add_parser("status", help="is the whiteboard model being served")
    args = parser.parse_args()

    if args.command == "serve":
        return serve(args)
    if args.command == "stop":
        return stop_serving()
    if args.command == "status":
        return serve_status()
    if args.command == "search":
        print_offers(search_offers(args.job))
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

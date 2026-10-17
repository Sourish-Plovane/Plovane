"""The money and secret guards in scripts/vast_run.py.

These are the parts that cost something when wrong: renting above the price
cap, or a GitHub token landing in text that vast.ai stores and displays. No
network is touched; the functions under test are pure.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def vast():
    name = "vast_run"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parent.parent / "scripts" / "vast_run.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def offer(price: float, offer_id: int = 1) -> dict:
    return {"id": offer_id, "dph_total": price, "gpu_name": "RTX A4000"}


class TestChooseOffer:
    def test_takes_the_cheapest(self, vast):
        chosen = vast.choose_offer([offer(0.15, 1), offer(0.09, 2), offer(0.12, 3)], max_price=0.2)
        assert chosen["id"] == 2

    def test_never_exceeds_the_cap(self, vast):
        chosen = vast.choose_offer([offer(0.25, 1), offer(0.18, 2)], max_price=0.2)
        assert chosen["dph_total"] <= 0.2

    def test_refuses_rather_than_overspend(self, vast):
        with pytest.raises(RuntimeError, match=r"no offer at or under \$0\.100/hr.*\$0\.150"):
            vast.choose_offer([offer(0.15), offer(0.30)], max_price=0.10)

    def test_refuses_an_empty_market(self, vast):
        with pytest.raises(RuntimeError, match="no offer"):
            vast.choose_offer([], max_price=1.0)


class TestOnstart:
    def test_carries_no_token(self, vast, monkeypatch):
        # vast.ai stores and shows the onstart text, so the token may only
        # appear as a reference that the box expands at run time.
        monkeypatch.setenv("GH_RESULTS_TOKEN", "github_pat_SECRET123")
        script = vast.build_onstart("run1", max_hours=2)
        assert "github_pat_SECRET123" not in script
        assert "${GH_TOKEN}" in script

    def test_never_traces_commands(self, vast):
        # `set -x` would echo $REPO into the log with the token expanded.
        script = vast.build_onstart("run1", max_hours=2)
        assert not any(line.strip().startswith("set -x") for line in script.splitlines())

    def test_bounds_the_job_by_the_time_cap(self, vast):
        assert "timeout 7200 " in vast.build_onstart("run1", max_hours=2)

    def test_reports_back_even_when_the_job_fails(self, vast):
        # No `set -e`, and the push comes after the job whatever its exit
        # status: a crash log is the result most worth having back.
        script = vast.build_onstart("run1", max_hours=2)
        assert "set -e" not in script
        job = script.index("scripts/gpu_box_setup.sh")
        assert script.index("git push", job) > job
        assert "exit_status" in script

    def test_pushes_to_its_own_results_branch_only(self, vast):
        script = vast.build_onstart("20261016T000000Z", max_hours=1)
        assert "HEAD:refs/heads/gpu-results/20261016T000000Z" in script
        assert "HEAD:refs/heads/Sourish" not in script


class TestOfferFilter:
    def test_excludes_gpus_this_image_cannot_drive(self, vast):
        # Pascal/Volta are dropped by current PyTorch; Blackwell needs a newer
        # CUDA build than the image pins.
        cap = vast.OFFER_FILTER["compute_cap"]
        assert cap["gte"] >= 800 and cap["lt"] <= 1000
        assert "cuda12.4" in vast.IMAGE

    def test_asks_for_one_gpu(self, vast):
        assert vast.OFFER_FILTER["num_gpus"] == {"eq": 1}


class TestSecret:
    def test_matches_the_name_whatever_its_capitalisation(self, vast, monkeypatch):
        monkeypatch.delenv("VAST_API_KEY", raising=False)
        monkeypatch.setenv("Vast_Api_Key", "k1")
        assert vast.secret("VAST_API_KEY") == "k1"

    def test_an_exact_match_wins(self, vast, monkeypatch):
        monkeypatch.setenv("VAST_API_KEY", "exact")
        monkeypatch.setenv("vast_api_key", "other")
        assert vast.secret("VAST_API_KEY") == "exact"

    def test_absent_or_empty_is_none(self, vast, monkeypatch):
        for name in [k for k in os.environ if k.lower() == "gh_results_token"]:
            monkeypatch.delenv(name)
        assert vast.secret("GH_RESULTS_TOKEN") is None
        monkeypatch.setenv("GH_RESULTS_TOKEN", "")
        assert vast.secret("GH_RESULTS_TOKEN") is None


class TestDestroy:
    def _api_raising(self, vast, status):
        def api(*_args, **_kwargs):
            raise vast.ApiError(status, f"DELETE /instances/1/ -> {status}")
        return api

    def test_reports_success(self, vast, monkeypatch):
        monkeypatch.setattr(vast, "api", lambda *a, **k: {"success": True})
        assert vast.destroy(1, "k") == "destroyed instance 1"

    def test_a_404_means_already_gone_not_billing(self, vast, monkeypatch):
        monkeypatch.setattr(vast, "api", self._api_raising(vast, 404))
        message = vast.destroy(1, "k")
        assert "already gone" in message and "billing" not in message

    def test_any_other_error_warns_it_is_still_billing(self, vast, monkeypatch):
        monkeypatch.setattr(vast, "api", self._api_raising(vast, 500))
        message = vast.destroy(1, "k")
        assert "could not destroy" in message and "still billing" in message


class TestFinalWeights:
    def test_uploads_the_final_model_as_a_release_asset(self, vast):
        script = vast.build_onstart("20261017T000000Z", max_hours=1)
        assert "cross-encoder-20261017T000000Z" in script
        assert "assets?name=cross_encoder_final.pt" in script
        # Before the results push, so the upload's answer is pushed with it.
        assert script.index("release_asset.json") < script.index("git push")

    def test_the_upload_never_runs_curl_verbose(self, vast):
        script = vast.build_onstart("run1", max_hours=1)
        commands = [l for l in script.splitlines() if not l.lstrip().startswith("#")]
        assert not any(" -v " in l or l.rstrip().endswith(" -v") or "--verbose" in l for l in commands)

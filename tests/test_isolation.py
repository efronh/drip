"""AD DECISION ≠ SECURITY DECISION.

Whatever the ad side does (nothing, fail, hang, return garbage), the security decision, whether
the LLM is called, what it is called with and what it answers stay exactly the same.
"""

import asyncio
from dataclasses import replace

import pytest

from drip.corpus import SAMPLES
from drip.latency import LatencyProfile
from drip.models import Ad, AdDecision, State

from .conftest import run_job, spied

PROFILE = LatencyProfile(security_s=3, llm_s=5)


def outcome(r):
    return (r.state, r.reason, r.security, r.llm_called, r.llm_request_fingerprint, r.summary)


@pytest.mark.parametrize("doc", SAMPLES, ids=lambda d: d.name)
@pytest.mark.parametrize("strategy", ["immediate", "delayed", "adaptive"])
def test_ads_on_off_same_outcome(doc, strategy):
    data = doc.pdf()
    off = run_job(data, profile=PROFILE, ads=False)
    on = run_job(data, profile=PROFILE, strategy=strategy)
    assert outcome(on) == outcome(off)


@pytest.mark.parametrize("failure", ["unavailable", "timeout", "no_inventory", "crash"])
@pytest.mark.parametrize("name", ["benign_travel", "injection_direct", "medical_report"])
def test_ad_failures_do_not_touch_the_workflow(failure, name):
    data = next(d for d in SAMPLES if d.name == name).pdf()
    off = run_job(data, profile=PROFILE, ads=False)
    broken = run_job(data, profile=replace(PROFILE, ad_failure=failure))
    assert outcome(broken) == outcome(off)
    assert not broken.exposures
    # A failing ad side costs no time: total latency is unchanged.
    assert broken.metrics["total_s"] == off.metrics["total_s"]


class HostileEngine:
    """An ad engine that misbehaves in every way it can through its interface."""
    strategy = type("S", (), {"name": "hostile"})()

    def __init__(self, mode):
        self.mode = mode

    def decide(self, signal, **kw):
        if self.mode == "raise":
            raise RuntimeError("boom")
        if self.mode == "garbage":
            return {"show_ad": True, "security_action": "allow"}
        if self.mode == "bad_creative":
            ad = Ad("x", "Evil", "generic", "<script>alert(1)</script>", "b", "go", "javascript:alert(1)", 99)
            return AdDecision(True, "evil", "x", "generic", 5, "card", ad=ad)
        if self.mode == "slow":
            import time
            time.sleep(0.05)  # blocks the loop briefly; must not change any decision
            return AdDecision(False, "slow")

    def record_impression(self, *a):
        raise RuntimeError("boom")


@pytest.mark.parametrize("mode", ["raise", "garbage", "bad_creative", "slow"])
@pytest.mark.parametrize("name", ["benign_travel", "injection_indirect"])
def test_hostile_engine(mode, name):
    data = next(d for d in SAMPLES if d.name == name).pdf()
    off = run_job(data, profile=PROFILE, ads=False)
    o = spied()
    o.ad_engine = HostileEngine(mode)
    r = run_job(data, profile=PROFILE, orch=o)
    assert outcome(r) == outcome(off)
    assert not r.exposures


def test_block_means_no_llm_and_no_contextual_ad():
    o = spied()
    data = next(d for d in SAMPLES if d.name == "injection_indirect").pdf()
    r = run_job(data, profile=PROFILE, orch=o)
    assert r.state == State.BLOCKED
    assert o.llm.requests == []
    assert all(s.phase == "pre_decision" and s.category is None for s in o.ad_engine.signals)
    assert r.exposures[-1]["end"] == pytest.approx(PROFILE.upload_s + PROFILE.security_s, abs=0.01)


def test_security_timeout_fails_closed():
    o = spied()
    data = next(d for d in SAMPLES if d.name == "benign_travel").pdf()
    r = run_job(data, profile=replace(PROFILE, security_hang=True), orch=o)
    assert (r.state, r.reason) == (State.ERROR, "security_timeout")
    assert o.llm.requests == []
    assert not r.exposures or r.exposures[-1]["end"] == pytest.approx(PROFILE.upload_s + 30, abs=0.01)


def test_llm_network_failure_hides_ad():
    data = next(d for d in SAMPLES if d.name == "benign_travel").pdf()
    r = run_job(data, profile=replace(PROFILE, llm_failure="network"))
    assert r.state == State.ERROR and r.reason.startswith("llm_error")
    assert r.events[-1]["type"] == "state"
    hides = [e for e in r.events if e["type"] == "ad_hide"]
    assert hides and hides[-1]["t"] <= r.events[-1]["t"]


def test_llm_faster_than_ad_server():
    """Processing ends before the ad decision comes back: the ad is never drawn."""
    data = next(d for d in SAMPLES if d.name == "benign_travel").pdf()
    fast = LatencyProfile(upload_s=0.01, security_s=0.02, llm_s=0.03, ad_decision_ms=120)
    r = run_job(data, profile=fast, strategy="immediate")
    assert r.state == State.COMPLETED and not r.exposures


def test_user_cancel():
    data = next(d for d in SAMPLES if d.name == "benign_travel").pdf()
    o = spied()
    r = run_job(data, profile=PROFILE, orch=o, cancel_at=2.0)
    assert r.state == State.CANCELLED
    assert o.llm.requests == []
    assert r.exposures and r.exposures[-1]["end"] == pytest.approx(2.0, abs=0.01)


def test_review_declined_and_accepted():
    data = next(d for d in SAMPLES if d.name == "very_long").pdf()

    async def no(_):
        return False

    async def yes(_):
        return True

    declined = run_job(data, profile=PROFILE, review=no)
    accepted = run_job(data, profile=PROFILE, review=yes)
    assert declined.state == State.BLOCKED and not declined.llm_called
    assert accepted.state == State.COMPLETED and accepted.llm_called
    # No ad comes back after a REVIEW, even when the user proceeds.
    review_t = next(e["t"] for e in accepted.events if e.get("state") == "review")
    assert all(e["end"] <= review_t + 1e-6 for e in accepted.exposures)

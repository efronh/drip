"""Strategies, frequency capping and the configurable sensitivity policy."""

import asyncio

import pytest

from drip.ads.engine import FrequencyCapper
from drip.app import build
from drip.corpus import SAMPLES
from drip.latency import LatencyProfile, VirtualClock, run_virtual
from drip.models import SessionContext

from .conftest import run_job

TRAVEL = next(d for d in SAMPLES if d.name == "benign_travel").pdf()
MEDICAL = next(d for d in SAMPLES if d.name == "medical_report").pdf()


def first_ad(r):
    return r.exposures[0] if r.exposures else None


def test_immediate_shows_at_start():
    r = run_job(TRAVEL, profile=LatencyProfile(security_s=1, llm_s=2), strategy="immediate")
    assert first_ad(r)["start"] == pytest.approx(0.03, abs=0.01)


@pytest.mark.parametrize("security,llm,shown", [(1, 1, False), (1, 2, True), (5, 10, True)])
def test_delayed_only_after_3s(security, llm, shown):
    r = run_job(TRAVEL, profile=LatencyProfile(security_s=security, llm_s=llm), strategy="delayed")
    assert bool(r.exposures) == shown
    if shown:
        assert first_ad(r)["start"] == pytest.approx(3.0, abs=0.05)


@pytest.mark.parametrize("security,llm,fmt", [(1, 1, None), (1, 5, "compact"), (5, 10, "card")])
def test_adaptive_sizes_to_predicted_wait_on_card_surface(security, llm, fmt):
    r = run_job(TRAVEL, profile=LatencyProfile(security_s=security, llm_s=llm), strategy="adaptive",
                overrides={"ads": {"surface": "card"}})
    assert (first_ad(r) or {}).get("format") == fmt


@pytest.mark.parametrize("security,llm,fmt", [(1, 1, None), (1, 5, "status_line"), (5, 10, "status_line")])
def test_status_line_surface_is_one_line_whatever_the_wait(security, llm, fmt):
    r = run_job(TRAVEL, profile=LatencyProfile(security_s=security, llm_s=llm), strategy="adaptive")
    assert (first_ad(r) or {}).get("format") == fmt


def test_frequency_cap_max_per_session():
    capper = FrequencyCapper(max_per_session=2, min_interval_s=0)
    clock = VirtualClock()
    o = build(clock, "mock", "immediate", capper=capper)
    s = SessionContext("same-user")

    async def three():
        return [await o.run(TRAVEL, "", s, LatencyProfile(), 8.5) for _ in range(3)]

    results = run_virtual(three())
    assert [bool(r.exposures) for r in results] == [True, True, False]
    assert results[2].metrics["decision_reason"] == "frequency_cap:max_per_session"


def test_frequency_cap_min_interval():
    capper = FrequencyCapper(max_per_session=10, min_interval_s=300)
    clock = VirtualClock()
    o = build(clock, "mock", "immediate", capper=capper)
    s = SessionContext("same-user")

    async def jobs():
        a = await o.run(TRAVEL, "", s, LatencyProfile(), 8.5)
        b = await o.run(TRAVEL, "", s, LatencyProfile(), 8.5)
        await asyncio.sleep(300)
        c = await o.run(TRAVEL, "", s, LatencyProfile(), 8.5)
        return a, b, c

    a, b, c = run_virtual(jobs())
    assert bool(a.exposures) and not b.exposures and bool(c.exposures)
    assert b.metrics["decision_reason"] == "frequency_cap:min_interval"


def test_policy_is_config_driven():
    """medical: generic_only by default; one config change turns ads off completely."""
    default = run_job(MEDICAL, profile=LatencyProfile(security_s=3, llm_s=6))
    strict = run_job(MEDICAL, profile=LatencyProfile(security_s=3, llm_s=6),
                     overrides={"ads": {"category_modes": {"medical": "none"}}})
    assert default.exposures[-1]["end"] == pytest.approx(default.metrics["total_s"], abs=0.01)
    assert strict.exposures[-1]["end"] == pytest.approx(3.5, abs=0.01)    # withdrawn at the decision


def test_ads_globally_disabled():
    r = run_job(TRAVEL, overrides={"ads": {"enabled": False}})
    assert not r.exposures and r.state.value == "completed"


def test_swap_waits_until_current_creative_has_been_seen():
    """Security decides at 1.0 s, but the generic creative stays until it has been up for 2 s."""
    r = run_job(TRAVEL, profile=LatencyProfile(upload_s=0.5, security_s=0.5, llm_s=10), strategy="immediate")
    assert [e["ad_id"].split("_")[0] for e in r.exposures] == ["food", "travel"]
    assert r.exposures[1]["start"] == pytest.approx(0.03 + 2.0 + 0.03, abs=0.01)   # shown + 2 s + ad server


def test_no_swap_when_the_new_creative_would_only_flash():
    r = run_job(TRAVEL, profile=LatencyProfile(security_s=3, llm_s=1), strategy="immediate")
    assert len(r.exposures) == 1
    assert r.metrics["decision_reason"] == "keep_current:short_remaining_wait"

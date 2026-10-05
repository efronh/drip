"""The ad side only ever sees a minimized ContextSignal."""

from dataclasses import asdict

import pytest

from drip.corpus import PERSONAL, SAMPLES, TRAVEL
from drip.latency import LatencyProfile
from drip.models import SessionContext
from drip.pdfgen import make_pdf
from drip.privacy import SIGNAL_FIELDS

from .conftest import run_job, spied

CANARY = "ZEBRA-CANARY-7731"
PROFILE = LatencyProfile(security_s=3, llm_s=6)


def all_signal_text(engine) -> str:
    return " ".join(repr(asdict(s)) for s in engine.signals)


@pytest.mark.parametrize("base,title", [(TRAVEL, "Roma"), (PERSONAL, "Mektup")])
def test_no_document_content_reaches_the_ad_side(base, title):
    data = make_pdf(list(base) + [f"Project code {CANARY}."], title=title)
    o = spied()
    r = run_job(data, profile=PROFILE, orch=o)
    assert o.ad_engine.signals, "the ad side was consulted"
    seen = all_signal_text(o.ad_engine)
    for secret in (CANARY, "10000000146", "ayse.yilmaz", "TR33", "0532", "Trastevere", title):
        assert secret not in seen
    for s in o.ad_engine.signals:
        assert set(asdict(s)) == SIGNAL_FIELDS
    # The LLM, on the other hand, does get the (masked) document.
    assert CANARY in o.llm.requests[0].document
    assert r.security["action"] == "allow"


@pytest.mark.parametrize("doc", SAMPLES, ids=lambda d: d.name)
def test_sensitive_categories_never_leave_the_boundary(doc):
    """The ad side learns a category only when it may target on it: never medical, financial, ..."""
    o = spied()
    run_job(doc.pdf(), profile=PROFILE, orch=o)
    for s in o.ad_engine.signals:
        assert s.category in (None, "general", "travel", "shopping", "education")
        if s.phase == "pre_decision":
            assert s.category is None and s.sensitivity is None


def test_premium_and_opt_out():
    data = next(d for d in SAMPLES if d.name == "benign_travel").pdf()
    premium = run_job(data, profile=PROFILE, session=SessionContext("p", tier="premium"))
    assert not premium.exposures
    o = spied()
    opted_out = run_job(data, profile=PROFILE, orch=o, session=SessionContext("o", contextual_ads_consent=False))
    assert opted_out.exposures and all(s.category is None for s in o.ad_engine.signals)


def test_keyword_stuffing_cannot_unlock_ads_on_a_sensitive_document():
    """An attacker pads a medical report with travel words so the top category becomes "travel".
    The ad mode must still follow the most sensitive category present, not the top one."""
    from drip.corpus import MEDICAL
    stuffing = "Flight hotel trip booking itinerary airport travel visa passport. " * 6
    data = make_pdf(list(MEDICAL) + [stuffing], title="Report")
    o = spied()
    r = run_job(data, profile=PROFILE, orch=o)
    assert r.security["category"] == "travel"          # the stuffing does win the argmax...
    assert r.security["sensitivity"] != "low"           # ...but not the sensitivity
    assert all(s.category is None for s in o.ad_engine.signals)
    assert not any(e["ad_id"].startswith("travel_") for e in r.exposures)

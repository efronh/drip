"""Size-based wait prediction, and sponsor rotation on long waits."""

import pytest

from drip.latency import LatencyProfile
from drip.models import State
from drip.predictor import estimate_output_tokens, predict

from .conftest import run_job, spied

QUICK = "Türkiye'nin başkenti neresi?"
NORMAL = "Roma'ya 4 günlük gezi planı hazırlar mısın?"
REPORT = "Yapay zeka güvenliği hakkında detaylı bir rapor yaz, giriş, yöntem ve sonuç bölümleri olsun."


def test_bigger_requests_predict_longer_waits():
    waits = [predict(p).total_s for p in (QUICK, NORMAL, REPORT, "Eksiksiz 10 sayfalık bir iş planı hazırla.")]
    assert waits == sorted(waits) and waits[0] < 4 and waits[-1] > 60


@pytest.mark.parametrize("text,lo,hi", [
    ("1500 kelimelik bir makale yaz", 2000, 2500),
    ("Write a 500-word essay", 700, 800),
    ("20 maddelik bir kontrol listesi çıkar", 1100, 1300),
    ("Kısaca anlat: kuantum bilgisayar nedir?", 60, 250),
    ("Write a comprehensive step-by-step guide with tests", 3000, 4000),
])
def test_output_length_cues(text, lo, hi):
    tokens, _ = estimate_output_tokens(text)
    assert lo <= tokens <= hi


def test_documents_scale_with_pages():
    assert predict("özetle", pages=40, input_chars=80_000).total_s > predict("özetle", pages=2, input_chars=4_000).total_s


def test_streaming_leaves_almost_no_window():
    """Without an output guard the answer streams: the wait ends at the first token."""
    assert predict(REPORT, output_guard=False).total_s < 1.5 < 40 < predict(REPORT).total_s


LONG = LatencyProfile(upload_s=0, security_s=0.3, llm_s=60)


def test_rotation_on_long_waits():
    r = run_job(NORMAL, kind="prompt", profile=LONG, estimate=60.3)
    starts = [e["start"] for e in r.exposures]
    ids = [e["ad_id"] for e in r.exposures]
    assert len(r.exposures) == 4                         # max_creatives_per_request
    assert len(set(ids)) == len(ids)                     # no creative repeated within the request
    assert starts[2] - starts[1] == pytest.approx(20, abs=0.1)
    assert r.state == State.COMPLETED and r.exposures[-1]["end"] == pytest.approx(r.metrics["total_s"], abs=0.01)


def test_rotation_off():
    r = run_job(NORMAL, kind="prompt", profile=LONG, estimate=60.3, overrides={"ads": {"rotation": {"enabled": False}}})
    assert len(r.exposures) == 2                         # generic, then the contextual upgrade


def test_no_rotation_when_little_wait_remains():
    """Predicted 22 s: after 20 s only ~2 s remain, so the sponsor stays as it is."""
    r = run_job(NORMAL, kind="prompt", profile=LatencyProfile(upload_s=0, security_s=0.3, llm_s=22), estimate=22.3)
    assert len(r.exposures) == 2


def test_rotation_does_not_count_against_frequency_cap_and_keeps_isolation():
    off = run_job(REPORT, kind="prompt", profile=LONG, estimate=60.3, ads=False)
    o = spied()
    on = run_job(REPORT, kind="prompt", profile=LONG, estimate=60.3, orch=o)
    assert (on.state, on.security, on.llm_request_fingerprint, on.summary, on.metrics["total_s"]) == \
           (off.state, off.security, off.llm_request_fingerprint, off.summary, off.metrics["total_s"])
    assert o.ad_engine.inner.capper.count("s1") == 1

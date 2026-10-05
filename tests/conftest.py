import asyncio

import pytest

from drip.app import build
from drip.latency import LatencyProfile, VirtualClock, run_virtual
from drip.models import SessionContext


class SpyLLM:
    """Wraps the real mock LLM and records every request it gets."""

    def __init__(self, inner):
        self.inner = inner
        self.requests = []

    async def generate(self, request, profile):
        self.requests.append(request)
        return await self.inner.generate(request, profile)

    async def chat(self, request, profile, on_state):
        self.requests.append(request)
        return await self.inner.chat(request, profile, on_state)


class SpyEngine:
    """Wraps an AdDecisionEngine and records every signal it sees."""

    def __init__(self, inner):
        self.inner = inner
        self.signals = []
        self.strategy = inner.strategy

    def decide(self, signal, **kw):
        self.signals.append(signal)
        return self.inner.decide(signal, **kw)

    def record_impression(self, *a):
        self.inner.record_impression(*a)


def run_job(data: bytes, *, profile: LatencyProfile | None = None, estimate: float | None = None,
            strategy: str | None = None, ads: bool = True, overrides: dict | None = None,
            session: SessionContext | None = None, mode: str = "mock", instruction: str = "",
            review=None, orch=None, cancel_at: float | None = None, kind: str = "pdf"):
    profile = profile or LatencyProfile()
    estimate = profile.total_s if estimate is None else estimate
    clock = VirtualClock()
    o = orch or build(clock, mode, strategy, ads, overrides)

    async def main():
        task = asyncio.ensure_future(o.run(data, instruction, session or SessionContext("s1"), profile, estimate,
                                           review=review, kind=kind))
        if cancel_at is not None:
            await asyncio.sleep(cancel_at)
            task.cancel()
        return await task

    return run_virtual(main())


def spied(strategy=None, ads=True, overrides=None, mode="mock"):
    o = build(VirtualClock(), mode, strategy, ads, overrides)
    o.llm = SpyLLM(o.llm)
    if o.ad_engine is not None:
        o.ad_engine = SpyEngine(o.ad_engine)
    return o


@pytest.fixture
def job():
    return run_job

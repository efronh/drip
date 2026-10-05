"""Isolation boundary around the Ad Decision Engine.

Failure behaviour, stated once:

    Ad side   -> FAIL-CLOSED: any error, timeout, malformed answer or missing inventory = no ad.
    Workflow  -> FAIL-OPEN with respect to ads: security and the LLM carry on as if ads didn't exist.
    Security  -> FAIL-CLOSED (in the orchestrator): no security answer = no LLM call.

SafeAdClient.decide() never raises (except cancellation) and never takes longer than the budget.
"""

import asyncio

from ..latency import Clock
from ..models import AdDecision, ContextSignal
from .engine import AdDecisionEngine, validate_creative


class AdServerUnavailable(ConnectionError):
    pass


class SafeAdClient:
    def __init__(self, engine: AdDecisionEngine, clock: Clock, timeout_ms: float,
                 latency_ms: float = 30.0, failure: str | None = None):
        self.engine = engine
        self.clock = clock
        self.timeout_s = timeout_ms / 1000
        self.latency_s = latency_ms / 1000
        self.failure = failure

    @property
    def strategy(self) -> str:
        return self.engine.strategy.name

    async def _call(self, signal: ContextSignal, **kw) -> AdDecision:
        # Stands in for the network hop to an ad server.
        await self.clock.sleep(self.latency_s)
        if self.failure == "unavailable":
            raise AdServerUnavailable("ad server unavailable")
        if self.failure == "timeout":
            await self.clock.sleep(60)
        if self.failure == "no_inventory":
            return AdDecision(False, "no_suitable_ad", strategy=self.strategy)
        if self.failure == "crash":
            raise RuntimeError("ad engine bug")
        return self.engine.decide(signal, **kw)

    async def decide(self, signal: ContextSignal, **kw) -> AdDecision:
        t0 = self.clock.now()
        try:
            decision = await asyncio.wait_for(self._call(signal, **kw), self.timeout_s * self.clock.scale)
        except asyncio.TimeoutError:
            decision = AdDecision(False, "ad_decision_timeout", strategy=self.strategy)
        except Exception as e:  # noqa: BLE001 - the whole point is that nothing escapes
            decision = AdDecision(False, f"ad_engine_error:{type(e).__name__}", strategy=self.strategy)
        if not isinstance(decision, AdDecision):
            decision = AdDecision(False, "invalid_decision", strategy=self.strategy)
        elif decision.show_ad and (decision.ad is None or validate_creative(decision.ad)):
            decision = AdDecision(False, "invalid_creative", strategy=self.strategy)
        elapsed_ms = (self.clock.now() - t0) * 1000
        return AdDecision(**{**decision.__dict__, "decision_ms": round(elapsed_ms, 2)})

    def record_impression(self, session_id: str, now: float) -> None:
        try:
            self.engine.record_impression(session_id, now)
        except Exception:  # noqa: BLE001
            pass

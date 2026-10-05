"""Latency simulator.

Real guardrails and LLMs take wildly different times depending on document size, model, load and
which layers run. The simulator replaces those with controlled, reproducible durations so the UX
and the ad strategies can be compared at 1, 3, 5, 10 s of security time and 2, 5, 10, 20 s of LLM
time.

All durations are in *simulated* seconds. The demo server uses Clock (real time). Tests and the
evaluation use VirtualClock on a VirtualTimeLoop: sleeps take no real time and every run is exact,
which is how the evaluation runs thousands of sessions in a few seconds, reproducibly.
"""

import asyncio
import math
import random
import selectors
import time
from dataclasses import dataclass, replace

SECURITY_LATENCIES = (1.0, 3.0, 5.0, 10.0)
LLM_LATENCIES = (2.0, 5.0, 10.0, 20.0)

# How the security time is split over the visible sub-states.
SECURITY_STAGE_SPLIT = {"scanning": 0.35, "analyzing": 0.45, "policy_check": 0.20}


class Clock:
    """Real time, optionally sped up. Used by the demo server."""
    virtual = False

    def __init__(self, scale: float = 1.0):
        self.scale = scale
        self.t0 = time.monotonic()

    def now(self) -> float:
        """Simulated seconds since the clock started."""
        return (time.monotonic() - self.t0) / self.scale

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds * self.scale)


class VirtualClock(Clock):
    """Virtual time, for tests and evaluation: sleeping costs no real time and every run is exact
    and reproducible. Needs VirtualTimeLoop (run_virtual)."""
    virtual = True

    def __init__(self):
        self.scale = 1.0

    def now(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


class _VirtualSelector(selectors.SelectSelector):
    def __init__(self):
        super().__init__()
        self.loop: "VirtualTimeLoop | None" = None

    def select(self, timeout=None):
        ready = super().select(0)
        if ready or timeout is None or timeout <= 0:
            return ready
        # Nothing to do until the next timer: jump straight to it. A sub-epsilon timeout would be
        # lost in the float addition and the clock would never move, so always step forward.
        self.loop.vtime = max(self.loop.vtime + timeout, math.nextafter(self.loop.vtime, math.inf))
        return []


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        selector = _VirtualSelector()
        super().__init__(selector)
        selector.loop = self
        self.vtime = 0.0

    def time(self) -> float:
        return self.vtime


def run_virtual(coro):
    loop = VirtualTimeLoop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@dataclass(frozen=True)
class LatencyProfile:
    upload_s: float = 0.5
    security_s: float = 3.0
    llm_s: float = 5.0
    ad_decision_ms: float = 30.0
    # Failure injection, used by the failure-case tests and the demo's debug panel.
    ad_failure: str | None = None       # None | "unavailable" | "timeout" | "no_inventory"
    llm_failure: str | None = None      # None | "network"
    security_hang: bool = False         # security never answers (tests the security timeout)
    output_tokens: int | None = None    # length of the answer being simulated (informational)

    @property
    def total_s(self) -> float:
        return self.upload_s + self.security_s + self.llm_s


class LatencySimulator:
    """Draws latency profiles, optionally with jitter, and produces the wait *estimate* the
    Adaptive strategy sees. The estimate is deliberately noisy: a real predictor is never exact."""

    def __init__(self, seed: int = 0, jitter: float = 0.0, estimate_error: float = 0.25):
        self.rng = random.Random(seed)
        self.jitter = jitter
        self.estimate_error = estimate_error

    def grid(self) -> list[LatencyProfile]:
        return [LatencyProfile(security_s=s, llm_s=l) for s in SECURITY_LATENCIES for l in LLM_LATENCIES]

    def sample(self, base: LatencyProfile | None = None) -> LatencyProfile:
        if base is None:
            base = LatencyProfile(security_s=self.rng.choice(SECURITY_LATENCIES), llm_s=self.rng.choice(LLM_LATENCIES))
        if not self.jitter:
            return base
        j = lambda v: max(0.05, v * self.rng.lognormvariate(0, self.jitter))
        return replace(base, upload_s=j(base.upload_s), security_s=j(base.security_s), llm_s=j(base.llm_s))

    def estimate(self, profile: LatencyProfile) -> float:
        """Predicted total wait, log-normally distributed around the truth, rounded to 0.5 s."""
        noisy = profile.total_s * math.exp(self.rng.gauss(0, self.estimate_error)) if self.estimate_error else profile.total_s
        return round(noisy * 2) / 2

"""Wiring: build an orchestrator from a config, a security mode and an ad strategy."""

from .ads.engine import AdDecisionEngine, FrequencyCapper
from .config import deep_merge, load_policy
from .latency import Clock
from .llm import MockLLM
from .orchestrator import Orchestrator
from .security.gateway import make_gateway


def build(clock: Clock, security_mode: str = "mock", strategy: str | None = None, ads: bool = True,
          policy_overrides: dict | None = None, capper: FrequencyCapper | None = None,
          surface: str | None = None) -> Orchestrator:
    policy = load_policy(deep_merge(policy_overrides or {}, {"ads": {"surface": surface}} if surface else {}))
    engine = AdDecisionEngine(policy["ads"], capper=capper, strategy=strategy) if ads and policy["ads"]["enabled"] else None
    return Orchestrator(policy, clock, make_gateway(security_mode, policy["security"], clock), MockLLM(clock), engine)

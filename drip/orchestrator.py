"""LLM Gateway / Orchestrator.

    document: upload ─► security (scanning → analyzing → policy_check) ─► [review] ─► generating ─► completed
    prompt:   input guard (scanning → policy_check) ─► [review] ─► thinking → writing ─► output_check ─► completed
                 │ BLOCK / error / timeout                                   │ error / output BLOCK
                 ▼                                                           ▼
              blocked / error  (LLM never called)                         error / blocked

The ad slot runs beside this flow and is only ever *told* what happened. The order of operations
is fixed: security decides first, the ad side learns about it afterwards, and no value from the ad
side is read by this file. That is the isolation invariant the tests check.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .ads.client import SafeAdClient
from .ads.engine import AdDecisionEngine
from .ads.slot import AdSlot
from .latency import Clock, LatencyProfile
from .llm import MockLLM, build_chat_request, build_request
from .models import SecurityAction, SecurityReport, SessionContext, State, label
from .security.gateway import SecurityGateway

ReviewCallback = Callable[[SecurityReport], Awaitable[bool]]
SECURITY_STATES = ("scanning", "analyzing", "policy_check", "output_check")
LLM_STATES = ("generating", "thinking", "writing")


@dataclass
class JobResult:
    job_id: str
    state: State
    reason: str = ""
    security: dict | None = None
    summary: str | None = None
    llm_called: bool = False
    llm_request_fingerprint: str | None = None
    events: list[dict] = field(default_factory=list)
    ad_log: list[dict] = field(default_factory=list)
    exposures: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class Orchestrator:
    def __init__(self, policy: dict, clock: Clock, security: SecurityGateway, llm: MockLLM,
                 ad_engine: AdDecisionEngine | None):
        self.policy = policy
        self.clock = clock
        self.security = security
        self.llm = llm
        self.ad_engine = ad_engine

    async def run(self, data: bytes | str, instruction: str, session: SessionContext, profile: LatencyProfile,
                  estimated_wait_s: float, on_event: Callable[[dict], None] | None = None,
                  review: ReviewCallback | None = None, job_id: str | None = None, kind: str = "pdf") -> JobResult:
        """kind="pdf": data is a PDF, instruction is the user's request about it.
        kind="prompt": data is the chat message; input guard → thinking → writing → output guard."""
        res = JobResult(job_id or uuid.uuid4().hex[:12], State.UPLOADING)
        t0 = self.clock.now()
        spent: dict[str, float] = {}
        current = {"state": None, "since": t0}
        compute_ms: list[float | None] = [None]

        def emit(kind_: str, data_: dict) -> None:
            ev = {"t": round(self.clock.now() - t0, 3), "type": kind_, **data_}
            res.events.append(ev)
            if on_event:
                try:
                    on_event(ev)
                except Exception:  # noqa: BLE001 - a broken UI listener must not break the job
                    pass

        def account() -> None:
            now = self.clock.now()
            if current["state"] is not None:
                spent[current["state"]] = spent.get(current["state"], 0.0) + now - current["since"]
            current["since"] = now

        async def set_state(state: State) -> None:
            account()
            current["state"] = state.value
            res.state = state
            slot.state = state   # the rotation log reports the current state
            emit("state", {"state": state.value, "label": label(state, kind)})

        client = None
        if self.ad_engine is not None:
            client = SafeAdClient(self.ad_engine, self.clock, self.policy["ads"]["decision_timeout_ms"],
                                  profile.ad_decision_ms, profile.ad_failure)
        slot = AdSlot(client, self.policy["ads"], session, self.clock, emit, t0)

        def finish(state: State, reason: str) -> JobResult:
            slot.close(reason)
            account()
            res.state, res.reason = state, reason
            emit("state", {"state": state.value, "label": label(state, kind), "reason": reason})
            total = self.clock.now() - t0
            sum_of = lambda names: round(sum(spent.get(n, 0.0) for n in names), 3) if any(n in spent for n in names) else None
            res.ad_log, res.exposures = slot.log, slot.exposures
            res.metrics = {
                "kind": kind,
                "total_s": round(total, 3),
                "security_s": sum_of(SECURITY_STATES),
                "llm_s": sum_of(LLM_STATES),
                "security_compute_ms": compute_ms[0],
                "estimated_wait_s": estimated_wait_s,
                **slot.metrics(),
            }
            return res

        try:
            if kind == "pdf":
                await set_state(State.UPLOADING)
                slot.start(State.UPLOADING, estimated_wait_s)
                await self.clock.sleep(profile.upload_s)
                inspect = self.security.inspect(data, instruction, profile, set_state)
            else:
                slot.start(State.SCANNING, estimated_wait_s)
                inspect = self.security.inspect_prompt(data, profile, set_state)

            # 1. Security first. No answer in time = no LLM call (fail closed).
            try:
                report = await asyncio.wait_for(inspect, self.policy["security"]["security_timeout_s"] * self.clock.scale)
            except asyncio.TimeoutError:
                return finish(State.ERROR, "security_timeout")
            except Exception as e:  # noqa: BLE001
                return finish(State.ERROR, f"security_error:{type(e).__name__}")
            res.security = report.summary()
            compute_ms[0] = report.compute_ms
            emit("security", res.security)

            # 2. The ad side is told about the decision only after it is final.
            if report.action == SecurityAction.BLOCK:
                return finish(State.BLOCKED, report.reason)

            next_state = State.GENERATING if kind == "pdf" else State.THINKING
            if report.action == SecurityAction.REVIEW:
                slot.on_security(report, State.REVIEW, 0)   # withdraws any ad: no ad next to a warning
                await set_state(State.REVIEW)
                mode = self.policy["security"]["review_policy"]
                proceed = mode == "proceed" or (mode == "ask_user" and review is not None and await review(report))
                emit("review", {"policy": mode, "proceed": proceed})
                if not proceed:
                    return finish(State.BLOCKED, f"review_declined:{report.reason}")
            else:
                remaining = max(0.0, estimated_wait_s - (self.clock.now() - t0))
                slot.on_security(report, next_state, remaining)

            # 3. LLM. Its input is built from the security report (and the instruction) only.
            request = build_request(report, instruction) if kind == "pdf" else build_chat_request(report)
            res.llm_called = True
            res.llm_request_fingerprint = request.fingerprint()
            try:
                if kind == "pdf":
                    await set_state(State.GENERATING)
                    res.summary = await self.llm.generate(request, profile)
                else:
                    answer = await self.llm.chat(request, profile, set_state)
            except Exception as e:  # noqa: BLE001
                return finish(State.ERROR, f"llm_error:{type(e).__name__}")

            # 4. Prompt mode: the answer is held back until the output guard has checked it.
            if kind == "prompt":
                try:
                    text, action, findings = await self.security.check_output(answer, profile, set_state)
                except Exception as e:  # noqa: BLE001
                    return finish(State.ERROR, f"output_check_error:{type(e).__name__}")
                emit("output_check", {"action": action, "findings": [f.__dict__ for f in findings]})
                if action == "block":
                    return finish(State.BLOCKED, "answer_withheld_by_output_guard")
                res.summary = text
            emit("summary", {"text": res.summary})
            return finish(State.COMPLETED, "completed")

        except asyncio.CancelledError:
            return finish(State.CANCELLED, "user_cancelled")

"""The sponsored slot of one processing job: when it opens, what it shows, when it closes.

This runs on the trusted side and only *reacts* to the workflow: the orchestrator tells it what
happened (start, security decision, finish); it never tells the orchestrator anything. Every
public method is non-blocking and swallows its own errors, and all ad work happens in background
tasks, so a slow or broken ad side cannot delay or change the workflow.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict

from ..latency import Clock
from ..models import AdDecision, SecurityReport, SessionContext, State
from ..privacy import build_signal
from .client import SafeAdClient

log = logging.getLogger(__name__)
Emit = Callable[[str, dict], None]


class AdSlot:
    def __init__(self, client: SafeAdClient | None, ads_cfg: dict, session: SessionContext, clock: Clock,
                 emit: Emit, job_t0: float):
        self.client = client
        self.cfg = ads_cfg
        self.session = session
        self.clock = clock
        self.emit = emit
        self.t0 = job_t0
        self.task: asyncio.Task | None = None
        self.rotation_task: asyncio.Task | None = None
        self.total_estimate = 0.0           # predicted total wait, for the remaining-time checks
        self.report: SecurityReport | None = None
        self.state: State | None = None
        self.closed = False
        self.closed_reason: str | None = None
        self.current: AdDecision | None = None
        self.shown_at: float | None = None
        self.log: list[dict] = []              # every decision, with the exact signal the ad side saw
        self.exposures: list[dict] = []        # [{ad_id, format, start, end, visible_s, billable}]

    # ---------------------------------------------------------------- called by the orchestrator

    def start(self, state: State, estimated_wait_s: float) -> None:
        self.total_estimate = estimated_wait_s
        self._guard(self._spawn, None, state, estimated_wait_s)

    def on_security(self, report: SecurityReport, state: State, remaining_wait_s: float) -> None:
        self._guard(self._spawn, report, state, remaining_wait_s)

    def close(self, reason: str) -> None:
        self._guard(self._close, reason)

    # ---------------------------------------------------------------- internals

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - the slot must never break the workflow
            log.exception("ad slot error (ignored)")

    def _elapsed(self) -> float:
        return self.clock.now() - self.t0

    def _spawn(self, report: SecurityReport | None, state: State, wait_s: float) -> None:
        if self.closed or self.client is None:
            return
        self.report, self.state = report, state
        signal = build_signal(self.session, state, report, wait_s, self.cfg)
        if report is not None and signal.ad_mode == "none":
            # Security said BLOCK/REVIEW, or the context forbids ads: withdraw right away, locally,
            # without asking the ad side anything.
            self._cancel_task()
            self._hide("withdrawn:" + ("security_" + report.action.value if report.action.value != "allow" else "sensitive_context"))
            self.log.append({"t": round(self._elapsed(), 2), "phase": signal.phase, "signal": asdict(signal),
                             "sent_to_ad_engine": False,
                             "decision": {"show_ad": False, "reason": "withdrawn_by_policy"}})
            return
        if report is not None and self.current is None:
            self._cancel_task()   # a delayed ad not shown yet is re-planned with the new information
        wait = 0.0
        if self.current is not None:
            # Don't replace a creative that has only just appeared: let it stay min_current_visible_s.
            on_screen = self._elapsed() - self.shown_at
            wait = max(0.0, self.cfg["swap"]["min_current_visible_s"] - on_screen)
            signal = build_signal(self.session, state, report, max(0.0, wait_s - wait), self.cfg)
        self.task = asyncio.get_running_loop().create_task(self._decide(signal, wait))

    async def _decide(self, signal, wait: float = 0.0) -> None:
        try:
            if wait:
                await self.clock.sleep(wait)
                if self.closed or self.current is None:
                    return
            active = self.current is not None
            decision = await self.client.decide(
                signal, now=self.clock.now(), elapsed_s=self._elapsed(), slot_active=active,
                current_ad=self.current.ad_id if active else None)
            self.log.append({"t": round(self._elapsed(), 2), "phase": signal.phase, "signal": asdict(signal),
                             "sent_to_ad_engine": True, "decision": decision.log()})
            if self.closed or not decision.show_ad:
                if self.closed and decision.show_ad:
                    self.log[-1]["decision"]["reason"] += "|arrived_after_processing"
                return
            if active:
                self._swap(decision)
                return
            if decision.delay > 0:
                await self.clock.sleep(decision.delay)
                if self.closed:
                    return
            self._show(decision)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("ad decision task failed (ignored)")

    def _show(self, decision: AdDecision) -> None:
        self.current = decision
        self.shown_at = self._elapsed()
        self.client.record_impression(self.session.session_id, self.clock.now())
        self.emit("ad_show", {"ad": decision.ad.public(), "format": decision.format, "decision": decision.log()})
        rot = self.cfg.get("rotation", {})
        if rot.get("enabled") and self.rotation_task is None:
            self.rotation_task = asyncio.get_running_loop().create_task(self._rotate(rot))

    async def _rotate(self, rot: dict) -> None:
        """Long waits: put the next sponsor in the line every `every_s` seconds, while enough wait
        is predicted to remain, up to max_creatives_per_request. Not a new slot: no capping."""
        try:
            while not self.closed and self.current is not None:
                due = self.shown_at + rot["every_s"] - self._elapsed()
                if due > 1e-6:
                    await self.clock.sleep(due)
                    continue
                if len(self.exposures) + 1 >= rot["max_creatives_per_request"]:
                    return
                remaining = max(0.0, self.total_estimate - self._elapsed())
                signal = build_signal(self.session, self.state or State.GENERATING, self.report, remaining, self.cfg)
                if signal.ad_mode == "none":
                    return
                shown = tuple(e["ad_id"] for e in self.exposures) + (self.current.ad_id,)
                decision = await self.client.decide(signal, now=self.clock.now(), elapsed_s=self._elapsed(),
                                                    slot_active=True, current_ad=self.current.ad_id, rotate=True,
                                                    recent=shown)
                self.log.append({"t": round(self._elapsed(), 2), "phase": "rotation", "signal": asdict(signal),
                                 "sent_to_ad_engine": True, "decision": decision.log()})
                if self.closed or self.current is None or not decision.show_ad or decision.ad_id == self.current.ad_id:
                    return
                self._swap(decision)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("ad rotation failed (ignored)")

    def _swap(self, decision: AdDecision) -> None:
        if decision.ad_id == self.current.ad_id:
            return
        fmt = self.current.format
        self._end_exposure()
        self.current = AdDecision(**{**decision.__dict__, "format": fmt})
        self.shown_at = self._elapsed()
        self.emit("ad_show", {"ad": decision.ad.public(), "format": fmt, "decision": self.current.log(), "swap": True})

    def _end_exposure(self) -> float:
        end = self._elapsed()
        visible = max(0.0, end - self.shown_at)
        self.exposures.append({"ad_id": self.current.ad_id, "format": self.current.format,
                               "start": round(self.shown_at, 2), "end": round(end, 2), "visible_s": round(visible, 2),
                               "billable": visible >= self.cfg["min_viewable_s"]})
        return visible

    def _hide(self, reason: str) -> None:
        if self.current is None:
            return
        visible = self._end_exposure()
        self.current, self.shown_at = None, None
        self.emit("ad_hide", {"reason": reason, "visible_s": round(visible, 2)})

    def _cancel_task(self) -> None:
        for task in (self.task, self.rotation_task):
            if task and not task.done():
                task.cancel()
        self.rotation_task = None

    def _close(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        self.closed_reason = reason
        self._cancel_task()
        self._hide(reason)

    # ---------------------------------------------------------------- metrics

    def metrics(self) -> dict:
        return {
            "ad_shown": bool(self.exposures),
            "ad_visible_s": round(sum(e["visible_s"] for e in self.exposures), 2),
            "impressions": sum(1 for e in self.exposures if e["billable"]),
            "creatives_shown": len(self.exposures),
            "first_ad_at": self.exposures[0]["start"] if self.exposures else None,
            "format": self.exposures[0]["format"] if self.exposures else "none",
            "ad_decision_ms": [d["decision"].get("decision_ms") for d in self.log if "decision_ms" in d["decision"]],
            "decision_reason": self.log[-1]["decision"]["reason"] if self.log else "no_decision",
            "closed_reason": self.closed_reason,
        }

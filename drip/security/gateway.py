"""Security gateway: PDF -> SecurityReport.

The pipeline (extract -> detect -> classify -> policy) is shared. Only the detector differs:
MockDetector (rules in this repo, Mode 1) or SieveDetector (Mode 2, security_sieve.py).

Nothing in here knows that ads exist.
"""

import asyncio
import hashlib
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from ..latency import SECURITY_STAGE_SPLIT, Clock, LatencyProfile
from ..models import Finding, SecurityAction, SecurityReport, Sensitivity, State
from . import injection, pii
from .classifier import classify
from .pdf import Extraction, extract_text

OnState = Callable[[State], Awaitable[None]]
PROMPT_INPUT_SHARE = 0.6    # prompt mode: input guard vs output guard share of the security latency


@dataclass
class Detection:
    findings: list[Finding]
    masked_text: str
    pii_counts: dict[str, int] = field(default_factory=dict)


class Detector(Protocol):
    name: str

    def detect(self, text: str, instruction: str) -> Detection: ...          # a document (+ instruction)

    def detect_message(self, text: str) -> Detection: ...                    # a chat prompt

    def check_output(self, answer: str) -> tuple[str, str, list[Finding]]: ...   # (text, action, findings)


def pii_finding(counts: dict[str, int]) -> list[Finding]:
    return [Finding("pii", "info", "masked:" + ",".join(sorted(counts)))] if counts else []


class MockDetector:
    name = "mock"

    def detect(self, text: str, instruction: str) -> Detection:
        findings = injection.check(text, "document") + injection.check(instruction, "instruction")
        masked, counts = pii.scan(text)
        return Detection(findings + pii_finding(counts), masked, counts)

    def detect_message(self, text: str) -> Detection:
        masked, counts = pii.scan(text)
        return Detection(injection.check(text, "instruction") + pii_finding(counts), masked, counts)

    def check_output(self, answer: str) -> tuple[str, str, list[Finding]]:
        masked, counts = pii.scan(answer)
        return masked, "allow", pii_finding(counts)


class PolicyEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def sensitivity(self, categories: tuple[str, ...], pii_counts: dict[str, int]) -> Sensitivity:
        # The most sensitive category present wins, not the most frequent one.
        s = Sensitivity.max(*(Sensitivity(self.cfg["category_sensitivity"].get(c, "medium")) for c in categories))
        if pii_counts:
            s = Sensitivity.max(s, Sensitivity.MEDIUM)
        if set(pii_counts) & set(self.cfg["pii_high_types"]) or sum(pii_counts.values()) >= self.cfg["pii_high_count"]:
            s = Sensitivity.HIGH
        return s

    def decide(self, ext: Extraction, det: Detection | None, category: str, source: str,
               present: tuple[str, ...] = ()) -> SecurityReport:
        def report(action, reason, findings=(), sensitivity=Sensitivity.HIGH, text="", pii_types=()):
            return SecurityReport(action, category, sensitivity, tuple(findings), tuple(pii_types), text,
                                  ext.pages, len(ext.text), source, reason, categories_present=present)

        # Fail closed: what we cannot read, we cannot inspect.
        if ext.error:
            return report(SecurityAction.BLOCK, f"unreadable:{ext.error}", [Finding("document", "block", ext.error)])
        if not ext.text.strip():
            return report(SecurityAction.BLOCK, "no_extractable_text", [Finding("document", "block", "empty_or_image_only")])

        assert det is not None
        findings = list(det.findings)
        if len(ext.text) > self.cfg["max_chars"]:
            findings.append(Finding("document", "review", f"partially_inspected:{len(ext.text)}_chars"))

        sensitivity = self.sensitivity((category, *present), det.pii_counts)
        llm_text = det.masked_text[: self.cfg["llm_max_chars"]]
        pii_types = sorted(det.pii_counts)
        blocking = [f for f in findings if f.severity == "block"]
        reviewing = [f for f in findings if f.severity == "review"]
        if blocking:
            return report(SecurityAction.BLOCK, blocking[0].detail, findings, sensitivity, "", pii_types)
        if reviewing:
            return report(SecurityAction.REVIEW, reviewing[0].detail, findings, sensitivity, llm_text, pii_types)
        return report(SecurityAction.ALLOW, "passed", findings, sensitivity, llm_text, pii_types)


class SecurityGateway:
    def __init__(self, detector: Detector, policy_cfg: dict, clock: Clock, cache_size: int = 256):
        self.detector = detector
        self.policy = PolicyEngine(policy_cfg)
        self.cfg = policy_cfg
        self.clock = clock
        # Same bytes + same instruction = same analysis (simulated latency still applies).
        self.cache: OrderedDict[str, tuple] = OrderedDict()
        self.cache_size = cache_size

    def analyze(self, data: bytes, instruction: str) -> tuple[Extraction, Detection | None, str, tuple[str, ...]]:
        key = hashlib.sha256(data + b"\x00" + instruction.encode()).hexdigest()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        ext = extract_text(data)
        det, category, present = None, "unknown", ()
        if not ext.error and ext.text.strip():
            text = ext.text[: self.cfg["max_chars"]]
            det = self.detector.detect(text, instruction)
            category, present = classify(text)
        self.cache[key] = (ext, det, category, present)
        if len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return ext, det, category, present

    def analyze_prompt(self, prompt: str) -> tuple[Extraction, Detection | None, str, tuple[str, ...]]:
        ext = Extraction(prompt, 0, None)
        if not prompt.strip():
            return ext, None, "unknown", ()
        text = prompt[: self.cfg["max_chars"]]
        category, present = classify(text)
        return ext, self.detector.detect_message(text), category, present

    async def run(self, fn, *args):
        # In virtual time a thread would let the clock jump ahead while it works, so run inline.
        return fn(*args) if self.clock.virtual else await asyncio.to_thread(fn, *args)

    @property
    def name(self) -> str:
        return self.detector.name

    async def inspect(self, data: bytes, instruction: str, profile: LatencyProfile, on_state: OnState) -> SecurityReport:
        if profile.security_hang:
            await self.clock.sleep(10**9)
        stage = lambda name: profile.security_s * SECURITY_STAGE_SPLIT[name]

        # The analysis runs once, at the start; the visible sub-states pace the simulated latency.
        await on_state(State.SCANNING)
        t = time.perf_counter()
        ext, det, category, present = await self.run(self.analyze, data, instruction)
        report = self.policy.decide(ext, det, category, self.detector.name, present)
        report = replace(report, compute_ms=round((time.perf_counter() - t) * 1000, 2))
        await self.clock.sleep(stage("scanning"))
        await on_state(State.ANALYZING)
        await self.clock.sleep(stage("analyzing"))
        await on_state(State.POLICY_CHECK)
        await self.clock.sleep(stage("policy_check"))
        return report


    async def inspect_prompt(self, prompt: str, profile: LatencyProfile, on_state: OnState) -> SecurityReport:
        """Input guard for a chat message. Uses PROMPT_INPUT_SHARE of the security latency; the rest
        is spent on the output check."""
        if profile.security_hang:
            await self.clock.sleep(10**9)
        budget = profile.security_s * PROMPT_INPUT_SHARE
        await on_state(State.SCANNING)
        t = time.perf_counter()
        ext, det, category, present = await self.run(self.analyze_prompt, prompt)
        report = self.policy.decide(ext, det, category, self.detector.name, present)
        if not prompt.strip():
            report = replace(report, reason="empty_prompt")
        report = replace(report, compute_ms=round((time.perf_counter() - t) * 1000, 2))
        await self.clock.sleep(budget * 0.7)
        await on_state(State.POLICY_CHECK)
        await self.clock.sleep(budget * 0.3)
        return report

    async def check_output(self, answer: str, profile: LatencyProfile, on_state: OnState) -> tuple[str, str, list[Finding]]:
        """Output guard: the answer is held back until it has been checked. This is the part of
        the security wait that streaming can't hide."""
        await on_state(State.OUTPUT_CHECK)
        result = await self.run(self.detector.check_output, answer)
        await self.clock.sleep(profile.security_s * (1 - PROMPT_INPUT_SHARE))
        return result


def make_gateway(mode: str, policy_cfg: dict, clock: Clock) -> SecurityGateway:
    if mode == "mock":
        return SecurityGateway(MockDetector(), policy_cfg, clock)
    if mode == "sieve":
        from .sieve_adapter import SieveDetector
        return SecurityGateway(SieveDetector(), policy_cfg, clock)
    raise ValueError(f"unknown security mode: {mode}")

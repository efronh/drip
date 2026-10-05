"""Shared types.

The types are split on purpose: the security side produces a SecurityReport (which may hold
document text), the ad side only ever receives a ContextSignal (which cannot). Nothing on the ad
side returns a value that the orchestrator uses for control flow.
"""

from dataclasses import dataclass, field
from enum import Enum


class SecurityAction(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class Sensitivity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return ("low", "medium", "high").index(self.value)

    @classmethod
    def max(cls, *values: "Sensitivity") -> "Sensitivity":
        return max(values, key=lambda s: s.rank)


class State(str, Enum):
    UPLOADING = "uploading"
    SCANNING = "scanning"
    ANALYZING = "analyzing"
    POLICY_CHECK = "policy_check"
    REVIEW = "review"           # waiting for the user / app policy on a REVIEW decision
    GENERATING = "generating"   # document mode
    THINKING = "thinking"       # prompt mode
    WRITING = "writing"         # prompt mode
    OUTPUT_CHECK = "output_check"   # prompt mode: the answer is checked before it is shown
    COMPLETED = "completed"
    BLOCKED = "blocked"
    ERROR = "error"
    CANCELLED = "cancelled"


STATE_LABELS = {
    State.UPLOADING: "Uploading document...",
    State.SCANNING: "Checking document security...",
    State.ANALYZING: "Analyzing content...",
    State.POLICY_CHECK: "Applying security policy...",
    State.REVIEW: "Waiting for review decision...",
    State.GENERATING: "Generating summary...",
    State.THINKING: "Thinking...",
    State.WRITING: "Writing the answer...",
    State.OUTPUT_CHECK: "Checking the answer...",
    State.COMPLETED: "Done",
    State.BLOCKED: "Blocked by security policy",
    State.ERROR: "Something went wrong",
    State.CANCELLED: "Cancelled",
}
# Prompt mode reuses the security states with wording that fits a chat message.
PROMPT_LABELS = {**STATE_LABELS, State.SCANNING: "Checking your message...",
                 State.ANALYZING: "Looking for personal data...", State.POLICY_CHECK: "Applying security policy..."}


def label(state: State, kind: str = "pdf") -> str:
    return (PROMPT_LABELS if kind == "prompt" else STATE_LABELS)[state]


CATEGORIES = (
    "financial", "medical", "legal", "hr", "personal", "confidential_business",
    "general", "travel", "shopping", "education",
)


@dataclass(frozen=True)
class Finding:
    check: str                  # e.g. "prompt_injection", "pii", "document"
    severity: str               # "info" | "review" | "block"
    detail: str                 # short machine-readable reason, never document text


@dataclass(frozen=True)
class SecurityReport:
    action: SecurityAction
    category: str
    sensitivity: Sensitivity
    findings: tuple[Finding, ...]
    pii_types: tuple[str, ...]
    text_for_llm: str           # masked, length-limited; empty unless action != BLOCK
    pages: int
    chars: int
    source: str                 # "mock" | "sieve"
    reason: str                 # main reason for the action
    compute_ms: float = 0.0     # real CPU time of the inspection (simulated latency is separate)
    categories_present: tuple[str, ...] = ()   # every category with evidence, for sensitivity

    @property
    def all_categories(self) -> tuple[str, ...]:
        return tuple(sorted({self.category, *self.categories_present}))

    def summary(self) -> dict:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "category": self.category,
            "categories_present": list(self.categories_present),
            "sensitivity": self.sensitivity.value,
            "pii_types": list(self.pii_types),
            "findings": [f.__dict__ for f in self.findings],
            "pages": self.pages,
            "chars": self.chars,
            "source": self.source,
        }


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    tier: str = "free"                          # "free" | "premium"
    contextual_ads_consent: bool = True         # user may opt out of contextual ads
    allowed_ad_categories: tuple[str, ...] | None = None   # app-level allowlist, None = all
    locale: str = "tr-TR"


@dataclass(frozen=True)
class ContextSignal:
    """The only thing the Ad Decision Engine ever sees.

    Every field is coarse and enumerable. There is no free-text field, so document text, PII or
    filenames cannot be passed through by accident. privacy.build_signal() is the only producer.
    """
    session_id: str             # opaque, rotated per browser session; used only for capping
    state: str                  # processing state
    phase: str                  # "pre_decision" | "post_decision"
    category: str | None        # None before the security decision
    sensitivity: str | None
    ad_mode: str                # "contextual" | "generic_only" | "none" (from the sensitivity policy)
    estimated_wait_s: float     # rounded to 0.5 s
    tier: str
    contextual_consent: bool
    allowed_ad_categories: tuple[str, ...] | None
    locale: str


@dataclass(frozen=True)
class Ad:
    id: str
    advertiser: str
    category: str               # ad category, or "generic" for untargeted ads
    headline: str
    body: str
    cta_label: str
    cta_url: str
    ecpm_usd: float

    def public(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "advertiser", "category", "headline", "body", "cta_label", "cta_url")}


@dataclass(frozen=True)
class AdDecision:
    show_ad: bool
    reason: str
    ad_id: str | None = None
    category: str | None = None
    duration: float = 0.0       # planned max display time in seconds (0 = until processing ends)
    format: str = "none"        # "none" | "status_line" | "compact" | "card"
    delay: float = 0.0          # seconds to wait before showing (Delayed strategy)
    strategy: str = ""
    decision_ms: float = 0.0
    ad: Ad | None = field(default=None, compare=False)

    def log(self) -> dict:
        return {
            "show_ad": self.show_ad, "ad_id": self.ad_id, "reason": self.reason,
            "duration": self.duration, "category": self.category, "format": self.format,
            "delay": self.delay, "strategy": self.strategy, "decision_ms": round(self.decision_ms, 2),
        }


NO_AD = AdDecision(False, "not_requested")

"""Ad Decision Engine.

Input: a ContextSignal (coarse enums only, see privacy.py) plus timing. Output: an AdDecision.
It has no access to the document, the SecurityReport or the LLM, and nothing it returns is used
for anything but drawing the sponsored card.
"""

import re
import time
from dataclasses import dataclass, field

from ..config import load_json
from ..models import Ad, AdDecision, ContextSignal

# ---------------------------------------------------------------- inventory

MAX_TEXT = {"advertiser": 40, "headline": 80, "body": 140, "cta_label": 30}
CONTROL = re.compile(r"[\x00-\x1f\x7f<>]")


def validate_creative(ad: Ad) -> str | None:
    """Ad creatives are third-party content. They are rendered as text and never reach the LLM,
    but are still checked: https only, bounded length, no markup or control characters."""
    if not ad.cta_url.startswith("https://"):
        return "cta_not_https"
    for name, limit in MAX_TEXT.items():
        value = getattr(ad, name)
        if not value or len(value) > limit:
            return f"{name}_length"
        if CONTROL.search(value):
            return f"{name}_markup"
    return None


def load_inventory(raw: list[dict] | None = None) -> list[Ad]:
    ads = [Ad(**a) for a in (raw if raw is not None else load_json("ads.json")["ads"])]
    return [a for a in ads if validate_creative(a) is None]


# ---------------------------------------------------------------- frequency capping

@dataclass
class FrequencyCapper:
    max_per_session: int = 2
    min_interval_s: float = 300.0
    history: dict[str, list[float]] = field(default_factory=dict)

    def check(self, session_id: str, now: float) -> str | None:
        shown = self.history.get(session_id, [])
        if len(shown) >= self.max_per_session:
            return "frequency_cap:max_per_session"
        if shown and now - shown[-1] < self.min_interval_s:
            return "frequency_cap:min_interval"
        return None

    def record(self, session_id: str, now: float) -> None:
        self.history.setdefault(session_id, []).append(now)

    def count(self, session_id: str) -> int:
        return len(self.history.get(session_id, []))

    def reset(self, session_id: str) -> None:
        self.history.pop(session_id, None)


# ---------------------------------------------------------------- strategies

@dataclass(frozen=True)
class Plan:
    show: bool
    format: str = "none"
    delay: float = 0.0
    reason: str = ""


class Immediate:
    """A: show as soon as processing starts."""
    name = "immediate"

    def __init__(self, cfg: dict):
        self.format = cfg["immediate"]["format"]

    def plan(self, signal: ContextSignal, elapsed_s: float) -> Plan:
        return Plan(True, self.format, 0.0, "immediate")


class Delayed:
    """B: show only once the user has already waited show_after_s."""
    name = "delayed"

    def __init__(self, cfg: dict):
        self.after = cfg["delayed"]["show_after_s"]
        self.format = cfg["delayed"]["format"]

    def plan(self, signal: ContextSignal, elapsed_s: float) -> Plan:
        return Plan(True, self.format, max(0.0, self.after - elapsed_s), f"delayed_{self.after:g}s")


class Adaptive:
    """C: size the ad to the *predicted* remaining wait: none, compact or card."""
    name = "adaptive"

    def __init__(self, cfg: dict):
        self.no_ad_below = cfg["adaptive"]["no_ad_below_s"]
        self.card_from = cfg["adaptive"]["card_from_s"]

    def plan(self, signal: ContextSignal, elapsed_s: float) -> Plan:
        wait = signal.estimated_wait_s
        if wait < self.no_ad_below:
            return Plan(False, reason=f"short_wait:{wait:g}s")
        fmt = "card" if wait >= self.card_from else "compact"
        return Plan(True, fmt, 0.0, f"adaptive_{fmt}")


STRATEGIES = {s.name: s for s in (Immediate, Delayed, Adaptive)}


# ---------------------------------------------------------------- engine

class AdDecisionEngine:
    def __init__(self, ads_cfg: dict, inventory: list[Ad] | None = None, capper: FrequencyCapper | None = None,
                 strategy: str | None = None):
        self.cfg = ads_cfg
        self.inventory = load_inventory() if inventory is None else inventory
        f = ads_cfg["frequency"]
        self.capper = capper or FrequencyCapper(f["max_ads_per_session"], f["min_interval_s"])
        self.strategy = STRATEGIES[strategy or ads_cfg["strategy"]](ads_cfg)

    def candidates(self, signal: ContextSignal) -> tuple[list[Ad], str]:
        allowed = signal.allowed_ad_categories
        ok = lambda a: allowed is None or a.category in allowed or a.category == "generic"
        if signal.ad_mode == "contextual" and signal.category:
            matched = [a for a in self.inventory if a.category == signal.category and ok(a)]
            if matched:
                return matched, f"contextual_match:{signal.category}"
        generic = [a for a in self.inventory if a.category == "generic" and ok(a)]
        return generic, "non_sensitive_context" if signal.ad_mode == "contextual" else "generic_untargeted"

    def pick(self, ads: list[Ad], signal: ContextSignal, exclude: str | None, recent: tuple[str, ...] = ()) -> Ad:
        # Highest eCPM first, rotated so a session doesn't see the same creative twice in a row;
        # within one request, prefer creatives not shown yet.
        ranked = (sorted((a for a in ads if a.id != exclude and a.id not in recent), key=lambda a: (-a.ecpm_usd, a.id))
                  or sorted((a for a in ads if a.id != exclude), key=lambda a: (-a.ecpm_usd, a.id)) or ads)
        return ranked[self.capper.count(signal.session_id) % len(ranked)]

    def decide(self, signal: ContextSignal, *, now: float, elapsed_s: float = 0.0,
               slot_active: bool = False, current_ad: str | None = None, rotate: bool = False,
               recent: tuple[str, ...] = ()) -> AdDecision:
        t0 = time.perf_counter()
        name = self.strategy.name

        def no(reason):
            return AdDecision(False, reason, strategy=name, decision_ms=(time.perf_counter() - t0) * 1000)

        if signal.ad_mode == "none":
            return no("premium_user" if signal.tier == "premium" else "policy:ads_disabled_for_context")

        if slot_active:
            # Upgrade an ad that is already on screen (generic -> contextual). Not a new slot:
            # no capping, no new timing decision; skipped if the new creative would only flash.
            if signal.estimated_wait_s < self.cfg["swap"]["min_remaining_s"]:
                return no("keep_current:short_remaining_wait")
            ads, reason = self.candidates(signal)
            if rotate:
                # Long wait: next sponsor in the same line. Pool = contextual matches + untargeted
                # creatives (always allowed when targeting is); anything but what was already shown.
                generic = self.candidates(ContextSignal(**{**signal.__dict__, "ad_mode": "generic_only"}))[0]
                ads = ads + [a for a in generic if a not in ads]
                if not [a for a in ads if a.id != current_ad]:
                    return no("keep_current:no_alternative")
                reason = "rotation:" + reason
            elif not ads or not reason.startswith("contextual"):
                return no("keep_current")
            ad = self.pick(ads, signal, exclude=current_ad, recent=recent)
            return AdDecision(True, reason, ad.id, ad.category, round(signal.estimated_wait_s, 1), "keep", 0.0,
                              name, (time.perf_counter() - t0) * 1000, ad)

        capped = self.capper.check(signal.session_id, now)
        if capped:
            return no(capped)
        plan = self.strategy.plan(signal, elapsed_s)
        if not plan.show:
            return no(plan.reason)
        ads, reason = self.candidates(signal)
        if not ads:
            return no("no_suitable_ad")
        ad = self.pick(ads, signal, exclude=None)
        duration = max(0.0, signal.estimated_wait_s - plan.delay)
        # On the status-line surface the ad *is* the "Thinking..." line: one size, one line.
        fmt = "status_line" if self.cfg.get("surface") == "status_line" else plan.format
        return AdDecision(True, reason, ad.id, ad.category, round(duration, 1), fmt, round(plan.delay, 2),
                          name, (time.perf_counter() - t0) * 1000, ad)

    def record_impression(self, session_id: str, now: float) -> None:
        self.capper.record(session_id, now)

"""Data minimization between the security boundary and the ad side.

    PDF ──► Security / Classification ──► build_signal() ──► ContextSignal ──► Ad Decision Engine
                     (full text)            (this file)     (coarse enums)

build_signal() is the only producer of ContextSignal. It reads a handful of fields from the
SecurityReport and never its text, findings or PII. The ad mode (contextual / generic_only / none)
is decided here, inside the trusted boundary, so the ad engine cannot widen it.
"""

from dataclasses import fields

from .models import CATEGORIES, ContextSignal, SecurityAction, SecurityReport, SessionContext, State

SIGNAL_FIELDS = frozenset(f.name for f in fields(ContextSignal))
MODES = ("none", "generic_only", "contextual")


def narrowest(*modes: str) -> str:
    return min(modes, key=MODES.index)


def ad_mode(report: SecurityReport | None, session: SessionContext, ads_cfg: dict) -> str:
    """What kind of ad, if any, this moment allows. Before the security decision there is no
    category yet, so only untargeted ads are possible."""
    if not ads_cfg.get("enabled", True) or session.tier == "premium":
        return "none"
    if report is None:
        mode = ads_cfg["pre_decision_mode"]
    elif report.action != SecurityAction.ALLOW:
        return "none"   # BLOCK / REVIEW: no ad next to a security decision
    else:
        # Every category present constrains the mode, so keyword stuffing can't widen it.
        mode = narrowest(*(ads_cfg["category_modes"].get(c, "none") for c in report.all_categories),
                         ads_cfg["sensitivity_caps"][report.sensitivity.value])
    if not session.contextual_ads_consent:
        mode = narrowest(mode, "generic_only")
    return mode


def build_signal(session: SessionContext, state: State, report: SecurityReport | None,
                 estimated_wait_s: float, ads_cfg: dict) -> ContextSignal:
    mode = ad_mode(report, session, ads_cfg)
    contextual = report is not None and mode == "contextual"
    category = report.category if contextual and report.category in CATEGORIES else None
    return ContextSignal(
        session_id=session.session_id,
        state=state.value,
        phase="pre_decision" if report is None else "post_decision",
        # The category is only released when it may be used for targeting.
        category=category,
        sensitivity=report.sensitivity.value if report is not None and contextual else None,
        ad_mode=mode,
        estimated_wait_s=round(estimated_wait_s * 2) / 2,
        tier=session.tier,
        contextual_consent=session.contextual_ads_consent,
        allowed_ad_categories=session.allowed_ad_categories,
        locale=session.locale,
    )

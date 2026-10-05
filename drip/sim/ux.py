"""Simulated user. Turns one job's timeline into perceived wait, annoyance, abandonment, clicks and
satisfaction.

There are no real users behind any of this. The model encodes two claims from the waiting
literature (occupied time feels shorter than unoccupied time; uncertain/unexplained waits feel
longer) and the usual costs of ads (annoyance, fatigue, flashing). Every coefficient lives in
config/sim.json; the evaluation sweeps the one that matters most (ad_occupied_factor).
"""

import random
from dataclasses import dataclass

from ..orchestrator import JobResult

GENERIC_ADS = {"generic"}


@dataclass
class Outcome:
    wait_s: float           # time until the final screen (answer or security verdict), or abandonment
    perceived_s: float
    annoyance: float
    abandoned: bool
    completed: bool         # reached the final screen
    answered: bool          # got an LLM answer
    ad_shown: bool
    ad_visible_s: float
    impressions: int        # billable
    contextual_impressions: int
    flashes: int            # ads visible for less than flash_below_s
    clicks: float           # expected clicks
    revenue_usd: float      # billable impressions x eCPM / 1000
    satisfaction: float


def _covered_until(exposures: list[dict], t: float) -> float:
    return sum(max(0.0, min(e["end"], t) - e["start"]) for e in exposures)


def _abandon_time(wait: float, exposures: list[dict], factor: float, patience: float) -> float | None:
    """First moment the perceived wait reaches the user's patience, if before the end."""
    edges = sorted({0.0, wait, *[e["start"] for e in exposures], *[e["end"] for e in exposures]})
    perceived = 0.0
    for a, b in zip(edges, edges[1:]):
        if a >= wait:
            break
        b = min(b, wait)
        covered = any(e["start"] <= a and e["end"] >= b for e in exposures)
        rate = factor if covered else 1.0
        if perceived + (b - a) * rate >= patience:
            return a + (patience - perceived) / rate
        perceived += (b - a) * rate
    return None


def evaluate(result: JobResult, *, patience: float, prior_ads: int, params: dict, rng: random.Random,
             ecpm: dict[str, float], factor: float | None = None, expectation: float = 1.0) -> Outcome:
    """expectation scales patience and the satisfaction tolerance: a user who asked for a long report
    expects to wait longer than one who asked a yes/no question."""
    patience *= expectation
    p_ann, p_sat, p_clk = params["annoyance"], params["satisfaction"], params["clicks"]
    factor = params["perception"]["ad_occupied_factor"] if factor is None else factor
    wait = result.metrics["total_s"]
    exposures = [dict(e) for e in result.exposures]

    # A "flash" is an ad the *system* showed for too short a time. Computed before abandonment
    # truncation: a user leaving mid-ad is not the system flashing an ad.
    for e in exposures:
        e["flash"] = (e["end"] - e["start"]) < p_ann["flash_below_s"]

    abandoned_at = _abandon_time(wait, exposures, factor, patience)
    # Annoyance can drive a user away too, at the moment the ad appears.
    if abandoned_at is None and exposures:
        pre = p_ann["per_exposure"] + (p_ann["card_extra"] if exposures[0]["format"] == "card" else 0)
        if rng.random() < p_ann["abandon_per_annoyance"] * (pre + p_ann["fatigue_per_prior_ad"] * prior_ads):
            abandoned_at = min(wait, exposures[0]["start"] + 1.0)
    if abandoned_at is not None:
        wait = abandoned_at
        exposures = [{**e, "end": min(e["end"], wait)} for e in exposures if e["start"] < wait]
    for e in exposures:
        e["visible_s"] = e["end"] - e["start"]
        e["billable"] = e["visible_s"] >= 1.0

    covered = _covered_until(exposures, wait)
    perceived = (wait - covered) * params["perception"]["unoccupied_factor"] + covered * factor

    annoyance = 0.0
    if exposures:
        for e in exposures:
            annoyance += p_ann["per_exposure"] + (p_ann["card_extra"] if e["format"] == "card" else 0)
            if e["flash"]:
                annoyance += p_ann["flash_penalty"]
        annoyance += p_ann["swap_penalty"] * (len(exposures) - 1)
        annoyance += p_ann["fatigue_per_prior_ad"] * prior_ads

    billable = [e for e in exposures if e["billable"]]
    mult = params["economics"]["format_ecpm_multiplier"]
    clicks = sum(p_clk["ctr_generic"] if ecpm_cat(e["ad_id"]) in GENERIC_ADS else p_clk["ctr_contextual"]
                 for e in billable)
    revenue = sum(ecpm.get(e["ad_id"], 0) * mult.get(e["format"], 1.0) / 1000 for e in billable)
    impressions = len(billable)

    abandoned = abandoned_at is not None
    sat = p_sat["start"] - p_sat["per_perceived_second_over"] * max(0.0, perceived - p_sat["tolerance_s"] * expectation) \
        - p_sat["per_annoyance"] * annoyance
    if abandoned:
        sat = p_sat["abandoned"]
    return Outcome(
        wait_s=wait, perceived_s=perceived, annoyance=annoyance, abandoned=abandoned,
        completed=not abandoned and result.state.value in ("completed", "blocked"),
        answered=not abandoned and result.state.value == "completed",
        ad_shown=bool(exposures), ad_visible_s=covered, impressions=impressions,
        contextual_impressions=sum(ecpm_cat(e["ad_id"]) not in GENERIC_ADS for e in billable),
        flashes=sum(e["flash"] for e in exposures),
        clicks=clicks, revenue_usd=revenue, satisfaction=max(1.0, min(5.0, sat)),
    )


_AD_CATEGORY: dict[str, str] = {}


def ecpm_cat(ad_id: str) -> str:
    return _AD_CATEGORY.get(ad_id, "generic")


def register_inventory(ads) -> dict[str, float]:
    for a in ads:
        _AD_CATEGORY[a.id] = a.category
    return {a.id: a.ecpm_usd for a in ads}

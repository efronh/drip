"""Experiments behind EVALUATION.md. Everything runs on virtual time and fixed seeds, so the
numbers are reproducible: python -m scripts.run_eval

Control and treatment see the *same* users (same documents, latencies, patience, random draws);
the only difference between the arms is whether the ad engine exists.
"""

import asyncio
import random
import statistics as st
import time
from dataclasses import dataclass, replace

from ..ads.engine import FrequencyCapper, load_inventory
from ..app import build
from ..config import deep_merge, load_json, load_policy
from ..corpus import SAMPLES, TEST_CASES, security_corpus
from ..latency import LLM_LATENCIES, SECURITY_LATENCIES, LatencyProfile, LatencySimulator, VirtualClock, run_virtual
from ..models import SessionContext
from ..security.gateway import make_gateway
from .ux import Outcome, evaluate, register_inventory

DOCS = {d.name: d.pdf() for d in SAMPLES}
SIM = load_json("sim.json")
ECPM = register_inventory(load_inventory())
# Simulated users cannot answer a review prompt: REVIEW is treated as a stop.
NO_REVIEW = {"security": {"review_policy": "block"}}


@dataclass
class Job:
    doc: str                    # sample document name, or the prompt itself when kind == "prompt"
    profile: LatencyProfile
    estimate: float
    gap_after: float
    kind: str = "pdf"
    expectation: float = 1.0    # patience / tolerance multiplier (bigger request, more patience)
    size: str = ""              # workload type, for breakdowns
    true_wait: float = 0.0


@dataclass
class User:
    id: str
    patience: float
    jobs: list[Job]


def draw_users(n: int, seed: int, docs: dict[str, float] | None = None, profile: LatencyProfile | None = None,
               jobs_per_user: int | None = None) -> list[User]:
    rng = random.Random(seed)
    lat = LatencySimulator(seed=seed + 1, jitter=0.0 if profile else 0.2, estimate_error=0.25)
    mix = docs or SIM["document_mix"]
    names, weights = list(mix), list(mix.values())
    u = SIM["users"]
    users = []
    for i in range(n):
        k = jobs_per_user or 1 + int(rng.expovariate(1 / (u["jobs_per_session_mean"] - 1)))
        jobs = []
        for _ in range(min(k, 8)):
            p = lat.sample(profile)
            jobs.append(Job(rng.choices(names, weights)[0], p, lat.estimate(p), rng.uniform(*u["gap_between_jobs_s"])))
        users.append(User(f"u{seed}-{i}", rng.lognormvariate(0, u["patience_sigma"]) * u["patience_median_s"], jobs))
    return users


def run_arm(users: list[User], *, ads: bool, strategy: str = "adaptive", overrides: dict | None = None,
            mode: str = "mock", surface: str | None = None) -> list[list]:
    clock = VirtualClock()
    policy = load_policy(overrides)
    f = policy["ads"]["frequency"]
    orch = build(clock, mode, strategy, ads, deep_merge(NO_REVIEW, overrides or {}),
                 capper=FrequencyCapper(f["max_ads_per_session"], f["min_interval_s"]), surface=surface)

    async def one(user: User):
        out = []
        for job in user.jobs:
            data = job.doc if job.kind == "prompt" else DOCS[job.doc]
            out.append(await orch.run(data, "", SessionContext(user.id), job.profile, job.estimate, kind=job.kind))
            await clock.sleep(job.gap_after)
        return out

    async def main():
        return await asyncio.gather(*(one(u) for u in users))

    return run_virtual(main())


def outcomes(users: list[User], results: list[list], seed: int = 0, factor: float | None = None):
    """Yields (user, job, result, outcome) for every request, with the per-session ad history."""
    for i, (user, jobs) in enumerate(zip(users, results)):
        rng = random.Random(seed * 1_000_003 + i)
        prior = 0
        for job, r in zip(user.jobs, jobs):
            o = evaluate(r, patience=user.patience, prior_ads=prior, params=SIM, rng=rng, ecpm=ECPM, factor=factor,
                         expectation=job.expectation)
            prior += o.ad_shown
            yield user, job, r, o


def score(users: list[User], results: list[list], seed: int = 0, factor: float | None = None) -> dict:
    outs: list[Outcome] = []
    eligible_users: set[str] = set()
    ads_seen: dict[str, int] = {u.id: 0 for u in users}
    for user, _, r, o in outcomes(users, results, seed, factor):
        outs.append(o)
        ads_seen[user.id] += o.ad_shown
        if any(e["signal"]["ad_mode"] != "none" for e in r.ad_log):
            eligible_users.add(user.id)
    eligible_sessions = len(eligible_users)
    ads_per_session = list(ads_seen.values())

    n = len(outs)
    shown = [o for o in outs if o.ad_shown]
    imps = sum(o.impressions for o in outs)
    q = lambda xs, p: st.quantiles(xs, n=100)[p - 1] if len(xs) > 1 else (xs[0] if xs else 0)
    return {
        "sessions": len(users), "jobs": n,
        "completion_rate": sum(o.completed for o in outs) / n,
        "answered_rate": sum(o.answered for o in outs) / n,
        "abandonment_rate": sum(o.abandoned for o in outs) / n,
        "mean_wait_s": st.fmean(o.wait_s for o in outs),
        "mean_perceived_s": st.fmean(o.perceived_s for o in outs),
        "p90_perceived_s": q([o.perceived_s for o in outs], 90),
        "satisfaction": st.fmean(o.satisfaction for o in outs),
        "mean_annoyance": st.fmean(o.annoyance for o in outs),
        "ad_eligible_sessions": eligible_sessions,
        "jobs_with_ad": len(shown),
        "ad_impressions": imps,
        "contextual_share": (sum(o.contextual_impressions for o in outs) / imps) if imps else 0.0,
        "mean_ad_display_s": st.fmean(o.ad_visible_s for o in shown) if shown else 0.0,
        "ad_visibility_share": (sum(o.ad_visible_s for o in outs) / sum(o.wait_s for o in outs)) if n else 0,
        "flash_rate": (sum(o.flashes for o in shown) / len(shown)) if shown else 0.0,
        "ctr": (sum(o.clicks for o in outs) / imps) if imps else 0.0,
        "ads_per_session": st.fmean(ads_per_session),
        "revenue_usd": sum(o.revenue_usd for o in outs),
        "revenue_per_1000_jobs_usd": 1000 * sum(o.revenue_usd for o in outs) / n,
        "sponsors_per_shown_request": (sum(o.impressions for o in shown) / len(shown)) if shown else 0.0,
        "impressions_per_1000_jobs": 1000 * imps / n,
    }


# ------------------------------------------------------------------------- experiments

def ab_test(n_users: int = 4000, seed: int = 42, strategy: str = "adaptive") -> dict:
    users = draw_users(n_users, seed)
    control_r = run_arm(users, ads=False)
    treat_r = run_arm(users, ads=True, strategy=strategy)
    out = {"control": score(users, control_r, seed), "treatment": score(users, treat_r, seed),
           "strategy": strategy, "n_users": n_users, "seed": seed,
           "sweep": {}}
    for f in SIM["perception"]["ad_occupied_factor_sweep"]:
        out["sweep"][str(f)] = {"control": score(users, control_r, seed, f), "treatment": score(users, treat_r, seed, f)}
    # Same treatment users, with ads fully off for every sensitive category (question 4).
    strict = {"ads": {"category_modes": {c: "none" for c in ("financial", "medical", "legal", "personal")}}}
    post_only = {"ads": {"pre_decision_mode": "none"}}
    both = deep_merge(strict, post_only)
    out["variants"] = {
        "card_surface": score(users, run_arm(users, ads=True, strategy=strategy, surface="card"), seed),
        "strict_sensitive": score(users, run_arm(users, ads=True, strategy=strategy, overrides=strict), seed),
        "post_decision_only": score(users, run_arm(users, ads=True, strategy=strategy, overrides=post_only), seed),
        "strict_and_post_decision_only": score(users, run_arm(users, ads=True, strategy=strategy, overrides=both), seed),
    }
    # How many ad seconds landed on documents that turned out to be sensitive?
    out["sensitive_exposure"] = {
        name: sensitive_exposure(users, run_arm(users, ads=True, strategy=strategy, overrides=o))
        for name, o in [("default", None), ("strict_sensitive", strict), ("post_decision_only", post_only),
                        ("strict_and_post_decision_only", both)]}
    return out


SENSITIVE_DOCS = {"financial_statement", "medical_report", "legal_contract", "hr_review", "pii_personal",
                  "confidential_memo"}


def sensitive_exposure(users: list[User], results: list[list]) -> dict:
    jobs = [(j, r) for u, rs in zip(users, results) for j, r in zip(u.jobs, rs) if j.doc in SENSITIVE_DOCS]
    with_ad = [r for _, r in jobs if r.exposures]
    return {"sensitive_jobs": len(jobs), "with_any_ad": len(with_ad),
            "share_with_ad": len(with_ad) / len(jobs) if jobs else 0.0,
            "mean_ad_seconds": st.fmean(r.metrics["ad_visible_s"] for _, r in jobs) if jobs else 0.0,
            "contextual_ads": sum(1 for _, r in jobs for e in r.exposures if not e["ad_id"].startswith(
                ("food_", "notes_", "ebook_")))}


def strategy_grid(n_users: int = 300, seed: int = 7) -> dict:
    """Single-job sessions on a benign travel document, every latency cell x every strategy."""
    grid = {}
    for s in SECURITY_LATENCIES:
        for l in LLM_LATENCIES:
            profile = LatencyProfile(security_s=s, llm_s=l)
            users = draw_users(n_users, seed, docs={"benign_travel": 1}, profile=profile, jobs_per_user=1)
            base = score(users, run_arm(users, ads=False), seed)
            cell = {"no_ads": base}
            for strat in ("immediate", "delayed", "adaptive"):
                cell[strat] = score(users, run_arm(users, ads=True, strategy=strat), seed)
            grid[f"{s:g}+{l:g}"] = cell
    return grid


def economics(ab: dict, factor: str = "1.0") -> dict:
    """Costs use the *neutral* perception assumption by default (an ad-covered second feels like
    any other second), so any benefit of "occupied time" is not counted as income."""
    eco = SIM["economics"]
    c, t = ab["sweep"][factor]["control"], ab["sweep"][factor]["treatment"]
    jobs = t["jobs"]
    per_k = lambda v: 1000 * v / jobs
    decisions = 2 * jobs   # at most two ad decisions per job (pre and post security)
    infra = per_k(decisions * eco["ad_decision_cost_usd"])
    lost_answers = (c["answered_rate"] - t["answered_rate"]) * 1000   # per 1000 jobs
    ux_cost = lost_answers * eco["value_per_completed_job_usd"]
    imps_k = t["impressions_per_1000_jobs"]
    rows = []
    for e in eco["ecpm_sweep_usd"]:
        revenue = imps_k * e / 1000
        rows.append({"ecpm_usd": e, "revenue_per_1000_jobs": revenue, "infra_cost": infra, "ux_cost": ux_cost,
                     "net": revenue - infra - ux_cost,
                     **{f"covers_{k}": revenue / (v * 1000) for k, v in eco["llm_cost_per_job_usd"].items()}})
    breakeven = (infra + max(0.0, ux_cost)) * 1000 / imps_k if imps_k else None
    # What is a lost answer worth? A free user's one-off job, or a churned subscriber?
    breakeven_by_value = {str(v): ((infra + max(0.0, lost_answers * v)) * 1000 / imps_k if imps_k else None)
                          for v in eco["value_per_lost_answer_sweep_usd"]}
    return {"perception_factor": factor, "impressions_per_1000_jobs": imps_k, "lost_answers_per_1000_jobs": lost_answers,
            "infra_cost_per_1000_jobs": infra, "ux_cost_per_1000_jobs": ux_cost, "rows": rows,
            "breakeven_ecpm_usd": breakeven, "breakeven_by_value_per_lost_answer": breakeven_by_value,
            "formula_check": {
                "eligible_sessions": t["ad_eligible_sessions"],
                "impressions_per_eligible_session": t["ad_impressions"] / max(1, t["ad_eligible_sessions"]),
                "revenue_formula_usd": t["ad_eligible_sessions"] * (t["ad_impressions"] / max(1, t["ad_eligible_sessions"]))
                * (1000 * t["revenue_usd"] / max(1, t["ad_impressions"])) / 1000,
                "revenue_simulated_usd": t["revenue_usd"]}}


def security_eval(mode: str) -> dict:
    policy = load_policy()
    gw = make_gateway(mode, policy["security"], VirtualClock())
    gw.cache_size = 0
    rows = []
    for doc in security_corpus():
        data = doc.pdf()
        t = time.perf_counter()
        ext, det, category, present = gw.analyze(data, "")
        report = gw.policy.decide(ext, det, category, mode, present)
        rows.append({"name": doc.name, "label": doc.label, "kind": doc.kind, "action": report.action.value,
                     "reason": report.reason, "ms": (time.perf_counter() - t) * 1000})
    attacks = [r for r in rows if r["label"] == "attack"]
    benign = [r for r in rows if r["label"] == "benign"]
    rate = lambda xs, f: sum(map(f, xs)) / len(xs) if xs else 0.0
    flagged = lambda r: r["action"] != "allow"
    by = {}
    for key, pick in {"visible": "_visible", "hidden": "_hidden", "footnote": "_footnote"}.items():
        sub = [r for r in attacks if r["name"].endswith(pick)]
        by[f"carrier:{key}"] = {"n": len(sub), "flagged": rate(sub, flagged)}
    for fam in ("en_", "tr_", "evasive_"):
        sub = [r for r in attacks if r["kind"].startswith(fam)]
        by[f"family:{fam.rstrip('_')}"] = {"n": len(sub), "flagged": rate(sub, flagged),
                                           "blocked": rate(sub, lambda r: r["action"] == "block")}
    en_benign = {"benign_course", "benign_course_short", "benign_catalog", "benign_catalog_short", "benign_medical",
                 "benign_medical_short", "benign_hr", "benign_hr_short", "benign_confidential", "benign_confidential_short"}
    ms = [r["ms"] for r in rows]
    return {
        "mode": mode, "n_attacks": len(attacks), "n_benign": len(benign),
        "detection_rate": rate(attacks, flagged),
        "block_rate": rate(attacks, lambda r: r["action"] == "block"),
        "false_negative_rate": 1 - rate(attacks, flagged),
        "false_positive_rate": rate(benign, flagged),
        "false_block_rate": rate(benign, lambda r: r["action"] == "block"),
        "fpr_english_benign": rate([r for r in benign if r["name"] in en_benign], flagged),
        "fpr_turkish_benign": rate([r for r in benign if r["name"].startswith("benign_") and r["name"] not in en_benign], flagged),
        "fpr_lookalikes": rate([r for r in benign if r["name"].startswith("lookalike_")], flagged),
        "latency_ms_mean": st.fmean(ms), "latency_ms_p95": st.quantiles(ms, n=20)[-1],
        "by": by,
        "missed": [r["name"] for r in attacks if not flagged(r)],
        "false_positives": [(r["name"], r["action"], r["reason"]) for r in benign if flagged(r)],
    }


def named_cases(mode: str = "mock") -> list[dict]:
    """The ten named documents: security result and ad result side by side, never merged."""
    clock = VirtualClock()
    profile = LatencyProfile(security_s=4, llm_s=6)
    rows = []
    for doc in TEST_CASES:
        orch = build(clock, mode, "adaptive", True, NO_REVIEW)
        r = run_virtual(orch.run(doc.pdf(), "", SessionContext("case-" + doc.name), profile, profile.total_s))
        sec = r.security
        rows.append({
            "case": doc.name,
            "security": {"action": sec["action"], "reason": sec["reason"], "category": sec["category"],
                         "sensitivity": sec["sensitivity"], "pii": sec["pii_types"]},
            "llm_called": r.llm_called, "final_state": r.state.value,
            "ad": {"shown": bool(r.exposures), "ads": [e["ad_id"] for e in r.exposures],
                   "visible_s": r.metrics["ad_visible_s"], "decision": r.metrics["decision_reason"],
                   "closed_by": r.metrics["closed_reason"],
                   "post_decision_signal_category": next((x["signal"]["category"] for x in r.ad_log
                                                          if x["phase"] == "post_decision"), "—")},
        })
    return rows


def invariance(mode: str = "mock") -> dict:
    clock = VirtualClock()
    profile = LatencyProfile(security_s=3, llm_s=5)
    checked = same = 0
    for doc in SAMPLES:
        off = run_virtual(build(clock, mode, None, False, NO_REVIEW).run(DOCS[doc.name], "", SessionContext("x"), profile, 8.5))
        for strat in ("immediate", "delayed", "adaptive"):
            for failure in (None, "unavailable", "timeout"):
                on = run_virtual(build(clock, mode, strat, True, NO_REVIEW).run(
                    DOCS[doc.name], "", SessionContext("x"), replace(profile, ad_failure=failure), 8.5))
                checked += 1
                same += (on.state, on.security, on.llm_request_fingerprint, on.summary) == \
                        (off.state, off.security, off.llm_request_fingerprint, off.summary)
    return {"runs": checked, "identical": same}


# ------------------------------------------------------------------------- size-driven chat workload

def size_workload(n_users: int = 3000, seed: int = 21) -> dict:
    """Chat requests whose wait follows their size. Compares: no ads, the size predictor with and
    without rotation, no predictor (same estimate for every request), an oracle, and streaming."""
    from . import workload as wl
    from ..predictor import ModelSpeed, predict

    rng = random.Random(seed)
    u, per_1k = SIM["users"], SIM["users"]["patience_per_1k_requested_tokens"]
    plans = []   # per user: (patience, [(request, gap)])
    for _ in range(n_users):
        k = min(8, 1 + int(rng.expovariate(1 / (u["jobs_per_session_mean"] - 1))))
        plans.append((rng.lognormvariate(0, u["patience_sigma"]) * u["patience_median_s"],
                      [(wl.draw(rng), rng.uniform(*u["gap_between_jobs_s"])) for _ in range(k)]))
    reqs = [r for _, rs in plans for r, _ in rs]
    mean_wait = round(st.fmean(r.true_wait_s for r in reqs), 1)
    speed = ModelSpeed()

    def users_for(estimate, streaming=False):
        out = []
        for i, (patience, rs) in enumerate(plans):
            jobs = []
            for r, gap in rs:
                if streaming:
                    llm = speed.ttft_s + r.input_tokens / speed.prefill_tokens_per_s
                    est = predict(r.prompt, output_guard=False).total_s
                else:
                    llm, est = r.true_wait_s - 0.3, estimate(r)
                jobs.append(Job(r.prompt, LatencyProfile(upload_s=0, security_s=0.3, llm_s=llm, output_tokens=r.true_tokens),
                                est, gap, "prompt", wl.expectation(r.predicted_tokens, per_1k), r.type,
                                r.streaming_wait_s if streaming else r.true_wait_s))
            out.append(User(f"w{seed}-{i}", patience, jobs))
        return out

    predicted = users_for(lambda r: r.predicted_wait_s)
    flat = users_for(lambda r: mean_wait)
    oracle = users_for(lambda r: r.true_wait_s)
    streaming = users_for(None, streaming=True)
    no_rotation = {"ads": {"rotation": {"enabled": False}}}

    control_r = run_arm(predicted, ads=False)
    main_r = run_arm(predicted, ads=True)
    arms = {
        "control": (predicted, control_r),
        "size_predictor+rotation": (predicted, main_r),
        "size_predictor, no rotation": (predicted, run_arm(predicted, ads=True, overrides=no_rotation)),
        "no predictor (flat estimate)+rotation": (flat, run_arm(flat, ads=True)),
        "oracle+rotation": (oracle, run_arm(oracle, ads=True)),
        "streaming, no ads": (streaming, run_arm(streaming, ads=False)),
        "streaming+ads": (streaming, run_arm(streaming, ads=True)),
    }
    res = {"n_users": n_users, "n_requests": len(reqs), "flat_estimate_s": mean_wait,
           "predictor": wl.predictor_accuracy(reqs, load_policy()["ads"]["adaptive"]["no_ad_below_s"],
                                              load_policy()["ads"]["rotation"]["every_s"]),
           "arms": {name: score(us, rs, seed) for name, (us, rs) in arms.items()}}
    res["sweep"] = {str(f): {"control": score(predicted, control_r, seed, f), "treatment": score(predicted, main_r, seed, f)}
                    for f in SIM["perception"]["ad_occupied_factor_sweep"]}

    # Where does it help and where does it hurt? Same users, per true-wait bucket.
    def by_bucket(results):
        groups: dict[str, list[Outcome]] = {}
        for _, job, _, o in outcomes(predicted, results, seed):
            w = job.true_wait
            key = "<4s" if w < 4 else "4-10s" if w < 10 else "10-20s" if w < 20 else "20-40s" if w < 40 else "40-90s" if w < 90 else ">90s"
            groups.setdefault(key, []).append(o)
        return groups
    c, t = by_bucket(control_r), by_bucket(main_r)
    order = ["<4s", "4-10s", "10-20s", "20-40s", "40-90s", ">90s"]
    res["buckets"] = {k: {"n": len(c[k]),
                          "ad_shown": sum(o.ad_shown for o in t[k]) / len(t[k]),
                          "sponsors_per_request": sum(o.impressions for o in t[k]) / len(t[k]),
                          "d_abandon_pp": 100 * (sum(o.abandoned for o in t[k]) - sum(o.abandoned for o in c[k])) / len(c[k]),
                          "d_satisfaction": st.fmean(o.satisfaction for o in t[k]) - st.fmean(o.satisfaction for o in c[k]),
                          "revenue_per_1000": 1000 * sum(o.revenue_usd for o in t[k]) / len(t[k])}
                      for k in order if k in c}
    return res

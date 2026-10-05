# Evaluation

> Can mandatory security and inference latency in LLM applications be converted into a
> privacy-preserving, non-intrusive monetization surface without influencing model outputs or
> weakening security decisions?

**Answer in one paragraph.** The *isolation* part is achievable and was demonstrated: across every
test and experiment, the ad side never changed a security decision, never reached the model, and
never received prompt or document content. The *monetization* part works only in a narrow band. In
the simulation, the sponsored status line helps or is neutral only for waits of roughly 10 seconds
or more, and only if a sponsored second feels shorter than an empty one (unverified). It earns on
the order of **$2–3 per 1000 requests**. That covers a small model's inference, but only about a
tenth of a frontier model's. The premise that the security layer creates most of the wait did
**not** hold for Sieve as measured: Sieve adds milliseconds. The wait that exists is the model's own
"thinking / writing" time, plus the time the output check holds the answer back, so it grows with
the size of the request. **The bigger the request, the better the case for a sponsor.** In the
size-driven workload, abandonment fell 2–5 points on requests waiting 20 s or more, with almost no
satisfaction cost beyond 90 s, while short requests only lost satisfaction.

**Read this first: what is measured and what is simulated.**

| Measured (code ran, numbers are exact) | Simulated (numbers follow from assumptions) |
|---|---|
| Sieve's real latency on prompts and answers | Perceived wait, annoyance, abandonment, satisfaction |
| Security decisions, detection and false-positive rates on a synthetic corpus | Click-through rate (assumed, not modelled) |
| Isolation and output invariance (ads on vs off) | eCPM and inference cost (swept) |
| What reached the ad engine (every signal is logged) | Stage latencies and answer lengths (swept or drawn) |
| Ad timing: when shown, how long, flashes, under exact virtual time | |

Every simulated number is labelled. The assumptions are in `config/sim.json`; reproduce with
`python -m scripts.run_eval` (fixed seeds, about 60 s). Generated tables: [results/report.md](results/report.md).

---

## Where does the wait actually come from? (measured)

The idea starts from an observation: "the LLM answers in about 5 s; with Sieve in front of it, the
answer can take 10–15 s or more, and that extra time can carry ads." So the first thing measured was
Sieve itself, on an Apple-silicon CPU with Sieve's default layers (rules + TF-IDF; BERTurk and the LLM
layer were not installed):

| Sieve step | Time |
|---|---|
| Input guard on a chat prompt (`Guardrail.check`), warm | **0.6 ms** mean, 0.7 ms p95 |
| Output guard on a 2,200-character answer (`OutputGuard.check`), warm | **3.8 ms** |
| Document guard on a 1–2 page PDF text (`DocumentGuard.check`) | 8 ms |
| Cold start: import + model load + first check | **0.8 s** |
| BERTurk stage, when TF-IDF is unsure (from Sieve's README) | ~35 ms |
| Optional LLM layer, Qwen3-1.7B (from Sieve's README) | 250–330 ms |

**Sieve as built adds milliseconds, not seconds.** If a setup sees +5–10 s, it comes from one of
these, and only the last is a durable source of ad time:

1. **Cold start on every request** (constructing `Guardrail()` or loading a model per call). This is
   a bug, and fixing it removes the "inventory".
2. **The optional LLM layer on a larger local model.** Seconds are possible here, but the layer is
   optional, and Sieve's own README found it not worth it for prompt injection.
3. **The output guard holding the answer back.** Without an output check, a chat UI streams: the
   user sees the first words after about 0.5–1 s. With an output check, the answer can only be shown
   once it has been fully generated *and* checked, so the user waits for the whole generation. The
   wait therefore grows with the size of the answer: a quick fact takes ~3 s, a detailed report
   ~40–50 s, a 10-page plan minutes. This is real, it is mandatory when outputs are checked, and it is
   exactly the window in which "Thinking… / Writing the answer…" is on screen.

**Rule this project adopts: security latency is never increased to create ad inventory.** The ad
system's revenue grows with waiting time, so it must not have a say in it. The security latency
budget is set and measured independently of ads, and every test checks that ads do not change it
(total latency is identical with ads on and off).

## Setup of the main simulation

- **Users:** 4,000 simulated users per arm, 2.5 requests per session on average, 1–15 min between
  requests. Patience is log-normal with an 18 s median. Control and treatment get *identical* users
  (inputs, latencies, patience, random draws); the only difference is whether the ad engine exists.
- **Inputs:** a mix of 15 documents: 48% benign general/travel/education/shopping, 38% sensitive
  (financial, medical, legal, HR, personal with PII, confidential), 6% attacks, 6% robustness. The
  ad lifecycle is the same for prompts (`tests/test_prompt_mode.py`); only the state names differ.
- **Latency (swept, not measured):** security from {1, 3, 5, 10} s and LLM from {2, 5, 10, 20} s,
  with ±20% jitter. Wait prediction is noisy (log-normal, σ = 0.25). Given the measurement above,
  the large security values stand for "security + held-back answer", not for Sieve's compute. The
  size-driven chat workload further down replaces this grid with waits that follow the request.
- **Surface:** the sponsored **status line** (the ad rides on the "Thinking…" line), compared with
  a separate card. Rotation is on (a new sponsor every 20 s on long waits).
- **Simulated user:** abandons when perceived wait exceeds patience, or, with a small probability,
  when an ad annoys them. Perceived wait = empty seconds × 1.0 + sponsored seconds × *f*. Annoyance
  comes from each ad, the card format, flashes (< 1.5 s), creative swaps and fatigue across a session.
- **Time:** a virtual-time event loop. Tens of thousands of requests run in seconds, with exact,
  reproducible timing.

## Results

### Security detection (measured, synthetic corpus: 42 attack documents, 28 benign)

| | detected | blocked | FNR | FPR | FPR on English benign | FPR on Turkish benign | FPR on look-alikes | ms / doc |
|---|---|---|---|---|---|---|---|---|
| Mock rules | 64.3% | 64.3% | 35.7% | 7.1% | 0% | 0% | 25% | 1.6 |
| Sieve adapter | 85.7% | 47.6% | 14.3% | 57.1% | **90%** | 0% | 87.5% | 8.2 |

- The mock rules catch every literal attack in English and Turkish, in all three carriers (visible
  paragraph, hidden white 1pt text, footnote), and **none** of the evasive ones (spaced letters,
  leetspeak, base64, social engineering without keywords).
- Sieve catches 80% of the evasive attacks. Its ML layer is trained on Turkish and flags almost every
  *English* benign document (score ≈ 0.95), so an English deployment would send most documents to
  review. Its ML only *reviews* and never blocks, so its block rate is lower than the mock's.
- Robustness: empty → BLOCK, malformed → BLOCK, very long (355k chars) → REVIEW. PII is masked before
  the LLM in both modes; in prompt mode, the output guard masks PII in the answer too.

### Isolation (measured)

- **135 of 135** ads-on vs ads-off runs (15 documents × 3 strategies × {no failure, ad server down,
  ad timeout}) produced an identical security decision, LLM request fingerprint and output. The same
  check passes for prompts, including 60 s waits with rotation.
- **160 tests pass.** They include hostile ad engines (raising, returning garbage, returning
  `javascript:` creatives, blocking the loop), every failure case in the spec, canary-string privacy
  tests, prompt mode, the size predictor, rotation, and Sieve mode.

### A/B simulation (simulated, f = 0.9: a sponsored second feels like 0.9 s)

| | control | **status line** (adaptive) | card | sensitive → no ads | ads only after ALLOW | both |
|---|---|---|---|---|---|---|
| abandonment | 32.7% | 30.1% | 30.3% | 30.4% | 31.1% | 31.4% |
| mean perceived wait | 10.9 s | 10.5 s | 10.4 s | 10.5 s | 10.7 s | 10.8 s |
| satisfaction (1–5) | 3.46 | **3.41** | 3.34 | 3.39 | 3.46 | 3.47 |
| billable impressions | 0 | 7,724 | 7,718 | 7,636 | 3,223 | 2,365 |
| contextual share | – | 27% | 27% | 27% | 68% | 98% |
| flash rate | – | 3.6% | 3.6% | 5.7% | 0.7% | 0.7% |
| ads per session | – | 1.39 | 1.39 | 1.39 | 0.80 | 0.59 |
| revenue / 1000 requests | – | $2.22 | $3.15 | $2.20 | $1.23 | $1.07 |

The card earns more only because the model assumes larger units sell at 1.5× eCPM; that premium is an
assumption, not data.

**Sensitivity to f, the assumption that decides the result (status line):**

| f | abandonment ctrl → treat | satisfaction ctrl → treat |
|---|---|---|
| 0.8 | 32.7% → 27.3% | 3.46 → **3.52** |
| 0.9 | 32.7% → 30.1% | 3.46 → 3.41 |
| 1.0 | 32.7% → **33.0%** | 3.46 → 3.30 |
| 1.1 | 32.7% → **35.2%** | 3.46 → 3.22 |

### Strategy comparison (simulated, single-request sessions, status line, f = 0.9)

Selected cells (Δ = vs no ads for the same users; the full 16-cell grid is in `results/report.md`):

| total wait | strategy | ad shown | flash | Δ abandonment | Δ satisfaction | $ / 1000 |
|---|---|---|---|---|---|---|
| 3.5 s (1+2) | immediate | 100% | 13% | +0.7 pp | −0.29 | 2.51 |
| | delayed | 100% | **100%** | +0.7 pp | **−0.78** | 0.00 |
| | adaptive | 42% | 30% | 0.0 pp | −0.17 | 1.35 |
| 8.5 s (3+5) | immediate / adaptive | 100% | 0% | −2.7 pp | −0.16 | 5.53 |
| | delayed | 100% | 0% | −1.7 pp | −0.17 | 4.49 |
| 13.5 s (3+10) | immediate / adaptive | 100% | 0% | −7.3 pp | +0.02 | 5.96 |
| | delayed | 100% | 0% | −5.0 pp | −0.06 | 5.85 |
| 30.5 s (10+20) | immediate / adaptive | 100% | 0% | −4.0 pp | +0.01 | 5.24 |

- On the status line, Adaptive and Immediate behave the same once the predicted wait is ≥ 4 s.
  Adaptive skips predicted waits under 4 s (raised from 3 s after this grid showed harm below 4 s),
  but a noisy predictor still lets some through: 42% shown at 3.5 s, and those mostly flash.
- **Delayed (B)** can't know the remaining time. When the wait ends just after 3 s it shows the
  sponsor for half a second and earns nothing.

### Size-driven chat workload (simulated): the wait follows the request

The latency grid above uses fixed stage times. Real waits follow the size of the request, so a
second simulation draws real prompts from four types: quick questions (30%), normal answers (38%),
long-form requests such as reports, guides and articles (22%), and very long ones such as a
10-page business plan or a full API with tests (10%). The "true" answer length is drawn around what
each type really produces, and around the stated length when the prompt names one ("1500 kelimelik").
Model time = 0.8 s to the first token + input / 3000 tok/s + answer / 50 tok/s; with the output guard,
the whole answer is held back. Users expect bigger requests to take longer (patience and tolerance
grow by 1× per 1,000 requested tokens, an assumption). 4,000 users, 8,282 requests, f = 0.9.

**Where the wait is:** <4 s 22%, 4–10 s 23%, 10–20 s 21%, 20–40 s 15%, 40–90 s 13%, >90 s 6%.
**With streaming (no output guard) 100% of requests wait under 4 s**, and the ad engine shows nothing
at all. The window exists because the answer is held back until it has been checked.

**The size predictor** (`drip/predictor.py`) reads only the prompt: stated lengths, size words
("detaylı", "rapor", "comprehensive", "kısaca"), the kind of question, and page counts for documents.
Median error 30% on the wait; the show / don't-show decision (4 s) is right for **81%** of requests,
and only **2.8%** of requests that are actually short get predicted as long. Its typical mistake:
"Python'da bir liste nasıl ters çevrilir?" contains "liste" and is predicted as a normal answer.

| arm | abandonment | satisfaction | requests with a sponsor | sponsors per shown request | flash | $ / 1000 requests |
|---|---|---|---|---|---|---|
| no ads | 15.0% | 3.84 | – | – | – | – |
| **size predictor + rotation** | 13.6% | 3.70 | 52% | 2.61 | 4.8% | 3.05 |
| size predictor, no rotation | 13.6% | 3.74 | 52% | 1.97 | 3.2% | 2.52 |
| no predictor (same estimate for all) + rotation | 13.7% | 3.60 | 70% | 1.87 | 16.3% | 3.16 |
| oracle (true wait known) + rotation | 13.5% | 3.70 | 61% | 2.43 | 0% | 3.32 |
| streaming (no output guard) + ads | 0.0% | 5.00 | 0% | – | – | 0.00 |

| true wait | requests | with a sponsor | sponsors / request | Δ abandonment | Δ satisfaction | $ / 1000 |
|---|---|---|---|---|---|---|
| < 4 s | 1,809 | 10% | 0.15 | +0.2 pp | −0.10 | 0.33 |
| 4–10 s | 1,892 | 44% | 0.87 | +0.1 pp | −0.19 | 2.18 |
| 10–20 s | 1,773 | 63% | 1.25 | −1.0 pp | −0.14 | 3.15 |
| 20–40 s | 1,270 | 75% | 2.04 | −2.1 pp | −0.17 | 4.54 |
| 40–90 s | 1,043 | 80% | 2.86 | −5.3 pp | −0.11 | 5.89 |
| > 90 s | 495 | 79% | 3.07 | −4.4 pp | −0.02 | 6.17 |

- **Bigger requests are where it works.** Under 10 s the sponsor costs satisfaction and saves
  nobody. From 20 s it cuts abandonment by 2–5 points, and beyond 90 s the satisfaction cost is
  almost zero while revenue per request is highest.
- **The predictor matters for UX, not for revenue.** Without one, sponsors land on 70% of requests,
  including short ones, and 16% of them flash: satisfaction 3.60 vs 3.70. The predictor matches the
  oracle on satisfaction and gives up about 8% of the revenue.
- **Rotation** (a new sponsor every 20 s, at most 4 per request, only if ≥ 3 s are predicted to remain)
  adds **21% revenue** (+33% impressions) for −0.04 satisfaction and a little more flashing. It does
  not count against the frequency cap.
- **Requests without a sponsor:** 48% of requests got none, mostly because of the frequency cap
  (2 slots per session, 5 min apart), not because they were short.
- **Sensitivity:** at f = 1.0 abandonment is 15.4% vs 15.0%; satisfaction stays below the control even
  at f = 0.8 (3.79 vs 3.84), because long requests carry several sponsors.

### Economics (simulated, f = 1.0 so that "occupied time" is *not* booked as income)

- About **954 billable impressions per 1000 requests** (main A/B). Ad infrastructure: $0.004 per 1000.
- Revenue per 1000 requests ≈ eCPM × 0.95: **$0.48 at $0.5, $2.86 at $3, $9.54 at $10, $33.39 at $35**
  (the vendor-claimed developer-audience CPM).
- Against assumed inference cost per 1000 requests ($1 small model / $10 mid / $30 frontier), $3 eCPM
  covers **286% / 29% / 9.5%**. Long requests cost more to generate *and* carry more sponsors
  (3 per request beyond 90 s), so the ratio is similar per request size.
- At f = 1.0 ads cost about 3 answers per 1000 requests. Break-even eCPM depends on what a lost answer
  is worth: **$0.06** if $0.02, **$0.75** if $0.25, **$3.00** if $1, **$14.99** if $5 (a churned subscriber).
- Not monetized: the satisfaction drop (−0.16 at f = 1.0). If it turns into churn, it is the largest cost.

---

## The ten questions

### 1. Does advertising actually improve the user experience?

**Not on its own, and only conditionally.** Measured: the ad adds no latency, even when the ad
server fails. Simulated: the sponsored status line lowers abandonment (32.7% → 30.1%) and perceived
wait (10.9 → 10.5 s) *if* a sponsored second feels 10% shorter, but satisfaction still dips
(3.46 → 3.41). Only at f = 0.8 does the treatment beat the control on satisfaction (3.52). At f = 1.0,
abandonment rises slightly. The status line is clearly the gentler surface (3.41 vs 3.34 for the
card). "Ads only after ALLOW" kept satisfaction equal to the control (3.46) with 1.6 pp less
abandonment, at 55% of the revenue. In the size-driven chat workload, no setting beat the control
on satisfaction, because long requests carry several rotating sponsors.

What improves UX regardless of ads is an *explained* wait: real state labels and elapsed time. That is
also why the sponsor is added *next to* the state ("Thinking… · Sponsored: …") instead of replacing
it: hiding what the system is doing makes the wait feel longer and hides security state.

### 2. At what latency is an ad meaningful?

**At roughly ≥ 10 s, and clearly at ≥ 20 s.** Below 10 s, the sponsor is on screen for a few seconds,
there is little abandonment to prevent, and satisfaction drops. In the size-driven workload the
benefit grows with the wait: −1 pp abandonment at 10–20 s, −2 pp at 20–40 s, −5 pp at 40–90 s, with the
smallest satisfaction cost beyond 90 s. Those are long-form requests (reports, guides, plans, full
code), about a third of the workload. A plain chat answer with streaming never gets there.

### 3. At what latency does an ad become annoying?

**Under about 4 s of total wait, and whenever a sponsor is visible for less than 1.5 s.** At 3.5 s
every strategy costs satisfaction, and Delayed flashes every time. In the size-driven workload,
requests under 10 s lost 0.10–0.19 satisfaction with no abandonment benefit. Mid-wait creative swaps
also caused flashes, so the slot keeps a creative for at least 2 s and swaps or rotates only when
≥ 3 s of wait are predicted to remain; with that rule the A/B flash rate is 3.6%. The frequency cap
(2 per session, 5 min apart) keeps ads per session at 1.39.

### 4. Should ads be switched off completely on sensitive content?

**Targeted ads: yes, always (and they are). Generic ads: a product decision, but if the answer is
"off", it requires disabling ads before the security decision.**

| policy | sensitive requests that saw *any* ad | mean ad seconds on them | contextual ads on them |
|---|---|---|---|
| default (generic allowed) | 68.3% | 7.5 | 0 |
| sensitive categories → none | **68.3%** | 3.8 | 0 |
| ads only after ALLOW | 33.9% | 4.0 | 0 |
| both | **0%** | 0 | 0 |

Turning sensitive categories "off" does not stop ads from appearing on sensitive content, because
nobody knows the content is sensitive until the check finishes. The generic sponsor shown during
"Checking your message…" is withdrawn at the decision, but it *was* shown. Only "no ads before the
security decision" **and** "sensitive → none" together give zero, at about half the revenue.

### 5. Is contextual advertising enough?

**For relevance, partly. For revenue, it is a minority of impressions.** Only 4 of 10 categories
allow targeting, and the generic pre-decision sponsor covers the first seconds of every request, so
27% of default impressions were contextual (68% when ads start only after ALLOW). ChatGPT's choice
(contextual-only at launch) and the mobile-ads literature suggest contextual targeting is
commercially viable; this prototype can't measure relevance or CTR without real users.

### 6. Can relevant ads be chosen without user data?

**Coarsely, yes, and that is the point.** The ad engine sees one of ten categories, only for
non-sensitive content. That is enough for a travel sponsor on a travel question and suits brand and
awareness campaigns. It is not enough for performance marketing: no history, no profile, no free text.

### 7. Does the ad system add attack surface to the security architecture?

**Yes, four kinds, each mitigated but not eliminated:**

1. **Third-party content in the UI** (malvertising, XSS, phishing links). Mitigated: https only,
   length and markup validation, `textContent` rendering, `rel="sponsored noopener"`, and creatives
   never reach a model. Tested with a `<script>` / `javascript:` creative.
2. **Availability.** Mitigated: 150 ms budget, background tasks, fail-closed. Measured: identical
   total latency with the ad server down or timing out.
3. **Side channel.** The ad side sees a post-decision request only for allowed, ad-eligible content.
   Its *absence* tells it "blocked, under review or sensitive". Fix (future work): a fixed-shape
   post-decision request, or local creative upgrades.
4. **Impression fraud.** Pre-decision sponsors are shown on blocked requests too, so a bot sending
   injection prompts generates billable impressions. A bot can also ask for "a 10-page report" to
   collect rotating impressions. Fix: don't bill impressions from blocked or errored requests,
   rate-limit, cap rotations, and verify impressions.

And one incentive problem, which is the most important: **an ad system earns more when security is
slower.** Security latency must be budgeted and monitored independently of ads (see "Where does the
wait come from?").

### 8. Can the ad system be manipulated with prompt injection?

**The ad engine itself can't be injected: it receives no text.** The only content-derived values that
cross are the category and the predicted wait, so the attack surface is *steering*:

- **Unlocking ads on sensitive content.** Padding a medical report with travel words made "travel"
  the top category, which in the first version would have allowed targeted travel ads on a medical
  document. Fixed: sensitivity and ad mode follow *every* category present
  (`test_keyword_stuffing_cannot_unlock_ads_on_a_sensitive_document`).
- **Choosing which non-sensitive category is shown.** Still possible, bounded to the vetted inventory;
  it belongs in fraud monitoring.
- **Inflating the predicted wait** ("write 5000 words") to get more rotating sponsors. Bounded by the
  rotation cap (4 per request) and the frequency cap, and the user then really waits that long.
- **Suppressing ads** (adding "CONFIDENTIAL"). Possible and harmless.

### 9. Can ad selection influence the model output?

**No, by construction, and verified at the input level.** `build_request()` / `build_chat_request()`
take only the security report (and the instruction); there is no parameter for ads. 135 of 135
ads-on/ads-off runs, and the prompt-mode runs, produced the same LLM request fingerprint, so for *any*
model the output distribution is the same. The sponsor sits in the status line, which disappears
when the answer appears; it is never part of the answer.

### 10. Could this become a real SaaS product?

**Possibly, as a narrow one.** The shape: an SDK for LLM application operators that turns their own
"Thinking… / Writing…" line into a sponsored line, with isolation, data minimization, a size-based
wait predictor and rotation built in, plus a demand partner. Obstacles:

- **Economics.** About $2–3 per 1000 requests at typical display eCPMs pays for small-model inference,
  not frontier-model inference. Only premium developer CPMs (vendor-claimed) change that.
- **The window depends on holding the answer back.** With streaming, every request in the workload
  waited under 4 s and no sponsor was shown. The window exists for checked answers, and it grows
  with request size; faster models shrink it.
- **Competition.** Spinyield and others already sell the coding-agent status line, with revenue
  sharing. The difference here is operator-side integration, security isolation and minimization.
- **Trust and compliance.** Perplexity stepped back from ads; developers rejected terminal ads. Ad
  labelling (FTC, EU DSA) and KVKK/GDPR apply even to a category derived from a prompt.

The most promising niche is **long, checked outputs**: long-form generation (reports, plans, code),
agent runs and document workflows with 20 s – several minute waits, run by the operator in its own
UI, on a free tier that the sponsored status line subsidizes.

---

## Failure cases (measured, `tests/test_isolation.py`, `tests/test_prompt_mode.py`)

| Case | Outcome |
|---|---|
| Ad server unavailable | No ad; workflow and total latency identical to ads-off |
| Ad decision timeout (> 150 ms) | No ad; identical workflow |
| No suitable advertisement | No ad; identical workflow |
| Ad engine crashes / returns garbage / returns a malicious creative | No ad; identical workflow |
| Security check takes too long (> 30 s) | ERROR (fail closed), LLM not called, sponsor withdrawn |
| LLM responds before the ad loads | Sponsor never drawn; decision logged as late |
| User cancels | CANCELLED, LLM not called, sponsor withdrawn at the cancel time |
| Security blocks the input | BLOCKED, LLM not called, sponsor withdrawn at decision time, ad side not consulted again |
| Sensitive content detected | Contextual sponsor never selected; generic kept or withdrawn per policy |
| Network failure (LLM) | ERROR, sponsor withdrawn |

## Threats to validity

- The simulated user has hand-set coefficients. The direction of the UX result depends on *f*,
  which has not been measured. A small user study is the most valuable next step.
- Stage latencies and answer lengths are swept or drawn, not measured on a real model. Sieve's own
  latency was measured and is three orders of magnitude below the swept security values.
- The size predictor was tuned on the same prompt templates it is evaluated on; on real traffic its
  accuracy will be lower.
- The security corpus is small (70 documents), synthetic, and written by the author of the mock rules.
- eCPM, CTR, the card premium and inference costs are parameters, not market data.

# Drip

**Security-aware LLM waiting-time monetization: a working prototype.**

[Sieve](https://github.com/efronh/sieve) filters what goes into and comes out of the model. Drip is
what the user sees while it drips through: the "Thinking… / Writing…" line, carrying a sponsor.

> **Research question.** Can mandatory security and inference latency in LLM applications be
> converted into a privacy-preserving, non-intrusive monetization surface without influencing model
> outputs or weakening security decisions?
>
> *LLM uygulamalarında güvenlik ve inference nedeniyle oluşan zorunlu bekleme süresi, güvenlik
> kararlarını veya model çıktısını etkilemeden ve kullanıcı mahremiyetini ihlal etmeden monetize
> edilebilir mi?*

```
Security controls the AI.  Advertising monetizes the waiting time.  Advertising never controls the AI.
```

**This project does not insert advertisements into the model's reasoning process. Sponsored content
is rendered exclusively at the application UI layer while inference or security processing is in
progress.**

Short answer, from the [evaluation](EVALUATION.md): *technically yes, and the isolation holds. As
a business, only under narrow conditions.* All UX numbers come from simulation, not from real users.

**The idea in one line:** the "Thinking… / Writing the answer…" line an LLM app shows while it works
becomes the ad surface:

```
⟳ Thinking...  ·  SPONSORED  Konakla  Ücretsiz iptalli oteller  Otelleri Gör ↗          6.1s
```

The answer itself never contains an ad. The sponsor lives only in the status line, and only while the
request is being checked and generated.

---

## Problem

A user sends a message (or uploads a PDF). Before the model sees it, the input is checked for prompt
injection and personal data and run through a policy. Then the model thinks and writes. If the answer
is checked too (output guard), it can't be streamed: it is held back until it is complete and clean.
Meanwhile the user looks at "Thinking…" for 5 to 30 seconds. The wait is mandatory (security must not
be skipped to save time) and today nobody gets anything out of it.

**Measured caveat:** Sieve itself adds about 0.6 ms on a prompt and 4 ms on an answer (warm), not
seconds. The real wait is the model's own time plus the held-back answer. Security latency is never
increased to make room for ads. See [EVALUATION.md](EVALUATION.md#where-does-the-wait-actually-come-from-measured).

## Why waiting time?

- **It is already there.** Showing something during an existing wait adds no latency, unlike an
  interstitial or a pre-roll.
- **It is attention without content.** The user is looking at the screen and waiting.
- **Occupied waits may feel shorter** than empty ones (Maister). Whether a *passive ad* has the
  same effect as engaging content is uncertain, and the evaluation treats it as an assumption and
  sweeps it.
- **Security makes the wait longer and more predictable.** A guardrail pipeline turns 2 seconds of
  generation into 5 to 15 seconds of processing, with known stages.

## Existing approaches

See [RESEARCH.md](RESEARCH.md). In short: Spinyield, Kickbacks.ai, Idlen, AgenticAds and
Trillboards sell the "thinking" line of coding agents as ad inventory and share revenue with
developers. ChatGPT shows labelled, contextual ad cards *below* the answer and keeps ads away from
sensitive topics. Research shows LLMs given sponsored context favour sponsors and conceal it, and
that ad content an LLM reads is an injection vector. Both findings shaped this design.

## Proposed solution

The application operator puts a clearly labelled sponsor into its own status line ("Thinking…",
"Writing the answer…"), next to the real state, never instead of it. A separate card under the status
is available as an alternative surface for comparison. The sponsor's lifecycle follows the security
pipeline:

```
prompt:   checking message ─► policy ─► thinking ─► writing ─► checking answer ─► answer shown
document: uploading ─► scanning ─► analyzing ─► policy ─► generating ─────────────► summary shown
          └── generic (untargeted) sponsor ──┘  └──── contextual sponsor possible ────┘
                                  │
          BLOCK / REVIEW / error ─┴──► sponsor withdrawn at once, LLM not called
```

Before the security decision nobody knows what the input is about, so only an untargeted sponsor is
possible. After an ALLOW, a contextual one may replace it, if the category and sensitivity allow
targeting. When the answer appears, the sponsor disappears.

## Architecture

```
User
 │
 ▼
Frontend  (web/: prompt / PDF input, state stepper, status line with SponsoredContent, debug view)
 │   SSE events
 ▼
LLM Gateway / Orchestrator  (drip/orchestrator.py)
 ├── Security Guardrail  (drip/security/)            ← decides first, never sees ads
 │     ├── Input inspection      prompt: as is; PDF: pdf.py extract, malformed / encrypted / empty → fail closed
 │     ├── Prompt injection       injection.py (mock) or Sieve (sieve_adapter.py)
 │     ├── PII detection          pii.py (checksummed TCKN / IBAN / card, e-mail, phone) or Sieve
 │     ├── Policy engine          gateway.py: ALLOW / REVIEW / BLOCK + category + sensitivity
 │     └── Output guard           prompt mode: answer held back and checked (PII masked; Sieve OutputGuard)
 │
 ├── LLM  (drip/llm.py)        ← input = build_request(security_report, instruction), nothing else
 │
 └── Ad Decision Engine  (drip/ads/)
       privacy.build_signal() ─► ContextSignal (11 enum fields) ─► SafeAdClient ─► AdDecisionEngine
                                                                     │ timeout, errors → no ad
                                                                     ▼
                                                               AdSlot ─► Sponsored UI
```

| Path | What it is |
|---|---|
| `drip/models.py` | Types. `SecurityReport` (may hold text) and `ContextSignal` (cannot) are separate on purpose |
| `drip/orchestrator.py` | The workflow and the states (prompt and document); the only place that sequences security → ads → LLM → output guard |
| `drip/security/` | Mock Security Gateway (mode 1) and Sieve adapter (mode 2) |
| `drip/privacy.py` | Data minimization: the only producer of `ContextSignal`, and where the ad mode is decided |
| `drip/ads/engine.py` | Ad Decision Engine: inventory, creative validation, frequency capping, strategies A/B/C |
| `drip/ads/client.py` | Isolation boundary: budget, error swallowing, fail-closed for ads |
| `drip/ads/slot.py` | Lifecycle of one job's sponsored slot (show, upgrade, withdraw, close) |
| `drip/predictor.py` | Wait prediction from the size of the request (answer length cues, page count) |
| `drip/latency.py` | Latency simulator, real clock, and a virtual-time event loop for exact, fast simulations |
| `drip/llm.py` | `build_request()` and a deterministic mock LLM |
| `drip/sim/` | Simulated users (UX model), A/B test, strategy grid, economics, security metrics |
| `drip/server.py`, `web/` | Demo: stdlib HTTP server + SSE, vanilla JS frontend |
| `config/policy.json` | Security policy, sensitivity → ad policy, strategies, frequency caps |
| `config/ads.json` | Ad inventory (fictional advertisers) |
| `config/sim.json` | Every simulation assumption, in one place |

### Two security modes

- **Mode 1, Mock Security Gateway** (default). Rules in this repo. The project runs on its own with
  no other dependency than `pypdf`.
- **Mode 2, Sieve Adapter.** Uses [Sieve](https://github.com/efronh/sieve)'s `DocumentGuard` (document
  parts, hidden text, rules + ML) and `Guardrail` for injection, and `mask()` for Turkish PII.
  Sieve is optional: it is imported if installed, or from `SIEVE_PATH` (defaults to `~/code/sieve`).
  Extraction, classification and the policy engine stay the same in both modes.

## Security model

- **Security decides first, alone.** The orchestrator awaits the security report before it tells
  the ad slot anything, and reads no value from the ad side. `AD DECISION ≠ SECURITY DECISION` is a
  property of the call graph, and `tests/test_isolation.py` checks it against ad engines that raise,
  hang, return garbage or return malicious creatives.
- **Fail closed on security.** Unreadable, encrypted, empty or image-only PDFs are BLOCKed, because
  what can't be read can't be inspected. A scan that takes longer than 30 s is an error and the
  LLM is not called. A very long document is only partly inspected, so it goes to REVIEW.
- **REVIEW** follows `security.review_policy`: `ask_user` (the demo asks), `proceed` or `block`. Ads
  are withdrawn during review and do not come back, even if the user continues.
- **Ad failure modes, stated once** (`ads/client.py`):

  | Side | Mode | Meaning |
  |---|---|---|
  | Ad engine | **fail-closed** | Error, timeout (150 ms budget), malformed answer, invalid creative, no inventory → no ad |
  | Workflow | **fail-open with respect to ads** | Security and LLM continue as if ads didn't exist; latency unchanged |
  | Security | **fail-closed** | No security answer → no LLM call |

- **Ad creatives are untrusted third-party content.** They must have an https CTA, bounded length,
  and no markup or control characters. They are rendered with `textContent` and `rel="sponsored
  noopener"`, and they never reach a model (see [Unit 42](https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/)
  on ad content as an injection vector).
- **Category steering is contained.** Padding a medical report with travel words can win the top
  category, but sensitivity and ad mode follow *every* category present, so the padding cannot
  unlock targeted ads (`test_keyword_stuffing_cannot_unlock_ads_on_a_sensitive_document`).

## Privacy model

```
WRONG:   PDF ──► Ad network
RIGHT:   PDF ──► Security / Classification ──► minimal context signal ──► Ad Decision Engine
```

`ContextSignal` has 11 fields, all coarse and enumerable: an opaque session id (only for capping),
the state, the phase, the category, the sensitivity, the ad mode, the predicted wait (rounded to
0.5 s), the tier, consent, the allowed ad categories and the locale. **There is no free-text field**,
so document text, PII, filenames or findings cannot leak through it by accident.

- The **category is released only when it may be targeted on**. Before the security decision it
  is `None`. For `financial`, `medical`, `legal`, `personal` (generic ads only) and `hr`,
  `confidential_business` (no ads) it stays `None`. The ad side never learns that a document was
  medical. Tested with a canary string and with every sample (`tests/test_privacy.py`).
- PII is masked before the LLM (TCKN, IBAN and card numbers checksum-validated). It never goes to
  the ad side in any form.
- Users can opt out of contextual ads (generic only), and premium users see no ads.
- The decision log in the demo's developer view shows **exactly** the signal the ad engine received.

### Sensitive content policy (`config/policy.json`)

| Category | Ad mode | | Sensitivity | Cap |
|---|---|---|---|---|
| general, travel, shopping, education | contextual | | low | contextual |
| financial, medical, legal, personal | generic_only (targeted ads disabled) | | medium | generic_only |
| hr, confidential_business | none (ads disabled) | | high | none |

Sensitivity is raised by PII: any PII means at least medium, and TCKN, IBAN, card numbers or 3+
PII hits mean high. The final mode is the narrowest of all present categories and the sensitivity
cap. One config change turns a category off completely, for example
`{"ads": {"category_modes": {"medical": "none"}}}`. `pre_decision_mode: "none"` disables ads before
the security decision; that is the only way to guarantee no ad *ever* appears on a sensitive document.

## Ad decision model

Input: `ContextSignal` (state, category, sensitivity, user context, allowed categories, session)
plus timing. Output: `{show_ad, ad_id, reason, duration, category, format, delay, strategy, decision_ms}`.

```json
{"show_ad": true, "ad_id": "travel_hotels_001", "reason": "contextual_match:travel", "duration": 4.5,
 "category": "travel", "format": "status_line", "delay": 0.0, "strategy": "adaptive", "decision_ms": 30.0}
```

| Strategy | Rule |
|---|---|
| **A, Immediate** | Show as soon as processing starts |
| **B, Delayed** | Show only after the user has waited 3 s |
| **C, Adaptive** (default) | Predicted wait < 4 s → no ad; otherwise show (on the card surface: compact for 4–8 s, larger card for ≥ 8 s) |

Surface (`ads.surface`): `status_line` (default, one line next to "Thinking…") or `card`.

The predicted wait comes from the size of the request (`drip/predictor.py`): stated lengths
("1500 kelimelik", "10 sayfa"), size words ("detaylı rapor", "comprehensive", "kısaca"), the kind of
question and, for documents, the page count. With an output guard the whole answer is held back, so
wait ≈ 0.3 s security + first token + answer tokens / writing speed.

**Rotation:** on long waits a new sponsor takes the line every 20 s (at most 4 per request, only while
≥ 3 s are predicted to remain). Rotations are not new slots and don't count against the frequency cap.

Frequency capping: at most 2 ad slots per session and at least 5 minutes between them. A generic
creative is upgraded to a contextual one only after it has been visible for 2 s, and only if at least
3 s of wait are predicted to remain, so creatives don't flash. An impression counts as billable
after 1 s of visibility.

## UX considerations

- "Sponsored" label and its own landmark (`role="complementary"`). On the status line it follows the
  real state after a "·" separator and never replaces it; as a card it has a dashed warm border.
  It is never inside the answer area and never looks like model output.
- No sound, video, autoplay or full-screen. Never more than one card.
- Removed the moment processing ends, is blocked, errors or is cancelled.
- No ad next to a security warning (REVIEW) or a BLOCK.
- The processing UI always shows the real state ("Checking document security...") and elapsed time,
  so the wait is explained, which matters for perceived wait regardless of ads.

## Evaluation

Full report: [EVALUATION.md](EVALUATION.md); generated tables: [results/report.md](results/report.md).
Headlines (synthetic corpus, simulated users):

- **Sieve is fast.** Measured: 0.6 ms per prompt, 3.8 ms per answer (warm), 0.8 s cold start. The
  ad window is the model's time plus the held-back answer, not the security layer.
- **Isolation holds.** 135 of 135 ads-on/ads-off runs gave an identical security decision, LLM
  request fingerprint and output, across 3 strategies and ad-server failures. 160 tests pass.
- **Ads help only on long waits.** At ≥ ~10 s total wait, simulated abandonment drops 4–7 points.
  Under ~4 s every strategy lowers satisfaction, and Delayed flashes in 100% of cases.
- **The status line is the gentler surface** (satisfaction 3.41 vs 3.34 for a card, control 3.46).
- **Bigger requests, better case.** With answers held back by the output guard, the wait follows the
  answer size. Simulated: requests waiting ≥ 20 s lose 2–5 points less to abandonment; requests under
  10 s only lose satisfaction. With streaming there is no window at all. The size predictor gets the
  show/no-show call right 81% of the time; rotation adds 21% revenue for −0.04 satisfaction.
- **The UX benefit is an assumption.** It holds only if a sponsored second feels shorter than an
  empty one. At "feels the same", ads slightly *increase* abandonment (32.7% → 33.0%).
- **Economics.** About 954 billable impressions per 1000 requests. At $3 eCPM that is about $2.9 per
  1000 requests, which covers a small model's inference cost but roughly 10% of a frontier model's
  (assumed costs).
- **Security.** Mock rules: 64% of attacks detected (all literal ones, none of the evasive ones),
  7% false positives. Sieve: 86% detected, including 80% of evasive ones, but 90% false positives on
  *English* benign documents (its ML layer is trained on Turkish).

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m drip.server          # demo at http://127.0.0.1:8765
.venv/bin/python -m pytest -q              # 160 tests, ~9 s
.venv/bin/python -m scripts.run_eval       # all experiments, ~60 s → results/
```

Mode 2 needs Sieve's dependencies (`numpy`, `scikit-learn==1.9.1`, `joblib`) and either
`pip install sieve-tr` or `SIEVE_PATH=/path/to/sieve`. Without them the Sieve tests are skipped.

## Limitations

- **No real users.** Perceived wait, annoyance, abandonment, satisfaction and CTR come from a
  simulated user whose coefficients are assumptions (`config/sim.json`). The direction of the main
  result flips with one of them, and that is reported.
- **No real ad demand.** eCPMs are swept, not observed. The $35 CPM upper end is a vendor claim for
  developer audiences.
- **Synthetic corpus, written by the author of the mock rules.** The mock detector's numbers are
  optimistic by construction. The evasive attacks and look-alikes are there to show where it breaks.
- **Mock LLM.** It is deterministic (extractive summaries, canned chat answers), so the "5 s" model
  time is simulated. Output invariance is proven at the *input* level
  (identical request fingerprint), which carries over to any model, but a real model's outputs
  were not compared.
- **Keyword classifier.** Ten coarse categories. A real deployment would use a small local
  classifier behind the same interface.
- **Side channel.** The ad side can infer "this job was blocked or sensitive" from the *absence* of
  a post-decision request. See EVALUATION.md, question 7.
- **Time.** Latencies are simulated. Models get faster every year, and the waits shrink with them.

## Future work

- A small user study, even 20 people, to replace the simulated perception coefficient with a real
  one. It is the single most important unknown.
- Real LLM adapter (`generate()` / `chat()` interface) to measure the actual thinking/writing time
  and compare semantic outputs across ads on/off.
- Streaming with incremental output checks, and measuring how much of the wait that removes; this is
  the main threat to the ad window.
- Close the side channel: always send a fixed-shape post-decision request, or decide upgrades locally.
- On-device ad selection (ship a small vetted inventory to the client, pick locally), so not even
  the coarse signal leaves the device.
- Long-running agent workflows (deep research, batch document review) with 30 s – 10 min waits,
  where the economics look better.
- Viewability measurement in the browser (IntersectionObserver) instead of server-side timing.
- Proof-of-inference style impression verification (as Spinyield does) to make impression fraud
  unprofitable.


## License

MIT, see [LICENSE](LICENSE). Dependencies keep their own licences and are not bundled: pypdf
(BSD-3-Clause), pytest (MIT), and for the optional Sieve adapter, [Sieve](https://github.com/efronh/sieve)
(MIT), numpy (BSD-3-Clause and others), scikit-learn (BSD-3-Clause) and joblib (BSD-3-Clause). The
advertisers in `config/ads.json` are fictional.

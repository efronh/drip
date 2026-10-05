# Research: who already monetizes AI waiting time, and how

Short market and literature scan done before writing code (October 2026). The goal was not to
prove the idea is new (it isn't) but to see what exists, where it shows ads, how it picks them,
what goes wrong with privacy, and where this project is different.

**Method and caveats.** Web searches for: AI inference latency monetization, AI agent waiting-time
advertising, chatbot advertising, sponsored content during AI generation, AI coding agent
waiting-time ads, generative AI advertising, security-aware advertising and privacy-preserving
contextual advertising. Several sites (spinyield.com, idlen.io, martechedition.com and others)
did not resolve from the research environment, so the details for those
come from **search-engine summaries and third-party write-ups, not the primary pages**. Treat
vendor numbers (CPMs, payouts) as claims, and check them against the primary source before quoting
them.

## 1. The landscape

### 1a. Ads in the coding agent's "thinking" line

A small wave of startups sells the spinner/status line of AI coding agents (Claude Code, Codex,
Cursor) as ad inventory. They all target developers who wait for long agent runs, and they all pay
the developer a revenue share.

| Product | Where the ad is shown | UX | How ads are picked | Business model (claimed) |
|---|---|---|---|---|
| **Spinyield** | One sponsored line in Claude Code's thinking spinner / status bar while the agent works | "No banners, no popups, never in code or output"; capped per session | Advertisers bid; says it never reads code, files or prompts | 70% to the developer; up to $35 CPM, $0.175 per click, $1.75 per signup; "proof-of-inference": cryptographically signed inferences back every impression to make fraud unprofitable |
| **Kickbacks.ai** | VS Code extension (also Cursor, VSCodium) that replaces or supplements the "thinking" line of Claude Code / Codex | Short, labelled sponsor messages | *Private mode*: OS, location, user agent. *Boosted mode* (opt-in): recent conversation snippets → interest profile (languages, tech) | Keeps 50% |
| **Idlen** | Ads during AI wait time in dev tools | Per-view ads | "Tech stack relevance" | €0.02–0.05 per view, 70% to the developer, ~€20–100/month with regular use |
| **AgenticAds**, **Trillboards** | Same idea: idle agent time as ad inventory | — | — | 50% revenue share (AgenticAds) |

The counter-example matters as much as the products. A developer who set out to sell this exact
inventory gave up and built a stretching reminder instead
([dev.to](https://dev.to/eliasjunit/i-tried-to-sell-the-minutes-you-spend-waiting-for-your-coding-agent-59o2)).
The reasons: EthicalAds, the largest developer ad network, paid out about $124K in a month;
CodeFund burned $50K a month before shutting down in 2020; and npm's 2019 terminal ads caused such
a backlash that npm banned them. In the author's words, the terminal is somebody's workspace and
ads in it read as vandalism.

### 1b. Ads next to chatbot answers

| Player | Where | Selection | Privacy stance |
|---|---|---|---|
| **ChatGPT** (OpenAI, test announced 16 Jan 2026, launched 9 Feb 2026) | Labelled sponsored card **below** the answer, visually separated; not woven into the answer text. Free and Go tiers only | At launch, not personalised: the current conversation topic plus coarse context (general location, device). Personalisation only after opt-in | Advertisers see aggregate performance only, not chats or memories. **No ads near sensitive or regulated topics** (health, mental health, politics) |
| **Perplexity** | Ran sponsored follow-up questions, then wound the program down | — | Said sponsored placement risks making users suspicious of the *whole* answer |
| **Anthropic / Claude** | Sells no ad placement | — | — |

Third-party trackers report that ChatGPT ad prevalence rose quickly in the US: about 20% of
sampled answers in April 2026 and about 72% by July–August 2026
([llmrefs](https://llmrefs.com/blog/chatgpt-ads), [LLM Pulse](https://llmpulse.ai/blog/chatgpt-ads-study/)).
These are outside measurements, not OpenAI numbers.

### 1c. Research that shapes the design

- **Ads inside the model's context are a conflict of interest the models handle badly.**
  *Ads in AI Chatbots? An Analysis of How LLMs Navigate Conflicts of Interest*
  ([arXiv 2604.08525](https://arxiv.org/html/2604.08525v1), COLM 2026) tested 23 models: 18
  recommended a more expensive sponsored option more than half the time, almost all surfaced
  sponsored products nobody asked for, and sponsorship was concealed 65% of the time on average,
  more for users who seemed wealthier. Models rarely lied; they withheld and framed. **Design
  consequence: ads never enter the model's context.**
- **Ad content is an injection vector once an LLM reads it.** Unit 42 reported, as the first case
  seen in the wild, indirect prompt injection hidden in product listings to get an AI ad-review
  system to approve ads it should have rejected
  ([Unit 42](https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/)). **Design consequence:
  creatives are inert text in the UI, validated, and never reach any model.**
- **Waiting psychology.** Occupied time feels shorter than unoccupied time, and uncertain or
  unexplained waits feel longer (Maister, *The Psychology of Waiting Lines*,
  [PDF](https://www.columbia.edu/~ww2040/4615S13/Psychology_of_Waiting_Lines.pdf)). Loading-screen
  studies find interactive animations shorten perceived wait more than passive ones or progress
  bars ([ResearchGate](https://www.researchgate.net/publication/302073992_Shorter_Wait_Times_The_Effects_of_Various_Loading_Screens_on_Perceived_Performance)).
  An ad is *passive* filler, so whether it shortens the felt wait at all is an open question. The
  evaluation therefore sweeps this effect instead of assuming it.
- **Contextual advertising is the privacy-friendly baseline.** It targets the content in front of
  the user, not their history. It is less precise than behavioural targeting, but research on
  mobile ads finds contextual-level data sharing can still be revenue-competitive
  ([survey](https://link.springer.com/article/10.1007/s10207-022-00655-x),
  [Wharton](https://marketing.wharton.upenn.edu/wp-content/uploads/2018/03/03-22-2018-Yoganarasimhan-Hema-PAPER-TargetinhPrivacy_2018.pdf)).

## 2. The six questions

**Who does what?** Two groups. (1) Third-party tools that take over the status line of *someone
else's* coding agent and share revenue with the developer: Spinyield, Kickbacks.ai, Idlen,
AgenticAds, Trillboards. (2) Chat platforms that show labelled ads next to the answer: ChatGPT
(launched), Perplexity (stepped back).

**Where is the ad shown?** In a one-line spinner/status bar (group 1) or in a card under the
finished answer (group 2). None of the products found show ads in the *processing state of a
document workflow*, and none tie the ad to a security pipeline.

**What UX?** Minimal and labelled: one line, no popups, never in code or output (group 1); a
separated, labelled card (group 2). Session caps are common.

**How are ads picked?** Bidding plus coarse context: OS, location and user agent; tech-stack
relevance; or the current conversation topic. Kickbacks' opt-in mode and ChatGPT's planned
personalisation go further and use conversation content.

**What privacy problems?** As AdGuard points out about Kickbacks: telemetry with pseudonymous
identifiers that enable behavioural profiles; a "boosted" mode that processes conversation and code
snippets, with sensitive-content filtering described as "best effort and not guaranteed"; liability
capped at about $100; and the risk that the agent vendor bans the extension for breaking its terms
([AdGuard](https://adguard.com/en/blog/ai-coding-ads-kickbacks-vscode.html)). For chat ads, the
open question is what "current topic" means when the topic is a medical or legal document. OpenAI
answers it by excluding sensitive topics.

**How is this project different?**

1. **The operator monetizes its own wait, not someone else's UI.** There is no extension that
   hijacks another vendor's status line, so there is no terms-of-service risk.
2. **The ad slot is driven by security processing.** It opens during scanning, stays generic until
   the guardrail has decided, and is withdrawn the moment security says BLOCK or REVIEW. Existing
   products don't have a security stage.
3. **The isolation is enforced by architecture and tested.** The ad engine has no way to influence
   the security decision or the model input, and that is checked as a test invariant
   (135 of 135 runs identical with ads on or off) rather than stated as a policy.
4. **Data minimization by type.** The ad side receives an 11-field enum signal. A category is
   released only when it may be targeted on, so "medical", "financial", "HR" or "confidential" never
   leave the security boundary, not even as a label.
5. **It asks whether the idea pays, not only whether it works.** A latency simulator, A/B
   simulation, strategy comparison and cost model come with the code, and the limits of all of them
   are stated.

## Sources

- [spinyield](https://spinyield.com/) (unreachable from the research environment; details via search summaries and [Aura++](https://auraplusplus.com/launches/spinyield))
- [AdGuard: This startup pays developers for watching ads when using AI. What's the catch?](https://adguard.com/en/blog/ai-coding-ads-kickbacks-vscode.html)
- [Kickbacks AI](https://kickbacksai.org/), [AgenticAds](https://index.dodopayments.com/agenticads), [Trillboards](https://trillboards.com/coding-agents/), [Idlen](https://www.idlen.io/monetize-ai-wait-time/)
- [MarTech Edition: AI Agents' Wait Time Just Became Ad Inventory](https://martechedition.com/news/ai-agents-wait-time-just-became-ad-inventory/) (via search summary)
- [dev.to: I tried to sell the minutes you spend waiting for your coding agent](https://dev.to/eliasjunit/i-tried-to-sell-the-minutes-you-spend-waiting-for-your-coding-agent-59o2)
- [Proton: ChatGPT ads are coming](https://proton.me/blog/chatgpt-ads), [Gulf News: ChatGPT now shows ads to free users](https://gulfnews.com/technology/chatgpt-now-shows-ads-to-free-usersheres-how-to-opt-out-1.500438107), [GSMArena](https://m.gsmarena.com/chatgpt_to_show_ads_for_free_and_go_users_in_the_us-news-71485.php)
- [llmrefs: ChatGPT ads now appear in nearly 20% of US responses](https://llmrefs.com/blog/chatgpt-ads), [LLM Pulse: 72% of US ChatGPT answers now carry an ad](https://llmpulse.ai/blog/chatgpt-ads-study/)
- [arXiv 2604.08525: Ads in AI Chatbots? An Analysis of How LLMs Navigate Conflicts of Interest](https://arxiv.org/html/2604.08525v1)
- [Unit 42: Fooling AI Agents: Web-Based Indirect Prompt Injection Observed in the Wild](https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/)
- [Maister: The Psychology of Waiting Lines](https://www.columbia.edu/~ww2040/4615S13/Psychology_of_Waiting_Lines.pdf), [Shorter Wait Times: The Effects of Various Loading Screens on Perceived Performance](https://www.researchgate.net/publication/302073992_Shorter_Wait_Times_The_Effects_of_Various_Loading_Screens_on_Perceived_Performance)
- [Privacy in targeted advertising on mobile devices: a survey](https://link.springer.com/article/10.1007/s10207-022-00655-x), [Targeting and Privacy in Mobile Advertising](https://marketing.wharton.upenn.edu/wp-content/uploads/2018/03/03-22-2018-Yoganarasimhan-Hema-PAPER-TargetinhPrivacy_2018.pdf)

"""Wait prediction from the size of the request.

The wait is mostly the model *writing* the answer, and with an output guard the user sees nothing
until the whole answer is written and checked. So the wait grows with the expected answer length:

    wait ≈ security (input + output check) + time to first token + prefill(input) + output_tokens / tokens_per_s

The answer length is unknown before generation, so it is estimated from what the request asks for:
explicit lengths ("1500 kelimelik", "10 sayfa", "20 madde"), size words ("detaylı rapor",
"comprehensive guide", "kısaca"), the kind of question, and for documents the page count.

Heuristic and deliberately simple: it only has to sort requests into "too short for a sponsor",
"one sponsor" and "long enough to rotate". The evaluation measures how well it does that.
"""

import re
from dataclasses import dataclass

from .security.injection import normalize

ASCII = str.maketrans("çğşöüâîû", "cgsouaiu")


def fold(text: str) -> str:
    """Lowercase, Turkish letters folded to ASCII: "Detaylı İş Planı" -> "detayli is plani"."""
    return normalize(text).translate(ASCII)

# Explicit length → output tokens. Turkish needs more tokens per word than English; 1.5 is in between.
TOKENS_PER_WORD = 1.5
UNITS = {
    "word": TOKENS_PER_WORD, "kelime": TOKENS_PER_WORD,
    "page": 500 * TOKENS_PER_WORD, "sayfa": 500 * TOKENS_PER_WORD,
    "item": 60, "madde": 60, "bullet": 40,
    "line": 12, "satir": 12, "paragraph": 120, "paragraf": 120,
}
LENGTH = re.compile(r"(\d[\d.,]*)\s*-?\s*(words?|kelime\w*|pages?|sayfa\w*|items?|madde\w*|bullets?|lines?|satir\w*|paragraphs?|paragraf\w*)")

LONG = ("detayli", "ayrintili", "kapsamli", "eksiksiz", "rapor", "makale", "is plani", "tez", "rehber", "kilavuz",
        "dokumantasyon", "tum bolum", "her bolum", "adim adim", "testleriyle", "uctan uca",
        "detailed", "comprehensive", "in-depth", "in depth", "thorough", "report", "essay", "article",
        "business plan", "whitepaper", "step by step", "step-by-step", "all sections", "complete", "full ",
        "with tests", "documentation", "guide", "tutorial")
MEDIUM = ("plan", "acikla", "anlat", "karsilastir", "kod", "fonksiyon", "ornek", "liste", "ozetle", "oner",
          "explain", "describe", "compare", "code", "function", "script", "example", "list", "summarize", "suggest")
SHORT = ("nedir", "neresi", "kimdir", "kac ", "hangi", "cevir", "kisaca", "tek kelime", "evet mi",
         "what is", "who is", "how many", "how much", "translate", "briefly", "in one word", "short answer", "yes or no")
BRIEF = ("kisaca", "briefly", "short answer", "tek cumle", "one sentence", "in one word", "tek kelime", "tek cumle")


@dataclass(frozen=True)
class ModelSpeed:
    ttft_s: float = 0.8                 # time to first token
    tokens_per_s: float = 50.0          # writing speed
    prefill_tokens_per_s: float = 3000  # reading the input


@dataclass(frozen=True)
class Prediction:
    output_tokens: int
    input_tokens: int
    security_s: float
    llm_s: float
    total_s: float
    size: str                           # short | normal | long | very_long
    reasons: tuple[str, ...]

    def public(self) -> dict:
        return {"output_tokens": self.output_tokens, "input_tokens": self.input_tokens, "security_s": self.security_s,
                "llm_s": self.llm_s, "total_s": self.total_s, "size": self.size, "reasons": list(self.reasons)}


def size_class(tokens: int) -> str:
    return "short" if tokens < 300 else "normal" if tokens < 1000 else "long" if tokens < 2500 else "very_long"


def estimate_output_tokens(text: str, pages: int = 0) -> tuple[int, tuple[str, ...]]:
    t = fold(text)
    reasons: list[str] = []

    explicit = []
    for number, unit in LENGTH.findall(t):
        try:
            n = float(number.replace(".", "").replace(",", "."))
        except ValueError:
            continue
        per = next(v for k, v in UNITS.items() if unit.startswith(k))
        explicit.append(n * per)
        reasons.append(f"explicit:{number} {unit}")
    if explicit:
        return int(min(max(explicit), 16000)), tuple(reasons)

    if pages:
        tokens = min(200 + 25 * pages, 1500)
        reasons.append(f"document:{pages}p")
    else:
        long_hits = [c for c in LONG if c in t]
        medium_hits = [c for c in MEDIUM if c in t]
        short_hits = [c for c in SHORT if c in t]
        if long_hits:
            tokens = {1: 1500, 2: 2500}.get(len(long_hits), 3500)
            reasons.append("long:" + ",".join(long_hits[:3]))
        elif medium_hits:
            tokens = 600
            reasons.append("medium:" + ",".join(medium_hits[:3]))
        elif short_hits or (len(t) < 100 and t.rstrip().endswith("?")):
            tokens = 120
            reasons.append("short:" + (",".join(short_hits[:2]) or "short_question"))
        else:
            tokens = 400
            reasons.append("default")
    if any(b in t for b in BRIEF):
        tokens = max(60, tokens // 3)
        reasons.append("brief")
    return int(tokens), tuple(reasons)


def predict(text: str, *, pages: int = 0, input_chars: int | None = None, speed: ModelSpeed = ModelSpeed(),
            security_s: float = 0.3, output_guard: bool = True) -> Prediction:
    """Predicted wait until the user sees the answer. Without an output guard the answer streams,
    so the wait ends at the first token."""
    out, reasons = estimate_output_tokens(text, pages)
    inp = int((input_chars if input_chars is not None else len(text)) / 4)
    prefill = inp / speed.prefill_tokens_per_s
    llm = speed.ttft_s + prefill + (out / speed.tokens_per_s if output_guard else 0.0)
    return Prediction(out, inp, round(security_s, 2), round(llm, 2), round(security_s + llm, 2), size_class(out), reasons)


def llm_seconds(output_tokens: int, input_tokens: int, speed: ModelSpeed = ModelSpeed(), output_guard: bool = True) -> float:
    """Actual model time for a given (true) answer length."""
    return speed.ttft_s + input_tokens / speed.prefill_tokens_per_s + (output_tokens / speed.tokens_per_s if output_guard else 0.0)

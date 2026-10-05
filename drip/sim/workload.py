"""A chat workload where the wait follows the size of the request.

Each request is a real prompt. The predictor only sees the prompt; the "true" answer length is
drawn around what that kind of request really produces (an assumption), so the predictor can be
wrong the way a real one would be.
"""

import random
from dataclasses import dataclass

from ..predictor import LENGTH, UNITS, fold, llm_seconds, predict

# (type, share of requests, true median answer tokens, prompts)
TYPES = [
    ("quick", 0.30, 110, [
        "Türkiye'nin başkenti neresi?", "What is the capital of Japan?",
        "Bu cümleyi İngilizceye çevir: Yarın sabah görüşürüz.", "How many grams are in a pound?",
        "Python'da bir liste nasıl ters çevrilir?", "Kısaca: HTTP 404 hatası ne demek?",
        "Ekim'de İstanbul'da hava genelde kaç derece olur?", "Who wrote Crime and Punishment?",
    ]),
    ("normal", 0.38, 500, [
        "Ekimde Roma'ya 4 günlük bir gezi planı hazırlar mısın? Uçuş ve otel önerisi de olsun.",
        "Explain how HTTPS works.", "CSV dosyası okuyup bir sütunun ortalamasını hesaplayan Python fonksiyonu yaz.",
        "30 bin TL bütçeyle laptop alacağım, hangi özelliklere bakmalıyım?",
        "Compare PostgreSQL and MySQL for a small startup.", "İngilizce öğrenmek için haftalık çalışma planı önerir misin?",
        "Write a cover letter for a junior data analyst role.", "Balkonda domates yetiştirmek için neler gerekir?",
    ]),
    ("long", 0.22, 1800, [
        "Yapay zeka güvenliği hakkında detaylı bir rapor yaz; giriş, yöntem ve sonuç bölümleri olsun.",
        "1200 kelimelik bir blog yazısı yaz: uzaktan çalışmanın verimliliğe etkisi.",
        "Write a comprehensive guide to setting up CI/CD for a Python project.",
        "Yeni bir kahve markası için kapsamlı bir pazarlama planı hazırla.",
        "Step-by-step tutorial: build a REST API with FastAPI and PostgreSQL.",
        "İki haftalık detaylı bir Japonya seyahat rehberi hazırla, şehir şehir.",
    ]),
    ("very_long", 0.10, 3500, [
        "Tüm bölümleriyle eksiksiz 10 sayfalık bir iş planı hazırla: online dil kursu girişimi.",
        "Write a complete REST API in Python with tests and documentation for a todo app.",
        "3000 kelimelik kapsamlı bir literatür taraması yaz: büyük dil modellerinde güvenlik.",
        "Write an in-depth technical whitepaper on zero-trust networking, full text for every section.",
    ]),
]


@dataclass(frozen=True)
class Request:
    type: str
    prompt: str
    true_tokens: int
    input_tokens: int
    predicted_tokens: int
    predicted_wait_s: float
    true_wait_s: float          # with output guard (answer held back)
    streaming_wait_s: float     # without output guard (answer streams after the first token)


def true_tokens(prompt: str, type_median: int, rng: random.Random) -> int:
    """What the model actually writes. Explicit lengths are followed closely; otherwise wide spread."""
    explicit = []
    for number, unit in LENGTH.findall(fold(prompt)):
        n = float(number.replace(".", "").replace(",", "."))
        explicit.append(n * next(v for k, v in UNITS.items() if unit.startswith(k)))
    if explicit:
        return max(20, int(max(explicit) * rng.lognormvariate(0, 0.15)))
    return max(20, int(type_median * rng.lognormvariate(0, 0.45)))


def draw(rng: random.Random, security_s: float = 0.3) -> Request:
    kind, _, median, prompts = rng.choices(TYPES, [t[1] for t in TYPES])[0]
    prompt = rng.choice(prompts)
    pred = predict(prompt, security_s=security_s)
    tokens = true_tokens(prompt, median, rng)
    return Request(kind, prompt, tokens, pred.input_tokens, pred.output_tokens, pred.total_s,
                   round(security_s + llm_seconds(tokens, pred.input_tokens), 2),
                   round(security_s + llm_seconds(tokens, pred.input_tokens, output_guard=False), 2))


def predictor_accuracy(reqs: list[Request], show_from_s: float, rotate_from_s: float) -> dict:
    n = len(reqs)
    ape = sorted(abs(r.predicted_wait_s - r.true_wait_s) / r.true_wait_s for r in reqs)
    bucket = lambda w: 0 if w < show_from_s else 1 if w < rotate_from_s else 2
    by_type = {}
    for t, *_ in TYPES:
        sub = [r for r in reqs if r.type == t]
        if sub:
            by_type[t] = {"n": len(sub), "mean_true_wait_s": sum(r.true_wait_s for r in sub) / len(sub),
                          "mean_predicted_wait_s": sum(r.predicted_wait_s for r in sub) / len(sub)}
    wait_buckets = {"<4s": 0, "4-10s": 0, "10-20s": 0, "20-40s": 0, "40-90s": 0, ">90s": 0}
    for r in reqs:
        w = r.true_wait_s
        key = "<4s" if w < 4 else "4-10s" if w < 10 else "10-20s" if w < 20 else "20-40s" if w < 40 else "40-90s" if w < 90 else ">90s"
        wait_buckets[key] += 1
    return {
        "n": n,
        "median_abs_pct_error": ape[n // 2],
        "within_50pct": sum(a <= 0.5 for a in ape) / n,
        "show_decision_accuracy": sum((r.predicted_wait_s >= show_from_s) == (r.true_wait_s >= show_from_s) for r in reqs) / n,
        "bucket_accuracy": sum(bucket(r.predicted_wait_s) == bucket(r.true_wait_s) for r in reqs) / n,
        "short_requests_predicted_long": sum(r.true_wait_s < show_from_s <= r.predicted_wait_s for r in reqs) / n,
        "by_type": by_type,
        "wait_share": {k: v / n for k, v in wait_buckets.items()},
        "streaming_wait_share_below_4s": sum(r.streaming_wait_s < 4 for r in reqs) / n,
    }


def expectation(predicted_tokens: int, per_1k: float) -> float:
    """People who ask for a 10-page plan expect to wait longer than for a one-line answer."""
    return 1.0 + per_1k * predicted_tokens / 1000

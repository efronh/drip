"""Keyword classifier: document -> one coarse category.

The category is the only content-derived value that ever crosses to the ad side, so it is kept
coarse on purpose (10 values). A real deployment would use a small local model; the contract
(10 labels, computed inside the security boundary) stays the same.
"""

import re
from collections import Counter

KEYWORDS: dict[str, tuple[str, ...]] = {
    "financial": ("invoice", "balance sheet", "bank statement", "loan", "credit", "tax return", "iban", "salary slip",
                  "fatura", "bilanço", "hesap özeti", "kredi", "vergi", "banka", "faiz", "borç", "ödeme planı"),
    "medical": ("patient", "diagnosis", "prescription", "blood test", "symptom", "treatment", "clinic", "mg",
                "hasta", "teşhis", "tanı", "reçete", "tahlil", "tedavi", "doktor", "hastane", "ilaç"),
    "legal": ("contract", "agreement", "plaintiff", "defendant", "court", "clause", "lawsuit", "attorney",
              "sözleşme", "dava", "mahkeme", "avukat", "madde", "taraflar", "hukuk", "vekalet"),
    "hr": ("employee", "performance review", "termination", "payroll", "candidate", "disciplinary", "onboarding",
           "çalışan", "personel", "performans değerlendirme", "işten çıkarma", "bordro", "aday", "disiplin", "insan kaynakları"),
    "personal": ("dear mom", "diary", "my family", "birthday", "personal letter",
                 "sevgili annem", "günlük", "ailem", "doğum günü", "kişisel"),
    "confidential_business": ("confidential", "internal only", "do not distribute", "nda", "trade secret", "board meeting",
                              "roadmap", "acquisition", "gizli", "kurum içi", "dağıtmayın", "ticari sır", "yönetim kurulu"),
    "travel": ("flight", "hotel", "itinerary", "visa", "passport", "airport", "trip", "booking",
               "uçuş", "otel", "seyahat", "vize", "havalimanı", "tatil", "rezervasyon", "gezi"),
    "shopping": ("product", "price", "discount", "order", "catalog", "warranty", "laptop", "phone",
                 "ürün", "fiyat", "indirim", "sipariş", "katalog", "garanti", "kargo", "mağaza"),
    "education": ("course", "lecture", "syllabus", "exam", "student", "homework", "university", "thesis",
                  "ders", "kurs", "sınav", "öğrenci", "ödev", "üniversite", "tez", "müfredat"),
}

# Strong markers decide on their own: one "CONFIDENTIAL" header outweighs ten travel words.
STRONG = {"confidential_business": ("confidential", "internal only", "do not distribute", "gizli", "kurum içi", "ticari sır")}
MIN_HITS = 2


def classify(text: str) -> tuple[str, tuple[str, ...]]:
    """Returns (top category, every category with enough evidence).

    The top category picks the ad. The full set decides sensitivity: padding a medical report with
    travel words can win the top spot, but "medical" is still present and still counts.
    """
    lower = text.lower()
    hits = Counter()
    for category, words in KEYWORDS.items():
        for w in words:
            n = len(re.findall(rf"(?<!\w){re.escape(w)}", lower))
            if n:
                hits[category] += min(n, 5)
    present = {c for c, n in hits.items() if n >= MIN_HITS}
    for category, markers in STRONG.items():
        if any(m in lower for m in markers):
            present.add(category)
            return category, tuple(sorted(present))
    if not present:
        return "general", ()
    return hits.most_common(1)[0][0], tuple(sorted(present))

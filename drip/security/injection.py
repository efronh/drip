"""Rule-based prompt-injection detection for the mock gateway (English + Turkish).

Direct injection: instructions that try to override the model ("ignore previous instructions").
Indirect injection: text inside the document that addresses the AI reading it ("Note to the AI
summarizer: ..."), the typical shape of an attack hidden in a document.

This is a baseline, not a product: it catches literal attacks and is easy to evade. The Sieve
adapter swaps it for Sieve's rules + ML.
"""

import re
import unicodedata

from ..models import Finding

ZERO_WIDTH = re.compile(r"[​-‏⁠-⁤﻿]")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

OVERRIDE = [
    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|earlier|all|your|the)\b.{0,20}\b(instructions?|prompts?|rules|guidelines)\b",
    r"\b(reveal|print|show|output|repeat)\b.{0,20}\b(system|hidden|initial)\s+(prompt|instructions?)\b",
    r"\byou are now\b.{0,30}\b(dan|developer mode|unrestricted|jailbroken)\b",
    r"\b(önceki|yukaridaki|tüm|bütün|verilen)\b.{0,25}\btalimat\w*\b.{0,25}\b(unut|yok say|görmezden gel|dikkate alma)",
    r"\bsistem\s+(prompt|talimat|mesaj)\w*\b.{0,25}\b(göster|yaz|açikla|paylaş|söyle)",
    r"\bkurallari\w*\b.{0,15}\b(unut|yok say|çiğne)",
]

ADDRESS_AI = [
    r"\b(note|message|instructions?|attention)\b.{0,10}\b(to|for)\b.{0,10}\b(the\s+)?(ai|assistant|llm|language model|model|chatbot|summari[sz]er|gpt|claude)\b",
    r"\bif you are an?\s+(ai|language model|assistant|llm|chatbot)\b",
    r"\b(ai|assistant|llm|model|summari[sz]er)s?\b.{0,20}\b(reading|processing|summari[sz]ing)\b.{0,10}\bthis\b",
    r"\bwhen (you )?summari[sz]ing this\b",
    r"\b(yapay zek[aâ]|asistan|dil modeli|model)\w*\b.{0,15}\b(not|uyari|talimat)\b",
    r"\bbu belgeyi\b.{0,20}\b(özetleyen|okuyan|işleyen)\b",
    r"\beğer\b.{0,10}\b(bir )?(yapay zek[aâ]|dil modeli|asistan)\w*\b",
]

# What an attack wants the model to do. Combined with ADDRESS_AI this is an indirect attack.
PAYLOAD = [
    r"https?://\S+",
    r"!\[[^\]]*\]\(",
    r"\b(do not|don't|never)\b.{0,15}\b(mention|tell|summari[sz]e|reveal|warn)\b",
    r"\b(tell|instruct|ask|urge)\b.{0,15}\b(the )?(user|reader)\b",
    r"\b(include|insert|append|add)\b.{0,30}\b(link|url|email|password|credentials|api key)\b",
    r"\b(send|forward|post|exfiltrate)\b.{0,40}\b(to|at)\b",
    r"\b(bahsetme|söyleme|belirtme|özetleme|uyarma)\b",
    r"\b(kullaniciya|okuyucuya)\b.{0,30}\b(söyle|yönlendir|tikla|gönder)",
]

# An attack phrase being *discussed* (security article, training material).
DISCUSSION = re.compile(r"\b(example|e\.g\.|attackers?|attack|such as|for instance|örne[ğk]\w*|saldir\w*|saldirgan\w*)\b")
QUOTED = re.compile(r"[\"“”'‘’«»]")

_c = lambda ps: [re.compile(p, re.IGNORECASE | re.DOTALL) for p in ps]
OVERRIDE_RE, ADDRESS_RE, PAYLOAD_RE = _c(OVERRIDE), _c(ADDRESS_AI), _c(PAYLOAD)


def normalize(text: str) -> str:
    # Fold dotted/dotless i so "IGNORE", "İGNORE" and "kuralları" all match ASCII-i patterns.
    text = unicodedata.normalize("NFKC", ZERO_WIDTH.sub("", text))
    text = text.replace("İ", "i").replace("I", "i").lower().replace("ı", "i")
    return re.sub(r"[ \t]+", " ", text)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text) if s.strip()]


def check(text: str, source: str = "document") -> list[Finding]:
    """source: "document" (indirect) or "instruction" (the user's own request, direct)."""
    findings: list[Finding] = []
    for sent in sentences(normalize(text)):
        override = any(r.search(sent) for r in OVERRIDE_RE)
        addressed = any(r.search(sent) for r in ADDRESS_RE)
        payload = any(r.search(sent) for r in PAYLOAD_RE)
        discussed = bool(DISCUSSION.search(sent)) and bool(QUOTED.search(sent))

        if override and discussed:
            findings.append(Finding("prompt_injection", "review", f"{source}:quoted_override"))
        elif override:
            kind = "direct_override" if source == "instruction" else "override_in_document"
            findings.append(Finding("prompt_injection", "block", f"{source}:{kind}"))
        elif addressed and payload:
            findings.append(Finding("prompt_injection", "block", f"{source}:indirect_instruction"))
        elif addressed:
            findings.append(Finding("prompt_injection", "review", f"{source}:addresses_ai"))
    return dedupe(findings)


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in findings:
        if (f.check, f.severity, f.detail) not in seen:
            seen.add((f.check, f.severity, f.detail))
            out.append(f)
    return out

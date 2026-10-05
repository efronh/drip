"""PII detection and masking for the mock gateway. Validated where a checksum exists (TCKN, IBAN,
card), so random 11-digit numbers are not flagged."""

import re

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE = re.compile(r"(?<!\d)(?:\+90[\s-]?|0)?5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)")
TCKN = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")
IBAN = re.compile(r"\bTR\d{2}(?:[\s]?\d{4}){5}[\s]?\d{2}\b", re.IGNORECASE)
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def tckn_valid(n: str) -> bool:
    d = [int(c) for c in n]
    if len(d) != 11 or d[0] == 0:
        return False
    d10 = ((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10
    return d10 == d[9] and sum(d[:10]) % 10 == d[10]


def iban_valid(s: str) -> bool:
    s = re.sub(r"\s", "", s).upper()
    s = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in s)) % 97 == 1


def luhn_valid(s: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", s)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


# (type, pattern, validator, placeholder). Order matters: IBAN and card before TCKN/phone.
DETECTORS = [
    ("iban", IBAN, iban_valid, "[IBAN]"),
    ("card", CARD, luhn_valid, "[CARD]"),
    ("email", EMAIL, None, "[EMAIL]"),
    ("tckn", TCKN, tckn_valid, "[TCKN]"),
    ("phone", PHONE, None, "[PHONE]"),
    ("ssn", SSN, None, "[SSN]"),
]


def scan(text: str) -> tuple[str, dict[str, int]]:
    """Returns (masked text, counts per PII type)."""
    counts: dict[str, int] = {}
    for kind, pattern, valid, placeholder in DETECTORS:
        def sub(m, kind=kind, valid=valid, placeholder=placeholder):
            if valid and not valid(m.group(0)):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            return placeholder
        text = pattern.sub(sub, text)
    return text, counts

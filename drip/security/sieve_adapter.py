"""Mode 2: use Sieve's security decisions (https://github.com/efronh/sieve).

Sieve is optional. It is imported from an installed package, or from SIEVE_PATH (a checkout),
so this project never depends on it. Extraction, classification and the policy engine stay ours;
only injection detection and PII masking come from Sieve:

    document text -> sieve.DocumentGuard.check()   (parts, hidden text, rules + ML)
    instruction   -> sieve.Guardrail.check()        (direct injection)
    document text -> sieve.mask()                   (TC kimlik, IBAN, card, phone, e-mail, VKN, keys)
"""

import os
import re
import sys

from ..models import Finding
from .gateway import Detection

PLACEHOLDER = re.compile(r"\[([A-ZÇĞİÖŞÜ_]+)\]")
PII_NAMES = {"TC_KIMLIK": "tckn", "IBAN": "iban", "KART": "card", "TELEFON": "phone", "EPOSTA": "email",
             "VKN": "vkn", "SKT": "card", "CVV": "card"}


def import_sieve():
    try:
        import sieve  # noqa: F401
    except ImportError:
        path = os.environ.get("SIEVE_PATH", os.path.expanduser("~/code/sieve"))
        if not os.path.isdir(os.path.join(path, "sieve")):
            raise ImportError("Sieve not found: pip install sieve-tr, or set SIEVE_PATH to a checkout") from None
        sys.path.insert(0, path)
    import sieve
    return sieve


class SieveDetector:
    name = "sieve"

    def __init__(self):
        sieve = import_sieve()
        self.sieve = sieve
        self.documents = sieve.DocumentGuard()
        self.messages = sieve.Guardrail()
        self.output = None

    @staticmethod
    def findings_from(result, source: str) -> list[Finding]:
        return [Finding("prompt_injection" if "injection" in f.check else f.check, f.action,
                        f"{source}:{f.check}:{','.join(map(str, f.matches))[:80]}")
                for f in result.findings if f.action != "allow" and not getattr(f, "shadow", False)]

    def pii_counts(self, original: str, masked: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        before = set(PLACEHOLDER.findall(original))
        for name in PLACEHOLDER.findall(masked):
            if name not in before:
                kind = PII_NAMES.get(name, name.lower())
                counts[kind] = counts.get(kind, 0) + 1
        return counts

    def detect_message(self, text: str) -> Detection:
        r = self.messages.check(text)
        counts = self.pii_counts(text, r.text)
        extra = [Finding("pii", "info", "masked:" + ",".join(sorted(counts)))] if counts else []
        return Detection(self.findings_from(r, "instruction") + extra, r.text, counts)

    def check_output(self, answer: str) -> tuple[str, str, list[Finding]]:
        if self.output is None:
            from ..llm import SYSTEM_PROMPT
            self.output = self.sieve.OutputGuard(SYSTEM_PROMPT)
        r = self.output.check(answer)
        findings = [Finding(f.check, f.action, f"output:{f.check}") for f in getattr(r, "findings", [])
                    if getattr(f, "action", "allow") != "allow"]
        return r.text, r.action, findings

    def detect(self, text: str, instruction: str) -> Detection:
        findings = self.findings_from(self.documents.check(text), "document")
        if instruction.strip():
            findings += self.findings_from(self.messages.check(instruction), "instruction")

        masked = self.sieve.mask(text)
        counts = self.pii_counts(text, masked)
        if counts:
            findings.append(Finding("pii", "info", "masked:" + ",".join(sorted(counts))))
        return Detection(findings, masked, counts)

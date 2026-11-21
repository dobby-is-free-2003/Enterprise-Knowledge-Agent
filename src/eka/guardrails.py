"""Guardrails: PII redaction, prompt-injection filtering, grounding check, output secret filter."""
from __future__ import annotations

import re

from .text_utils import content_terms, split_sentences

# ---------------------------------------------------------------------------- PII redaction
_PII = {
    "aadhaar": re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "phone": re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"),
    "api_key": re.compile(r"\b(?:AIza[0-9A-Za-z_\-]{30,}|AKIA[0-9A-Z]{16})\b"),
}
_CARD = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")


def _luhn(num: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", num)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Replace sensitive identifiers with placeholders. Returns (text, kinds_found)."""
    found: list[str] = []

    def card_sub(m):
        if _luhn(m.group(0)):
            found.append("card")
            return "[REDACTED:card]"
        return m.group(0)

    text = _CARD.sub(card_sub, text)
    for kind, pat in _PII.items():
        if pat.search(text):
            found.append(kind)
            text = pat.sub(f"[REDACTED:{kind}]", text)
    return text, sorted(set(found))


# ---------------------------------------------------------------------------- injection filter
_INJECTION = {
    # one or more determiners may precede the noun: "ignore previous instructions", "disregard your previous instructions"
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\s+(?:(?:all|any|the|your|my|these|those|previous|prior|above|earlier)\s+)+"
        r"(?:instructions?|prompts?|rules|guidelines|messages|directions|restrictions)\b",
        re.I,
    ),
    "role_override": re.compile(r"\byou are now\b|\bnew (system )?instructions?\s*:|\bact as (an? )?(unrestricted|jailbroken)", re.I),
    "system_markup": re.compile(r"<\s*/?\s*(system|assistant|instructions?)\s*>|\bsystem (prompt|message|note)\b", re.I),
    "ai_directive": re.compile(r"\b(note|message|instruction)s? (to|for) (the )?(ai|assistant|model|llm|agent)s?\b|\bassistants? (must|should|will)\b", re.I),
    "secrecy": re.compile(r"(do not|don't|never)\s+(tell|inform|mention|reveal|show)[^.]{0,40}\b(user|anyone|human)\b", re.I),
    "reveal_prompt": re.compile(r"reveal (your|the) (system )?(prompt|instructions)", re.I),
    "exfiltration": re.compile(
        r"\b(forward|send|email|post|upload|exfiltrate)\b[^.]{0,80}\b(conversation|chat history|credentials|passwords?|api keys?|secrets?|user'?s? (data|question|query))\b",
        re.I,
    ),
}
REMOVED = "[removed: suspected prompt injection]"


def scan_injection(text: str) -> list[str]:
    return [name for name, pat in _INJECTION.items() if pat.search(text)]


def sanitize_passage(text: str) -> tuple[str, list[str]]:
    """Drop sentences that look like instructions aimed at the model. Retrieved text is data."""
    flags: list[str] = []
    out_lines = []
    for line in text.splitlines():
        parts = re.split(r"(?<=[.!?])\s+", line)
        kept = []
        for part in parts:
            hit = scan_injection(part)
            if hit:
                flags.extend(hit)
                kept.append(REMOVED)
            else:
                kept.append(part)
        out_lines.append(" ".join(kept))
    return "\n".join(out_lines), sorted(set(flags))


def wrap_untrusted(passages) -> str:
    """Format passages for the prompt, clearly marked as untrusted data."""
    blocks = []
    for p in passages:
        blocks.append(
            f'<passage id="{p.label}" title="{p.chunk.title}" section="{p.chunk.heading_path}">\n{p.chunk.text}\n</passage>'
        )
    return "<untrusted_documents>\n" + "\n".join(blocks) + "\n</untrusted_documents>"


# ---------------------------------------------------------------------------- grounding
_LABEL = re.compile(r"\[(P\d+)\]")


def cited_labels(answer: str) -> list[str]:
    return list(dict.fromkeys(_LABEL.findall(answer)))


def strip_labels(answer: str) -> str:
    return re.sub(r"\s*\[P\d+\]", "", answer).strip()


def check_grounding(answer: str, cited_texts: list[str], min_support: float = 0.5) -> tuple[bool, float]:
    """Every sentence of the answer must be mostly supported by the cited passages.

    Support = share of the sentence's content terms present in the union of cited passage terms.
    It is a cheap lexical check, not a proof; it catches answers that wander away from the sources.
    """
    if not cited_texts:
        return False, 0.0
    support_terms = set()
    for t in cited_texts:
        support_terms.update(content_terms(t))
    worst = 1.0
    for sent in split_sentences(strip_labels(answer)):
        terms = set(content_terms(sent))
        if not terms:
            continue
        worst = min(worst, len(terms & support_terms) / len(terms))
    return worst >= min_support, round(worst, 3)


# ---------------------------------------------------------------------------- output filter
def filter_output(text: str) -> tuple[str, list[str]]:
    """Block secrets and high-risk identifiers from leaving in an answer."""
    red, kinds = redact_pii(text)
    return red, [k for k in kinds if k in {"card", "aadhaar", "pan", "api_key"}]

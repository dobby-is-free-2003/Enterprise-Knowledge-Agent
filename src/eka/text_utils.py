"""Small text helpers shared by the offline embedder, retrieval and guardrails."""
from __future__ import annotations

import math
import re
from collections import Counter

STOP = set(
    """a an the of to in on for and or is are was were be been being what which who whom whose when
    where why how do does did can could should would will shall may might must i we you he she they it
    its our your their my me us them this that these those with at by from as about into than then
    there here any all some per if not no yes please tell show give list explain much many long often
    get got have has had also so too very just i'm what's whats there's""".split()
)


def stem(word: str) -> str:
    """Tiny suffix stripper. Also drops a trailing 'e' so take/takes/taking all map to 'tak'."""
    for suf in ("ing", "edly", "ed", "es", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            word = word[: -len(suf)]
            break
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def content_terms(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text) if t not in STOP and len(t) > 1]


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    out = []
    for part in _SENT_SPLIT.split(text):
        part = part.strip().lstrip("-*• ").strip()
        if part and not part.startswith("#"):
            out.append(part)
    return out


class Lexicon:
    """Corpus statistics used for IDF-weighted query coverage.

    Coverage = share of the query's informative terms (weighted by rarity) that appear in a text.
    It is the relevance gate that lets the agent say "not found" instead of guessing.
    """

    def __init__(self) -> None:
        self.df: Counter[str] = Counter()
        self.n_docs = 0

    def fit(self, texts) -> None:
        self.df = Counter()
        self.n_docs = 0
        for t in texts:
            self.n_docs += 1
            for term in set(content_terms(t)):
                self.df[term] += 1

    def idf(self, term: str) -> float:
        return math.log((self.n_docs + 1) / (self.df.get(term, 0) + 1)) + 1.0

    def coverage(self, query_terms: list[str], text: str) -> float:
        qs = set(query_terms)
        if not qs:
            return 0.0
        have = set(content_terms(text))
        total = sum(self.idf(t) for t in qs)
        hit = sum(self.idf(t) for t in qs if t in have)
        return hit / total if total else 0.0

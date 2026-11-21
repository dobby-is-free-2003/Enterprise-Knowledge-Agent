"""LLM and embedding backends.

Two families:
  * Gemini (google-genai SDK): used when GEMINI_API_KEY is set.
  * Offline: a deterministic hashing embedder so the whole pipeline, tests and evaluation run with
    no network and no key. It is a stand-in, not a quality claim about Gemini.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from typing import Protocol

import numpy as np

from .text_utils import content_terms


# ----------------------------------------------------------------------------- embedders
class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


class HashingEmbedder:
    """Deterministic bag-of-stems + bigram hashing vectors (md5, so stable across runs)."""

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = f"hashing-{dim}"

    @staticmethod
    def _h(token: str, dim: int) -> int:
        return int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % dim

    def _vec(self, text: str) -> np.ndarray:
        terms = content_terms(text)
        v = np.zeros(self.dim, dtype=np.float32)
        counts: dict[str, int] = {}
        for t in terms:
            counts[t] = counts.get(t, 0) + 1
        for a, b in zip(terms, terms[1:]):
            counts[f"{a}_{b}"] = counts.get(f"{a}_{b}", 0) + 1
        for tok, c in counts.items():
            v[self._h(tok, self.dim)] += 1.0 + np.log(c)
        return v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _l2(np.stack([self._vec(t) for t in texts])) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return _l2(self._vec(text)[None, :])[0]


# ----------------------------------------------------------------------------- Gemini
def _retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    s = str(exc).upper()
    return any(k in s for k in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "429"))


def call_with_backoff(fn, max_retries: int = 5, base: float = 1.0, cap: float = 30.0, sleep=time.sleep):
    """Exponential backoff with jitter on rate-limit and transient errors (free tier hits 429s)."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - SDK raises several exception types
            if attempt == max_retries - 1 or not _retryable(e):
                raise
            sleep(min(cap, base * (2**attempt)) + random.random() * 0.25)
    raise RuntimeError("unreachable")


class GeminiLLM:
    """Thin wrapper with retries and usage counters."""

    def __init__(self, api_key: str | None, model: str, client=None, max_retries: int = 5, sleep=time.sleep):
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self._sleep = sleep
        self.requests = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def generate(self, prompt: str, system: str | None = None, json_mode: bool = False, temperature: float = 0.1) -> str:
        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json" if json_mode else None,
        )

        def _call():
            return self.client.models.generate_content(model=self.model, contents=prompt, config=cfg)

        resp = call_with_backoff(_call, self.max_retries, sleep=self._sleep)
        self.requests += 1
        usage = getattr(resp, "usage_metadata", None)
        if usage is not None:
            self.tokens_in += getattr(usage, "prompt_token_count", 0) or 0
            self.tokens_out += getattr(usage, "candidates_token_count", 0) or 0
        return resp.text or ""

    def generate_json(self, prompt: str, system: str | None = None) -> dict:
        for _ in range(2):
            raw = self.generate(prompt, system=system, json_mode=True)
            parsed = parse_json_loose(raw)
            if parsed is not None:
                return parsed
        return {}


def parse_json_loose(raw: str):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


class GeminiEmbedder:
    def __init__(self, api_key: str | None, model: str, dim: int = 768, client=None, sleep=time.sleep):
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self.client = client
        self.model = model
        self.dim = dim
        self.name = f"gemini:{model}:{dim}"
        self._sleep = sleep

    def _embed(self, texts: list[str], task: str) -> np.ndarray:
        from google.genai import types

        out: list[list[float]] = []
        for i in range(0, len(texts), 50):  # batch to stay inside request-size and rate limits
            batch = texts[i : i + 50]
            cfg = types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim)
            resp = call_with_backoff(
                lambda: self.client.models.embed_content(model=self.model, contents=batch, config=cfg),
                sleep=self._sleep,
            )
            out.extend([e.values for e in resp.embeddings])
        return _l2(np.array(out, dtype=np.float32)) if out else np.zeros((0, self.dim), np.float32)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "RETRIEVAL_QUERY")[0]

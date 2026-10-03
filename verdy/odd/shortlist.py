"""Embedding shortlist: the top-k ontology entries for each extracted candidate.

A resolver never sees the whole ontology, only the ``k`` entries closest to the
candidate. This keeps Laya's option list within its token budget and an LLM prompt short.

An embedder is any callable mapping a list of strings to an array of shape
``(len(texts), dim)``, the same contract as ``laya.shortlist``'s ``embed_fn``. Built in:

``hashing``
    Character-trigram and word hashing. Local, deterministic, no dependencies. It finds
    lexical overlap ("strong current" → ``current_speed``), not meaning; use a sentence
    embedder for paraphrases.
``sentence-transformers[:MODEL]``
    Any `sentence-transformers` model, run locally (``pip install sentence-transformers``).
    Default model ``all-MiniLM-L6-v2``.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from verdy.odd.ontology import Ontology, OntologyEntry

Embedder = Callable[[Sequence[str]], Any]
DEFAULT_TOP_K = 15


def normalize(text: str) -> str:
    """``"Murky-Water "`` → ``"murky_water"``: the key exact matching compares."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class HashingEmbedder:
    """Bag of hashed character trigrams and words, L2-normalised."""

    name = "hashing"

    def __init__(self, dim: int = 2048):
        self.dim = dim

    def _bucket(self, token: str) -> int:
        return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big") \
            % self.dim

    def __call__(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim))
        for i, text in enumerate(texts):
            words = normalize(text).split("_")
            for w in words:
                if not w:
                    continue
                out[i, self._bucket("w:" + w)] += 2.0
                padded = f"#{w}#"
                for j in range(len(padded) - 2):
                    out[i, self._bucket("c:" + padded[j:j + 3])] += 1.0
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


class SentenceTransformerEmbedder:
    """A local sentence-transformers model."""

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "the sentence-transformers embedder needs: pip install sentence-transformers"
            ) from exc
        self.name = f"sentence-transformers:{model}"
        self._model = SentenceTransformer(model)

    def __call__(self, texts: Sequence[str]) -> np.ndarray:  # pragma: no cover - optional
        return np.asarray(self._model.encode(list(texts), normalize_embeddings=True))


def make_embedder(spec: str | Embedder | None) -> Embedder:
    """``None``/``"hashing"``, ``"sentence-transformers[:MODEL]"``, or a callable."""
    if spec is None or spec == "hashing":
        return HashingEmbedder()
    if callable(spec):
        return spec
    if spec.startswith("sentence-transformers"):
        _, _, model = spec.partition(":")
        return SentenceTransformerEmbedder(model or "all-MiniLM-L6-v2")
    raise ValueError(f"unknown embedder {spec!r}: use 'hashing' or 'sentence-transformers[:MODEL]'")


def embedder_name(embed: Embedder) -> str:
    return str(getattr(embed, "name", type(embed).__name__))


@dataclass(frozen=True)
class Scored:
    entry: OntologyEntry
    score: float


class Shortlister:
    """Ranks ontology entries for a query. Entry vectors are computed once."""

    def __init__(self, ontology: Ontology, embed: Embedder | str | None = None,
                 k: int = DEFAULT_TOP_K):
        if k < 1:
            raise ValueError("top-k must be at least 1")
        self.ontology = ontology
        self.embed = make_embedder(embed)
        self.k = k
        self._vectors = self._unit(self.embed([e.text() for e in ontology.entries])) \
            if len(ontology) else np.zeros((0, 1))
        self._lexical: dict[str, OntologyEntry] = {}
        for e in ontology.entries:
            for key in [e.name, *e.synonyms]:
                self._lexical.setdefault(normalize(key), e)

    @staticmethod
    def _unit(x: Any) -> np.ndarray:
        x = np.nan_to_num(np.asarray(x, dtype=float))
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        return x / np.where(norms > 0, norms, 1.0)

    def lexical_hits(self, keys: Sequence[str]) -> list[OntologyEntry]:
        """Entries whose name or a synonym equals one of ``keys`` after normalisation."""
        hits: list[OntologyEntry] = []
        for key in keys:
            e = self._lexical.get(normalize(key))
            if e is not None and e not in hits:
                hits.append(e)
        return hits

    def __call__(self, query: str, exact_keys: Sequence[str] = ()) -> list[Scored]:
        """Top-k entries by cosine similarity; exact name/synonym hits always come first."""
        if not len(self.ontology):
            return []
        q = self._unit(self.embed([query]))[0]
        sims = self._vectors @ q
        order = sorted(range(len(sims)), key=lambda i: (-sims[i], i))
        hits = self.lexical_hits(exact_keys)
        ranked = [Scored(e, 1.0) for e in hits]
        for i in order:
            entry = self.ontology.entries[i]
            if entry not in hits:
                ranked.append(Scored(entry, round(float(sims[i]), 4)))
        return ranked[: max(self.k, len(hits))]

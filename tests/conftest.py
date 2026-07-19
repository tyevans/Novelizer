from __future__ import annotations
import hashlib


class FakeEmbeddingFunction:
    """Deterministic, network-free stand-in for chromadb's OpenAIEmbeddingFunction.

    Hashes character trigrams of each input string into a fixed 16-dim bag-
    of-trigrams count vector (L2-normalized). Similar strings share many
    trigrams and land close together in the vector space -- unlike a naive
    `hash(text) % N` one-hot, which would scatter near-identical strings
    randomly -- so near-duplicate-detection tests are meaningful without any
    ML dependency or live embedding endpoint.
    """

    _DIM = 16

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in input]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    @staticmethod
    def name() -> str:
        return "fake_trigram_embedding"

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self._DIM
        normalized = text.lower()
        trigrams = [normalized[i : i + 3] for i in range(len(normalized) - 2)] or [normalized]
        for tri in trigrams:
            bucket = int(hashlib.md5(tri.encode()).hexdigest(), 16) % self._DIM
            vec[bucket] += 1.0
        norm = sum(v * v for v in vec) ** 0.5
        if norm == 0:
            return vec
        return [v / norm for v in vec]

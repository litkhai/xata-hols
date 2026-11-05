"""Synthetic, seeded embeddings: no model and no API call.

Real embeddings cluster by topic, so these do too: 64 topic centroids in 384 dimensions
(the size of small sentence-embedding models), and every vector is a centroid plus noise,
normalised for cosine distance. A text decides its own vector:

  m:<user>:<i>        a preloaded memory of <user>
  s:<user>:<session>:<turn>   a memory written during a session
  q:<user>:<j>        a recall query of <user>: one of their memories plus noise

so the stores can call `embed()` with plain strings and the ground truth for recall can be
computed here without asking either store.
"""
from __future__ import annotations

import hashlib

import numpy as np

DIMS = 384
CENTROIDS = 64
USERS = 1000
PER_USER = 50
SEED = 20261105

_rng = np.random.default_rng(SEED)
_C = _rng.standard_normal((CENTROIDS, DIMS)).astype(np.float32)
_C /= np.linalg.norm(_C, axis=1, keepdims=True)


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "little")


def _unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


def memory_vec(text: str) -> np.ndarray:
    r = np.random.default_rng(_seed(text))
    user = text.split(":")[1]
    # each user talks about a handful of topics
    topics = np.random.default_rng(_seed("topics:" + user)).integers(0, CENTROIDS, 5)
    c = _C[topics[r.integers(0, len(topics))]]
    return _unit(c + r.standard_normal(DIMS).astype(np.float32) / np.sqrt(DIMS))


def query_vec(text: str) -> np.ndarray:
    _, user, j = text.split(":")
    r = np.random.default_rng(_seed(text))
    target = memory_vec(f"m:{user}:{r.integers(0, PER_USER)}")
    return _unit(target + 0.5 * r.standard_normal(DIMS).astype(np.float32) / np.sqrt(DIMS))


def vec(text: str) -> np.ndarray:
    return query_vec(text) if text.startswith("q:") else memory_vec(text)


def embed(texts) -> list[list[float]]:
    """EmbeddingsFunc for LangGraph stores: Sequence[str] -> list[list[float]]."""
    return [vec(t).tolist() for t in texts]


async def aembed(texts) -> list[list[float]]:
    return embed(texts)


def user_id(u: int) -> str:
    return f"u{u:04d}"


def exact_top_k(user: str, query: str, k: int) -> list[str]:
    """Ground truth over the preloaded memories of one user (cosine)."""
    keys = [f"m:{user}:{i}" for i in range(PER_USER)]
    m = np.stack([memory_vec(t) for t in keys])
    scores = m @ query_vec(query)
    return [keys[i] for i in np.argsort(-scores)[:k]]

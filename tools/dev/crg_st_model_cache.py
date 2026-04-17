"""
Optional perf patch for code-review-graph (install into env site-packages).

Caches SentenceTransformer by model name so hybrid semantic search does not
reload weights on every MCP tool call. Explicit device=cuda when available.

Enable only when CRG_APPLY_ST_CACHE_PATCH=1 (set in .cursor/mcp.json for MCP).
"""

from __future__ import annotations

import os


def _apply() -> None:
    if os.environ.get("CRG_APPLY_ST_CACHE_PATCH", "").strip() != "1":
        return
    try:
        import code_review_graph.embeddings as emb
        import torch
    except Exception:
        return
    if getattr(emb.LocalEmbeddingProvider, "_crg_cache_patched", False):
        return

    _instances: dict[str, object] = {}

    def _get_model_cached(self):  # type: ignore[no-untyped-def]
        if self._model is not None:
            return self._model
        key = self._model_name
        if key not in _instances:
            from sentence_transformers import SentenceTransformer

            device = "cuda" if torch.cuda.is_available() else "cpu"
            _instances[key] = SentenceTransformer(
                self._model_name,
                device=device,
                trust_remote_code=True,
                model_kwargs={"trust_remote_code": True},
            )
        self._model = _instances[key]
        return self._model

    emb.LocalEmbeddingProvider._get_model = _get_model_cached  # type: ignore[method-assign]
    emb.LocalEmbeddingProvider._crg_cache_patched = True


_apply()

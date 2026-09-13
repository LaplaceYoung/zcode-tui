"""Translate zcode reasoning levels into request-body patches.

Source of truth: the ZCode app ships a models catalog where each reasoning
level is described as a list of `{path, value}` patches applied to the
request body per protocol kind. We read that catalog when present and fall
back to sensible built-in patches when it is not.
"""

from __future__ import annotations

import glob
import json
from functools import lru_cache

from .zconfig import ModelInfo


@lru_cache(maxsize=1)
def _catalog() -> dict | None:
    from .zconfig import CATALOG_GLOB

    paths = sorted(glob.glob(CATALOG_GLOB))
    if not paths:
        return None
    try:
        return json.loads(open(paths[-1]).read())
    except Exception:
        return None


def _norm(s: str) -> str:
    return s.lower().replace("builtin:", "").strip()


def _catalog_patches(provider_id: str, model: ModelInfo, level: str, kind: str) -> list[dict]:
    cat = _catalog()
    if not cat:
        return []
    pid = _norm(provider_id)
    model_keys = {_norm(model.id), _norm(model.api_id)}
    for p in cat.get("providers", []):
        tail = _norm(p.get("id", ""))
        # Match either exact id tail or a suffix (coding-plan providers share
        # the underlying vendor id, e.g. bigmodel-coding-plan -> bigmodel).
        if tail != pid and not (pid.startswith(tail) or tail.startswith(pid)):
            continue
        for m in p.get("models", []):
            ids = {_norm(m.get("id", "")), _norm(m.get("name", ""))}
            if not ids & model_keys:
                continue
            levels = (m.get("reasoning") or {}).get("levels", {})
            spec = levels.get(level) or {}
            per_kind = spec.get(kind) or spec.get("openai" if kind == "openai-compatible" else kind)
            if per_kind:
                return list(per_kind.get("set", []))
    return []


def _fallback_patches(level: str, kind: str) -> list[dict]:
    if level in ("off", "none"):
        return []
    if kind == "anthropic":
        return [{"path": ["output_config", "effort"], "value": level}]
    return [{"path": ["reasoning_effort"], "value": level}]


def apply_reasoning(body: dict, kind: str, level: str | None, model: ModelInfo) -> None:
    """Mutate a request body with the patches implied by `level`."""
    if not level or not model.reasoning_variants:
        return
    if level == "off":
        # Explicit off: only apply if the model declares an "off" variant.
        if "off" not in model.reasoning_variants:
            return
    elif level not in model.reasoning_variants:
        level = model.reasoning_default or model.reasoning_variants[0]
    patches = _catalog_patches(model.provider_id, model, level, kind) or _fallback_patches(level, kind)
    for patch in patches:
        path, value = patch.get("path", []), patch.get("value")
        if not path:
            continue
        node = body
        for key in path[:-1]:
            nxt = node.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                node[key] = nxt
            node = nxt
        node[path[-1]] = value

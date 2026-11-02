# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""Stories 知识库：DashScope embedding + 双层检索（角色库 + 公共库） + rerank"""
import json
import math
import os
import threading
import time as _time
import urllib.request

import db
import logger
import secrets_cfg

DATA_DIR = os.environ.get("STORIES_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
VECTOR_DIR = os.path.join(DATA_DIR, "vectors")

DASHSCOPE_KEY = secrets_cfg.get("DASHSCOPE_API_KEY")
# 2026-09-30 起迁移至业务空间专属域名（共享域名进入维护状态）
DASHSCOPE_HOST = os.environ.get("DASHSCOPE_HOST", "dashscope.aliyuncs.com")
EMBED_BASE = "https://" + DASHSCOPE_HOST + "/compatible-mode/v1"
EMBED_MODEL = "qwen3.7-text-embedding"
RERANK_MODEL = "qwen3-vl-rerank"
RERANK_URL = "https://" + DASHSCOPE_HOST + "/api/v1/services/rerank/text-rerank/text-rerank"

EMBED_BATCH = 20
CHAR_TOPN = 6
PUBLIC_TOPN = 2

_cache = {}  # scope_id -> (mtime, items)
_embed_fail_until = 0.0  # 查询向量失败的负缓存（避免每条消息白等超时）


def _log(msg: str, level: str = "INFO"):
    logger.log(msg, level=level)


def _scope_id(scope: str, character_id: int) -> str:
    return "public" if scope == "public" else f"char_{character_id}"


def _vector_path(scope: str, character_id: int) -> str:
    return os.path.join(VECTOR_DIR, f"kb_{_scope_id(scope, character_id)}.json")


def _load(scope: str, character_id: int):
    """加载向量文件（带 mtime+size 缓存；损坏条目自动丢弃）"""
    path = _vector_path(scope, character_id)
    key = _scope_id(scope, character_id)
    try:
        st = os.stat(path)
        stamp = (st.st_mtime, st.st_size)
    except OSError:
        return []
    cached = _cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        items = []
    if not isinstance(items, list):
        items = []
    items = [it for it in items if isinstance(it, dict) and it.get("content") and it.get("vector")]
    _cache[key] = (stamp, items)
    return items


def _embed(texts: list, timeout: int = 120):
    """批量 embedding（DashScope），返回向量列表或 None（timeout 可按场景收紧）"""
    if not texts:
        return []
    t0 = _time.time()
    _log(f"[knowledge] 调用 DashScope embedding ({EMBED_MODEL}, {len(texts)} 条)")
    payload = {"model": EMBED_MODEL, "input": texts}
    req = urllib.request.Request(
        EMBED_BASE + "/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + DASHSCOPE_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        _log(f"[knowledge] embedding 完成 ({( _time.time()-t0):.1f}s)")
        return [d["embedding"] for d in resp["data"]]
    except Exception as e:
        _log(f"[knowledge] embedding 失败: {e}")
        return None


def _rerank(query: str, documents: list, timeout: int = 60):
    """DashScope 原生 rerank（嵌套格式），失败返回 None。timeout 可按调用场景收紧。"""
    if not documents:
        return None
    t0 = _time.time()
    _log(f"[knowledge] 调用 DashScope rerank ({RERANK_MODEL}, {len(documents)} 文档)")
    payload = {
        "model": RERANK_MODEL,
        "input": {"query": query, "documents": documents},
        "parameters": {"top_n": len(documents)},
    }
    req = urllib.request.Request(
        RERANK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + DASHSCOPE_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        results = resp.get("output", {}).get("results", [])
        if not isinstance(results, list):
            return None
        results = [r for r in results if isinstance(r, dict)]
        results = sorted(results, key=lambda x: -float(x.get("relevance_score") or 0))
        _log(f"[knowledge] rerank 完成 ({( _time.time()-t0):.1f}s)")
        return results
    except Exception as e:
        _log(f"[knowledge] rerank 失败: {e}")
        return None


def _cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


_rebuild_locks = {}
_rebuild_locks_guard = threading.Lock()


def _scope_lock(scope: str, character_id: int):
    key = _scope_id(scope, character_id)
    with _rebuild_locks_guard:
        lock = _rebuild_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _rebuild_locks[key] = lock
        return lock


def rebuild_scope(scope: str, character_id: int = 0) -> int:
    """重建某范围（public / 某角色）的向量库，返回条目数。可重复调用（增量复用相同文本的向量）。
    同一范围串行执行：上传/删除并发时避免交错写坏向量文件。"""
    with _scope_lock(scope, character_id):
        chunks = db.list_kb_chunks(scope, character_id)
        old = _load(scope, character_id)
        old_map = {item["content"]: item.get("vector") for item in old}

        items = []
        todo = []  # (idx, content)
        for c in chunks:
            items.append({"doc_id": c["doc_id"], "heading": c.get("heading", ""), "content": c["content"], "vector": None})
            if old_map.get(c["content"]):
                items[-1]["vector"] = old_map[c["content"]]
            else:
                todo.append((len(items) - 1, c["content"]))

        for i in range(0, len(todo), EMBED_BATCH):
            batch = todo[i:i + EMBED_BATCH]
            vecs = None
            for attempt in range(3):
                vecs = _embed([b[1] for b in batch])
                if vecs is not None:
                    break
                _time.sleep(1.5 * (attempt + 1))
            if vecs is None:
                # 该批重试仍失败：跳过继续后续批次，最后按实际条数返回（调用方会提示部分失败）
                _log(f"[knowledge] embedding 批次失败（{len(batch)} 条，已跳过）", level="WARNING")
                continue
            for (idx, _), vec in zip(batch, vecs):
                items[idx]["vector"] = vec

        items = [it for it in items if it.get("vector")]
        os.makedirs(VECTOR_DIR, exist_ok=True)
        path = _vector_path(scope, character_id)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            # 失败时清理临时文件，避免残留
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise
        _log(f"[knowledge] 向量库重建完成 {path}: {len(items)} 条")
        return len(items)


def _cap_public(items: list, top_k: int, cap: int) -> list:
    """限制结果中公共库条数（kb_public_max，默认 1）：角色库优先占位，公共库超出的丢弃"""
    if cap < 0:
        return items[:top_k]
    out = []
    pub = 0
    for it in items:
        if it.get("scope") == "public":
            if pub >= cap:
                continue
            pub += 1
        out.append(it)
        if len(out) >= top_k:
            break
    return out


def retrieve(query: str, character_id: int, top_k: int = 3) -> list:
    """双层检索：角色库 Top6 + 公共库 Top2 → rerank → Top3（结果中公共库最多 kb_public_max 条，默认 1）。失败返回空列表"""
    global _embed_fail_until
    try:
        char_items = _load("character", character_id)
        pub_items = _load("public", 0)
        if not char_items and not pub_items:
            return []
        if _time.time() < _embed_fail_until:
            return []  # 刚失败过：短暂冷却，避免每条消息都白等
        q_vec = _embed([query], timeout=12)
        if not q_vec:
            _embed_fail_until = _time.time() + 60
            return []
        q_vec = q_vec[0]

        def topn(items, n):
            scored = [(it, _cosine(q_vec, it["vector"])) for it in items if it.get("vector")]
            scored.sort(key=lambda x: -x[1])
            return [it for it, _ in scored[:n]]

        candidates = []
        for it in topn(char_items, CHAR_TOPN):
            candidates.append({**it, "scope": "character"})
        for it in topn(pub_items, PUBLIC_TOPN):
            candidates.append({**it, "scope": "public"})

        # 相关性下限：避免"今天好累"也能命中《提醒总纲》这类低相关文档
        try:
            _st = db.get_settings()
            thr = float(_st.get("kb_min_score", 0.30) or 0.30)
            pub_max = int(float(_st.get("kb_public_max", 1)))
        except Exception:
            thr = 0.30
            pub_max = 1
        if pub_max < 0:
            pub_max = 0
        if thr > -1:
            candidates = [c for c in candidates if c.get("vector") and _cosine(q_vec, c["vector"]) >= thr]
        if not candidates:
            return []

        docs = [c["content"] for c in candidates]
        ranked = _rerank(query, docs)
        if ranked:
            out = []
            for r in ranked[:top_k]:
                try:
                    idx = int(r.get("index", -1))
                except Exception:
                    continue
                if 0 <= idx < len(candidates):
                    out.append(candidates[idx])
            if out:
                return _cap_public(out, top_k, pub_max)
        # rerank 失败/异常：退回余弦排序（角色库+公共库统一排序，结果中公共库不超过 pub_max 条）
        scored = [(c, _cosine(q_vec, c["vector"])) for c in candidates if c.get("vector")]
        scored.sort(key=lambda x: -x[1])
        return _cap_public([c for c, _ in scored], top_k, pub_max)
    except Exception as e:
        _log(f"[knowledge] 检索异常: {e}")
        return []

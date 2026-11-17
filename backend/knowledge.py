# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""Stories 知识库：DashScope embedding + 双层检索（角色库 + 公共库） + rerank

检索链路（任一环节失败都不丢结果）：
1) 向量：DashScope embedding → numpy 批量余弦（一次性算完，供排序/阈值/回退复用）
2) 重排：DashScope rerank（失败退回向量分）
3) 兜底：embedding 不可用时走关键词（bigram 覆盖率），与 memory_kb 共用同一套降级语义
"""
import json
import math
import os
import re
import threading
import time as _time
import urllib.request

import db
import logger
import secrets_cfg

try:
    import numpy as _np
except Exception:  # 无 numpy 时退化为纯 Python 路径（慢但可用）
    _np = None

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
KW_THRESHOLD = 0.5      # 纯关键词兜底时的命中阈值（查询覆盖率）

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
    payload = {"model": EMBED_MODEL, "input": texts}
    req = urllib.request.Request(
        EMBED_BASE + "/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + DASHSCOPE_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return [d["embedding"] for d in resp["data"]]
    except Exception as e:
        _log(f"[knowledge] embedding 失败: {e}", level="WARNING")
        return None


def embed_query(text: str, timeout: float = 12.0):
    """查询向量（带失败冷却）：冷却中/失败返回 None，调用方据此立刻降级，不再重复等待。"""
    global _embed_fail_until
    if _time.time() < _embed_fail_until:
        return None
    v = _embed([text], timeout=max(1.0, float(timeout)))
    if not v:
        _embed_fail_until = _time.time() + 60
        return None
    return v[0]


def embed_queries(texts: list, timeout: float = 12.0):
    """多查询批量向量（同样带失败冷却）。"""
    global _embed_fail_until
    if not texts:
        return None
    if _time.time() < _embed_fail_until:
        return None
    v = _embed(list(texts), timeout=max(1.0, float(timeout)))
    if not v:
        _embed_fail_until = _time.time() + 60
        return None
    return v


def _rerank(query: str, documents: list, timeout: int = 60):
    """DashScope 原生 rerank（嵌套格式），失败返回 None。timeout 可按调用场景收紧。"""
    if not documents:
        return None
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
        return results
    except Exception as e:
        _log(f"[knowledge] rerank 失败: {e}", level="WARNING")
        return None


# ============ 打分：numpy 批量 + 关键词兜底 ============

def _cosine(a, b) -> float:
    """单条余弦（numpy 优先）"""
    if a is None or b is None or len(a) != len(b):
        return 0.0
    if _np is not None:
        try:
            va = _np.asarray(a, dtype=_np.float32)
            vb = _np.asarray(b, dtype=_np.float32)
            na = float(_np.linalg.norm(va))
            nb = float(_np.linalg.norm(vb))
            if not na or not nb:
                return 0.0
            return float(va @ vb) / (na * nb)
        except Exception:
            pass
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def score_all(q_vec, items: list) -> list:
    """一次性算完所有条目余弦，返回 [(item, score)]。
    调用方在阈值过滤、排序、rerank 回退里复用它，避免同一批向量算两三遍。"""
    if not items:
        return []
    if q_vec is None:
        return []
    if _np is not None:
        try:
            M = _np.asarray([it.get("vector") for it in items], dtype=_np.float32)
            q = _np.asarray(q_vec, dtype=_np.float32)
            if M.ndim == 2 and M.shape[1] == q.shape[0]:
                qn = float(_np.linalg.norm(q)) or 1.0
                n = _np.linalg.norm(M, axis=1)
                n[n == 0] = 1.0
                s = (M @ q) / (n * qn)
                return list(zip(items, [float(x) for x in s]))
        except Exception:
            pass
    return [(it, _cosine(q_vec, it.get("vector"))) for it in items]


def grams(text: str) -> set:
    """中文按二元组切分（与 memory_kb 一致）"""
    t = re.sub(r"\s+", "", str(text or ""))
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


_TERM_SPLIT = re.compile(r"[\s,，。.!！?？、;；:：/\\|]+")


def term_grams(text: str) -> list:
    """把查询拆词后各自切 bigram（多词查询可区分"命中一个词"和"命中全部词"）。"""
    terms = [x for x in _TERM_SPLIT.split(str(text or "")) if x]
    if not terms:
        terms = [str(text or "")]
    out = []
    for x in terms:
        g = grams(x)
        if g:
            out.append(g)
    return out


def keyword_score(query_terms: list, item_grams: set) -> float:
    """关键词按词平均覆盖率（0~1），不因记忆变长而被稀释。

    query_terms 可以是 [[set,set], ...]（多查询、每查询多词），也兼容旧的 [set,...]。
    """
    if not query_terms or not item_grams:
        return 0.0
    best = 0.0
    for terms in query_terms:
        if isinstance(terms, set):
            terms = [terms]
        if not terms:
            continue
        total = 0.0
        for tg in terms:
            inter = len(tg & item_grams)
            if inter:
                total += inter / float(len(tg))
        avg = total / float(len(terms))
        if avg > best:
            best = avg
    return best


def _score_items(q_vec, items: list, q_grams: list = None, kw_scale: float = 0.55) -> list:
    """向量分（有则用）+ 关键词分（并行加分；纯关键词模式下 kw_scale=1.0 独立支撑），返回 [(item, score)]"""
    if q_vec is not None:
        scored = score_all(q_vec, items)
    else:
        scored = [(it, 0.0) for it in items]
    # 关键词只在"向量不可用"时计算：向量正常时它的边际收益低，
    # 而大库逐条算 bigram 的代价高（900 条 × 上千字）。
    if q_grams and q_vec is None:
        out = []
        for it, s in scored:
            kw = keyword_score(q_grams, grams(it.get("content")))
            if kw > s:
                s = kw * kw_scale
            out.append((it, s))
        return out
    return scored


# ============ 重建 ============

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
    同一范围串行执行：上传/删除并发时避免交错写坏向量文件。
    失败批次会追加两轮重试；仍失败的条目会记录数量（不会静默丢弃整批）。"""
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

        failed = []
        for i in range(0, len(todo), EMBED_BATCH):
            batch = todo[i:i + EMBED_BATCH]
            vecs = None
            for attempt in range(3):
                vecs = _embed([b[1] for b in batch])
                if vecs is not None:
                    break
                _time.sleep(1.5 * (attempt + 1))
            if vecs is None:
                failed.extend(batch)
                continue
            for (idx, _), vec in zip(batch, vecs):
                items[idx]["vector"] = vec

        # 失败批次：再做两轮补采，避免"服务刚好抖一下"就永久缺条目
        for attempt in range(2):
            if not failed:
                break
            _time.sleep(2.0 * (attempt + 1))
            still = []
            for i in range(0, len(failed), EMBED_BATCH):
                batch = failed[i:i + EMBED_BATCH]
                vecs = _embed([b[1] for b in batch])
                if vecs is None:
                    still.extend(batch)
                    continue
                for (idx, _), vec in zip(batch, vecs):
                    items[idx]["vector"] = vec
            failed = still

        kept = [it for it in items if it.get("vector")]
        if failed:
            _log(f"[knowledge] embedding 仍有 {len(failed)} 条缺失（{scope}/{character_id}），"
                 f"已保留其余 {len(kept)} 条；下次重建会自动补齐", level="WARNING")
        os.makedirs(VECTOR_DIR, exist_ok=True)
        path = _vector_path(scope, character_id)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(kept, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            # 失败时清理临时文件，避免残留
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise
        _cache.pop(_scope_id(scope, character_id), None)
        return len(kept)


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
    """双层检索：角色库 Top6 + 公共库 Top2 → rerank → Top3。
    embedding 不可用时自动降级为关键词检索（不再直接返回空）。"""
    try:
        char_items = _load("character", character_id)
        pub_items = _load("public", 0)
        if not char_items and not pub_items:
            return []
        try:
            _st = db.get_settings()
            thr = float(_st.get("kb_min_score", 0.30) or 0.30)
            pub_max = int(float(_st.get("kb_public_max", 1)))
        except Exception:
            thr = 0.30
            pub_max = 1
        if pub_max < 0:
            pub_max = 0

        q_vec = embed_query(query, timeout=12)
        q_grams = [term_grams(query)]
        kw_mode = q_vec is None
        used_thr = KW_THRESHOLD if kw_mode else thr

        kw_scale = 1.0 if kw_mode else 0.55
        char_scored = _score_items(q_vec, char_items, q_grams, kw_scale)
        pub_scored = _score_items(q_vec, pub_items, q_grams, kw_scale)
        char_scored.sort(key=lambda x: -x[1])
        pub_scored.sort(key=lambda x: -x[1])

        candidates = []
        for it, s in char_scored[:CHAR_TOPN]:
            if s >= used_thr:
                candidates.append({**it, "scope": "character", "_score": s})
        for it, s in pub_scored[:PUBLIC_TOPN]:
            if s >= used_thr:
                candidates.append({**it, "scope": "public", "_score": s})
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
        # rerank 失败/异常：用已经算好的分数排序（不再重算余弦）
        candidates.sort(key=lambda x: -x["_score"])
        return _cap_public(candidates, top_k, pub_max)
    except Exception as e:
        _log(f"[knowledge] 检索异常: {e}", level="WARNING")
        return []

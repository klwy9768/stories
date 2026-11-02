# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""记忆库：把聊天记录自动索引为「可检索记忆」，并在回复前检索注入。

与 knowledge.py 的分工：
- knowledge.py  用户上传的知识库（角色库/公共库），保持独立
- memory_kb.py  聊天记录记忆：入库管道 + 混合检索 + 群隔离 + 时间衰减 + 遗忘 + 主动回忆选材

设计要点（与使用方约定）：
1) 高召回入库：只有判定模型"高置信无长期价值"的消息才丢弃，判定失败一律保留（失败安全）。
2) 检索是「叠加」不是「替换」：调用方仍保留完整的近期上下文，本模块只补充相关往事。
3) 群聊记忆按群隔离，只有该群成员角色才能检索到，且注入时标注来源，避免串味。
4) 向量以 base64(float32) 存储（体积约为 JSON 数字数组的 1/25），numpy 加速余弦。
"""
import base64
import json
import os
import re
import threading
import time as _time

import db
import logger

try:
    import numpy as _np
except Exception:  # 无 numpy 时退化为纯 Python 路径（慢但可用）
    _np = None

DATA_DIR = os.environ.get("STORIES_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
MEM_DIR = os.path.join(DATA_DIR, "vectors")

_lock = threading.Lock()
_cache = {}          # scope -> {"stamp":..., "items": [...], "mat": ndarray|None, "grams": [set,...]}
_dirty = {}          # scope -> bool（召回统计有更新待落盘）
_flush_timer = {}

MAX_ITEMS_PER_SCOPE = 20000   # 单范围上限（防膨胀；超出按"最旧且召回最少"淘汰）
EMBED_BATCH = 20


def _log(msg: str, level: str = "INFO"):
    logger.log(msg, level=level)


# ============ 配置 ============

def _cfg() -> dict:
    try:
        s = db.get_settings()
    except Exception:
        s = {}

    def _f(k, d, lo, hi):
        try:
            return min(max(float(s.get(k, d)), lo), hi)
        except Exception:
            return d

    def _i(k, d, lo, hi):
        try:
            return min(max(int(float(s.get(k, d))), lo), hi)
        except Exception:
            return d

    def _b(k, d):
        v = s.get(k, d)
        return str(v).strip().lower() not in ("0", "false", "no", "off", "")

    return {
        "enabled": _b("memory_enabled", 1),
        # 阈值按决策模型实测标尺标定：无用消息 p≈0.15~0.58，有用消息 p≤0.07。
        # 取 0.55 / 0.45 时，只有"最像纯寒暄无信息"的才会被丢，且对有用消息保留 ~7 倍安全边际。
        # （实测：0.9 在实际标尺上不可达 = 永不丢弃；可在管理页按需调低）
        "judge_p_single": _f("memory_judge_p_single", 0.55, 0.30, 0.999),
        "judge_p_group": _f("memory_judge_p_group", 0.45, 0.30, 0.999),
        "daily_max": _i("memory_daily_max", 500, 0, 5000),
        "topn": _i("memory_topn", 3, 0, 10),
        "group_topn": _i("memory_group_topn", 1, 0, 5),
        "group_topn_relax": _i("memory_group_relax_topn", 2, 0, 5),
        "threshold": _f("memory_threshold", 0.30, -1.0, 1.0),
        "group_threshold": _f("memory_group_threshold", 0.45, -1.0, 1.0),
        "decay_days": _f("memory_decay_days", 30.0, 1.0, 3650.0),
        "query_expand": _b("memory_query_expand", 0),
        "rerank_min": _i("memory_rerank_min", 7, 0, 50),
        "rerank_timeout": _i("memory_rerank_timeout", 4, 1, 60),
        "proactive_enabled": _b("memory_proactive_enabled", 1),
        "proactive_daily": _i("memory_proactive_daily", 1, 0, 5),
        "proactive_cool_days": _i("memory_proactive_cool_days", 7, 1, 365),
        "forget_enabled": _b("memory_forget_enabled", 1),
        "forget_days": _i("memory_forget_days", 180, 7, 3650),
        "timeout_ms": _i("memory_timeout_ms", 3000, 300, 15000),
    }


# ============ 向量编解码（base64 float32，兼容旧的 JSON 数组）============

def _vec_pack(vec) -> str:
    try:
        if _np is not None:
            arr = _np.asarray(vec, dtype=_np.float32)
            return base64.b64encode(arr.tobytes()).decode("ascii")
        import array
        a = array.array("f", [float(x) for x in vec])
        return base64.b64encode(a.tobytes()).decode("ascii")
    except Exception:
        return ""


def _vec_unpack(s: str):
    try:
        raw = base64.b64decode(str(s or ""))
        if not raw:
            return None
        if _np is not None:
            return _np.frombuffer(raw, dtype=_np.float32)
        import array
        a = array.array("f")
        a.frombytes(raw)
        return list(a)
    except Exception:
        return None


def _item_vec(it: dict):
    """取条目向量：优先 vec_b64，兼容旧的 vector（JSON 数组）"""
    v = it.get("vec_b64")
    if v:
        return _vec_unpack(v)
    raw = it.get("vector")
    if isinstance(raw, list) and raw:
        if _np is not None:
            try:
                return _np.asarray(raw, dtype=_np.float32)
            except Exception:
                return None
        return raw
    return None


# ============ 存储 ============

def scope_of_char(cid: int) -> str:
    return "char_%d" % int(cid)


def scope_of_group(gid: int) -> str:
    return "group_%d" % int(gid)


def _path(scope: str) -> str:
    return os.path.join(MEM_DIR, "mem_%s.json" % re.sub(r"[^A-Za-z0-9_]", "", scope))


def _bigrams(text: str) -> set:
    t = re.sub(r"\s+", "", str(text or ""))
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _build_entry(scope: str, items: list) -> dict:
    """构造缓存条目：条目列表 + 归一化矩阵 + bigram 索引"""
    mat = None
    if _np is not None and items:
        rows = []
        keep = []
        for it in items:
            v = _item_vec(it)
            if v is None or len(v) == 0:
                continue
            rows.append(v)
            keep.append(it)
        if rows:
            try:
                M = _np.vstack([_np.asarray(r, dtype=_np.float32) for r in rows])
                n = _np.linalg.norm(M, axis=1, keepdims=True)
                n[n == 0] = 1.0
                mat = M / n
                items = keep
            except Exception:
                mat = None
    return {
        "stamp": None,
        "items": items,
        "mat": mat,
        "grams": [_bigrams(it.get("content")) for it in items],
    }


def _load(scope: str) -> dict:
    """加载某范围记忆（带 mtime+size 缓存；损坏条目自动丢弃）"""
    path = _path(scope)
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        ent = _build_entry(scope, [])
        with _lock:
            _cache[scope] = ent
        return ent
    with _lock:
        c = _cache.get(scope)
        if c and c.get("stamp") == stamp:
            return c
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = []
    if not isinstance(raw, list):
        raw = []
    items = [it for it in raw if isinstance(it, dict) and it.get("content")]
    ent = _build_entry(scope, items)
    ent["stamp"] = stamp
    with _lock:
        _cache[scope] = ent
    return ent


def _save(scope: str, items: list):
    """原子落盘（tmp → rename），并失效缓存"""
    try:
        os.makedirs(MEM_DIR, exist_ok=True)
        path = _path(scope)
        tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, path)
        with _lock:
            _cache.pop(scope, None)
        return True
    except Exception as e:
        _log("[memory] 落盘失败 %s: %s" % (scope, e), level="WARNING")
        return False


def _schedule_flush(scope: str, delay: float = 3.0):
    """召回统计更新后延迟落盘（合并多次写，避免频繁重写整文件）"""
    with _lock:
        _dirty[scope] = True
        t = _flush_timer.get(scope)
        if t:
            t.cancel()
        timer = threading.Timer(delay, lambda: flush_scope(scope))
        timer.daemon = True
        _flush_timer[scope] = timer
        timer.start()


def flush_scope(scope: str):
    """把内存里的召回统计写回磁盘（仅在确有待写入时）"""
    with _lock:
        if not _dirty.get(scope):
            return
        ent = _cache.get(scope)
        if not ent:
            _dirty[scope] = False
            return
        items = list(ent["items"])
        _dirty[scope] = False
    _save(scope, items)


# ============ 入库管道 ============

_SKIP_EXACT = re.compile(
    r"^[\s，。！？~～、,.!?…:：;；（）()\[\]【】\-—_]*"
    r"(在吗|在么|在不在|嗯+|哦+|喔+|啊+|哈+|呵+|嘿+|喂+|好的?|行|可以|收到|没事|哈哈+|呵呵+|"
    r"笑死|草|6+|哇+|呀+|吧|呢|那个|这个|emmm*|ok|OK|嗯嗯+|哈哈哈+|谢谢|晚安|早安|中午好|晚上好)"
    r"+[\s，。！？~～、,.!?…:：;；（）()\[\]【】\-—_]*$"
)
_SKIP_KINDS = ("photo", "video", "sticker", "voice")


def worth_indexing(m: dict) -> bool:
    """预过滤：不值得索引的消息直接跳过（不消耗判定调用）"""
    try:
        kind = str(m.get("kind") or "text")
        if kind in _SKIP_KINDS:
            return False
        text = str(m.get("content") or "").strip()
        if len(text) < 4:
            return False
        if _SKIP_EXACT.match(text):
            return False
        # 纯符号/纯数字
        if re.sub(r"[\W_]+", "", text, flags=re.UNICODE) == "":
            return False
        return True
    except Exception:
        return False


def judge_drop(text: str, is_group: bool, cfg: dict) -> bool:
    """判定模型：是否"高置信完全无长期价值"。失败/异常一律返回 False（保留）。"""
    try:
        import ai  # 延迟导入，避免模块循环依赖
        p = ai.decision_probs(
            ("下面这条是群聊里的一条消息：\n" if is_group else "下面这条是私聊里的一条消息：\n") + text[:300],
            {"drop": "这条消息是否【完全没有任何】值得长期记住的信息？"
                     "（纯寒暄、语气词、表情、无关紧要的闲聊 = 是；"
                     "包含事实、约定、偏好、经历、情绪事件、人物关系 = 否）"},
            timeout=6,
        ).get("drop")
        if p is None:
            return False  # 判定不可用 → 保留（失败安全）
        thr = cfg["judge_p_group"] if is_group else cfg["judge_p_single"]
        return float(p) >= thr
    except Exception:
        return False


def _items_of_scope(scope: str) -> list:
    return list(_load(scope)["items"])


def index_scope(scope: str, cid: int = 0, gid: int = 0, is_group: bool = False,
                max_batch: int = 200, respect_daily: bool = True) -> dict:
    """增量索引：处理水位之后的新消息。

    respect_daily=True 时受 memory_daily_max 限制（日常 tick 用）；
    全量回填传 False（否则历史消息会被每日上限卡住）。
    返回 {"scanned":n, "added":n, "dropped":n, "skipped":n, "last_id":n, "capped":bool}
    """
    cfg = _cfg()
    stat = {"scanned": 0, "added": 0, "dropped": 0, "skipped": 0, "last_id": 0, "capped": False}
    if not cfg["enabled"]:
        return stat
    day_key = "mem_index_day_%s" % re.sub(r"[^A-Za-z0-9_]", "", scope)
    used_today = 0
    if respect_daily:
        try:
            raw = str(db.get_settings().get(day_key) or "")
            d, _, c = raw.partition(":")
            if d == _time.strftime("%Y%m%d", _time.localtime()):
                used_today = int(float(c or 0))
            if used_today >= cfg["daily_max"]:
                stat["capped"] = True
                return stat
            max_batch = min(int(max_batch), max(1, cfg["daily_max"] - used_today))
        except Exception:
            pass
    conn = db.get_conn()
    last = db.mem_index_get(scope)
    if is_group:
        rows = conn.execute(
            "SELECT id, role, kind, content, created_at, cid FROM group_messages "
            "WHERE group_id=? AND id>? AND content<>'' ORDER BY id LIMIT ?",
            (int(gid), int(last), int(max_batch))).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, role, kind, content, created_at, 0 AS cid FROM messages "
            "WHERE character_id=? AND id>? AND content<>'' ORDER BY id LIMIT ?",
            (int(cid), int(last), int(max_batch))).fetchall()
    if not rows:
        return stat
    items = _items_of_scope(scope)
    seen_mid = {int(it.get("mid") or 0) for it in items}
    new_items = []
    prev_text = ""
    for r in rows:
        m = dict(r)
        mid = int(m["id"])
        stat["scanned"] += 1
        stat["last_id"] = mid
        text = str(m.get("content") or "").strip()
        if mid in seen_mid:
            continue
        if not worth_indexing(m):
            stat["skipped"] += 1
            prev_text = text
            continue
        # 高置信无用 → 丢弃；判定失败保留
        if judge_drop(text, is_group, cfg):
            stat["dropped"] += 1
            prev_text = text
            continue
        new_items.append({
            "mid": mid,
            "cid": int(cid or 0),
            "gid": int(gid or 0),
            "ts": float(m.get("created_at") or 0),
            "role": str(m.get("role") or ""),
            "content": text[:1200],
            "ctx": prev_text[:60],
            "pin": 0,
            "recall_n": 0,
            "recall_ts": 0.0,
            "dormant": 0,
        })
        prev_text = text
    if not new_items:
        if stat["last_id"]:
            db.mem_index_set(scope, stat["last_id"])
        return stat

    vecs = None
    try:
        import knowledge
        for i in range(0, len(new_items), EMBED_BATCH):
            batch = new_items[i:i + EMBED_BATCH]
            got = None
            for attempt in range(3):
                got = knowledge._embed([it["content"] for it in batch])
                if got is not None:
                    break
                _time.sleep(1.2 * (attempt + 1))
            if not got:
                _log("[memory] 嵌入批次失败（%d 条），本轮跳过，下轮补" % len(batch), level="WARNING")
                continue
            for it, v in zip(batch, got):
                it["vec_b64"] = _vec_pack(v)
            if vecs is None:
                vecs = True
    except Exception as e:
        _log("[memory] 嵌入异常: %s" % e, level="WARNING")

    merged = [it for it in items if it.get("vec_b64") or it.get("vector")] + \
             [it for it in new_items if it.get("vec_b64")]
    merged.sort(key=lambda x: (float(x.get("ts") or 0), int(x.get("mid") or 0)))
    if len(merged) > MAX_ITEMS_PER_SCOPE:
        merged = merged[-MAX_ITEMS_PER_SCOPE:]
    if _save(scope, merged):
        stat["added"] = sum(1 for it in new_items if it.get("vec_b64"))
        db.mem_index_set(scope, stat["last_id"])
        if respect_daily and stat["added"]:
            try:
                db.set_settings({day_key: "%s:%d" % (
                    _time.strftime("%Y%m%d", _time.localtime()), used_today + stat["added"])})
            except Exception:
                pass
    return stat


# ============ 混合检索 ============

_GROUP_HINT = ("群里", "群聊", "大家都在", "大家说", "你们说", "他们", "群友", "家族群", "同学群",
               "昨天群里", "群里说", "群里聊", "去群里")


def _group_hint(text: str) -> bool:
    t = str(text or "")
    return any(k in t for k in _GROUP_HINT)


def _expand_queries(text: str, recent: list, cfg: dict) -> list:
    """多查询：默认用"当前消息 + 最近两条"本地构造（0 延迟、0 成本）。

    可选开启 flash 扩展（memory_query_expand=1）：能提高召回，但每次要多花 ~1.5s，
    对"逐条回复"的实时链路代价太大，因此默认关闭。
    """
    text = str(text or "").strip()
    local = [text]
    for x in reversed(list(recent or [])[-2:]):
        s = str(x or "").strip()
        if s and s != text and s not in local:
            local.append(s)
    local = [q[:80] for q in local if q][:3]
    if not cfg["query_expand"]:
        return local or [text or ""]
    try:
        import ai
        ctx = "\n".join(str(x)[:120] for x in (recent or [])[-3:])
        prompt = (
            "下面是一段聊天。请生成 2~3 条用于【检索历史记忆】的查询语句，"
            "要求：覆盖当前最可能想回忆的事实/人名/地点/事件；每条不超过 20 字；"
            "只输出 JSON 数组，例如 [\"咖啡店\",\"他说过的周末安排\"]。\n\n"
            "最近对话：\n" + (ctx or "（无）") + "\n\n当前消息：" + str(text or "")[:300]
        )
        content, err = ai._call_model([{"role": "user", "content": prompt}],
                                      model=ai.MODEL_FLASH, temperature=0.2, max_tokens=220,
                                      timeout=8)
        if not err and content:
            s = content.strip()
            i, j = s.find("["), s.rfind("]")
            if i >= 0 and j > i:
                arr = json.loads(s[i:j + 1])
                qs = [str(x).strip()[:40] for x in arr if str(x).strip()]
                if qs:
                    merged = local + [q for q in qs if q not in local]
                    return merged[:4]
    except Exception:
        pass
    return local or [text or ""]


def _decay(ts: float, half_life_days: float) -> float:
    try:
        age_days = max(0.0, (_time.time() - float(ts or 0)) / 86400.0)
        return 0.5 ** (age_days / max(1.0, half_life_days))
    except Exception:
        return 1.0


def _score_scope(ent: dict, q_vecs: list, q_grams: list, cfg: dict) -> list:
    """对单个范围打分：向量余弦（numpy 优先）× 时间衰减，并与关键词分数取较大者"""
    out = []
    items = ent["items"]
    if not items:
        return out
    mat = ent["mat"]
    scores = [0.0] * len(items)
    if mat is not None and _np is not None and q_vecs:
        qn = []
        for qv in q_vecs:
            try:
                a = _np.asarray(qv, dtype=_np.float32)
                n = float(_np.linalg.norm(a)) or 1.0
                qn.append(a / n)
            except Exception:
                pass
        for a in qn:
            try:
                s = mat @ a
                for i in range(len(scores)):
                    if s[i] > scores[i]:
                        scores[i] = float(s[i])
            except Exception:
                pass
    else:
        # 纯 Python 回退
        for i, it in enumerate(items):
            v = _item_vec(it)
            if v is None:
                continue
            best = 0.0
            for qv in q_vecs:
                num = sum(x * y for x, y in zip(v, qv))
                na = sum(x * x for x in v) ** 0.5
                nb = sum(x * x for x in qv) ** 0.5
                if na and nb:
                    best = max(best, num / (na * nb))
            scores[i] = best
    # 关键词（bigram）分数：专名/人名的兜底
    grams = ent["grams"]
    for i in range(len(items)):
        kw = 0.0
        g = grams[i] if i < len(grams) else set()
        if g:
            for qg in q_grams:
                if not qg:
                    continue
                inter = len(qg & g)
                if inter:
                    kw = max(kw, inter / ((len(qg) ** 0.5) * (len(g) ** 0.5) + 1e-9))
        if kw > scores[i]:
            scores[i] = kw
    for i, it in enumerate(items):
        if int(it.get("dormant") or 0):
            continue
        d = _decay(it.get("ts") or 0, cfg["decay_days"])
        eff = scores[i] * (0.7 + 0.3 * d)
        if eff <= 0:
            continue
        out.append({"item": it, "score": eff, "decay": d})
    out.sort(key=lambda x: -x["score"])
    return out


def retrieve(cid: int, query_text: str, recent_texts: list = None,
             gids: list = None, same_group_only: bool = True) -> dict:
    """检索相关往事。

    返回 {"items":[{...}], "latency_ms":n, "queries":[...], "ok":bool}
    - items 已按"私聊/知识库 Top3 + 群聊 ≤1（群语境时 ≤2）"配额裁剪，并带 source 标注
    - 任何环节失败都返回 ok=False（调用方据此保持原有上下文行为）
    """
    t0 = _time.time()
    cfg = _cfg()
    out = {"items": [], "latency_ms": 0, "queries": [], "ok": False}
    if not cfg["enabled"]:
        return out
    text = str(query_text or "").strip()
    if len(text) < 2:
        return out
    try:
        queries = _expand_queries(text, recent_texts or [], cfg)
        out["queries"] = queries
        try:
            import knowledge
            q_vecs = knowledge._embed(queries, timeout=12)
        except Exception:
            q_vecs = None
        if not q_vecs:
            out["latency_ms"] = int((_time.time() - t0) * 1000)
            return out
        q_grams = [_bigrams(q) for q in queries]

        char_ent = _load(scope_of_char(cid))
        cands = _score_scope(char_ent, q_vecs, q_grams, cfg)
        cands = [c for c in cands if c["score"] >= cfg["threshold"]][:12]
        for c in cands:
            c["source"] = "character"

        group_cands = []
        if same_group_only:
            for gid in (gids or [])[:5]:
                ge = _load(scope_of_group(gid))
                gs = _score_scope(ge, q_vecs, q_grams, cfg)
                gs = [c for c in gs if c["score"] >= cfg["group_threshold"]][:4]
                for c in gs:
                    c["source"] = "group"
                    c["gid"] = int(gid)
                group_cands.extend(gs)
        group_cands.sort(key=lambda x: -x["score"])

        pool = cands + group_cands[:4]
        if not pool:
            out["latency_ms"] = int((_time.time() - t0) * 1000)
            out["ok"] = True
            db.recall_log_add(cid, text, [], out["latency_ms"])
            return out

        # 重排（候选太少时跳过：本地余弦已够准，省一次网络往返）
        # 同时受"整体时间预算"约束：剩余时间不足 1.2s 就跳过重排，保证不拖慢整条回复
        deadline = t0 + (cfg["timeout_ms"] / 1000.0)
        docs = [c["item"].get("content") or "" for c in pool]
        ranked = None
        if len(pool) >= int(cfg["rerank_min"]) and (_time.time() + 1.2) < deadline:
            try:
                import knowledge
                ranked = knowledge._rerank(text, docs, timeout=cfg["rerank_timeout"])
            except Exception:
                ranked = None
        if ranked:
            for r in ranked:
                try:
                    idx = int(r.get("index", -1))
                    if 0 <= idx < len(pool):
                        pool[idx]["rr"] = float(r.get("relevance_score") or 0)
                except Exception:
                    continue
        for c in pool:
            base = c.get("rr")
            c["final"] = (base if base is not None else c["score"]) * (0.7 + 0.3 * c["decay"])

        pool.sort(key=lambda x: -x["final"])
        pick, group_used = [], 0
        relax = _group_hint(text) or _group_hint(" ".join(str(x) for x in (recent_texts or [])[-6:]))
        g_cap = cfg["group_topn_relax"] if relax else cfg["group_topn"]
        for c in pool:
            if len(pick) >= cfg["topn"] + g_cap:
                break
            if c["source"] == "group":
                if group_used >= g_cap:
                    continue
                group_used += 1
            else:
                if len([p for p in pick if p["source"] != "group"]) >= cfg["topn"]:
                    continue
            pick.append(c)

        # 冲突消解：同主题（bigram 重叠）只保留最新，并标注
        picked_items = []
        used_grams = []
        for c in pick:
            it = dict(c["item"])
            g = _bigrams(it.get("content"))
            dup_idx = -1
            for i, ug in enumerate(used_grams):
                if not g or not ug:
                    continue
                inter = len(g & ug)
                if inter / (min(len(g), len(ug)) + 1e-9) >= 0.5:
                    dup_idx = i
                    break
            if dup_idx >= 0:
                old = picked_items[dup_idx]
                if float(it.get("ts") or 0) > float(old.get("ts") or 0):
                    it["updated"] = 1
                    picked_items[dup_idx] = it
                    used_grams[dup_idx] = g
                continue
            picked_items.append(it)
            used_grams.append(g)

        for it in picked_items:
            it["source"] = "group" if int(it.get("gid") or 0) else "character"
            it["date"] = _time.strftime("%m-%d", _time.localtime(float(it.get("ts") or 0)))
            # 召回统计（延迟落盘）
            try:
                for real in _load(scope_of_group(it["gid"]) if it["source"] == "group"
                                  else scope_of_char(cid))["items"]:
                    if int(real.get("mid") or 0) == int(it.get("mid") or 0):
                        real["recall_n"] = int(real.get("recall_n") or 0) + 1
                        real["recall_ts"] = _time.time()
                        _schedule_flush(scope_of_group(it["gid"]) if it["source"] == "group"
                                        else scope_of_char(cid))
                        break
            except Exception:
                pass

        out["items"] = picked_items
        out["ok"] = True
        out["latency_ms"] = int((_time.time() - t0) * 1000)
        try:
            db.recall_log_add(cid, text, [
                {"mid": it.get("mid"), "source": it.get("source"), "date": it.get("date"),
                 "content": (it.get("content") or "")[:120], "updated": it.get("updated", 0)}
                for it in picked_items], out["latency_ms"])
        except Exception:
            pass
        return out
    except Exception as e:
        _log("[memory] 检索异常: %s" % e, level="WARNING")
        out["latency_ms"] = int((_time.time() - t0) * 1000)
        return out


# ============ 遗忘曲线 ============

def apply_forgetting(scope: str) -> int:
    """把"长期未被召回且召回次数极少"的非 pin 记忆标记为 dormant（不删除，可恢复）"""
    cfg = _cfg()
    if not cfg["forget_enabled"]:
        return 0
    ent = _load(scope)
    items = list(ent["items"])
    if not items:
        return 0
    now = _time.time()
    limit = now - cfg["forget_days"] * 86400
    n = 0
    for it in items:
        if int(it.get("pin") or 0) or int(it.get("dormant") or 0):
            continue
        last = float(it.get("recall_ts") or 0) or float(it.get("ts") or 0)
        if last < limit and int(it.get("recall_n") or 0) <= 1:
            it["dormant"] = 1
            n += 1
    if n and _save(scope, items):
        _log("[memory] 遗忘处理 %s：%d 条转入 dormant" % (scope, n))
    return n


# ============ 主动回忆 ============

def _day_key() -> str:
    return _time.strftime("%Y%m%d", _time.localtime())


def pick_proactive(cid: int) -> dict:
    """挑一条"高价值且久未提及"的私聊记忆用于主动开场；没有则返回 {}"""
    cfg = _cfg()
    if not cfg["enabled"] or not cfg["proactive_enabled"]:
        return {}
    try:
        # 每角色每日上限
        s = db.get_settings()
        key = "mem_proactive_%d_%s" % (int(cid), _day_key())
        used = int(float(s.get(key) or 0))
        if used >= cfg["proactive_daily"]:
            return {}
    except Exception:
        used = 0
    ent = _load(scope_of_char(cid))
    now = _time.time()
    cool = cfg["proactive_cool_days"] * 86400
    best = None
    for it in ent["items"]:
        if int(it.get("dormant") or 0):
            continue
        if str(it.get("role") or "") != "character":
            continue  # 用"她说过的话"主动开场最自然
        c = str(it.get("content") or "").strip()
        if len(c) < 12:
            continue
        try:
            import main as _m
            if _m._world_is_secret(c):
                continue
        except Exception:
            pass
        last = float(it.get("recall_ts") or 0)
        if last and (now - last) < cool:
            continue
        age = now - float(it.get("ts") or 0)
        if age < 86400:  # 24 小时内的内容不算"往事"
            continue
        score = (1 + int(it.get("recall_n") or 0)) * min(1.0, age / (30 * 86400))
        if best is None or score > best[0]:
            best = (score, it)
    if not best:
        return {}
    it = dict(best[1])
    try:
        db.set_settings({key: used + 1})
    except Exception:
        pass
    return it


def stats() -> dict:
    """记忆库规模统计（管理页展示）"""
    out = {}
    try:
        import glob
        for p in glob.glob(os.path.join(MEM_DIR, "mem_*.json")):
            name = os.path.basename(p)[4:-5]
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            out[name] = {"bytes": size, "mb": round(size / 1048576.0, 2)}
    except Exception:
        pass
    return out

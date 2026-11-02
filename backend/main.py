# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""Stories 后端：FastAPI 主应用（多角色聊天 + 知识库 + 媒体 + 管理端）"""
import asyncio
import hashlib
import hmac
import json as _json
import math
import os
import random
import re
import secrets as _secrets
# (原 import shutil 未使用，已移除)
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import db
import ai
import knowledge
import media
import memory_kb
import mini_balance
import mini_weather
import news
import logger
import secrets_cfg

APP_VERSION = "0.2.0"
DATA_DIR = os.environ.get("STORIES_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
ADMIN_DIR = os.path.join(os.path.dirname(__file__), "admin")
MINIAPP_DIR = os.path.join(os.path.dirname(__file__), "miniapps")
ADMIN_PASSWORD = secrets_cfg.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    # 未配置时生成随机口令（启动日志中打印一次），避免使用已知默认口令
    ADMIN_PASSWORD = "stories-" + hashlib.sha256(os.urandom(16)).hexdigest()[:10]

# 管理令牌：HMAC 签名 + 有效期（修复"口令的确定性哈希、永不过期"两个问题）。
# 格式 <签发时间戳>.<签名前32位>；签名密钥独立于管理口令（优先 env，否则持久化到数据目录，重启不失效）。
ADMIN_TOKEN_TTL = 7 * 24 * 3600
_ADMIN_SECRET_CACHE = {"v": ""}


def _admin_secret() -> str:
    if _ADMIN_SECRET_CACHE["v"]:
        return _ADMIN_SECRET_CACHE["v"]
    env = secrets_cfg.get("ADMIN_TOKEN_SECRET")
    if env:
        _ADMIN_SECRET_CACHE["v"] = env
        return env
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, "admin_token_secret")
        s = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                s = f.read().strip()
        if not s:
            s = _secrets.token_hex(32)
            with open(path, "w", encoding="utf-8") as f:
                f.write(s)
        _ADMIN_SECRET_CACHE["v"] = s
        return s
    except Exception:
        # 极端情况（数据目录不可写）：退化为进程内随机密钥，重启后需重新登录
        _ADMIN_SECRET_CACHE["v"] = _secrets.token_hex(32)
        return _ADMIN_SECRET_CACHE["v"]


def _admin_token_issue() -> str:
    """签发管理令牌（时间戳 + HMAC 签名）"""
    ts = int(time.time())
    sig = hmac.new(_admin_secret().encode("utf-8"),
                   ("%s:%d" % (ADMIN_PASSWORD, ts)).encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return "%d.%s" % (ts, sig)


def _admin_token_valid(tok: str) -> bool:
    """校验管理令牌：签名正确且在有效期内（默认 7 天）"""
    try:
        ts_s, _, sig = str(tok or "").partition(".")
        ts = int(ts_s)
        if ts <= 0 or ts > time.time() + 60 or (time.time() - ts) > ADMIN_TOKEN_TTL:
            return False
        want = hmac.new(_admin_secret().encode("utf-8"),
                        ("%s:%d" % (ADMIN_PASSWORD, ts)).encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        return _safe_eq(sig, want)
    except Exception:
        return False


ADMIN_TOKEN = _admin_token_issue()  # 启动时签发一枚（供内部引用/测试；登录会另发新令牌）


# URL 内嵌凭证：短时效票据（替代把长期 App 令牌塞进 ?token=）。
# 仅适用于"无法携带请求头"的场景：/media/、/uploads/、/api/mini/。
TICKET_TTL = 12 * 3600
_TICKET_SECRET_CACHE = {"v": ""}


def _ticket_secret() -> str:
    if _TICKET_SECRET_CACHE["v"]:
        return _TICKET_SECRET_CACHE["v"]
    env = secrets_cfg.get("STORIES_TICKET_SECRET")
    if env:
        _TICKET_SECRET_CACHE["v"] = env
        return env
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, "ticket_secret")
        s = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                s = f.read().strip()
        if not s:
            s = _secrets.token_hex(32)
            with open(path, "w", encoding="utf-8") as f:
                f.write(s)
        _TICKET_SECRET_CACHE["v"] = s
        return s
    except Exception:
        _TICKET_SECRET_CACHE["v"] = _secrets.token_hex(32)
        return _TICKET_SECRET_CACHE["v"]


def _ticket_issue():
    """签发短时效票据，返回 (票据, 过期时间戳)"""
    ts = int(time.time())
    sig = hmac.new(_ticket_secret().encode("utf-8"),
                   ("ticket:%d" % ts).encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return ("%d.%s" % (ts, sig), ts + TICKET_TTL)


def _ticket_valid(tok: str) -> bool:
    try:
        ts_s, _, sig = str(tok or "").partition(".")
        ts = int(ts_s)
        if ts <= 0 or ts > time.time() + 60 or (time.time() - ts) > TICKET_TTL:
            return False
        want = hmac.new(_ticket_secret().encode("utf-8"),
                        ("ticket:%d" % ts).encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        return _safe_eq(sig, want)
    except Exception:
        return False


_TICKET_PREFIXES = ("/media/", "/uploads/", "/api/mini/")


def _ticket_path_ok(path: str) -> bool:
    return str(path or "").startswith(_TICKET_PREFIXES)


APP_TOKEN = (secrets_cfg.get("STORIES_APP_TOKEN") or "").strip()
# 安全基线：未配置令牌时拒绝启动，避免"默认零鉴权"。
# 本地调试如确需无鉴权，必须显式设置 STORIES_ALLOW_NO_AUTH=1（启动日志会持续告警）。
_ALLOW_NO_AUTH = str(os.environ.get("STORIES_ALLOW_NO_AUTH", "")).strip().lower() in ("1", "true", "yes", "on")
if not APP_TOKEN and not _ALLOW_NO_AUTH:
    raise RuntimeError(
        "拒绝启动：未配置 STORIES_APP_TOKEN。生产环境必须启用 App 鉴权；"
        "如仅本地调试需要无鉴权，请显式设置 STORIES_ALLOW_NO_AUTH=1。"
    )

os.makedirs(UPLOAD_DIR, exist_ok=True)
for sub in ("avatars", "backgrounds", "stickers", "kb"):
    os.makedirs(os.path.join(UPLOAD_DIR, sub), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "media"), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "vectors"), exist_ok=True)

logger.init_logging()


def _log(msg: str, level: str = "INFO"):
    logger.log(msg, level=level)


# 启动横幅：日志开头写明版权与仓库地址
_log("============================================================")
_log("Stories Server %s" % APP_VERSION)
_log("Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>")
_log("License: MIT")
_log("============================================================")


if not secrets_cfg.get("ADMIN_PASSWORD"):
    _log(f"[security] 未配置 ADMIN_PASSWORD，本次启动使用随机口令：{ADMIN_PASSWORD}", level="WARNING")
if APP_TOKEN:
    _log("[security] App Token 鉴权：已启用")
else:
    _log("[security] 警告：STORIES_ALLOW_NO_AUTH=1，App 接口处于无鉴权状态（仅限本地调试）", level="WARNING")

db.init_db()

app = FastAPI(title="Stories Server", version=APP_VERSION, docs_url=None, redoc_url=None, openapi_url=None)
# CORS：默认关闭（App 为原生请求、管理页同源，均不需要跨域）。
# 如需从其他网页调用，可配置 STORIES_CORS_ORIGINS=https://example.com,http://localhost:3000
_cors_origins = [o.strip() for o in (os.environ.get("STORIES_CORS_ORIGINS") or "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

_models_cache = {"ts": 0, "data": None}
_models_fetching = False
_login_fails = {}
_login_lock = threading.Lock()


_proc_locks = {}
_proc_locks_guard = threading.Lock()
_cheat_lock = threading.Lock()


def _proc_lock_for(cid: int) -> threading.Lock:
    """按角色取处理锁：不同角色互不阻塞（原来全局单锁会让多角色互相饿死）"""
    with _proc_locks_guard:
        lk = _proc_locks.get(cid)
        if lk is None:
            lk = threading.Lock()
            _proc_locks[cid] = lk
        return lk


def _safe_eq(a, b) -> bool:
    """恒定时间字符串比较（防时序侧信道）。
    注意：compare_digest 对 str 仅支持 ASCII，非 ASCII 会抛 TypeError，
    因此先统一编码为 bytes 再比较。"""
    try:
        return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))
    except Exception:
        return False


@app.middleware("http")
async def _token_guard(request: Request, call_next):
    """App/媒体 Token 鉴权：默认强制启用（未配置时进程已在启动阶段拒绝运行）。
    - /api/*：App 请求头 X-App-Token；管理端接口另有 X-Admin-Token 校验
    - /media /uploads：?token=App令牌（App）或 ?token=管理令牌（管理页图片）
    - /media /uploads /api/mini：也接受 /api/ticket 签发的短时效票据（避免长期令牌进 URL）
    - OPTIONS 预检直接放行，避免 CORS 失效"""
    if APP_TOKEN and request.method != "OPTIONS":
        path = request.url.path
        if path.startswith("/api/") or path.startswith("/media/") or path.startswith("/uploads/"):
            supplied = request.headers.get("X-App-Token", "") or request.query_params.get("token", "")
            admin_h = request.headers.get("X-Admin-Token", "")
            ok = (_safe_eq(supplied, APP_TOKEN) or _admin_token_valid(supplied)
                  or _admin_token_valid(admin_h))
            if not ok and _ticket_path_ok(path) and _ticket_valid(supplied):
                ok = True  # 短时效 URL 票据（图片/媒体/小程序专用，不适用于其它接口）
            if not ok:
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    t0 = time.time()
    try:
        response = await call_next(request)
    except Exception as e:
        logger.log_exc(f"[http] {request.method} {request.url.path} 异常: {e} ({(time.time()-t0):.2f}s)")
        raise
    if not request.url.path.startswith(("/uploads", "/media")):
        _log(f"[http] {request.method} {request.url.path} -> {response.status_code} ({(time.time()-t0):.2f}s)")
    return response


# ============ 工具 ============

def _require_admin(request: Request):
    token = request.headers.get("X-Admin-Token", "")
    if not _admin_token_valid(token):
        raise HTTPException(status_code=401, detail="unauthorized")


def _cfg():
    s = db.get_settings()

    def _f(key, d, lo, hi):
        try:
            return min(max(float(s.get(key, d)), lo), hi)
        except Exception:
            return d

    return {
        "reply_delay": _f("reply_delay", 1.2, 0.0, 60.0),
        "msg_delay_factor": _f("msg_delay_factor", 1.0, 0.1, 10.0),
        "max_history": int(_f("max_history", 40, 1, 500)),
    }


def _msg_delay(text: str) -> float:
    base = 0.4 + len(text) * 0.04
    return max(0.8, min(3.0, base))


def _clean_preview(text: str, limit: int = 80) -> str:
    """列表/通知预览统一净化：剥离时间标注与伪标签（锁屏通知最容易暴露系统标记）"""
    try:
        s = ai.scrub_user_ref(ai.strip_wait_tags(ai.clean_meta_time(ai._strip_fake_tags(str(text or "")))), "对方")
    except Exception:
        s = str(text or "")
    s = (s or "").strip()
    return s[:limit] if limit and len(s) > limit else s


def _preview(m: dict) -> str:
    if not m:
        return ""
    kind = m.get("kind") or "text"
    if kind == "voice":
        return "[语音]"
    if kind == "photo":
        return "[照片]"
    if kind == "video":
        return "[视频]"
    if kind == "sticker":
        return "[表情]"
    if kind == "image":
        return "[图片]"
    return _clean_preview(m.get("content") or "", 0)


def _char_avatar_url(avatar: str) -> str:
    return f"/uploads/avatars/{avatar}" if avatar else ""


def _avatar_path(name: str) -> str:
    """返回 avatars 目录内的安全路径；越界或空名返回空串。"""
    base = os.path.basename(str(name or "").strip())
    if not base or base in (".", ".."):
        return ""
    root = os.path.realpath(os.path.join(UPLOAD_DIR, "avatars"))
    path = os.path.realpath(os.path.join(root, base))
    return path if os.path.dirname(path) == root else ""


def _bootstrap_char(c: dict, chat: dict = None, last: dict = None, msg_count: int = None) -> dict:
    if chat is None:
        chat = db.get_chat(c["id"])
    if last is None:
        last = db.get_last_message(c["id"])
    if msg_count is None:
        msg_count = db.count_messages(c["id"])
    return {
        "id": c["id"],
        "name": c["name"],
        "avatar": _char_avatar_url(c.get("avatar", "")),
        "description": c.get("description", ""),
        "greeting": c.get("greeting", ""),
        "enabled": int(c.get("enabled", 1) or 0),
        "chat": {
            "photo_enabled": bool(chat.get("photo_enabled")),
            "voice_enabled": bool(chat.get("voice_enabled")),
            "sticker_enabled": bool(chat.get("sticker_enabled")),
            "video_enabled": bool(chat.get("video_enabled")),
            "img_desc_enabled": bool(chat.get("img_desc_enabled", 1)),
            "voice_desc_enabled": bool(chat.get("voice_desc_enabled", 1)),
            "wake_enabled": bool(chat.get("wake_enabled", 1)),
            "reminder_enabled": bool(chat.get("reminder_enabled", 0)),
            "reminder_target": float(chat.get("reminder_target") or 5),
            "moments_auto": bool(chat.get("moments_auto", 0)),
            "moments_react": bool(chat.get("moments_react", 1)),
            "background": chat.get("background") or "",
        },
        "last": {
            "content": _preview(last),
            "ts": last["created_at"] if last else 0,
            "role": last["role"] if last else "",
        } if last else None,
        "msg_count": msg_count,
    }


def _msg_dict(r: dict) -> dict:
    return {
        "id": r["id"],
        "role": r["role"],
        "kind": r["kind"],
        "content": r["content"],
        "img_desc": r.get("img_desc") or "",
        "media_url": r["media_url"] or None,
        "duration": r.get("duration") or 0,
        "ts": r["created_at"],
    }


_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?…~～；;])")
_COMMA_SPLIT_RE = re.compile(r"(?<=[，,、])")


def _smart_split(text: str, limit: int = 42):
    """把过长文本按句子边界拆成适合聊天逐条发送的短段（不改一字，只拆分、不跨句）"""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    parts = [p for p in _SENT_SPLIT_RE.split(text) if p and p.strip()]
    segs = []
    buf = ""
    for p in parts:
        if not buf or len(buf) + len(p) <= limit:
            buf += p
        else:
            segs.append(buf)
            buf = p
    if buf.strip():
        segs.append(buf)
    # 仍超长的段再按逗号拆
    out = []
    for s in segs:
        if len(s) <= limit * 1.6:
            out.append(s)
            continue
        pieces = [x for x in _COMMA_SPLIT_RE.split(s) if x and x.strip()]
        b2 = ""
        for p in pieces:
            if not b2 or len(b2) + len(p) <= limit:
                b2 += p
            else:
                out.append(b2)
                b2 = p
        if b2.strip():
            out.append(b2)
    return [s for s in out if s and s.strip()]


def _as_bool_soft(v, default=True):
    """兼容旧调用名：与 _as_bool 完全同义"""
    return _as_bool(v, default)


def _seg_cfg():
    """分段管线配置（管理页可调，默认沿用旧版参数）"""
    try:
        s = db.get_settings()
    except Exception:
        s = {}

    def _i(key, d):
        try:
            return int(float(s.get(key)))
        except Exception:
            return d

    kws = str(s.get("seg_voice_keywords") or "").strip()
    if not kws:
        kws = "晚安,想你,爱你,舍不得,心疼,抱抱,亲爱的,想见你,么么,好梦"
    return {
        "enabled": _as_bool_soft(s.get("seg_enabled", 1)),
        "voice_mix": _as_bool_soft(s.get("seg_voice_mix", 1)),
        "voice_max_chars": max(1, _i("seg_voice_max_chars", 20)),
        "voice_daily_limit": max(0, _i("seg_voice_daily_limit", 15)),
        "keywords": [k.strip() for k in re.split(r"[,，、\s]+", kws) if k.strip()],
    }


def _voice_quota_key(cid: int) -> str:
    try:
        day = _wake_local_now(_wake_cfg()).strftime("%Y%m%d")
    except Exception:
        day = time.strftime("%Y%m%d")
    return "voice_count_%d_%s" % (cid, day)


_voice_quota_lock = threading.Lock()


def _voice_quota_reserve(cid: int) -> bool:
    """原子预留一次语音额度（查+扣在同一锁内），防并发超发"""
    with _voice_quota_lock:
        try:
            s = db.get_settings()
            used = int(float(s.get(_voice_quota_key(cid)) or 0))
            limit = int(_seg_cfg()["voice_daily_limit"] or 0)
            if used >= limit:
                return False
            db.set_settings({_voice_quota_key(cid): used + 1})
            return True
        except Exception:
            return False


def _voice_quota_refund(cid: int):
    """语音合成失败时退还预留额度"""
    with _voice_quota_lock:
        try:
            s = db.get_settings()
            used = int(float(s.get(_voice_quota_key(cid)) or 0))
            if used > 0:
                db.set_settings({_voice_quota_key(cid): used - 1})
        except Exception:
            pass


def _seg_voice_ok(cid: int, seg: str) -> bool:
    """段级语音判定：短段 + 关键词 + 角色语音开关 + 每日限额"""
    if not _seg_cfg()["voice_mix"]:
        return False
    seg = (seg or "").strip()
    if not seg or len(seg) > _seg_cfg()["voice_max_chars"]:
        return False
    chat = db.get_chat(cid)
    if not chat.get("voice_enabled"):
        return False
    if not any(k in seg for k in _seg_cfg()["keywords"]):
        return False
    return _voice_quota_reserve(cid)  # 原子预留（TTS 失败时退还）


def _tts_context(cid: int) -> str:
    """语音情绪判定用的最近对话（最多 3 条纯文本，供模型判断语气）"""
    try:
        rows = db.get_recent_messages(cid, 4)
    except Exception:
        return ""
    out = []
    for r in rows:
        if (r.get("kind") or "text") not in ("text", "voice"):
            continue
        c = (r.get("content") or "").strip()
        if not c:
            continue
        who = "对方" if r.get("role") == "user" else "你"
        out.append("%s：%s" % (who, c[:40]))
    return "\n".join(out[-3:])


def _queue_segments(cid: int, reply: str, factor: float, ref_id: int = 0):
    """投递回复：分段管线（AI 拆分 → 段级语音混发/文字，逐条延迟）"""
    reply = (reply or "").strip()
    if not reply:
        reply = "……"
    segs = None
    if _seg_cfg()["enabled"]:
        try:
            segs = ai.split_segments(reply)
        except Exception as e:
            _log(f"[segment] AI 拆分异常，回退规则拆分: {e}", level="WARNING")
    if not segs:
        raw = [s.strip() for s in reply.split("\n") if s.strip()] or [reply]
        segs = []
        for s in raw:
            segs.extend(_smart_split(s))
    if not segs:
        segs = ["……"]
    delay = 0.0
    now = time.time()
    queued = 0
    _ctx = _tts_context(cid)
    for seg in segs:
        # [wait:N]：先发本段文本，再等待 N 秒才发下一段（大小写/全角括号/可选冒号都兼容）
        m = re.search(r"[\[【〔［]\s*wait\s*[:：]?\s*(\d{1,3})\s*秒?\s*[\]】〕］]", seg, re.I)
        if m:
            wait_sec = max(1, min(int(m.group(1)), 300))
            rest = re.sub(r"[\[【〔［]\s*wait\s*[:：]?\s*\d{1,3}\s*秒?\s*[\]】〕］]", "", seg, flags=re.I).strip()
            if rest:
                db.add_pending(cid, rest, kind="script", ts=now + delay, ref_id=ref_id)
                queued += 1
                delay += _msg_delay(rest) * factor
            delay += wait_sec * factor
            continue
        # 段级语音混发（分段管线）
        if _seg_voice_ok(cid, seg):
            tts = media.tts_minimax(seg, context=_ctx)
            if tts.get("url"):
                db.add_pending(cid, seg, kind="voice", media_url=tts["url"],
                               duration=tts.get("duration", 0), ts=now + delay, ref_id=ref_id)
                queued += 1
            else:
                _voice_quota_refund(cid)  # 合成失败，退还预留额度
                _log(f"[segment] 段级语音合成失败，回退文字: {tts.get('error')}", level="WARNING")
                db.add_pending(cid, seg, kind="script", ts=now + delay, ref_id=ref_id)
                queued += 1
        else:
            db.add_pending(cid, seg, kind="script", ts=now + delay, ref_id=ref_id)
            queued += 1
        delay += _msg_delay(seg) * factor
    if queued == 0:
        # 整条回复只有 [wait:N] 标记时兜底，避免角色彻底哑掉
        db.add_pending(cid, "……", kind="script", ts=now, ref_id=ref_id)
    return len(segs)


def _extra_instruction(cid: int, character: dict, chat: dict, user_text: str) -> str:
    """生成主回复前的附加指令（提醒插件 / 联网搜索在此注入）"""
    parts = []
    try:
        parts.append(_reminder_instruction(cid, user_text))
    except Exception as e:
        _log(f"[reminder] 指令生成异常: {e}", level="WARNING")
    try:
        parts.append(_search_instruction(user_text))
    except Exception as e:
        _log(f"[search] 指令生成异常: {e}", level="WARNING")
    return "\n".join([p for p in parts if p])


# ===== 提醒插件（reminder）=====
# 本仓库**不附带**任何提醒提示词与关键词，三项默认全部留空。
# 需要该功能时，请在管理后台「设置与日志 → 提醒插件」里自行填写：
#   reminder_judge_prompt（判断时机 + 生成例句）
#   reminder_prompt（注入到回复里的要求）
#   reminder_keywords（用于计数检测的关键词，逗号分隔）
# 留空时该功能自动停用，不会向模型注入任何指令。
DEFAULT_REMINDER_KEYWORDS = ""
DEFAULT_REMINDER_JUDGE_PROMPT = ""
DEFAULT_REMINDER_PROMPT = ""


def _reminder_cfg():
    """提醒插件全局参数（管理页可调）"""
    try:
        s = db.get_settings()
    except Exception:
        s = {}

    def _i(key, d):
        try:
            return int(float(s.get(key)))
        except Exception:
            return d

    kws = [k.strip() for k in re.split(r"[,，、\s]+", str(s.get("reminder_keywords") or "")) if k.strip()]
    return {
        "cooldown": max(0, _i("reminder_cooldown", 30)),
        "retry": max(1, _i("reminder_retry", 15)),
        "count_cooldown": max(0, _i("reminder_count_cooldown", 60)),
        "keywords": kws or [k for k in DEFAULT_REMINDER_KEYWORDS.split(",") if k],
        "judge_prompt": str(s.get("reminder_judge_prompt") or "").strip() or DEFAULT_REMINDER_JUDGE_PROMPT,
        "remind_prompt": str(s.get("reminder_prompt") or "").strip() or DEFAULT_REMINDER_PROMPT,
    }


def _week_key() -> str:
    d = _wake_local_now(_wake_cfg())
    iso = d.isocalendar()
    return "%04dW%02d" % (iso[0], iso[1])


def _reminder_state_now(cid: int) -> dict:
    st = db.get_reminder_state(cid)
    wk = _week_key()
    if st.get("week") != wk:
        db.update_reminder_state(cid, week=wk, count=0)
        st = db.get_reminder_state(cid)
    return st


def _reminder_target_of(chat: dict) -> int:
    try:
        return max(1, min(99, int(float(chat.get("reminder_target") or 5))))
    except Exception:
        return 5


def _reminder_instruction(cid: int, user_text: str) -> str:
    """回复前判断是否注入「主动提醒」指令（reminder）

    本仓库默认不提供提示词：未在后台配置 `reminder_judge_prompt` 时功能自动停用。
    """
    chat = db.get_chat(cid)
    if not chat.get("reminder_enabled"):
        return ""
    cfg = _reminder_cfg()
    if not str(cfg.get("judge_prompt") or "").strip():
        return ""  # 未配置提示词 → 不注入（默认行为）
    st = _reminder_state_now(cid)
    target = _reminder_target_of(chat)
    if target <= 0 or int(st.get("count") or 0) >= target:
        return ""
    now = time.time()
    if now - float(st.get("last_reminder_ts") or 0) < cfg["cooldown"] * 60:
        return ""
    if now - float(st.get("last_judge_ts") or 0) < cfg["retry"] * 60:
        return ""
    db.update_reminder_state(cid, last_judge_ts=now)
    local_now = _wake_local_now(_wake_cfg())
    days_since = (now - float(st.get("last_reminder_ts") or 0)) / 86400 if st.get("last_reminder_ts") else 0
    prompt = (cfg["judge_prompt"]
              .replace("{progress}", str(int(st.get("count") or 0)))
              .replace("{target}", str(target))
              .replace("{days_since}", "%.1f" % days_since)
              .replace("{time_desc}", local_now.strftime("%m月%d日 %H:%M"))
              .replace("{user_msg}", user_text or ""))
    content, err = ai._call_model([{"role": "user", "content": prompt}],
                                  model=ai._judge_model(), temperature=0.3, max_tokens=600)
    if err or not (content or "").strip():
        return ""
    ok, examples = False, []
    try:
        start, end = content.find("{"), content.rfind("}") + 1
        data = _json.loads(content[start:end])
        ok = str(data.get("ok") or "").strip() in ("是", "true", "True", "yes")
        examples = [str(x).strip() for x in (data.get("examples") or []) if str(x).strip()][:3]
    except Exception:
        head = content[:30]
        ok = ("是" in head) and ("否" not in head)
    if not ok:
        return ""
    ex_txt = "\n".join("- " + e for e in examples) if examples else "（按你的风格自由发挥）"
    remind = (cfg["remind_prompt"]
              .replace("{progress}", str(int(st.get("count") or 0)))
              .replace("{target}", str(target))
              .replace("{examples}", ex_txt))
    _log(f"[reminder] 已注入提醒指令 cid={cid}（本周 {int(st.get('count') or 0)}/{target}）")
    return remind


_reminder_lock = threading.Lock()


def _reminder_count_reply(cid: int, reply: str):
    """回复后检测提醒关键词并计数"""
    chat = db.get_chat(cid)
    if not chat.get("reminder_enabled"):
        return
    cfg = _reminder_cfg()
    if not any(k in (reply or "") for k in cfg["keywords"]):
        return
    with _reminder_lock:
        now = time.time()
        st = _reminder_state_now(cid)
        if now - float(st.get("last_count_ts") or 0) < cfg["count_cooldown"] * 60:
            return
        new_count = int(st.get("count") or 0) + 1
        db.update_reminder_state(cid, count=new_count, last_reminder_ts=now, last_count_ts=now)
    _log(f"[reminder] 提醒计数 +1 cid={cid} → {new_count}/{_reminder_target_of(chat)}")


_commit_lock = threading.Lock()


def _commit_parse(chat: dict):
    try:
        existing = _json.loads(chat.get("commit_memory") or "[]")
    except Exception:
        existing = []
    if not isinstance(existing, list):
        existing = []
    texts = [str((it.get("t") if isinstance(it, dict) else it) or "").strip() for it in existing]
    return existing, [x for x in texts if x]


_COMMIT_SIGNALS = (
    "答应", "说好", "约定", "约好", "承诺", "保证", "发誓", "拉钩", "一言为定",
    "以后", "下次", "明天", "后天", "周末", "放假", "假期", "暑假", "寒假",
    "下周", "开学", "别告诉", "不许说", "不能说", "保密", "别跟别人",
    "再也", "不许再", "底线", "别忘",
)


def _commit_has_signal(text: str) -> bool:
    """承诺信号词门槛：没有任何承诺类字样时不必调用模型（省钱+防废话进库）"""
    t = text or ""
    return any(w in t for w in _COMMIT_SIGNALS)


def _commit_extract_async(cid: int, user_text: str, reply_text: str):
    """后台抽取承诺/立场记忆（不阻塞回复投递；失败静默）"""
    try:
        # 省钱门槛：双方这话都太短，不可能有承诺
        if len((user_text or "").strip()) + len((reply_text or "").strip()) < 20:
            return
        try:
            if str((db.get_settings() or {}).get("commit_extract_enabled", "1")).lower() in ("0", "false", "no", "off"):
                return
        except Exception:
            pass
        if not _commit_has_signal((user_text or "") + "\n" + (reply_text or "")):
            return
        character = db.get_character(cid)
        if not character:
            return
        _existing, texts = _commit_parse(db.get_chat(cid))
        new_items = ai.extract_commitments(character, user_text, reply_text, texts)
        if not new_items:
            return

        def _norm(s):
            return re.sub(r"\s+", "", s or "")

        def _pick(items, base_texts):
            out = []
            for it in items:
                n = _norm(it)
                if not n:
                    continue
                if any(n in _norm(x) or _norm(x) in n for x in base_texts + out):
                    continue
                out.append(it)
            return out

        added = _pick(new_items, texts)
        if not added:
            return
        with _commit_lock:
            existing, texts_now = _commit_parse(db.get_chat(cid))  # 锁内重读，避免并发丢更新
            added = _pick(added, texts_now)
            if not added:
                return
            merged = existing + [{"t": t, "ts": time.time()} for t in added]
            db.update_chat(cid, commit_memory=_json.dumps(merged[-15:], ensure_ascii=False))
            _log(f"[commit] 新增承诺/立场记忆 cid={cid}: " + "; ".join(added))
    except Exception as e:
        _log(f"[commit] 抽取失败 cid={cid}: {e}", level="WARNING")


_longmem_lock = threading.Lock()


def _longmem_trim(items: list, cap: int = 100) -> list:
    """永久记忆（pin=1）永不清理；普通记忆超 cap 时保留最早 20 + 最近 (cap-20)"""
    pinned = [x for x in items if isinstance(x, dict) and x.get("pin")]
    rest = [x for x in items if not (isinstance(x, dict) and x.get("pin"))]
    if len(rest) > cap:
        rest = rest[:20] + rest[-(cap - 20):]
    try:
        return sorted(pinned + rest, key=lambda x: float(x.get("ts") or 0))
    except Exception:
        return pinned + rest


def _longmem_parse(chat: dict):
    try:
        existing = _json.loads(chat.get("long_memory") or "[]")
    except Exception:
        existing = []
    if not isinstance(existing, list):
        existing = []
    texts = [str((it.get("t") if isinstance(it, dict) else it) or "").strip() for it in existing]
    return existing, [x for x in texts if x]


def _longmem_extract_async(cid: int, user_text: str, reply_text: str):
    """后台抽取「重要记忆」（重要事件/关系进展/偏好；不阻塞回复投递；失败静默）"""
    try:
        if len((user_text or "").strip()) + len((reply_text or "").strip()) < 20:
            return
        try:
            if str((db.get_settings() or {}).get("long_memory_enabled", "1")).lower() in ("0", "false", "no", "off"):
                return
        except Exception:
            pass
        character = db.get_character(cid)
        if not character:
            return
        _existing, texts = _longmem_parse(db.get_chat(cid))
        new_items = ai.extract_long_memory(character, user_text, reply_text, texts)
        if not new_items:
            return

        def _norm(s):
            return re.sub(r"\s+", "", s or "")

        def _pick(items, base_texts):
            out = []
            for it in items:
                n = _norm(it)
                if not n:
                    continue
                dup = False
                for x in base_texts + out:
                    m = _norm(x)
                    if not m:
                        continue
                    short, lng = (n, m) if len(n) <= len(m) else (m, n)
                    # 完全一致，或短句≈长句（长句需比短句长 ≥25% 才有资格另存）才算重复；措辞微调视为重复，带新细节的长句保留（LM7）
                    if short == lng or (short in lng and len(short) * 1.25 >= len(lng)):
                        dup = True
                        break
                if dup:
                    continue
                out.append(it)
            return out

        added = _pick(new_items, texts)
        if not added:
            return
        with _longmem_lock:
            existing, texts_now = _longmem_parse(db.get_chat(cid))  # 锁内重读，避免并发丢更新
            added = _pick(added, texts_now)
            if not added:
                return
            merged = existing + [{"t": t, "ts": time.time()} for t in added]
            db.update_chat(cid, long_memory=_json.dumps(_longmem_trim(merged), ensure_ascii=False))
            _log(f"[longmem] 新增重要记忆 cid={cid}: " + "; ".join(added))
    except Exception as e:
        _log(f"[longmem] 抽取失败 cid={cid}: {e}", level="WARNING")


_world_lock = threading.Lock()
_world_busy = set()
_world_last_call = {}  # key -> 上次识别时间（同会话限频，省钱）
_world_sem = threading.Semaphore(3)
_WORLD_SECRET_MARKS = ("别告诉", "不要告诉", "别跟", "不要跟", "偷偷", "保密", "说漏", "别说出去",
                       "别让我", "别让他", "别让她", "只跟你说", "只告诉你", "就咱俩", "咱俩知道",
                       "你我知道", "只属于我们", "替我保密", "别往外说", "别外传", "别声张",
                       "你懂就好", "装不知道", "千万不要说", "只有我们知道",
                       "只跟你一个人", "就跟你说了", "烂在肚子里", "谁都不告诉", "先别往外传")
_WORLD_SECRET_RE = re.compile(
    r"别让[^，。！？\n]{0,8}知道|不想让[^，。！？\n]{0,8}知道|(?:只(?:有)?|就)(?:我们|咱俩|你我)[^，。！？\n]{0,6}知道|这(?:事|件事|事儿)就[^，。！？\n]{0,8}知道")


def _world_is_secret(text: str) -> bool:
    t = text or ""
    return any(k in t for k in _WORLD_SECRET_MARKS) or bool(_WORLD_SECRET_RE.search(t))


def _world_recognize_async(chat_cid: int, text: str, speaker_cid: int = 0, group_id: int = 0):
    """每一条私聊/群聊消息都用便宜模型结合上下文识别世界事件并广播（保证同世界见闻一致）。
    - 私聊：chat_cid=角色id；群聊：group_id 传群 id
    - speaker_cid：说话的角色 id（用户发言传 0）
    优化：总开关、保密关键词硬拦截、同会话防重入、全局并发限流、模型兜底"""
    key = f"g{group_id}" if group_id else f"c{chat_cid}"
    try:
        text = (text or "").strip()
        if not text:
            return
        try:
            if str(db.get_settings().get("world_recognize_enabled", 1)) in ("0", "False", "false", ""):
                return
        except Exception:
            pass
        # 省钱预过滤：太短/纯语气词消息不值得调模型（关键信息会被后续消息的上下文带走）
        if len(text) < 5:
            return
        if text.strip("，。！？!?~～. 好的嗯哦哈哈笑死在吗呀吧呢啊") == "":
            return
        if _world_is_secret(text):
            _log("[world] 检测到保密内容，跳过广播")
            return
        with _world_lock:
            if key in _world_busy:
                return  # 同一会话已有识别在跑，跳过这次（避免重复与并发）
            last = float(_world_last_call.get(key) or 0)
            if time.time() - last < 20:
                return  # 同一会话 20 秒内只识别一次（识别器会读取对话上下文，不丢事件）
            _world_busy.add(key)
            _world_last_call[key] = time.time()
    except Exception:
        return
    try:
        chars = db.list_characters(include_disabled=True)
        all_names = {int(c["id"]): (c.get("name") or "") for c in chars}
        # 世界集合 + 对话上下文
        if group_id:
            members = db.group_members(group_id)
            world_ids = set()
            for m in members:
                for w in db.worlds_for_cid(int(m["cid"])):
                    world_ids.add(int(w["id"]))
            names = {int(m["cid"]): (m.get("name") or "") for m in members}
            group_names = {v for v in names.values() if v}
            ctx_lines = []
            for r in db.get_group_recent(group_id, 14):
                body = _group_msg_body(r)
                if not body:
                    continue
                who = "用户" if r.get("role") == "user" else names.get(int(r.get("cid") or 0), "角色")
                ctx_lines.append(f"{who}：{body[:80]}")
        else:
            world_ids = {int(w["id"]) for w in db.worlds_for_cid(chat_cid)}
            cname = all_names.get(chat_cid) or "角色"
            group_names = set()
            ctx_lines = []
            for r in db.get_recent_messages(chat_cid, 14):
                body = (r.get("content") or "").strip()
                if not body:
                    continue
                who = "用户" if r.get("role") == "user" else cname
                ctx_lines.append(f"{who}：{body[:80]}")
        if not world_ids:
            return
        # 世界成员名 + 已有动态（去重）
        member_names = set()
        existing = []
        for wid in list(world_ids)[:3]:
            for m in db.world_members(wid):
                nm = all_names.get(int(m["cid"]))
                if nm:
                    member_names.add(nm)
            for e in db.world_recent_events(wid, 15):
                t = (e.get("content") or "").strip()
                if t:
                    existing.append(t)
        # 保密复核：不仅查触发消息，也查最近上下文（防止"别告诉…"的下一句无关消息仍被抽取广播）
        if _world_is_secret(text) or _world_is_secret(" ".join(ctx_lines[-5:])):
            _log("[world] 保密复核命中（含上下文），跳过广播")
            return
        speaker = all_names.get(int(speaker_cid)) if speaker_cid else "用户"
        prompt = (
            "你在维护一个多角色世界的共享记忆。下面是最近的对话上下文和最新一条消息，"
            f"世界里的人物有：{'、'.join(sorted(member_names))}。\n"
            "任务：结合上下文，只挑出最新消息里【公开的、值得让世界里所有角色都知道】的事件。\n\n"
            "✅ 应该广播（公开事件）：\n"
            "- 涉及两个及以上人物的公开活动：见面、一起吃饭/运动/逛街、约好时间地点等\n"
            "- 世界里其他角色做过的事、说过的话（让相关的人知道\"确实发生了\"，避免互相矛盾）\n"
            "- 发生在公共场合、其他人可能看到或知道的事\n\n"
            "❌ 绝对不广播（私密事件）：\n"
            "- 私密的两人互动（尤其涉及隐私的私事）\n"
            "- 明确要求保密的事（\"别告诉别人\"\"偷偷\"\"只跟你说\"等），或明显不便让第三人知道的谈话内容\n"
            "- 只属于两人之间的私人谈话细节、心里话、吵架内容\n"
            "- 单个角色或用户的私人琐事、纯情绪表达、日常闲聊\n\n"
            "原则：广播等于让全世界知道，宁可少发也不要泄露私密信息。\n"
            "注意消解代词和省略（她/他/那件事指谁）。\n"
            "不要重复【已有世界动态】；最多 2 条；每条 ≤40 字、第三人称、写清涉及角色的名字。\n"
            '只输出 JSON：{"events": ["..."]}\n\n'
            "【已有世界动态】\n" + ("\n".join(f"- {x}" for x in existing[-15:]) or "（无）") + "\n\n"
            "【对话上下文（最新在下）】\n" + ("\n".join(ctx_lines[-14:]) or "（无）") + "\n\n"
            f"【最新消息】\n{speaker}：{text[:300]}"
        )
        content = ""
        err = None
        with _world_sem:
            content, err = ai._call_model([{"role": "user", "content": prompt}],
                                          model=ai._judge_model(), temperature=0.1, max_tokens=300)
        if err or not (content or "").strip():
            with _world_sem:
                content, err = ai._call_model([{"role": "user", "content": prompt}],
                                              model=ai.ARK_MODEL, temperature=0.1, max_tokens=300)
        if err or not (content or "").strip():
            with _world_lock:
                _world_last_call.pop(key, None)  # 失败不占限频额度：下一条消息可立即重试
            return
        d = ai._extract_json(content)
        events = []
        if isinstance(d, dict):
            events = [str(x).strip()[:60] for x in (d.get("events") or []) if str(x).strip()]
        if not events:
            return

        def _norm(s):
            return re.sub(r"[\s，。！？、,.!?]", "", s or "")

        existing_norm = [_norm(x) for x in existing]
        for ev in events[:2]:
            n = _norm(ev)
            if not n:
                continue
            if any(n in x or x in n for x in existing_norm):
                continue
            if group_id:
                # 群聊来源：只有提到"不在这个群里的世界成员"才需要广播（保护群内私房话不外传）
                outside = [nm for nm in member_names if nm and nm not in group_names and nm in ev]
                if not outside:
                    continue
            # 落库前统一清洗（防伪标签/时间标注进入世界动态并被跨角色看到）
            ev = ai.clean_meta_time(ai._strip_fake_tags(ev))[:60]
            ev = ai.scrub_user_ref(ev, "对方")
            if _world_is_secret(ev):  # 产出事件二次复核（防上下文里带出的保密内容落库）
                continue
            if not ev:
                continue
            # 选目标世界：事件里提到的成员所属的**所有**世界（跨世界共享）；谁都没提到则广播到全部世界
            targets = []
            for wid in world_ids:
                names_in_world = {all_names.get(int(m["cid"])) for m in db.world_members(wid)}
                if any((nm and len(str(nm)) >= 2 and nm in ev) for nm in names_in_world if nm):
                    targets.append(wid)
            targets = targets or sorted(world_ids)
            for target in targets:
                db.add_world_event(target, ev, sender=("character" if speaker_cid else "user"),
                                   source_cid=int(speaker_cid or 0))
            existing_norm.append(n)
            _log(f"[world] 识别并广播到 #{targets}: {ev}（说话人 cid={speaker_cid}）")
    except Exception as e:
        _log(f"[world] 识别失败 cid={chat_cid}: {e}", level="WARNING")
    finally:
        try:
            with _world_lock:
                _world_busy.discard(key)
        except Exception:
            pass


def _world_cleanup():
    """清理 30 天前的世界动态，避免数据库无限增长"""
    try:
        conn = db.get_conn()
        conn.execute("DELETE FROM world_events WHERE created_at < ?", (time.time() - 30 * 86400,))
        conn.commit()
    except Exception:
        pass


def _search_instruction(user_text: str) -> str:
    """联网搜索指令（anysearch 接口）"""
    return ai.search_instruction(user_text)


def _dispatch_reply(cid: int, result: dict, cfg: dict, ref_id: int = 0, user_text: str = ""):
    """把生成的回复投递到队列/媒体（普通回复与主动联系共用，保证行为一致）"""
    if result.get("voice"):
        tts = media.tts_minimax(result["reply"], context=_tts_context(cid))
        if tts.get("url"):
            db.add_message(cid, "character", "voice", result["reply"], tts["url"], tts.get("duration", 0))
        else:
            _log(f"[chat] 语音合成失败，回退文字: {tts.get('error')}", level="WARNING")
            _queue_segments(cid, result["reply"], cfg["msg_delay_factor"], ref_id=ref_id)
    elif not (result.get("reply") or "").strip() and result.get("sticker"):
        pass  # 纯贴纸回复：不补"……"文字，只发贴纸
    else:
        _queue_segments(cid, result["reply"], cfg["msg_delay_factor"], ref_id=ref_id)

    if result.get("sticker"):
        db.add_pending(cid, result["sticker"], kind="sticker", ts=time.time() + 0.5, ref_id=ref_id)

    if result.get("photo_prompt"):
        threading.Thread(target=_photo_async, args=(cid, result["photo_prompt"], ref_id), daemon=True).start()

    if result.get("video_prompt"):
        threading.Thread(target=_video_async, args=(cid, result["video_prompt"], ref_id), daemon=True).start()

    if result.get("group_msg"):
        threading.Thread(target=_send_group_msg_async, args=(cid, result["group_msg"], user_text), daemon=True).start()
    elif _group_msg_followup(user_text, result.get("reply") or ""):
        threading.Thread(target=_send_group_msg_from_instruction,
                         args=(cid, user_text, result.get("reply") or ""), daemon=True).start()

    db.update_chat(cid, last_msg_at=time.time())


def _group_instruct_wanted(text: str) -> bool:
    """用户是否明确要求角色去群里说/问/发一句。
    要求同时满足：①"去/到/在/往…群"或"跟/和/给群里"的转达结构 + 附近有动作词；
    ②排除描述性用法（看到/听说…、自述习惯"我一般/平时/不怎么在群里…"）。"""
    t = (text or "").strip()
    if not t or "群" not in t:
        return False
    for neg in ("别在群里", "不要在群里", "别去群里", "不用在群里", "先别在群里", "别跟群里"):
        if neg in t:
            return False
    # 描述性用法排除（提到群 ≠ 让她转达）
    if re.search(r"(看到|听见|听说)[^，。！？\n]{0,4}群里?(在|有人|都|那|这)", t):
        return False
    if re.search(r"我[^，。！？\n]{0,10}(一般|平时|经常|总是|不怎么|很少|就)", t):
        return False
    verbs = ("说", "问", "发", "告诉", "提醒", "转告", "喊", "催", "通知", "讲", "聊", "冒泡", "冒个泡", "打招呼", "打个招呼", "发言", "问问", "提一句", "提一嘴")
    if not any(v in t for v in verbs):
        return False
    # 转达结构：去/到/在/往 + （最多 6 字）+ 群里，且动作词跟在群的近旁
    if re.search(r"(去|到|在|往)[^，。！？\n]{0,6}群里?\s*[^，。！？\n]{0,8}?"
                 r"(说|问|发|告诉|提醒|转告|喊|催|通知|讲|聊|冒.{0,2}泡|打.{0,1}招呼|发言|提.{0,1}句)", t):
        return True
    if re.search(r"(跟|和|给)群里?\s*[^，。！？\n]{0,8}?(说|问|发|告诉|提醒|转告|喊|催|通知|讲)", t):
        return True
    return False


def _group_msg_followup(user_text: str, reply_text: str) -> bool:
    """这次回复后是否需要兜底去群里发一句：
    用户明确要求去群里说/问，或追问"发了吗/没发"，或她回复里声称要去群/已去群发"""
    u = (user_text or "").strip()
    r = (reply_text or "").strip()
    if _group_instruct_wanted(u):
        return True
    if any(v in u for v in ("发了吗", "发了没", "发了么", "没发", "发出去", "发送")):
        # 防误触：需有"群体语境"证据 —— 用户提到群，或她的回复在说"要去群/已发出去"
        if ("群" in u) or ("群" in r) or any(v in r for v in ("发出去", "发过去", "发到", "去说", "去问", "去群", "去发")):
            return True
    # 她回复里明确说"要去群/已去群说"才算（描述性提到群不算，避免"群里都在发照片呢"误触发）
    if re.search(r"(去|到|在|往)群里?\s*(说|问|发|告诉|提醒|喊|催|通知|讲|聊|冒.{0,2}泡|打.{0,1}招呼)", r):
        return True
    return False


def _pick_group_for(cid: int, hint: str = "") -> int:
    """选群：用户点名了群名（hint 里包含）→ 优先该群；否则选最近活跃的群"""
    try:
        groups = db.groups_for_cid(int(cid))
    except Exception:
        groups = []
    if not groups:
        return 0
    h = hint or ""
    if h:
        import difflib  # 群名模糊匹配（"去家里群" vs "家人群"）
        best_g, best_r = 0, 0.0
        for g in groups:
            nm = str(g.get("name") or "").strip()
            if not nm:
                continue
            key = nm.rstrip("群").strip() or nm
            if nm in h or key in h:
                return int(g["id"])
            for i in range(max(0, len(h) - len(key)) + 1):
                window = h[i:i + len(key)]
                r = difflib.SequenceMatcher(None, key, window).ratio()
                if r > best_r:
                    best_r, best_g = r, int(g["id"])
        if best_r >= 0.6:
            return best_g
    gid, best = 0, -1.0
    for g in groups:
        last = db.group_last_message(int(g["id"]))
        ts = float((last or {}).get("created_at") or 0)
        if ts >= best:
            best = ts
            gid = int(g["id"])
    return gid


def _send_group_msg_from_instruction(cid: int, user_text: str, reply_text: str):
    """兜底：主模型没填 group_msg 但对话表明她该去群里说 → flash 生成真正要发的内容并发送"""
    try:
        if not _group_msg_followup(user_text, reply_text):
            return
        gid = _pick_group_for(int(cid), user_text)
        if not gid:
            return
        character = db.get_character(int(cid))
        if not character:
            return
        g = db.get_group(gid) or {}
        try:
            rows = db.get_recent_messages(int(cid), 8)
            hist_lines = []
            for m in rows:
                who = "她" if m.get("role") == "character" else "用户"
                hist_lines.append(f"{who}：{(m.get('content') or '')[:60]}")
        except Exception:
            hist_lines = []
        text = ai.generate_group_msg_from_instruction(character, g.get("name") or "群聊",
                                                      user_text, reply_text, hist_lines)
        if not text:
            _log(f"[group] 兜底判断：这轮无需群发，跳过 cid={cid}")
            return
        _log(f"[group] 兜底补发（模型未填 group_msg） cid={cid} -> {text[:40]!r}")
        _send_group_msg_async(cid, text, user_text)
    except Exception as e:
        _log(f"[group] 兜底群发失败 cid={cid}: {e}", level="WARNING")


def _send_group_msg_async(cid: int, text: str, hint: str = ""):
    """私聊里角色决定去群里说一句：延迟几秒发送（模拟切应用）；群已删则放弃。
    - hint：用户原话，用于"用户点名群名"时定向选群
    - 发送前统一清洗伪标签 + 保密拦截（私密内容绝不外发到群）
    发送后小概率引发群内另一角色接话（走群每日额度与 flash 模型，费用可控）"""
    try:
        text = ai.scrub_user_ref(ai.strip_wait_tags(ai.clean_meta_time(ai._strip_fake_tags((text or "").strip()))), "你")
        if not text:
            return
        if _world_is_secret(text):
            _log(f"[group] 群发内容命中保密词，已拦截 cid={cid}: {text[:30]!r}", level="WARNING")
            return
        gid = _pick_group_for(int(cid), hint)
        if not gid:
            return
        time.sleep(random.uniform(2.5, 6.0))  # 模拟切到群聊再发
        if not db.get_group(gid):
            return
        db.add_group_message(gid, "character", int(cid), "text", text[:200])
        _log(f"[group] {cid} 从私聊转到 #{gid}: {text[:40]!r}")
        threading.Thread(target=_world_recognize_async, args=(0, text, cid, gid), daemon=True).start()
        # 私聊分享到群后：大概率引发另一位角色接话（强制一轮，群忙则等待重试）
        _group_chain_when_idle(gid, int(cid))
    except Exception as e:
        _log(f"[group] 私聊触发群聊发言失败 cid={cid}: {e}", level="WARNING")


# ============ 主动回复 / 动态闹铃 ============

_wake_lock = threading.Lock()


def _as_bool(v, default=True):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "off")
    return default


def _safe_temperature(v, default=0.9):
    """非有限值/脏值的温度兜底（防止历史脏数据让接口 500）"""
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def _upload_too_big(f: UploadFile, limit: int) -> bool:
    """上传前用 size 预检，避免把超大文件整体读进内存"""
    try:
        return bool(f.size and f.size > limit)
    except Exception:
        return False


async def _save_upload_stream(f: UploadFile, dst_path: str, limit: int) -> int:
    """流式落盘（不把整个文件读进内存），返回写入字节数；超限/异常返回 -1 并清理半成品。
    用于朋友圈多文件上传（最多 9 个文件，原实现逐个整读内存，峰值可达数百 MB）。"""
    total = 0
    try:
        with open(dst_path, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    out.close()
                    try:
                        os.remove(dst_path)
                    except Exception:
                        pass
                    return -1
                out.write(chunk)
    except Exception:
        try:
            os.remove(dst_path)
        except Exception:
            pass
        return -1
    return total


def _wake_cfg():
    """主动回复全局配置（管理页设置，默认与 主动联系一致）"""
    try:
        s = db.get_settings()
    except Exception:
        s = {}

    def _num(key, default):
        try:
            return float(s.get(key))
        except Exception:
            return default

    return {
        "enabled": _as_bool(s.get("wake_enabled", 1), True),
        "silence": max(1, int(_num("wake_silence", 3))),
        "tz": min(max(_num("wake_tz", 8), -12.0), 14.0),  # 钳制时区，防 timedelta 溢出
        "daily_max": min(5, max(0, int(_num("wake_daily_max", 1)))),  # 每日主动联系上限（0=不主动；0~5）
    }


def _wake_local_now(cfg: dict):
    return datetime.now(timezone.utc) + timedelta(hours=cfg["tz"])


def _time_of_day_text(hour: int) -> str:
    """给 AI 的时段语感（也用于日志）"""
    if 0 <= hour < 5:
        return "凌晨"
    if 5 <= hour < 8:
        return "早上"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "晚上"
    return "深夜"


def _wake_schedule_after_reply(cid: int):
    """角色回复后：取消旧闹铃、重置沉默倒计时（聊天真正结束后才考虑主动联系）"""
    try:
        cfg = _wake_cfg()
        if not cfg["enabled"]:
            return
        with _wake_lock:
            chat = db.get_chat(cid)
            if not chat.get("wake_enabled"):
                return
            now = time.time()
            db.update_chat(cid, wake_pending_ts=now + cfg["silence"] * 60,
                           wake_last_reply_ts=now, wake_ts=0, wake_topic="", wake_retry=0)
    except Exception as e:
        _log(f"[wake] 设置沉默倒计时失败 cid={cid}: {e}", level="WARNING")


def _wake_cancel(cid: int):
    """用户发消息/关闭开关：取消闹铃与倒计时（与闹铃线程共用锁，避免竞态）"""
    try:
        with _wake_lock:
            db.cancel_wake(cid)
    except Exception as e:
        _log(f"[wake] 取消失败 cid={cid}: {e}", level="WARNING")


def _wake_since_desc(chat: dict) -> str:
    last = float(chat.get("wake_last_reply_ts") or 0)
    if last <= 0:
        return "一段时间"
    diff = max(0.0, time.time() - last)
    if diff < 3600:
        return f"{int(diff / 60)}分钟"
    return f"{diff / 3600:.1f}小时"


def _wake_fire(cid: int, character: dict, cfg: dict, topic: str):
    """闹铃到点：生成一条自然的主动消息并投递（失败 30 分钟后重试，最多 2 次）"""
    started = time.time()
    try:
        chat = db.get_chat(cid)
        history = db.get_recent_messages(cid, _cfg()["max_history"])
        local_now = _wake_local_now(cfg)
        _wd = "一二三四五六日"[local_now.weekday()]
        # 主动回忆：优先用一条"久未提及"的私聊往事开场（没有则走普通主动联系）
        _mem_extra = ""
        try:
            _pick = memory_kb.pick_proactive(cid)
            if _pick.get("content"):
                _date = time.strftime("%m-%d", time.localtime(float(_pick.get("ts") or 0)))
                _mem_extra = (
                    "【主动回忆（可选）】你忽然想起一件旧事：\n- [%s] %s\n"
                    "如果此刻提起它显得自然，就用它作为开场（用你自己的话，不要生硬复述原文，"
                    "也绝对不要提「记忆」「记录」这类词）；如果不合适，就按下面的时机自然开场。"
                    % (_date, str(_pick.get("content"))[:160]))
                _log(f"[memory] 主动回忆选材 mid={_pick.get('mid')} 日期={_date}")
        except Exception:
            _mem_extra = ""
        result = ai.generate_wake(
            character, chat, history, topic,
            _wake_since_desc(chat),
            f"{local_now.strftime('%m月%d日')}（星期{_wd}）{local_now.strftime('%H:%M')}",
            _time_of_day_text(local_now.hour),
            extra=_mem_extra,
        )
        # 生成期间有新消息（用户发言/新回复）→ 放弃本次主动联系，避免"抢话"
        last = db.get_last_message(cid)
        if last and float(last.get("created_at") or 0) >= started:
            _log(f"[wake] 生成期间有新消息，放弃本次主动联系 cid={cid}")
            db.update_chat(cid, wake_retry=0)
            return True
        if db.has_pending(cid):
            _log(f"[wake] 已有待处理回复，放弃本次主动联系 cid={cid}")
            db.update_chat(cid, wake_retry=0)
            return True
        reply = (result.get("reply") or "").strip()
        if not reply or reply.startswith(f"（{character.get('name')}似乎没有回复"):
            raise RuntimeError("主动联系生成为空")
        if not ai.strip_wait_tags(reply).strip():
            raise RuntimeError("主动联系只有等待标记、无正文")
        _dispatch_reply(cid, result, _cfg())
        # 主动消息里如果也有承诺/约定（如"晚上见"），同样记入长期一致性记忆
        threading.Thread(target=_commit_extract_async, args=(cid, "", reply), daemon=True).start()
        # 主动消息里如果有重要事件，同样记入重要记忆
        threading.Thread(target=_longmem_extract_async, args=(cid, "", reply), daemon=True).start()
        # 同一世界广播：每条消息都结合上下文识别世界事件
        threading.Thread(target=_world_recognize_async, args=(cid, reply, cid), daemon=True).start()
        # 主动消息后不设沉默倒计时（否则会形成 主动发→沉默→订铃→再发 的死循环）
        today = _wake_local_now(cfg).strftime("%Y%m%d")
        ch_now = db.get_chat(cid)
        cnt = int(ch_now.get("wake_count") or 0) if str(ch_now.get("wake_day") or "") == today else 0
        db.update_chat(cid, wake_retry=0, wake_last_reply_ts=time.time(),
                       wake_day=today, wake_count=cnt + 1)
        _log(f"[wake] 主动联系已投递 cid={cid}: {reply[:40]!r}")
        return True
    except Exception as e:
        _log(f"[wake] 主动联系失败 cid={cid}: {e}", level="WARNING")
        try:
            chat2 = db.get_chat(cid)
            retry = int(chat2.get("wake_retry") or 0)
            if retry < 2 and chat2.get("wake_enabled"):
                db.update_chat(cid, wake_ts=time.time() + 30 * 60, wake_retry=retry + 1, wake_topic=topic)
                _log(f"[wake] 30 分钟后重试（{retry + 1}/2）cid={cid}")
        except Exception:
            pass
        return False


def _wake_fire_guarded(cid: int, character: dict, cfg: dict, topic: str):
    """带互斥的主动联系：角色正在处理消息时跳过，避免与用户回复并发生成"""
    lk = _proc_lock_for(cid)
    if not lk.acquire(blocking=False):
        _log(f"[wake] 角色正在处理消息，跳过本次主动联系 cid={cid}")
        return
    try:
        _wake_fire(cid, character, cfg, topic)
    finally:
        lk.release()


def _wake_process_chat(chat: dict, cfg: dict):
    """单个角色的闹铃状态机（异常由调用方兜住，避免拖垮整轮）"""
    if not chat.get("wake_enabled"):
        return
    cid = chat["character_id"]
    character = db.get_character(cid)
    if not character or not character.get("enabled"):
        return
    pending_ts = float(chat.get("wake_pending_ts") or 0)
    wake_ts = float(chat.get("wake_ts") or 0)

    # 1) 沉默倒计时到期 → 聊天结束，AI 判断下次联系时机
    if pending_ts > 0 and time.time() >= pending_ts and wake_ts <= 0:
        with _wake_lock:
            cur = db.get_chat(cid)
            if not (float(cur.get("wake_pending_ts") or 0) > 0
                    and time.time() >= float(cur.get("wake_pending_ts") or 0)
                    and float(cur.get("wake_ts") or 0) <= 0):
                return
            db.update_chat(cid, wake_pending_ts=0)
        recent = db.get_recent_messages(cid, 12)
        local_now = _wake_local_now(cfg)
        # 每日上限（宁少勿多）：今天已主动联系过 N 次 → 不再订铃
        today = local_now.strftime("%Y%m%d")
        chat_now = db.get_chat(cid)
        already = int(chat_now.get("wake_count") or 0) if str(chat_now.get("wake_day") or "") == today else 0
        if already >= cfg["daily_max"]:
            _log(f"[wake] 今日主动联系已达上限（{already}/{cfg['daily_max']}），跳过订铃 cid={cid}")
            return
        # 一律由 AI 判断（含具体钟点/时间约定/当前时间），失败才用兜底间隔
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        tod = _time_of_day_text(local_now.hour)
        delay, topic = ai.judge_wake(character, recent,
                                     local_now.strftime("%m月%d日 %H:%M"),
                                     weekdays[local_now.weekday()], tod,
                                     already_today=already, daily_max=cfg["daily_max"])
        if delay is None:
            # AI 判断失败兜底：1~4 小时的普通间隔
            delay, topic = random.randint(60, 240), ""
        delay = max(1, min(int(delay), 10080))  # 钳制 1 分钟 ~ 7 天（"下周一/后天"等约定不再被截成 24h）
        with _wake_lock:
            latest = db.get_chat(cid)
            if float(latest.get("wake_pending_ts") or 0) > 0:
                _log(f"[wake] 判断期间有新对话，跳过订铃 cid={cid}")
                return
            if db.has_pending(cid):
                _log(f"[wake] 判断期间有回复待处理，跳过订铃 cid={cid}")
                return
            db.update_chat(cid, wake_ts=time.time() + delay * 60, wake_topic=topic, wake_retry=0)
            _log(f"[wake] 已订闹铃 cid={cid} {delay} 分钟后 topic={topic!r}")
        return

    # 2) 闹铃到点 → 触发主动联系（落地前复查：上限调小/关 0 后，已排期的也不再发）
    if wake_ts > 0 and time.time() >= wake_ts:
        topic = chat.get("wake_topic") or ""
        with _wake_lock:
            cur = db.get_chat(cid)
            if float(cur.get("wake_ts") or 0) <= 0:
                return
            today = _wake_local_now(cfg).strftime("%Y%m%d")
            already = int(cur.get("wake_count") or 0) if str(cur.get("wake_day") or "") == today else 0
            if already >= cfg["daily_max"]:
                db.update_chat(cid, wake_ts=0, wake_topic="")
                _log(f"[wake] 闹铃到点但今日已达上限（{already}/{cfg['daily_max']}），取消发送 cid={cid}")
                return
            db.update_chat(cid, wake_ts=0, wake_topic="")
        # 后台执行：主动联系含模型生成，不能阻塞 _ai_loop；内部与回复生成互斥（防抢话）
        threading.Thread(target=_wake_fire_guarded, args=(cid, character, cfg, topic), daemon=True).start()


_wake_global_prev = None
_last_prune_ts = 0.0


def _prune_runtime_dicts():
    """定期清理「只增」的运行期字典（限频/失败时间戳），防长期运行内存缓慢膨胀"""
    global _last_prune_ts
    now = time.time()
    if now - _last_prune_ts < 3600:
        return
    _last_prune_ts = now
    cutoff = now - 24 * 3600
    for d in (_world_last_call, _moment_plan_fail_ts, _group_revive_roll):
        try:
            for k in [k for k, v in list(d.items()) if float(v or 0) < cutoff]:
                d.pop(k, None)
        except Exception:
            pass


def _wake_tick():
    """一轮检查：沉默倒计时到期 → AI 判断订铃；闹铃到点 → 触发主动联系"""
    global _wake_global_prev
    _prune_runtime_dicts()
    cfg = _wake_cfg()
    if not cfg["enabled"]:
        _wake_global_prev = False
        return
    if _wake_global_prev in (False, None):
        # 全局开关从关→开：过期闹铃错峰到近期，避免多角色集中补发
        try:
            for ch in db.list_chats():
                ts = float(ch.get("wake_ts") or 0)
                if ts > 0 and ts <= time.time():
                    db.update_chat(ch["character_id"], wake_ts=time.time() + random.randint(60, 600))
        except Exception:
            pass
    _wake_global_prev = True
    try:
        chats = db.list_chats()
    except Exception:
        return
    for chat in chats:
        try:
            _wake_process_chat(chat, cfg)
        except Exception as e:
            _log(f"[wake] 处理角色 {chat.get('character_id')} 异常: {e}", level="WARNING")


def _process_all_pending():
    """后台批量处理到期队列：每个角色独立线程（per-cid 锁防重），互不阻塞"""
    try:
        chars = db.list_characters(include_disabled=False)
    except Exception:
        return
    for c in chars:
        try:
            threading.Thread(target=_process_pending, args=(int(c["id"]),), daemon=True).start()
        except Exception as e:
            _log(f"[chat] 后台处理队列异常 cid={c.get('id')}: {e}", level="WARNING")


_last_settings_cleanup = 0.0


def _settings_cleanup_tick():
    """每 6 小时清一次过期的语音配额键（settings 表防膨胀）"""
    global _last_settings_cleanup
    now = time.time()
    if now - _last_settings_cleanup < 6 * 3600:
        return
    _last_settings_cleanup = now
    try:
        n = db.cleanup_voice_quota(7)
        if n:
            _log(f"[settings] 清理过期语音配额键 {n} 条")
    except Exception as e:
        _log(f"[settings] 配额键清理失败: {e}", level="WARNING")
    try:
        n2 = db.mini_events_cleanup(7)
        if n2:
            _log(f"[mini] 清理过期小程序动态 {n2} 条")
    except Exception as e:
        _log(f"[mini] 小程序动态清理失败: {e}", level="WARNING")
    try:
        cleaned = media.cleanup_generated(30)
        if cleaned:
            _log(f"[media] 清理过期未引用媒体文件 {cleaned} 个")
    except Exception as e:
        _log(f"[media] 媒体清理失败: {e}", level="WARNING")


def _wake_loop():
    """快速定时器循环：只做轻量扫描/清理（AI 密集步骤在 _ai_loop，避免互相饿死）"""
    time.sleep(15)
    while True:
        for step in (db.reap_dead_conns, db.cleanup_orphans, _group_tick, _news_tick, _world_cleanup, _settings_cleanup_tick):
            try:
                step()
            except Exception as e:
                _log(f"[wake] 后台步骤 {getattr(step, '__name__', step)} 异常: {e}", level="WARNING")
        time.sleep(30)


def _ai_loop():
    """AI 密集步骤独立线程：闹铃判断/回复生成/朋友圈任务慢也不会拖慢快速定时器"""
    time.sleep(20)
    while True:
        for step in (_wake_tick, _process_all_pending, _moment_tick, _memory_tick):
            try:
                step()
            except Exception as e:
                _log(f"[ai] 后台步骤 {getattr(step, '__name__', step)} 异常: {e}", level="WARNING")
        time.sleep(30)


# ============ 朋友圈（Moments）引擎 ============

DEFAULT_MOMENT_POST_PROMPT = "关于旅游，生活和学习主题来写一段适合朋友圈的说说，简短、有个性、不要解释。"
DEFAULT_MOMENT_COMMENT_PROMPT = "生成一句简短、直接、贴题的评论，不要解释。"
DEFAULT_MOMENT_REPLY_PROMPT = "这条动态收到了一条评论，请自然回复此条评论，不要解释。"


def _moment_cfg():
    try:
        s = db.get_settings()
    except Exception:
        s = {}
    return {
        "post_prompt": str(s.get("moment_post_prompt") or "").strip() or DEFAULT_MOMENT_POST_PROMPT,
        "comment_prompt": str(s.get("moment_comment_prompt") or "").strip() or DEFAULT_MOMENT_COMMENT_PROMPT,
        "reply_prompt": str(s.get("moment_reply_prompt") or "").strip() or DEFAULT_MOMENT_REPLY_PROMPT,
    }


_recent_life_cache = {}   # cid -> (last_msg_id, ts, text)
_recent_life_lock = threading.Lock()


def _recent_life_summary(character: dict, chat: dict) -> str:
    """把最近聊天压缩成「近况摘要」（3~6 条要点；1 小时内直接复用缓存）；失败返回空串"""
    try:
        cid = int(character.get("id") or 0)
    except Exception:
        return ""
    if not cid:
        return ""
    try:
        rows = db.get_recent_messages(cid, 40)
    except Exception:
        rows = []
    if not rows:
        return ""
    last_id = int(rows[-1].get("id") or 0)
    now = time.time()
    with _recent_life_lock:
        cached = _recent_life_cache.get(cid)
        if cached:
            c_last, c_ts, c_text = cached
            fresh = (now - c_ts) < 3600 or ((now - c_ts) < 6 * 3600 and c_last == last_id)
            if fresh and c_text:
                return c_text
    name = character.get("name") or "她"
    lines = []
    for r in rows:
        kind = r.get("kind") or "text"
        if kind in ("photo", "image", "video", "sticker"):
            continue
        body = (r.get("content") or "").strip()
        if not body:
            continue
        who = "对方" if r.get("role") == "user" else name
        lines.append(f"{who}：{body[:80]}")
    if len(lines) < 4:
        return ""
    prompt = (
        f"下面是「{name}」和对方最近的聊天记录。请压缩成「她最近的生活近况」（用于她发朋友圈/群聊时保持一致）：\n"
        "1. 输出 3~6 条要点，每条 ≤30 字，短句，不要用第一人称「我」（用她/两人/对方）；\n"
        "2. 覆盖：她最近做了什么、两人之间发生了什么（只写事实）、她的心情、最近的安排；\n"
        "3. 只保留事实与状态，去掉情话原文与氛围描写；不要脑补；与聊天无关的不要写。\n"
        '只输出 JSON：{"items": ["..."]}\n\n'
        "【最近聊天（最新在下）】\n" + "\n".join(lines[-40:])
    )
    content = ""
    try:
        content, err = ai._call_model([{"role": "user", "content": prompt}], model=ai._judge_model(),
                                      temperature=0.3, max_tokens=3000)
        if err or not (content or "").strip():
            content, err = ai._chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=400)
    except Exception:
        content = ""
    text = ""
    if (content or "").strip():
        data = ai._extract_json(content)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items = [ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(str(x).strip())), "对方")[:40]
                     for x in data["items"] if str(x).strip()]
            items = items[:6]
            if items:
                text = "\n".join(f"- {x}" for x in items)
    with _recent_life_lock:
        if text:
            _recent_life_cache[cid] = (last_id, now, text)
        else:
            prev = _recent_life_cache.get(cid)
            if prev and (now - prev[1]) < 6 * 3600:
                return prev[2]  # 生成失败先用旧摘要兜底
            return ""
    return text


def _moment_brief(character: dict, chat: dict, with_recent: bool = True) -> str:
    """朋友圈规划/互动共用的角色档案：人设 + 摘要 + 承诺记忆 + 最近聊天记录"""
    persona = (character.get("persona_full") or "").strip()
    summary = (chat.get("summary") or "").strip()
    parts = []
    if persona:
        parts.append("人物设定（摘录）：\n" + persona[:1200])
    if summary:
        parts.append("你与对方最近的相处摘要：\n" + summary[:800])
    # 承诺/立场记忆（保持一致）
    try:
        commits = _json.loads(chat.get("commit_memory") or "[]")
    except Exception:
        commits = []
    if isinstance(commits, list):
        lines = [str(it.get("t") if isinstance(it, dict) else it).strip() for it in commits]
        lines = [x for x in lines if x][-10:]
        if lines:
            parts.append("你答应/拒绝过、需要保持一致的事：\n" + "\n".join(f"- {x}" for x in lines))
    # 重要记忆（含永久）：让朋友圈与私聊更连贯
    try:
        _lm = _json.loads(chat.get("long_memory") or "[]")
    except Exception:
        _lm = []
    if isinstance(_lm, list):
        _pins, _rest = [], []
        for it in _lm:
            t = str((it.get("t") if isinstance(it, dict) else it) or "").strip()
            if not t:
                continue
            (_pins if isinstance(it, dict) and it.get("pin") else _rest).append(t)
        mem_lines = _pins[-8:] + _rest[-12:]
        if mem_lines:
            parts.append("你一直记得的重要的事（保持一致）：\n" + "\n".join(f"- {x}" for x in mem_lines))
    # 她自己最近发过的朋友圈（防跨天重复/自我矛盾）
    try:
        cid_m = int(character.get("id") or 0)
        own = db.recent_character_moments(cid_m, 5) if cid_m else []
    except Exception:
        own = []
    if own:
        parts.append("你最近发过的朋友圈（不要重复主题、不要自我矛盾）：\n"
                     + "\n".join(f"- {str(o.get('content') or '').strip()[:60]}"
                                 for o in own if str(o.get('content') or '').strip()))
    # 最近相处近况（压缩摘要，避免大段原文噪音；失败时退回最近 8 条原文）
    if with_recent:
        try:
            life = _recent_life_summary(character, chat)
        except Exception:
            life = ""
        if life:
            parts.append("你们最近的相处近况（摘要，保持一致）：\n" + life)
        else:
            try:
                cid = int(character.get("id") or 0)
                rows = db.get_recent_messages(cid, 8) if cid else []
            except Exception:
                rows = []
            name = character.get("name") or "她"
            lines = []
            for r in rows:
                kind = r.get("kind") or "text"
                if kind == "sticker":
                    body = "[表情]"
                elif kind == "voice":
                    body = "[语音]" + (r.get("content") or "")
                elif kind == "photo":
                    body = "[照片]"  # 生图提示词属内部字段，不注入
                elif kind in ("image", "video"):
                    body = "[图片/视频]" + (r.get("content") or "")
                else:
                    body = (r.get("content") or "").strip()
                if not body:
                    continue
                who = "对方" if r.get("role") == "user" else name
                lines.append(f"{who}：{body[:80]}")
            if lines:
                parts.append("你们最近的聊天（最新在下）：\n" + "\n".join(lines[-8:]))
    # 群聊感知（持续注入：她所在的群与最近群消息）
    try:
        gt = ai._group_context_text(int(character.get("id") or 0))
    except Exception:
        gt = ""
    if gt:
        parts.append(gt)
    # 同一世界的共享动态
    try:
        wt = ai._world_context_text(int(character.get("id") or 0))
    except Exception:
        wt = ""
    if wt:
        parts.append(wt)
    return "\n\n".join(parts)


def _moment_visible(m: dict, cid: int) -> bool:
    """该角色能否看到这条朋友圈：visible_to 非空=仅这些人可见；hidden_from 优先排除"""
    def _ids(key):
        try:
            arr = _json.loads(m.get(key) or "[]")
        except Exception:
            arr = []
        if not isinstance(arr, list):
            return set()
        out = set()
        for x in arr:
            try:
                v = int(x)
            except Exception:
                continue
            if v > 0:
                out.add(v)
        return out

    if cid in _ids("hidden_from"):
        return False
    vis = _ids("visible_to")
    if vis:
        return cid in vis
    return True


def _moment_daily_max() -> int:
    """每天自动发朋友圈条数上限（管理页可调，默认 2 条，范围 1~5）"""
    try:
        return min(max(int(float(db.get_settings().get("moment_daily_max", 2))), 1), 5)
    except Exception:
        return 2


def _moment_plan_ai(character: dict, chat: dict, local_now, max_posts: int = 1) -> dict:
    """清晨决策：今天发不发、发几条、几点发、各写什么方向"""
    if max_posts <= 1:
        prompt = (
            f"你是「{character.get('name')}」的日程助手。现在是 {local_now.strftime('%m月%d日 %H:%M')}。\n"
            "请结合她的生活与状态，决定她今天要不要发朋友圈、大概几点发、写什么方向。\n"
            "要求：严格按她的人设过真实的一天（身份/年龄/性格/说话习惯都按人设来，不要套模板）；"
            "内容要和她最近与对方的相处【强相关】（参考下方档案里的最近聊天/重要记忆/摘要），像她这种人真会发的内容；"
            "默认每天发 1 条——用她日常的真实素材就能发；只有在完全没有任何可发内容时才不发；"
            "时间是 09:00~23:00 之间的整点或半点。\n"
            "只输出 JSON：{\"post\": true/false, \"time\": \"HH:MM\", \"topic\": \"方向（10字内）\"}\n\n"
            + _moment_brief(character, chat)
        )
    else:
        prompt = (
            f"你是「{character.get('name')}」的日程助手。现在是 {local_now.strftime('%m月%d日 %H:%M')}。\n"
            f"请结合她的生活与状态，决定她今天要不要发朋友圈、发几条（1~{max_posts} 条）、每条大概几点发、各写什么方向。\n"
            f"要求：严格按她的人设过真实的一天，内容要和她最近与对方的相处【强相关】（参考下方档案）；"
            f"默认至少发 1 条（最多 {max_posts} 条）——用日常素材就能发，只有完全没有可发内容时才不发；每条时间在 09:00~23:00 之间，"
            "彼此间隔至少 2 小时，方向各不相同（不要重复主题，也不要把同一件事拆成几条）。\n"
            "只输出 JSON："
            "{\"post\": true/false, \"posts\": [{\"time\": \"HH:MM\", \"topic\": \"方向（10字内）\"}, ...]}\n\n"
            + _moment_brief(character, chat)
        )
    content, err = ai._call_model([{"role": "user", "content": prompt}], model=ai._judge_model(),
                                  temperature=0.7, max_tokens=16000)
    if err or not (content or "").strip():
        return None  # 失败（与"今天不发"区分）：调用方不落盘、下轮重试
    try:
        start, end = content.find("{"), content.rfind("}") + 1
        return _json.loads(content[start:end]) or {}
    except Exception:
        return None


def _moment_write_post(character: dict, chat: dict, topic: str) -> str:
    cfg = _moment_cfg()
    prompt = (
        f"你是「{character.get('name')}」。请以她的身份写一条朋友圈动态（纯文字，不配图）：\n"
        "- 严格保持人设（身份/年龄/性格/说话习惯）；内容要和她最近的生活、和对方的相处【强相关】，像她随手发的真实动态；\n"
        f"- 主题参考：{cfg['post_prompt']}\n"
        f"- 今天的方向：{topic or '日常'}\n"
        "- 简短、自然、有个性，1~2 句，不超过 80 字；\n"
        "- 不要解释、不要标签、不要提到 AI/系统；只输出动态正文。\n"
        + ai.time_context_text()
        + "\n" + _moment_brief(character, chat)
    )
    model = character.get("model") or ai._model_cfg()["ark"]
    content, err = ai._call_model([{"role": "user", "content": prompt}], model=model, temperature=0.9, max_tokens=500)
    if err or not (content or "").strip() or ai._is_refusal(str(content or "")):
        content, err = ai._chat([{"role": "user", "content": prompt}], temperature=0.9, max_tokens=500)
    text = (content or "").strip().strip('"“”').replace("\n", " ")
    text = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(text))[:120], "对方")
    if text and ai._is_refusal(text):
        _log(f"[moment] 动态被模型拒答，放弃本条 cid={character.get('id')}", level="WARNING")
        return ""
    return text


def _moment_eval_user_post(character: dict, chat: dict, moment: dict) -> dict:
    """每个角色独立评估：赞不赞、回不回、多久回、回什么"""
    cfg = _moment_cfg()
    prompt = (
        f"你是「{character.get('name')}」。对方刚发了一条朋友圈：\n"
        f"「{moment.get('content') or '（图片）'}」\n\n"
        "结合你和他/她的关系与最近相处，决定：\n"
        "1. 点赞吗？（like: true/false）\n"
        "2. 评论吗？（comment: true/false；不想评论就把 comment_text 留空）\n"
        "   提示：除非你完全不想理这条动态，否则至少点个赞；评论可以非常简短（几个字也可以）\n"
        "3. 多久之后回应？（delay_minutes：1~720，像真人一样自然，别太秒回）\n"
        "4. 评论内容（comment_text：一句短短的话，符合你的性格，≤60字，不要解释）\n"
        f"评论语气参考：{cfg['comment_prompt']}\n"
        '只输出 JSON：{"like": true/false, "comment": true/false, "delay_minutes": 数字, "comment_text": "..."}\n\n'
        + _moment_brief(character, chat)
    )
    content, err = ai._call_model([{"role": "user", "content": prompt}], model=ai._judge_model(),
                                  temperature=0.8, max_tokens=500)
    if err or not (content or "").strip():
        # 评估模型失败 → 用默认模型兜底一次，避免静默跳过互动
        content, err = ai._chat([{"role": "user", "content": prompt}], temperature=0.8, max_tokens=500)
    if err or not (content or "").strip():
        _log(f"[moment] 互动评估模型无输出 cid={character.get('id')}: {err}", level="WARNING")
        return {}
    try:
        start, end = content.find("{"), content.rfind("}") + 1
        return _json.loads(content[start:end]) or {}
    except Exception:
        return {}


def _moment_write_reply(character: dict, chat: dict, comment: str, moment_text: str = "") -> str:
    cfg = _moment_cfg()
    prompt = (
        f"你是「{character.get('name')}」。你发了一条朋友圈：\n「{moment_text or '（你发的内容）'}」\n"
        f"现在收到了这条评论：\n「{comment}」\n"
        f"请自然回复这条评论：{cfg['reply_prompt']}\n"
        "（保持你的人设与最近相处的一贯语气，像平时一样自然）\n"
        "一句话，≤50 字，符合你的性格；只输出回复内容。\n\n"
        + _moment_brief(character, chat)
    )
    model = character.get("model") or ai._model_cfg()["ark"]
    content, err = ai._call_model([{"role": "user", "content": prompt}], model=model, temperature=0.9, max_tokens=400)
    if err or not (content or "").strip() or ai._is_refusal(str(content or "")):
        content, err = ai._chat([{"role": "user", "content": prompt}], temperature=0.9, max_tokens=400)
    text = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags((content or "").strip().strip('"“”')))[:80], "对方")
    if text and ai._is_refusal(text):
        _log(f"[moment] 回评被模型拒答，放弃本条 cid={character.get('id')}", level="WARNING")
        return ""
    return text


def _moment_schedule_react(moment: dict):
    """用户发帖后：可见的角色把动态记进主对话上下文；开启互动的角色独立评估并排期"""
    try:
        chars = db.list_characters(include_disabled=False)
    except Exception:
        chars = []
    for ch in chars:
        try:
            cid = ch["id"]
            if not _moment_visible(moment, cid):
                continue  # 不让看的直接不触发
            chat = db.get_chat(cid)
            if not chat.get("moments_react"):
                continue
            verdict = _moment_eval_user_post(ch, chat, moment)
            if not verdict:
                _log(f"[moment] 互动评估为空（跳过）cid={cid}")
                continue
            try:
                delay = int(float(verdict.get("delay_minutes") or 30))
            except Exception:
                delay = 30
            try:
                cap = int(float(db.get_settings().get("moment_react_delay_max", 720)))
            except Exception:
                cap = 720
            cap = max(1, min(cap, 1440))
            delay = max(1, min(delay, cap)) * 60
            like = _as_bool_soft(verdict.get("like"), False)
            do_comment = _as_bool_soft(verdict.get("comment"), False)
            text = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(str(verdict.get("comment_text") or "").strip()))[:80], "对方")
            if text and ai._is_refusal(text):
                _log(f"[moment] 评论为拒答文本，跳过评论 cid={cid}: {text[:30]!r}", level="WARNING")
                text = ""
            if text and _world_is_secret(text):
                _log(f"[moment] 评论命中保密词，跳过评论 cid={cid}: {text[:30]!r}", level="WARNING")
                text = ""
            # 兜底：角色没选择任何互动时，至少点个赞（管理页可关）
            try:
                _force_like = str(db.get_settings().get("moment_force_like", 1)) not in ("0", "False", "false", "")
            except Exception:
                _force_like = True
            if _force_like and not like and not (do_comment and text):
                like = True
                _log(f"[moment] 兜底点赞（模型未选择互动）cid={cid}")
            if like:
                db.add_moment_task(cid, "char_like", time.time() + delay, moment_id=moment["id"])
            if do_comment and text:
                db.add_moment_task(cid, "char_comment", time.time() + delay, moment_id=moment["id"],
                                   payload={"content": text})
            _log(f"[moment] 互动已排期 cid={cid} like={like} comment={do_comment and bool(text)} {delay // 60}min 后")
        except Exception as e:
            _log(f"[moment] 互动评估失败 cid={ch.get('id')}: {e}", level="WARNING")


def _moment_do_task(t: dict):
    kind = t["kind"]
    cid = int(t["character_id"] or 0)
    mid = int(t["moment_id"] or 0)
    try:
        payload = _json.loads(t.get("payload") or "{}")
    except Exception:
        payload = {}
    character = db.get_character(cid) if cid else None
    chat = db.get_chat(cid) if cid else {}
    alive = bool(character and character.get("enabled"))
    if kind == "auto_post":
        if not alive or not chat.get("moments_auto"):
            return
        try:
            today = _wake_local_now(_wake_cfg()).strftime("%Y-%m-%d")
        except Exception:
            today = ""
        if payload.get("date") and today and payload.get("date") != today:
            _log(f"[moment] 跳过过期计划（{payload.get('date')}）cid={cid}")
            return
        content = _moment_write_post(character, chat, payload.get("topic") or "")
        if content and _world_is_secret(content):
            _log(f"[moment] 动态命中保密词，已拦截 cid={cid}: {content[:30]!r}", level="WARNING")
            content = ""
        if content:
            db.add_moment("character", cid, content, [], source="auto")
            _log(f"[moment] 自动发朋友圈 cid={cid}: {content[:40]!r}")
    elif kind == "char_like":
        if mid and alive and chat.get("moments_react") and db.get_moment(mid):
            db.add_moment_like(mid, cid)
            db.set_moment_like(mid, "character", 1)
    elif kind == "char_comment":
        text = (payload.get("content") or "").strip()
        if text and _world_is_secret(text):
            _log(f"[moment] 评论命中保密词，已拦截 cid={cid}: {text[:30]!r}", level="WARNING")
            text = ""
        if mid and text and alive and chat.get("moments_react") and db.get_moment(mid):
            db.add_moment_comment(mid, "character", text, character_id=cid)
            _log(f"[moment] 角色评论了动态 {mid}: {text[:40]!r}")
    elif kind == "char_reply":
        comment = (payload.get("comment") or "").strip()
        if mid and comment and alive and chat.get("moments_react") and db.get_moment(mid):
            _m = db.get_moment(mid) or {}
            content = _moment_write_reply(character, chat, comment, (_m.get("content") or "").strip())
            if content and _world_is_secret(content):
                _log(f"[moment] 回评命中保密词，已拦截 cid={cid}: {content[:30]!r}", level="WARNING")
                content = ""
            if content:
                db.add_moment_comment(mid, "character", content, reply_to=int(payload.get("comment_id") or 0), character_id=cid)
                _log(f"[moment] 角色回复了评论 {mid}: {content[:40]!r}")


_moment_plan_fail_ts = {}
_moment_plan_fails = {}   # cid -> 连续失败次数（超过阈值改用默认计划，防止无限重试烧 token）
_moment_running = set()
_moment_running_lock = threading.Lock()


def _moment_run_task(t: dict):
    """执行朋友圈任务：先执行后删除（崩溃任务可重试）；内存占位防同任务重复执行"""
    try:
        tid = int(t["id"])
    except Exception:
        return
    with _moment_running_lock:
        if tid in _moment_running:
            return
        _moment_running.add(tid)
    try:
        _moment_do_task(t)
    except Exception as e:
        _log(f"[moment] 任务失败 {t.get('kind')}: {e}", level="WARNING")
    finally:
        with _moment_running_lock:
            _moment_running.discard(tid)
        try:
            db.delete_moment_task(tid)
        except Exception:
            pass


def _moment_tick():
    """朋友圈定时引擎：清晨规划 + 到期任务（由后台循环调用）"""
    cfg = _wake_cfg()
    local_now = _wake_local_now(cfg)
    if local_now.hour * 60 + local_now.minute >= 5 * 60 + 30:
        date_key = local_now.strftime("%Y-%m-%d")
        try:
            chars = db.list_characters(include_disabled=False)
        except Exception:
            chars = []
        for ch in chars:
            try:
                cid = ch["id"]
                chat = db.get_chat(cid)
                if not chat.get("moments_auto"):
                    continue
                if db.get_moment_plan(date_key, cid):
                    continue
                if _moment_plan_fail_ts.get(cid, 0) > time.time():
                    continue  # 规划失败退避 10 分钟，避免每 30 秒高频重试
                plan = _moment_plan_ai(ch, chat, local_now, _moment_daily_max())
                if plan is None or "post" not in plan:
                    _fails = _moment_plan_fails.get(cid, 0) + 1
                    _moment_plan_fails[cid] = _fails
                    if _fails >= 3:
                        plan = {"post": True, "time": "17:30", "topic": "日常"}
                        _log(f"[moment] 规划连续失败 {_fails} 次，改用默认计划（17:30 日常）cid={cid}")
                    else:
                        _moment_plan_fail_ts[cid] = time.time() + 600
                        continue  # 失败/解析缺字段不落盘，退避后重试（避免把失败当成"今天不发"）
                else:
                    _moment_plan_fails.pop(cid, None)
                post = plan.get("post")
                if isinstance(post, str):
                    post = post.strip().lower() in ("true", "1", "yes", "是")
                if not post:
                    db.upsert_moment_plan(date_key, cid, planned=0)
                    _log(f"[moment] 今日不发朋友圈 cid={cid}")
                    continue
                # 解析 1~N 条计划（兼容单条格式），规范化时间、去重、按上限截断
                max_n = _moment_daily_max()
                raw = plan.get("posts")
                entries = []
                if isinstance(raw, list):
                    for it in raw:
                        if isinstance(it, dict):
                            entries.append((str(it.get("time") or ""), str(it.get("topic") or "")[:30]))
                if not entries:
                    entries = [(str(plan.get("time") or "17:30"), str(plan.get("topic") or "")[:30])]
                slots = []
                for t_str, topic in entries[:max_n + 2]:
                    try:
                        hh, mm = map(int, t_str.split(":"))
                        if not (0 <= hh <= 23 and 0 <= mm <= 59):
                            continue
                    except Exception:
                        continue
                    if any(abs((hh * 60 + mm) - (h * 60 + m)) < 45 for h, m, _ in slots):
                        continue  # 间隔过近的时段去掉
                    slots.append((hh, mm, topic))
                slots.sort()
                slots = slots[:max_n]
                if not slots:
                    slots = [(17, 30, "")]
                times_txt = ",".join("%02d:%02d" % (h, m) for h, m, _ in slots)
                topics_txt = " | ".join(t for _, _, t in slots if t)[:200]
                db.upsert_moment_plan(date_key, cid, planned=1, post_time=times_txt[:60], topic=topics_txt)
                day_end = local_now.replace(hour=23, minute=0, second=0, microsecond=0)
                scheduled = 0
                for hh, mm, topic in slots:
                    target_local = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    if target_local <= local_now:
                        target_local = local_now + timedelta(minutes=30 + 60 * scheduled)
                    if target_local > day_end:
                        continue
                    due = time.time() + (target_local - local_now).total_seconds()
                    db.add_moment_task(cid, "auto_post", due, payload={"topic": topic, "date": date_key})
                    scheduled += 1
                    _log(f"[moment] 今日计划：{target_local.strftime('%H:%M')} 发朋友圈（方向：{topic or '日常'}）cid={cid}")
                if scheduled == 0:
                    # 时段都已过/超出窗口：顺延明早 09:00 一条，避免今天完全没动态
                    nxt = (local_now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                    due = time.time() + (nxt - local_now).total_seconds()
                    db.add_moment_task(cid, "auto_post", due, payload={"topic": slots[0][2], "date": nxt.strftime("%Y-%m-%d")})
                    _log(f"[moment] 时段已过，顺延明早 09:00 cid={cid}")
            except Exception as e:
                _log(f"[moment] 规划异常 cid={ch.get('id')}: {e}", level="WARNING")
    try:
        due_tasks = db.get_due_moment_tasks(time.time())
    except Exception:
        due_tasks = []
    for t in due_tasks:
        # 异步执行：任务内的模型调用不再阻塞 _ai_loop（防闹铃/待处理扫描漂移）
        threading.Thread(target=_moment_run_task, args=(t,), daemon=True).start()


@app.on_event("startup")
def _start_wake_loop():
    if os.environ.get("STORIES_NO_WAKE_LOOP") == "1":
        return
    _log(f"[wake] 动态闹铃循环启动（全局启用={_wake_cfg()['enabled']}）")
    threading.Thread(target=_wake_loop, daemon=True).start()
    threading.Thread(target=_ai_loop, daemon=True).start()


def _process_pending(cid: int):
    lk = _proc_lock_for(cid)
    if not lk.acquire(blocking=False):
        return
    try:
        _process_pending_locked(cid)
    finally:
        lk.release()


def _process_pending_locked(cid: int):
    character = db.get_character(cid)
    if not character:
        return
    cfg = _cfg()
    due = db.get_due_pending(cid, cfg["reply_delay"])
    for q in due:
        started = time.time()
        try:
            _process_one(cid, q, cfg)
        except Exception as e:
            logger.log_exc(f"[chat] 处理队列任务失败 id={q['id']} kind={q.get('kind')}: {e}")
            # 生成回复失败时，给用户一个可见提示；但如果部分内容已经投递出去则不再补提示，避免"真实回复+报错"同时出现
            if q.get("kind") == "reply":
                last = db.get_last_message(cid)
                already = bool(last and last.get("role") == "character"
                               and float(last.get("created_at") or 0) >= started)
                if not already:
                    try:
                        db.add_message(cid, "character", "text", "（……信号好像出了点问题，你刚才说的我没听清，再说一次好吗？）")
                    except Exception:
                        pass
                _wake_schedule_after_reply(cid)  # 失败也要恢复沉默倒计时，否则主动回复功能停摆
        finally:
            # 处理完（或明确失败）才删任务；进程被杀时任务保留，重启后会重试（配合 _process_one 的防重检查）
            try:
                db.delete_pending(q["id"])
            except Exception:
                pass


def _prepare_image_context(last_user: dict, chat: dict):
    """处理图片/视频/语音消息的上下文：返回 (user_text, image_path, audio_path)。
    - 图片/视频：角色开了转述（或视频）→ 转成文字进 user_text；关了 → 原文件交多模态主模型
    - 语音：转述开启 → 用转述文本（缺失时补转述）；关闭 → 原音频交主模型"听"语气"""
    user_text = last_user.get("content") or ""
    kind = last_user.get("kind")
    if kind not in ("image", "video", "voice"):
        return user_text, None, None
    if kind == "voice":
        if ai._voice_desc_enabled(chat):
            if not user_text.strip():
                path = media.media_fs_path(last_user.get("media_url"))
                if path:
                    desc = (media.transcribe_audio(path) or "").strip()
                    desc = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(desc)), "对方")
                    if desc:
                        db.update_message(last_user["id"], content=desc)
                        user_text = desc
                        _log(f"[chat] 语音补转述 msg={last_user['id']} desc={desc[:30]!r}")
            return (user_text or "（对方发来一段语音）"), None, None
        audio_path = media.media_fs_path(last_user.get("media_url"))
        if not audio_path and not user_text:
            return "（对方发来一段语音）", None, None
        return user_text, None, audio_path
    is_video = kind == "video"
    if is_video or ai._image_desc_enabled(chat):
        desc = (last_user.get("img_desc") or "").strip()
        if not desc:
            path = media.media_fs_path(last_user.get("media_url"))
            if path:
                desc = ((media.understand_video(path) if is_video else media.describe_image(path)) or "").strip()
                desc = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(desc)), "对方")
                if desc:
                    db.update_message(last_user["id"], img_desc=desc)
                    _log(f"[chat] {'视频' if is_video else '图片'}描述已生成 msg={last_user['id']} desc={desc[:40]!r}")
        label = "一段视频" if is_video else "一张图片"
        if desc:
            user_text = (user_text + f"\n[对方发来{label}，内容是：" + desc + "]").strip()
        elif not user_text:
            user_text = f"（对方发来{label}）"
        return user_text, None, None
    image_path = media.media_fs_path(last_user.get("media_url"))
    if not image_path and not user_text:
        user_text = "（对方发来一张图片）"
    return user_text, image_path, None


def _process_one(cid: int, q: dict, cfg: dict):
    character = db.get_character(cid)
    if not character:
        return
    kind = q["kind"]
    chat = db.get_chat(cid)
    if kind == "script":
        db.add_message(cid, "character", "text", q["content"])
        return
    if kind == "voice":
        # 出队时复查语音开关：关掉后降级为文字，内容不丢
        if chat.get("voice_enabled"):
            db.add_message(cid, "character", "voice", q.get("content") or "",
                           q.get("media_url") or "", q.get("duration") or 0)
        else:
            _voice_quota_refund(cid)  # 降级为文字：退还已预留的语音额度
            db.add_message(cid, "character", "text", q.get("content") or "")
        return
    if kind == "sticker":
        if not chat.get("sticker_enabled"):
            return  # 表情开关已关，丢弃
        st = db.get_sticker_by_label(q["content"])
        if st:
            db.add_message(cid, "character", "sticker", st.get("label", ""), f"/uploads/stickers/{st['file']}")
        else:
            # 表情名对不上：不能静默（否则整条回复凭空消失），降级为一句轻文字
            _log(f"[sticker] 表情「{q['content']}」不存在，降级为文字 cid={cid}", level="WARNING")
            db.add_message(cid, "character", "text", "……")
        return
    if kind != "reply":
        return

    ref_id = int(q.get("ref_id") or 0)
    last_user = None
    hist = []
    if ref_id > 0:
        # 防重（崩溃重试场景）：仅当该消息已到回复水位才跳过。
        # 不能用"存在更新的角色消息"判断——异步照片/视频晚到会误吞新消息
        try:
            _done_ref = int((db.get_chat(cid) or {}).get("reply_done_ref") or 0)
        except Exception:
            _done_ref = 0
        if ref_id <= _done_ref:
            _log(f"[chat] 引用消息 {ref_id} 已在回复水位（{_done_ref}）内，跳过重复生成 cid={cid}")
            return
        # 精准对应：以该条用户消息为上下文结尾（消息可能已被管理端删除，删除后放弃本次）
        history = db.get_messages_upto(cid, ref_id, cfg["max_history"] + 50)
        cand = history[-1] if history else None
        if not cand or cand.get("role") != "user":
            _log(f"[chat] 引用消息 {ref_id} 已删除或非用户消息，跳过回复 cid={cid}", level="WARNING")
            return
        last_user = cand
        hist = history[:-1]
        hist = hist[-(cfg["max_history"]):]
    else:
        # 兜底：取最近消息中的最后一条用户消息
        history = db.get_recent_messages(cid, cfg["max_history"] + 2)
        for h in reversed(history):
            if h["role"] == "user":
                last_user = h
                break
        if last_user is None:
            _log(f"[chat] 无可用用户消息，跳过回复 cid={cid}", level="WARNING")
            return
        hist = [h for h in history if not (h["id"] == last_user["id"])]

    if last_user:
        user_text, image_path, audio_path = _prepare_image_context(last_user, chat)
    else:
        user_text, image_path, audio_path = "", None, None

    _log(f"[chat] 生成回复 character={character['name']} ref={ref_id or 'last'} text={user_text[:30]!r}")
    t0 = time.time()
    result = ai.generate_reply(character, chat, hist, user_text, image_path,
                               extra=_extra_instruction(cid, character, chat, user_text),
                               audio_path=audio_path)
    # 生成期间消息被回溯/清空（引用消息已不存在）→ 丢弃本次回复，避免"回溯后补刀"
    if ref_id and not db.get_message(ref_id):
        _log(f"[chat] 生成期间引用消息 {ref_id} 已被删除，丢弃本次回复 cid={cid}", level="WARNING")
        return
    # 模型彻底失败时会返回哨兵句：不要当正常回复发出
    if (result.get("reply") or "").startswith(f"（{character.get('name')}似乎没有回复"):
        _log(f"[chat] 生成为空（哨兵回复），改为提示 cid={cid}", level="WARNING")
        try:
            db.add_message(cid, "character", "text", "（……信号好像出了点问题，你刚才说的我没听清，再说一次好吗？）")
        except Exception:
            pass
        if ref_id:
            db.bump_reply_done(cid, ref_id)
        _wake_schedule_after_reply(cid)
        return
    _log(f"[chat] 回复生成完成 ({time.time()-t0:.1f}s) voice={result.get('voice')} "
         f"photo={bool(result.get('photo_prompt'))} video={bool(result.get('video_prompt'))} sticker={result.get('sticker')}")

    _dispatch_reply(cid, result, cfg, ref_id=ref_id, user_text=user_text)
    if ref_id:
        db.bump_reply_done(cid, ref_id)
    _reminder_count_reply(cid, result.get("reply") or "")
    # 承诺/立场记忆：后台抽取，保证以后不反悔、不改口
    threading.Thread(target=_commit_extract_async, args=(cid, user_text, result.get("reply") or ""), daemon=True).start()
    # 重要记忆：后台抽取（重要事件/关系进展/偏好，长期保留）
    threading.Thread(target=_longmem_extract_async, args=(cid, user_text, result.get("reply") or ""), daemon=True).start()
    # 同一世界广播：每条消息都结合上下文识别世界事件
    threading.Thread(target=_world_recognize_async, args=(cid, result.get("reply") or "", cid), daemon=True).start()

    # 她回复后：取消旧闹铃、重置沉默倒计时（聊天结束 silence 分钟后才考虑主动联系）
    _wake_schedule_after_reply(cid)
    threading.Thread(target=ai.maybe_summarize, args=(cid,), daemon=True).start()


def _photo_async(cid: int, prompt: str, ref_id: int = 0):
    try:
        gen = media.generate_photo(prompt)
        if gen.get("url"):
            ch = db.get_character(cid)
            if not ch:
                _log(f"[photo] 角色 {cid} 已被删除，丢弃照片", level="WARNING")
                return
            if not db.get_chat(cid).get("photo_enabled"):
                _log(f"[photo] 角色 {cid} 照片开关已关闭，丢弃照片", level="WARNING")
                return
            if ref_id > 0 and not db.get_message(ref_id):
                _log(f"[photo] 生成期间会话被回溯/清空，丢弃照片 cid={cid}", level="WARNING")
                return
            prompt = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(prompt)), "对方")[:200]
            db.add_message(cid, "character", "photo", prompt, gen["url"])
            _log(f"[photo] 角色 {cid} 照片已生成: {gen['url']}")
        else:
            _log(f"[photo] 角色 {cid} 照片生成失败: {gen.get('error')}", level="WARNING")
    except Exception as e:
        logger.log_exc(f"[photo] 角色 {cid} 照片线程异常: {e}")


def _video_async(cid: int, prompt: str, ref_id: int = 0):
    """视频生成（MiniMax，通常 1~3 分钟），完成后作为消息发出"""
    try:
        gen = media.generate_video(prompt)
        if gen.get("url"):
            ch = db.get_character(cid)
            if not ch:
                _log(f"[video] 角色 {cid} 已被删除，丢弃视频", level="WARNING")
                return
            if not db.get_chat(cid).get("video_enabled"):
                _log(f"[video] 角色 {cid} 视频开关已关闭，丢弃视频", level="WARNING")
                return
            if ref_id > 0 and not db.get_message(ref_id):
                _log(f"[video] 生成期间会话被回溯/清空，丢弃视频 cid={cid}", level="WARNING")
                return
            prompt = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(prompt)), "对方")[:200]
            db.add_message(cid, "character", "video", prompt, gen["url"], gen.get("duration", 6))
            _log(f"[video] 角色 {cid} 视频已生成: {gen['url']}")
        else:
            _log(f"[video] 角色 {cid} 视频生成失败: {gen.get('error')}", level="WARNING")
    except Exception as e:
        logger.log_exc(f"[video] 角色 {cid} 视频线程异常: {e}")


def _rebuild_kb_async(scope: str, character_id: int):
    def work():
        try:
            n = knowledge.rebuild_scope(scope, character_id)
            total_chunks = len(db.list_kb_chunks(scope, character_id))
            complete = total_chunks > 0 and n >= total_chunks
            for doc in db.list_kb_docs(scope, character_id):
                if complete and doc["status"] in ("embedding", "error"):
                    # 全量完成：把 embedding/error 一并恢复 ready（并发上传时先完成的文档不再被误标 error）
                    db.update_kb_doc(doc["id"], status="ready", error="")
                elif doc["status"] == "embedding":
                    if n > 0:
                        db.update_kb_doc(doc["id"], status="error",
                                         error=f"部分向量化失败（{n}/{total_chunks}），请删除后重新上传")
                    else:
                        db.update_kb_doc(doc["id"], status="error", error="向量化失败")
        except Exception as e:
            _log(f"[kb] 重建失败 {scope}/{character_id}: {e}")
            for doc in db.list_kb_docs(scope, character_id):
                if doc["status"] == "embedding":
                    db.update_kb_doc(doc["id"], status="error", error=str(e)[:200])
    threading.Thread(target=work, daemon=True).start()


# ============ 请求模型 ============

_MAX_ID = 2 ** 63 - 1


class ChatSendIn(BaseModel):
    character_id: int = Field(ge=0, le=_MAX_ID)
    content: str = Field(max_length=5000)


class UnreadItem(BaseModel):
    cid: int = Field(ge=0, le=_MAX_ID)
    after: int = Field(0, ge=0, le=_MAX_ID)


class UnreadBatchIn(BaseModel):
    items: list[UnreadItem] = Field(default_factory=list)


class CheatIn(BaseModel):
    character_id: int = Field(ge=0, le=_MAX_ID)
    command: str = Field(max_length=4000)


class RewindIn(BaseModel):
    character_id: int = Field(ge=0, le=_MAX_ID)
    msg_id: int = Field(ge=0, le=_MAX_ID)


class MomentCommentIn(BaseModel):
    id: int = Field(ge=0, le=_MAX_ID)
    content: str = Field(max_length=2000)
    reply_to: int = Field(0, ge=0, le=_MAX_ID)


class MessageIdIn(BaseModel):
    id: int = Field(ge=0, le=_MAX_ID)


class ChatSettingsIn(BaseModel):
    character_id: int = Field(ge=0, le=_MAX_ID)
    photo_enabled: bool | None = None
    voice_enabled: bool | None = None
    sticker_enabled: bool | None = None
    video_enabled: bool | None = None
    wake_enabled: bool | None = None
    reminder_enabled: bool | None = None
    reminder_target: float | str | None = None
    moments_auto: bool | None = None
    moments_react: bool | None = None
    img_desc_enabled: bool | None = None
    voice_desc_enabled: bool | None = None
    background: str | None = None


class CharacterIn(BaseModel):
    id: int | None = Field(None, ge=0, le=_MAX_ID)
    name: str = Field("", max_length=200)
    description: str = Field("", max_length=5000)
    persona_full: str = Field("", max_length=50000)
    story: str = Field("", max_length=50000)
    fields: dict = {}
    greeting: str = Field("", max_length=5000)
    model: str = Field("", max_length=200)
    temperature: float = Field(0.9, ge=0.0, le=2.0)
    sort_order: int = Field(0, ge=-9999, le=9999)
    enabled: int = Field(1, ge=0, le=1)
    chat: dict | None = None


class IdIn(BaseModel):
    id: int = Field(ge=0, le=_MAX_ID)


class LoginIn(BaseModel):
    password: str


class AppLogIn(BaseModel):
    logs: list[dict] = Field(default_factory=list)
    level: str = "INFO"
    tag: str = "app"
    message: str = ""
    device: str = ""


# ============ App 日志上报 ============

@app.post("/api/app_log")
def app_log(data: AppLogIn):
    items = data.logs or []
    if not items and data.message:
        items = [{"level": data.level, "tag": data.tag, "message": data.message, "device": data.device}]
    for it in items[-80:]:  # 取最新 80 条（客户端缓冲 300，截断应保留尾部最新日志）
        try:
            level = str(it.get("level") or "INFO").upper()
            tag = str(it.get("tag") or "app")[:24].replace("\r", " ").replace("\n", " ")
            msg = str(it.get("message") or "")[:2000].replace("\r", " ").replace("\n", " ")
            dev = str(it.get("device") or data.device or "")[:80].replace("\r", " ").replace("\n", " ")
            suffix = f" | {dev}" if dev else ""
            logger.log(f"[app:{tag}] {msg}{suffix}", level=level if level in ("ERROR", "WARNING", "DEBUG") else "INFO")
        except Exception:
            continue
    return {"ok": True, "count": min(len(items), 80)}


# ============ 基础 / 角色 ============

@app.get("/")
def root():
    return RedirectResponse("/admin/")


@app.get("/api/ticket")
def api_ticket():
    """签发短时效 URL 票据（图片/媒体/小程序等无法携带请求头的场景专用）。
    有效期内可访问 /media/、/uploads/、/api/mini/；到期后 App 会自动换新。"""
    tok, exp = _ticket_issue()
    return {"ticket": tok, "exp": exp, "ttl": TICKET_TTL}


# ============ App 端：回忆（查看/整理她的记忆）============

class MemAppEditIn(BaseModel):
    cid: int = Field(ge=0, le=_MAX_ID)
    mid: int = Field(ge=0, le=_MAX_ID)
    content: str | None = None
    pin: int | None = None
    dormant: int | None = None


@app.get("/api/memory/list")
def api_memory_list(cid: int, q: str = "", limit: int = 60, offset: int = 0):
    """回忆列表（App 端）：该角色的私聊记忆，支持按内容搜索"""
    if not db.get_character(cid):
        raise HTTPException(404, "not found")
    items = list(memory_kb._load(memory_kb.scope_of_char(cid))["items"])
    kw = (q or "").strip()
    if kw:
        items = [it for it in items if kw in str(it.get("content") or "")]
    items.sort(key=lambda x: -float(x.get("ts") or 0))
    lim = max(1, min(int(limit), 300))
    off = max(0, int(offset))
    return {"total": len(items), "items": [
        {"mid": it.get("mid"), "ts": it.get("ts"), "role": it.get("role"),
         "content": it.get("content"), "pin": int(it.get("pin") or 0),
         "dormant": int(it.get("dormant") or 0), "recall_n": int(it.get("recall_n") or 0)}
        for it in items[off:off + lim]]}


@app.post("/api/memory/update")
def api_memory_update(data: MemAppEditIn):
    """置顶 / 恢复 / 纠正内容（改内容会自动重新嵌入）"""
    r = _mem_update(memory_kb.scope_of_char(data.cid), data.mid, data.content, data.pin, data.dormant)
    if r == "not_found":
        raise HTTPException(404, "记忆不存在")
    if r == "empty":
        raise HTTPException(400, "内容不能为空")
    if r != "ok":
        raise HTTPException(500, "保存失败")
    return {"ok": True}


@app.post("/api/memory/delete")
def api_memory_delete(data: MemAppEditIn):
    left = _mem_delete(memory_kb.scope_of_char(data.cid), data.mid)
    if left < 0:
        raise HTTPException(404, "记忆不存在")
    return {"ok": True, "left": left}


@app.get("/api/bootstrap")
def bootstrap():
    chars = db.list_characters()  # 含停用角色：App 列表侧自行过滤显示，角色管理页才能重新启用
    cids = [int(c["id"]) for c in chars]
    chats = db.chats_by_ids(cids)
    lasts = db.last_messages_by_ids(cids)
    counts = db.message_counts_by_ids(cids)
    out = [_bootstrap_char(c, chats.get(int(c["id"])), lasts.get(int(c["id"])), counts.get(int(c["id"]), 0))
           for c in chars]
    return {"version": APP_VERSION, "characters": out, "news_enabled": 1 if _news_enabled() else 0}


@app.get("/api/characters/{cid}")
def get_character(cid: int):
    c = db.get_character(cid)
    if not c:
        raise HTTPException(404, "not found")
    chat = db.get_chat(cid)
    try:
        fields = _json.loads(c.get("fields_json") or "{}")
        if not isinstance(fields, dict):
            fields = {}
    except Exception:
        fields = {}
    return {
        "id": c["id"],
        "name": c["name"],
        "avatar": _char_avatar_url(c.get("avatar", "")),
        "description": c.get("description", ""),
        "persona_full": c.get("persona_full", ""),
        "story": c.get("story", ""),
        "fields": fields,
        "greeting": c.get("greeting", ""),
        "model": c.get("model", ""),
        "temperature": _safe_temperature(c.get("temperature", 0.9)),
        "enabled": int(c.get("enabled", 1) or 0),
        "sort_order": int(c.get("sort_order", 0) or 0),
        "chat": {
            "photo_enabled": bool(chat.get("photo_enabled")),
            "voice_enabled": bool(chat.get("voice_enabled")),
            "sticker_enabled": bool(chat.get("sticker_enabled")),
            "video_enabled": bool(chat.get("video_enabled")),
            "img_desc_enabled": bool(chat.get("img_desc_enabled", 1)),
            "voice_desc_enabled": bool(chat.get("voice_desc_enabled", 1)),
            "wake_enabled": bool(chat.get("wake_enabled", 1)),
            "reminder_enabled": bool(chat.get("reminder_enabled", 0)),
            "reminder_target": float(chat.get("reminder_target") or 5),
            "moments_auto": bool(chat.get("moments_auto", 0)),
            "moments_react": bool(chat.get("moments_react", 1)),
            "background": chat.get("background") or "",
        },
    }


@app.post("/api/character/save")
def save_character(data: CharacterIn):
    if data.id:
        old = db.get_character(data.id)
        if not old:
            raise HTTPException(404, "not found")
        # 只更新调用方实际提供的字段（避免部分更新把 model/temperature/sort_order/enabled 重置为默认）
        dump = data.model_dump(exclude_unset=True)
        dump.pop("id", None)
        dump.pop("chat", None)
        if "name" in dump:
            dump["name"] = (dump.get("name") or "").strip() or "未命名"
        # App/管理页都可能只回传部分 fields：与服务端已有 fields 增量合并，避免整体覆盖丢失（gender/age/job 等）
        if isinstance(dump.get("fields"), dict):
            try:
                old_fields = _json.loads(old.get("fields_json") or "{}")
                if not isinstance(old_fields, dict):
                    old_fields = {}
            except Exception:
                old_fields = {}
            merged = dict(old_fields)
            merged.update(dump["fields"])
            dump["fields"] = merged
        if dump:
            db.update_character(data.id, dump)
        cid = data.id
    else:
        payload = {
            "name": (data.name or "").strip() or "未命名",
            "description": data.description,
            "persona_full": data.persona_full,
            "story": data.story,
            "fields": data.fields or {},
            "greeting": data.greeting,
            "model": data.model,
            "temperature": _safe_temperature(data.temperature, 0.9),
            "sort_order": data.sort_order,
            "enabled": data.enabled,
        }
        cid = db.create_character(payload)
        greeting = (data.greeting or "").strip()
        if greeting:
            db.add_message(cid, "character", "text", greeting)
    if data.chat:
        allowed = {}
        for k in ("photo_enabled", "voice_enabled", "sticker_enabled", "video_enabled", "wake_enabled", "reminder_enabled", "reminder_target", "moments_auto", "moments_react", "img_desc_enabled", "voice_desc_enabled", "background"):
            if k in data.chat and data.chat[k] is not None:
                v = data.chat[k]
                if k == "background":
                    v = str(v)
                elif k == "reminder_target":
                    try:
                        v = max(1, min(99, int(float(v))))
                    except Exception:
                        v = 5
                else:
                    v = 1 if _as_bool_soft(v, True) else 0
                allowed[k] = v
        if allowed:
            db.update_chat(cid, **allowed)
            if not int(allowed.get("wake_enabled", 1)):
                _wake_cancel(cid)  # 关闭该角色主动回复时清掉残留闹铃
    return {"ok": True, "id": cid}


@app.post("/api/character/avatar")
async def upload_avatar(character_id: int = Form(...), file: UploadFile = File(...)):
    c = db.get_character(character_id)
    if not c:
        raise HTTPException(404, "not found")
    if _upload_too_big(file, 10 * 1024 * 1024):
        raise HTTPException(400, "图片太大（>10MB）")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片太大（>10MB）")
    ext = os.path.splitext(os.path.basename(file.filename or ""))[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".png"
    fname = f"char_{character_id}_{int(time.time())}_{os.urandom(2).hex()}{ext}"
    fpath = _avatar_path(fname)
    if not fpath:
        raise HTTPException(400, "文件名不合法")
    with open(fpath, "wb") as f:
        f.write(data)
    old = os.path.basename(str(c.get("avatar") or ""))
    if old and old != fname:
        op = _avatar_path(old)
        if op:
            try:
                os.remove(op)
            except Exception:
                pass
    db.update_character(character_id, {"avatar": fname})
    return {"ok": True, "url": _char_avatar_url(fname)}


@app.post("/api/character/delete")
def delete_character(data: IdIn):
    c = db.get_character(data.id)
    if not c:
        raise HTTPException(404, "not found")
    # 清理文件
    av = _avatar_path(c.get("avatar") or "")
    if av:
        try:
            os.remove(av)
        except Exception:
            pass
    for doc in db.list_kb_docs("character", data.id):
        if doc.get("stored_name"):
            try:
                os.remove(os.path.join(UPLOAD_DIR, "kb", doc["stored_name"]))
            except Exception:
                pass
    try:
        os.remove(os.path.join(DATA_DIR, "vectors", f"kb_char_{data.id}.json"))
    except Exception:
        pass
    # 清理该角色的朋友圈图片文件
    for img in db.list_character_moment_images(data.id):
        if img.startswith("/media/moments/"):
            try:
                os.remove(os.path.join(DATA_DIR, "media", "moments", os.path.basename(img)))
            except Exception:
                pass
    # 清理该角色消息关联的媒体文件（照片/视频/语音/用户上传图；表情为共享资源不动）
    _media_root = os.path.realpath(os.path.join(DATA_DIR, "media"))
    for m in db.get_messages(data.id):
        u = str(m.get("media_url") or "")
        if not u.startswith("/media/"):
            continue
        fp = os.path.realpath(os.path.join(DATA_DIR, "media", *u[len("/media/"):].split("/")))
        if fp.startswith(_media_root + os.sep):
            try:
                os.remove(fp)
            except Exception:
                pass
    db.delete_character(data.id)
    return {"ok": True}


# ============ 聊天 ============

@app.get("/api/chat/unread")
def chat_unread(character_id: int, after_id: int = 0):
    """轻量未读数：角色最后回复 id 之后角色消息条数（App 宫格红点用）"""
    c = db.get_character(character_id)
    if not c:
        raise HTTPException(404, "not found")
    conn = db.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE character_id=? AND role='character' AND id>?",
        (character_id, after_id),
    ).fetchone()
    return {"count": int(row["c"]) if row else 0, "last_id": db.max_message_id(character_id)}


@app.post("/api/chat/unread_batch")
def chat_unread_batch(data: UnreadBatchIn):
    """批量未读：一次请求取回全部角色的未读数（替代每角色一次请求的 N+1 风暴）"""
    conn = db.get_conn()
    out = {}
    for it in (data.items or [])[:50]:
        cid = int(it.cid)
        after = int(it.after or 0)
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE character_id=? AND role='character' AND id>?",
            (cid, after),
        ).fetchone()
        out[str(cid)] = {"count": int(row["c"]) if row else 0, "last_id": db.max_message_id(cid)}
    return {"items": out}


@app.get("/api/chat/messages")
async def chat_messages(character_id: int, after_id: int = 0, wait: bool = False):
    # 异步端点：长轮询等待用 await 让出事件循环，不占用同步线程池
    c = db.get_character(character_id)
    if not c:
        raise HTTPException(404, "not found")

    # 触发队列处理放在独立线程（per-cid 锁防重）：
    # 若在请求线程里同步生成，AI 慢时会把本请求和线程池一起堵住，客户端会 40s 超时
    try:
        threading.Thread(target=_process_pending, args=(character_id,), daemon=True).start()
    except Exception:
        pass

    if wait:
        for _ in range(25):
            rows = db.get_messages(character_id, after_id)
            if rows:
                break
            await asyncio.sleep(1)

    rows = db.get_messages(character_id, after_id, 800 if after_id <= 0 else 0)
    chat = db.get_chat(character_id)
    return {
        "messages": [_msg_dict(r) for r in rows],
        "total": db.count_messages(character_id),
        "typing": db.has_pending(character_id),
        "chat": {
            "photo_enabled": bool(chat.get("photo_enabled")),
            "voice_enabled": bool(chat.get("voice_enabled")),
            "sticker_enabled": bool(chat.get("sticker_enabled")),
            "video_enabled": bool(chat.get("video_enabled")),
            "img_desc_enabled": bool(chat.get("img_desc_enabled", 1)),
            "voice_desc_enabled": bool(chat.get("voice_desc_enabled", 1)),
            "wake_enabled": bool(chat.get("wake_enabled", 1)),
            "reminder_enabled": bool(chat.get("reminder_enabled", 0)),
            "reminder_target": float(chat.get("reminder_target") or 5),
            "moments_auto": bool(chat.get("moments_auto", 0)),
            "moments_react": bool(chat.get("moments_react", 1)),
            "background": chat.get("background") or "",
        },
        "character": {
            "id": c["id"],
            "name": c["name"],
            "avatar": _char_avatar_url(c.get("avatar", "")),
        },
    }


@app.post("/api/chat/send")
def chat_send(data: ChatSendIn):
    c = db.get_character(data.character_id)
    if not c:
        raise HTTPException(404, "not found")
    text = (data.content or "").strip()
    if not text:
        raise HTTPException(400, "empty")
    if len(text) > 5000:
        raise HTTPException(400, "消息过长（上限 5000 字）")
    msg_id = db.add_message(data.character_id, "user", "text", text)
    # 用户新消息打断旧回复：把未发出的分段合并补发（防"半句话永久丢失"），再清其余队列
    # 原子取走未发出的分段并清空其余延迟任务（原 pop+clear 两步存在并发窗口）
    _left, _dropped_voice = db.take_script_pending_and_clear(data.character_id)
    for _ in range(_dropped_voice):  # 被丢弃的语音任务退还预留额度，避免配额被静默吞掉
        _voice_quota_refund(data.character_id)
    if _left:  # 补发合并文本
        db.add_pending(data.character_id, _left, kind="script", ts=time.time())
    _wake_cancel(data.character_id)
    db.add_pending(data.character_id, text, kind="reply", ts=time.time(), ref_id=msg_id)
    # 同一世界：用户消息也结合上下文识别世界事件
    threading.Thread(target=_world_recognize_async, args=(data.character_id, text, 0), daemon=True).start()
    _log(f"[chat] 用户 -> {c['name']}: {text[:40]}")
    return {"ok": True, "msg_id": msg_id}


@app.post("/api/chat/send_image")
async def chat_send_image(character_id: int = Form(...), file: UploadFile = File(...), caption: str = Form("")):
    c = db.get_character(character_id)
    if not c:
        raise HTTPException(404, "not found")
    if _upload_too_big(file, 10 * 1024 * 1024):
        raise HTTPException(400, "图片太大（>10MB）")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片太大（>10MB）")
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".jpg"
    url = media.save_user_image(data, ext)
    msg_id = db.add_message(character_id, "user", "image", caption or "", url)
    # 原子取走未发出的分段并清空其余延迟任务（原 pop+clear 两步存在并发窗口）
    _left, _dropped_voice = db.take_script_pending_and_clear(character_id)
    for _ in range(_dropped_voice):  # 被丢弃的语音任务退还预留额度，避免配额被静默吞掉
        _voice_quota_refund(character_id)
    if _left:  # 补发合并文本
        db.add_pending(character_id, _left, kind="script", ts=time.time())
    _wake_cancel(character_id)
    db.add_pending(character_id, caption or "（图片）", kind="reply", ts=time.time(), ref_id=msg_id)
    _log(f"[chat] 用户 -> {c['name']}: [图片] {caption[:20]}")
    return {"ok": True, "url": url, "msg_id": msg_id}


@app.post("/api/chat/send_video")
async def chat_send_video(character_id: int = Form(...), file: UploadFile = File(...), caption: str = Form("")):
    """用户发视频（video_understand 接口）：≤50MB，转描述进上下文"""
    c = db.get_character(character_id)
    if not c:
        raise HTTPException(404, "not found")
    if _upload_too_big(file, 50 * 1024 * 1024):
        raise HTTPException(400, "视频太大（>50MB）")
    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"):
        ext = ".mp4"
    fname = f"user_{int(time.time())}_{os.urandom(3).hex()}{ext}"
    vdir = os.path.join(DATA_DIR, "media", "video")
    os.makedirs(vdir, exist_ok=True)
    if not await run_in_threadpool(_stream_upload_to, file, os.path.join(vdir, fname), 50 * 1024 * 1024):
        raise HTTPException(400, "视频太大（>50MB）")
    url = f"/media/video/{fname}"
    msg_id = db.add_message(character_id, "user", "video", caption or "", url)
    # 原子取走未发出的分段并清空其余延迟任务（原 pop+clear 两步存在并发窗口）
    _left, _dropped_voice = db.take_script_pending_and_clear(character_id)
    for _ in range(_dropped_voice):  # 被丢弃的语音任务退还预留额度，避免配额被静默吞掉
        _voice_quota_refund(character_id)
    if _left:  # 补发合并文本
        db.add_pending(character_id, _left, kind="script", ts=time.time())
    _wake_cancel(character_id)
    db.add_pending(character_id, caption or "（视频）", kind="reply", ts=time.time(), ref_id=msg_id)
    _log(f"[chat] 用户 -> {c['name']}: [视频] {caption[:20]}")
    return {"ok": True, "url": url, "msg_id": msg_id}


@app.post("/api/cheat")
def cheat(data: CheatIn):
    """作弊器：口述指令 → AI 读懂（读最近上下文）→ 注入剧情事件。
    长期生效（写入该角色的 cheat_memory，系统提示词持续注入），每个对话框独立。"""
    c = db.get_character(data.character_id)
    if not c:
        raise HTTPException(404, "not found")
    command = (data.command or "").strip()
    if not command:
        return {"ok": False, "error": "空指令"}
    _log(f"[cheat] 作弊指令 cid={data.character_id}: {command[:60]}")
    chat = db.get_chat(data.character_id)
    history = db.get_recent_messages(data.character_id, 12)
    result = ai.cheat_parse(c, chat, command, history)
    if result.get("error"):
        _log(f"[cheat] 指令解析失败 cid={data.character_id}: {result.get('error')}", level="WARNING")
        return {"ok": False, "error": "AI 无法理解这条指令"}
    if result.get("action") != "inject_event":
        return {"ok": False, "error": "AI 无法理解该指令"}
    event = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags((result.get("target") or "").strip())), "对方")
    if not event:
        return {"ok": False, "error": "事件描述为空"}
    db.add_message(data.character_id, "character", "text", f"（{event}）")
    with _cheat_lock:
        chat_now = db.get_chat(data.character_id)
        try:
            events = _json.loads(chat_now.get("cheat_memory") or "[]")
        except Exception:
            events = []
        if not isinstance(events, list):
            events = []
        events.append(event)
        db.update_chat(data.character_id, cheat_memory=_json.dumps(events[-50:], ensure_ascii=False))
    # 剧情事件同样进入"世界广播审查"：同世界角色该知道的会进世界动态，私密内容会被过滤
    try:
        threading.Thread(target=_world_recognize_async,
                         args=(data.character_id, event, data.character_id), daemon=True).start()
    except Exception:
        pass
    _log(f"[cheat] 已注入剧情事件（长期生效）cid={data.character_id}: {event[:60]}")
    return {
        "ok": True,
        "action": "inject_event",
        "msg": f"已注入剧情事件（长期生效）：{event}",
        "reply": ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(str(result.get("reply") or ""))), "你"),
    }


@app.post("/api/chat/rewind")
def chat_rewind(data: RewindIn):
    """消息回溯（rewind，适配纯聊天）：删除该消息之后的全部消息，
    并同步处理摘要水位线、作弊器长期设定、未发路线与主动回复闹铃。"""
    c = db.get_character(data.character_id)
    if not c:
        raise HTTPException(404, "not found")
    target = db.get_message(data.msg_id)
    if not target or target.get("character_id") != data.character_id:
        return {"ok": False, "error": "该消息不存在"}
    # 与在途生成互斥：等生成线程投递完成后才回溯，避免"回溯后旧回复补刀"
    _lk = _proc_lock_for(data.character_id)
    if not _lk.acquire(timeout=20):
        return {"ok": False, "error": "正在生成回复，请稍后重试"}
    try:
        deleted = db.delete_messages_after(data.character_id, data.msg_id)
        db.clear_pending(data.character_id)   # 清掉未发出的分段与待生成回复
    finally:
        _lk.release()
    _wake_cancel(data.character_id)       # 取消闹铃与沉默倒计时
    chat = db.get_chat(data.character_id)
    # 摘要水位线在回溯点之后 → 摘要已包含"未来"内容，清掉等重新压缩
    if int(chat.get("summarized_until") or 0) > data.msg_id:
        db.update_chat(data.character_id, summary="", summarized_until=0)
    # 作弊器长期设定：只保留旁白消息仍在的那些（被回溯掉的设定同步撤销）
    try:
        events = _json.loads(chat.get("cheat_memory") or "[]")
    except Exception:
        events = []
    if events:
        remaining = { (m.get("content") or "") for m in db.get_messages(data.character_id) }
        kept = [e for e in events if f"（{e}）" in remaining]
        if len(kept) != len(events):
            db.update_chat(data.character_id, cheat_memory=_json.dumps(kept, ensure_ascii=False))
    # 承诺/立场记忆：丢弃在回溯点之后产生的（其依据的消息已被删）
    try:
        cms = _json.loads(chat.get("commit_memory") or "[]")
    except Exception:
        cms = []
    if isinstance(cms, list) and cms:
        cutoff = float(target.get("created_at") or 0)
        kept_cms = [it for it in cms if not (isinstance(it, dict) and float(it.get("ts") or 0) > cutoff)]
        if len(kept_cms) != len(cms):
            db.update_chat(data.character_id, commit_memory=_json.dumps(kept_cms, ensure_ascii=False))
    # 重要记忆：同样丢弃在回溯点之后产生的（管理端手写条目 ts=0，不受影响）
    with _longmem_lock:
        try:
            lms = _json.loads(db.get_chat(data.character_id).get("long_memory") or "[]")
        except Exception:
            lms = []
        if isinstance(lms, list) and lms:
            cutoff = float(target.get("created_at") or 0)
            def _keep_lm(it):
                if isinstance(it, dict) and it.get("pin"):
                    return True  # 永久记忆不受回溯影响
                return not (isinstance(it, dict) and float(it.get("ts") or 0) > cutoff)
            kept_lms = [it for it in lms if _keep_lm(it)]
            if len(kept_lms) != len(lms):
                db.update_chat(data.character_id, long_memory=_json.dumps(kept_lms, ensure_ascii=False))
    # 世界事件：删除该角色在回溯点之后产生的广播（防止"记得已不存在的事"）
    try:
        cutoff_ts = float(target.get("created_at") or 0)
        if cutoff_ts > 0:
            n_world = db.delete_world_events_by_source(data.character_id, cutoff_ts)
            if n_world:
                _log(f"[world] 回溯同步清理世界事件 {n_world} 条 cid={data.character_id}")
    except Exception:
        pass
    last = db.get_last_message(data.character_id)
    db.update_chat(data.character_id, last_msg_at=float(last.get("created_at") or 0) if last else 0)
    _log(f"[chat] 回溯 cid={data.character_id} 到消息 #{data.msg_id}，删除 {deleted} 条")
    return {"ok": True, "deleted": deleted, "total": db.count_messages(data.character_id)}


@app.post("/api/chat/settings")
def chat_settings(data: ChatSettingsIn):
    c = db.get_character(data.character_id)
    if not c:
        raise HTTPException(404, "not found")
    fields = {}
    if data.photo_enabled is not None:
        fields["photo_enabled"] = int(data.photo_enabled)
    if data.voice_enabled is not None:
        fields["voice_enabled"] = int(data.voice_enabled)
    if data.sticker_enabled is not None:
        fields["sticker_enabled"] = int(data.sticker_enabled)
    if data.video_enabled is not None:
        fields["video_enabled"] = int(data.video_enabled)
    if data.wake_enabled is not None:
        fields["wake_enabled"] = int(data.wake_enabled)
    if data.reminder_enabled is not None:
        fields["reminder_enabled"] = int(data.reminder_enabled)
    if data.reminder_target is not None:
        try:
            fields["reminder_target"] = max(1, min(99, int(float(data.reminder_target))))
        except Exception:
            pass
    if data.moments_auto is not None:
        fields["moments_auto"] = int(data.moments_auto)
    if data.moments_react is not None:
        fields["moments_react"] = int(data.moments_react)
    if data.img_desc_enabled is not None:
        fields["img_desc_enabled"] = int(data.img_desc_enabled)
    if data.voice_desc_enabled is not None:
        fields["voice_desc_enabled"] = int(data.voice_desc_enabled)
    if data.background is not None:
        fields["background"] = data.background
    if fields:
        db.update_chat(data.character_id, **fields)
        if fields.get("wake_enabled") == 0:
            _wake_cancel(data.character_id)  # 关闭主动回复时清掉残留闹铃
    return {"ok": True, "chat": get_character(data.character_id)["chat"]}


@app.post("/api/chat/reset")
def chat_reset(data: IdIn):
    c = db.get_character(data.id)
    if not c:
        raise HTTPException(404, "not found")
    db.clear_pending(data.id)  # 连待发队列一起清：重置后不再有旧回复/分段迟到落库
    with _longmem_lock:
        db.reset_messages(data.id)
    greeting = (c.get("greeting") or "").strip()
    greeting_id = 0
    if greeting:
        greeting_id = db.add_message(data.id, "character", "text", greeting)
    return {"ok": True, "greeting_id": greeting_id}


# ============ 跨角色轮询（后台通知用） ============

@app.get("/api/app_version")
def app_version():
    """App 更新检查：版本信息由部署工具写入 app_version.json；缺失时用 APK 文件信息兜底"""
    info = {"version": "", "notes": "", "apk": "/uploads/Stories.apk", "md5": "", "sha256": "", "size": 0, "ts": 0}
    try:
        with open(os.path.join(DATA_DIR, "app_version.json"), "r", encoding="utf-8") as f:
            d = _json.load(f)
        if isinstance(d, dict):
            for k in list(info.keys()):
                if k in d and d[k] is not None:
                    info[k] = d[k]
    except Exception:
        pass
    try:
        st = os.stat(os.path.join(UPLOAD_DIR, "Stories.apk"))
        if not info.get("size"):
            info["size"] = st.st_size
        if not info.get("ts"):
            info["ts"] = st.st_mtime
    except Exception:
        pass
    return info

@app.get("/api/poll_all")
def poll_all(since: float = 0):
    now0 = time.time()  # 游标：查询加时间上界（<= now0），既不漏也不重
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT m.id, m.character_id, m.kind, m.content, m.created_at, c.name, c.avatar "
        "FROM messages m JOIN characters c ON c.id=m.character_id "
        "WHERE c.enabled=1 AND m.role='character' AND m.created_at > ? AND m.created_at <= ? ORDER BY m.id LIMIT 200",
        (since, now0),
    ).fetchall()
    items = []
    for r in rows:
        kind = r["kind"] or "text"
        if kind == "voice":
            preview = "[语音]" + ((" " + _clean_preview(r["content"], 30)) if (r["content"] or "").strip() else "")
        elif kind == "photo":
            preview = "[照片]"
        elif kind == "video":
            preview = "[视频]"
        elif kind == "sticker":
            preview = "[表情]"
        elif kind == "image":
            preview = "[图片]"
        else:
            preview = _clean_preview(r["content"], 60)
        items.append({
            "id": r["id"],
            "character_id": r["character_id"],
            "name": r["name"],
            "avatar": _char_avatar_url(r["avatar"]),
            "kind": kind,
            "preview": preview,
            "ts": r["created_at"],
        })
    # 朋友圈事件（新动态 + 角色评论/回复）→ 复用后台通知通道
    mrows, crows = [], []
    try:
        mrows = conn.execute(
            "SELECT m.id, m.character_id, m.content, m.created_at, c.name, c.avatar "
            "FROM moments m LEFT JOIN characters c ON c.id=m.character_id "
            "WHERE m.author='character' AND m.created_at > ? AND m.created_at <= ? ORDER BY m.id LIMIT 50",
            (since, now0)).fetchall()
        for r in mrows:
            items.append({
                "id": r["id"], "character_id": r["character_id"],
                "name": r["name"] or "她", "avatar": _char_avatar_url(r["avatar"] or ""),
                "kind": "moment", "preview": "发了朋友圈：" + _clean_preview(r["content"], 40),
                "ts": r["created_at"],
            })
        crows = conn.execute(
            "SELECT cc.id, cc.content, cc.created_at, cc.character_id, c.name, c.avatar "
            "FROM moment_comments cc LEFT JOIN characters c ON c.id=cc.character_id "
            "WHERE cc.role='character' AND cc.created_at > ? AND cc.created_at <= ? ORDER BY cc.id LIMIT 50",
            (since, now0)).fetchall()
        for r in crows:
            items.append({
                "id": r["id"], "character_id": r["character_id"],
                "name": r["name"] or "她", "avatar": _char_avatar_url(r["avatar"] or ""),
                "kind": "moment_comment", "preview": "评论了朋友圈：" + _clean_preview(r["content"], 40),
                "ts": r["created_at"],
            })
    except Exception as e:
        _log(f"[poll] 朋友圈事件查询失败: {e}", level="WARNING")
    # 群聊角色消息（群自动接话/互聊也要能通知）
    grows = []
    try:
        grows = conn.execute(
            "SELECT gm.id, gm.group_id, gm.cid, gm.kind, gm.content, gm.created_at, "
            "g.name AS gname, c.name AS sname "
            "FROM group_messages gm JOIN groups g ON g.id=gm.group_id "
            "LEFT JOIN characters c ON c.id=gm.cid "
            "WHERE gm.role='character' AND gm.created_at > ? AND gm.created_at <= ? "
            "ORDER BY gm.id LIMIT 100",
            (since, now0)).fetchall()
        for r in grows:
            gkind = r["kind"] or "text"
            if gkind == "image":
                body = "[图片]"
            elif gkind == "video":
                body = "[视频]"
            else:
                body = _clean_preview(r["content"], 40)
            items.append({
                "id": r["id"], "character_id": 0, "gid": r["group_id"],
                "name": r["gname"] or "群聊", "avatar": "",
                "kind": "group",
                "preview": f"{r['sname'] or '群友'}：{body}",
                "ts": r["created_at"],
            })
    except Exception as e:
        _log(f"[poll] 群聊事件查询失败: {e}", level="WARNING")
    items.sort(key=lambda x: x.get("ts") or 0)
    # 截断游标：任一类达上限时把游标回退到该类最后一条的时间，下一轮继续消费（宁可偶发重复，不可丢失）
    cursor = now0
    if len(rows) >= 200 and rows:
        cursor = min(cursor, float(rows[-1]["created_at"] or 0))
    if len(mrows) >= 50 and mrows:
        cursor = min(cursor, float(mrows[-1]["created_at"] or 0))
    if len(crows) >= 50 and crows:
        cursor = min(cursor, float(crows[-1]["created_at"] or 0))
    if len(grows) >= 100 and grows:
        cursor = min(cursor, float(grows[-1]["created_at"] or 0))
    return {"messages": items, "now": cursor}


# ============ 朋友圈 API ============

def _moment_dict(m: dict, chars_map: dict = None, comments_map: dict = None, likes_map: dict = None) -> dict:
    char = None
    if (m.get("author") or "") == "character" and m.get("character_id"):
        mid_cid = int(m["character_id"])
        char = chars_map.get(mid_cid) if chars_map is not None else db.get_character(mid_cid)
    try:
        images = _json.loads(m.get("images") or "[]")
    except Exception:
        images = []
    comments = []
    c_rows = comments_map.get(int(m["id"])) if comments_map is not None else db.list_moment_comments(m["id"])
    for c in (c_rows or []):
        cm_char = None
        cm_cid = int(c.get("character_id") or 0)
        if (c.get("role") or "") == "character" and cm_cid:
            cm_char = chars_map.get(cm_cid) if chars_map is not None else db.get_character(cm_cid)
        c_role = c.get("role") or "user"
        comments.append({
            "id": c["id"], "role": c_role,
            "content": c.get("content") or "", "ts": c.get("created_at"),
            "reply_to": int(c.get("reply_to") or 0),
            "character_id": int(c.get("character_id") or 0),
            "name": (cm_char.get("name") if cm_char else ("她" if c_role == "character" else "我")),
            "avatar": _char_avatar_url(cm_char.get("avatar", "")) if cm_char else "",
        })
    likes = []
    l_rows = likes_map.get(int(m["id"])) if likes_map is not None else db.list_moment_likes(m["id"])
    for lk in (l_rows or []):
        likes.append({"character_id": int(lk.get("character_id") or 0), "name": lk.get("name") or "她"})
    if (m.get("author") or "") == "character":
        name = char.get("name") if char else "（已删除角色）"
        avatar = _char_avatar_url(char.get("avatar", "")) if char else ""
    else:
        name, avatar = "我", ""

    def _vis_list(key):
        try:
            arr = _json.loads(m.get(key) or "[]")
        except Exception:
            arr = []
        if not isinstance(arr, list):
            return []
        out = []
        for x in arr:
            try:
                v = int(x)
            except Exception:
                continue
            if v > 0:
                out.append(v)
        return out

    return {
        "id": m["id"],
        "author": m.get("author") or "character",
        "character_id": int(m.get("character_id") or 0),
        "name": name,
        "avatar": avatar,
        "content": m.get("content") or "",
        "images": images,
        "ts": m.get("created_at"),
        "liked_by_user": bool(m.get("liked_by_user")),
        "liked_by_character": bool(m.get("liked_by_character")),
        "like_names": [lk["name"] for lk in likes],
        "source": m.get("source") or "auto",
        "visible_to": _vis_list("visible_to"),
        "hidden_from": _vis_list("hidden_from"),
        "comments": comments,
    }


@app.get("/api/moments")
def moments_list(after_id: int = 0, limit: int = 100):
    rows = db.list_moments(after_id, max(1, min(limit, 200)))
    mids = [int(m["id"]) for m in rows]
    comments_map = db.list_moment_comments_for(mids)
    likes_map = db.list_moment_likes_for(mids)
    chars_map = {int(c["id"]): c for c in db.list_characters()}
    return {"moments": [_moment_dict(m, chars_map, comments_map, likes_map) for m in rows],
            "unseen": db.count_unseen_moments_and_comments(),
            "now": time.time()}


@app.post("/api/moments/post")
async def moments_post(content: str = Form(""), visible_to: str = Form(""), hidden_from: str = Form(""),
                       files: list[UploadFile] = File(default=[])):
    content = (content or "").strip()
    if len(content) > 2000:
        return {"ok": False, "error": "内容过长（上限 2000 字）"}

    def _parse_ids(s: str):
        try:
            arr = _json.loads(s or "[]")
        except Exception:
            arr = []
        if not isinstance(arr, list):
            return []
        out = []
        for x in arr:
            try:
                v = int(x)
            except Exception:
                continue
            if v > 0:
                out.append(v)
        return out[:50]

    vis = _parse_ids(visible_to)
    hid = _parse_ids(hidden_from)

    images = []
    mdir = os.path.join(DATA_DIR, "media", "moments")
    os.makedirs(mdir, exist_ok=True)
    for f in (files or [])[:9]:
        ext = os.path.splitext(f.filename or "")[1].lower() or ".jpg"
        is_video = ext in (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")
        if not is_video and ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".jpg"
        limit = 50 * 1024 * 1024 if is_video else 10 * 1024 * 1024
        if _upload_too_big(f, limit):
            continue
        name = f"m_{int(time.time())}_{os.urandom(3).hex()}{ext}"
        path = os.path.join(mdir, name)
        if await _save_upload_stream(f, path, limit) <= 0:
            continue
        images.append("/media/moments/" + name)
    if not content and not images:
        return {"ok": False, "error": "内容为空"}
    mid = db.add_moment("user", 0, content, images, source="user", visible_to=vis, hidden_from=hid)
    moment = db.get_moment(mid)
    threading.Thread(target=_moment_schedule_react, args=(moment,), daemon=True).start()
    _log(f"[moment] 用户发朋友圈 #{mid}（图 {len(images)} 张，可见={vis or '所有人'}，不可见={hid or '无'}）")
    return {"ok": True, "id": mid}


@app.post("/api/moments/like")
def moments_like(data: MessageIdIn):
    m = db.get_moment(data.id)
    if not m:
        return {"ok": False, "error": "不存在"}
    new_val = 0 if m.get("liked_by_user") else 1
    db.set_moment_like(data.id, "user", new_val)
    return {"ok": True, "liked": bool(new_val)}


@app.post("/api/moments/comment")
def moments_comment(data: MomentCommentIn):
    m = db.get_moment(data.id)
    if not m:
        return {"ok": False, "error": "不存在"}
    text = (data.content or "").strip()
    if not text:
        return {"ok": False, "error": "空评论"}
    cid2 = db.add_moment_comment(data.id, "user", text, reply_to=int(data.reply_to or 0))
    # 触发回评的对象：优先"被回复评论的作者（角色）"；否则动态作者（角色）
    target_cid = 0
    reply_to = int(data.reply_to or 0)
    if reply_to > 0:
        try:
            rc = db.get_moment_comment(reply_to)
        except Exception:
            rc = None
        if rc and (rc.get("role") or "") == "character" and rc.get("character_id"):
            # 只接受“同一条动态下”的评论回评，防串动态
            if int(rc.get("moment_id") or 0) == int(data.id):
                target_cid = int(rc["character_id"])
    if not target_cid and (m.get("author") or "") == "character" and m.get("character_id"):
        target_cid = int(m["character_id"])
    if target_cid:
        mc = db.get_chat(target_cid)
        if mc.get("moments_react"):
            # 有概率触发回评（可在后台设置概率，默认 80%）
            try:
                chance = int(float(db.get_settings().get("moment_reply_chance", 80)))
            except Exception:
                chance = 80
            chance = max(0, min(chance, 100))
            if random.randint(1, 100) <= chance:
                db.add_moment_task(target_cid, "char_reply",
                                   time.time() + random.randint(20, 90),
                                   moment_id=data.id,
                                   payload={"comment": text, "comment_id": cid2, "reply_to": reply_to})
            else:
                _log(f"[moment] 本次评论未触发回评（概率 {chance}%）moment={data.id}")
    return {"ok": True, "id": cid2}


@app.post("/api/moments/seen")
def moments_seen():
    db.mark_moments_seen()
    return {"ok": True}


# ============ 知识库 ============

def _handle_kb_upload(scope: str, character_id: int, file: UploadFile, data: bytes):
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "文件太大（>5MB）")
    filename = file.filename or "doc.md"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".md", ".txt", ".markdown"):
        raise HTTPException(400, "仅支持 md / txt")
    doc_hash = hashlib.md5(data).hexdigest()
    stored = f"{int(time.time())}_{os.urandom(3).hex()}{ext}"
    with open(os.path.join(UPLOAD_DIR, "kb", stored), "wb") as f:
        f.write(data)
    doc_id = db.add_kb_doc(scope, character_id, filename, stored, doc_hash, status="parsing")
    try:
        import parser as doc_parser
        text = doc_parser.parse_document(filename, data)
        chunks = doc_parser.chunk_document(text)
        if not chunks:
            db.update_kb_doc(doc_id, status="error", error="文档内容为空")
            return {"ok": False, "doc_id": doc_id, "error": "文档内容为空"}
        db.add_kb_chunks(doc_id, scope, character_id, chunks)
        db.update_kb_doc(doc_id, status="embedding", chunk_count=len(chunks))
    except Exception as e:
        db.update_kb_doc(doc_id, status="error", error=str(e)[:200])
        _log(f"[kb] 上传解析失败 doc={doc_id}: {e}", level="WARNING")
        return {"ok": False, "doc_id": doc_id, "error": "文档解析失败"}
    _rebuild_kb_async(scope, character_id)
    return {"ok": True, "doc_id": doc_id, "chunks": len(chunks)}


@app.post("/api/kb/upload")
async def kb_upload(
    request: Request,
    scope: str = Form("character"),
    character_id: int = Form(0),
    file: UploadFile = File(...),
):
    if scope == "public":
        _require_admin(request)
    elif scope != "character":
        raise HTTPException(400, "scope 只能是 character/public")
    if scope == "character" and not db.get_character(character_id):
        raise HTTPException(404, "角色不存在")
    if _upload_too_big(file, 5 * 1024 * 1024):
        raise HTTPException(400, "文件太大（>5MB）")
    data = await file.read()
    return _handle_kb_upload(scope, character_id, file, data)


@app.get("/api/kb/list")
def kb_list(character_id: int = 0):
    out = {"character": [], "public": []}
    if character_id:
        out["character"] = [
            {**{k: d[k] for k in ("id", "filename", "status", "error", "chunk_count", "uploaded_at")}}
            for d in db.list_kb_docs("character", character_id)
        ]
    out["public"] = [
        {**{k: d[k] for k in ("id", "filename", "status", "error", "chunk_count", "uploaded_at")}}
        for d in db.list_kb_docs("public", 0)
    ]
    return out


@app.post("/api/kb/delete")
def kb_delete(request: Request, data: IdIn):
    doc = db.get_kb_doc(data.id)
    if not doc:
        raise HTTPException(404, "not found")
    if doc["scope"] == "public":
        _require_admin(request)
    if doc.get("stored_name"):
        try:
            os.remove(os.path.join(UPLOAD_DIR, "kb", doc["stored_name"]))
        except Exception:
            pass
    db.delete_kb_doc(data.id)
    _rebuild_kb_async(doc["scope"], doc["character_id"])
    return {"ok": True}


# ============ 背景 / 表情 / 日志 ============

@app.get("/api/backgrounds")
def backgrounds():
    return {
        "backgrounds": [
            {"id": b["id"], "url": f"/uploads/backgrounds/{b['file']}", "name": b.get("name", "")}
            for b in db.list_backgrounds()
        ]
    }


@app.get("/api/stickers")
def stickers():
    return {
        "stickers": [
            {"id": s["id"], "url": f"/uploads/stickers/{s['file']}", "label": s.get("label", ""), "tags": s.get("tags", "")}
            for s in db.list_stickers()
        ]
    }


@app.get("/api/logs")
def logs(request: Request, lines: int = 200):
    _require_admin(request)
    return {"logs": logger.read_logs(lines)}


# ============ 管理端 ============

@app.post("/admin/login")
def admin_login(data: LoginIn, request: Request):
    ip = (request.client.host if request.client else "?") or "?"
    now = time.time()
    with _login_lock:
        if len(_login_fails) > 200:  # 防内存无限增长：清掉 1 小时无活动的记录
            stale = [k for k, v in _login_fails.items() if now - float(v.get("last") or 0) > 3600]
            for k in stale:
                _login_fails.pop(k, None)
        rec = _login_fails.get(ip) or {"n": 0, "until": 0.0, "last": 0.0}
        if now < rec["until"]:
            left = int(rec["until"] - now) + 1
            return {"ok": False, "error": f"尝试过于频繁，请 {left} 秒后再试"}
        if _safe_eq(data.password, ADMIN_PASSWORD):
            _login_fails.pop(ip, None)
            return {"ok": True, "token": _admin_token_issue()}
        if now - rec["last"] > 300:
            rec["n"] = 0
        rec["n"] += 1
        rec["last"] = now
        if rec["n"] >= 5:
            rec["until"] = now + 60
            rec["n"] = 0
            _login_fails[ip] = rec
            return {"ok": False, "error": "尝试过于频繁，请 60 秒后再试"}
        _login_fails[ip] = rec
        return {"ok": False, "error": "口令错误"}


@app.get("/admin/api/characters")
def admin_characters(request: Request):
    _require_admin(request)
    out = []
    for c in db.list_characters():
        try:
            fields = _json.loads(c.get("fields_json") or "{}")
            if not isinstance(fields, dict):
                fields = {}
        except Exception:
            fields = {}
        c["temperature"] = _safe_temperature(c.get("temperature"))
        chat = db.get_chat(c["id"])
        try:
            fl = db.get_reminder_state(c["id"])
            wk = _week_key()
            fl_count = 0 if fl.get("week") != wk else int(fl.get("count") or 0)
        except Exception:
            fl_count = 0
        out.append({
            **c,
            "fields": fields,
            "avatar_url": _char_avatar_url(c.get("avatar", "")),
            "msg_count": db.count_messages(c["id"]),
            "reminder": {
                "enabled": bool(chat.get("reminder_enabled")),
                "target": _reminder_target_of(chat),
                "week_count": fl_count,
            },
            "chat": {
                "photo_enabled": bool(chat.get("photo_enabled")),
                "voice_enabled": bool(chat.get("voice_enabled")),
                "sticker_enabled": bool(chat.get("sticker_enabled")),
                "video_enabled": bool(chat.get("video_enabled")),
                "wake_enabled": bool(chat.get("wake_enabled", 1)),
                "reminder_enabled": bool(chat.get("reminder_enabled")),
                "reminder_target": _reminder_target_of(chat),
                "moments_auto": bool(chat.get("moments_auto")),
                "moments_react": bool(chat.get("moments_react", 1)),
                "img_desc_enabled": bool(chat.get("img_desc_enabled", 1)),
                "voice_desc_enabled": bool(chat.get("voice_desc_enabled", 1)),
                "background": chat.get("background") or "",
            },
        })
    return {"characters": out}


@app.post("/admin/api/stickers")
async def admin_sticker_upload(
    request: Request,
    label: str = Form(""),
    tags: str = Form(""),
    file: UploadFile = File(...),
):
    _require_admin(request)
    if _upload_too_big(file, 5 * 1024 * 1024):
        raise HTTPException(400, "图片太大")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "图片太大")
    ext = os.path.splitext(file.filename or "")[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        ext = ".png"
    fname = f"st_{int(time.time())}_{os.urandom(3).hex()}{ext}"
    with open(os.path.join(UPLOAD_DIR, "stickers", fname), "wb") as f:
        f.write(data)
    sid = db.add_sticker(fname, label or os.path.splitext(file.filename or "")[0], tags)
    return {"ok": True, "id": sid, "url": f"/uploads/stickers/{fname}"}


@app.post("/admin/api/stickers/delete")
def admin_sticker_delete(request: Request, data: IdIn):
    _require_admin(request)
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM stickers WHERE id=?", (data.id,)).fetchone()
    if row:
        try:
            os.remove(os.path.join(UPLOAD_DIR, "stickers", row["file"]))
        except Exception:
            pass
        db.delete_sticker(data.id)
    return {"ok": True}


@app.post("/admin/api/backgrounds")
async def admin_background_upload(request: Request, name: str = Form(""), file: UploadFile = File(...)):
    _require_admin(request)
    if _upload_too_big(file, 10 * 1024 * 1024):
        raise HTTPException(400, "图片太大")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片太大")
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".jpg"
    fname = f"bg_{int(time.time())}_{os.urandom(3).hex()}{ext}"
    with open(os.path.join(UPLOAD_DIR, "backgrounds", fname), "wb") as f:
        f.write(data)
    bid = db.add_background(fname, name)
    return {"ok": True, "id": bid, "url": f"/uploads/backgrounds/{fname}"}


@app.post("/admin/api/backgrounds/delete")
def admin_background_delete(request: Request, data: IdIn):
    _require_admin(request)
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM backgrounds WHERE id=?", (data.id,)).fetchone()
    if row:
        try:
            os.remove(os.path.join(UPLOAD_DIR, "backgrounds", row["file"]))
        except Exception:
            pass
        db.delete_background(data.id)
    return {"ok": True}


@app.get("/admin/api/messages")
def admin_messages(request: Request, character_id: int, limit: int = 100, offset: int = 0, before_id: int = 0,
                   oldest: int = 0):
    """聊天记录：默认取最新一页；翻更早用 before_id（游标，避免新消息写入时 offset 错位）；
    oldest=1 直接返回最早一页（修复"跳到最早"实际只能再翻一页的问题）"""
    _require_admin(request)
    conn = db.get_conn()
    total = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE character_id=?", (character_id,)).fetchone()["c"]
    min_id = conn.execute("SELECT COALESCE(MIN(id),0) AS m FROM messages WHERE character_id=?", (character_id,)).fetchone()["m"]
    lim = max(1, min(limit, 500))
    if oldest:
        rows = list(reversed(conn.execute(
            "SELECT * FROM messages WHERE character_id=? ORDER BY id ASC LIMIT ?",
            (character_id, lim),
        ).fetchall()))
    elif before_id > 0:
        rows = conn.execute(
            "SELECT * FROM messages WHERE character_id=? AND id<? ORDER BY id DESC LIMIT ?",
            (character_id, before_id, lim),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages WHERE character_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (character_id, lim, max(0, offset)),
        ).fetchall()
    return {"total": total, "min_id": min_id, "messages": [_msg_dict(dict(r)) for r in reversed(rows)]}


@app.post("/admin/api/messages/delete")
def admin_message_delete(request: Request, data: MessageIdIn):
    """删除单条消息"""
    _require_admin(request)
    msg = db.get_message(data.id)
    if not msg:
        raise HTTPException(404, "消息不存在")
    db.delete_message(data.id)
    db.delete_pending_by_ref(data.id)  # 引用该消息的待生成回复一并取消，避免回复"幽灵消息"
    # 一致性：删到摘要水位线之前的消息时，摘要已含被删内容 → 清掉等待重新压缩；并修正 last_msg_at
    try:
        chat = db.get_chat(msg["character_id"])
        if int(chat.get("summarized_until") or 0) >= int(data.id or 0):
            db.update_chat(msg["character_id"], summary="", summarized_until=0)
        last = db.get_last_message(msg["character_id"])
        db.update_chat(msg["character_id"], last_msg_at=float(last.get("created_at") or 0) if last else 0)
    except Exception:
        pass
    _log(f"[admin] 删除消息 id={data.id} character={msg['character_id']}")
    return {"ok": True}


@app.post("/admin/api/messages/clear")
def admin_messages_clear(request: Request, data: IdIn):
    """清空某角色的整段对话（保留开场白）"""
    _require_admin(request)
    c = db.get_character(data.id)
    if not c:
        raise HTTPException(404, "角色不存在")
    with _longmem_lock:
        db.clear_messages(data.id)
    greeting = (c.get("greeting") or "").strip()
    if greeting:
        db.add_message(data.id, "character", "text", greeting)
    _log(f"[admin] 清空对话 character={data.id} ({c['name']})")
    return {"ok": True}


@app.get("/admin/api/export")
def admin_export(request: Request, character_id: int, format: str = "json"):
    _require_admin(request)
    c = db.get_character(character_id)
    if not c:
        raise HTTPException(404, "not found")
    rows = db.get_messages(character_id)
    if format == "txt":
        lines = [f"===== {c['name']} 聊天记录导出（{time.strftime('%Y-%m-%d %H:%M')}）====="]
        for r in rows:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"]))
            who = c["name"] if r["role"] == "character" else "我"
            kind = r.get("kind") or "text"
            if kind == "text":
                body = r["content"]
            elif kind == "voice":
                body = f"[语音] {r['content']}"
            elif kind == "image":
                body = f"[图片] {r['content']}"
            elif kind == "photo":
                body = "[照片]"
            elif kind == "video":
                body = "[视频]"
            elif kind == "sticker":
                body = "[表情]"
            else:
                body = r["content"]
            lines.append(f"[{ts}] {who}: {body}")
        return PlainTextResponse("\n".join(lines), media_type="text/plain; charset=utf-8")
    data = {
        "character": {k: c[k] for k in ("id", "name", "description", "persona_full", "greeting") if k in c},
        "messages": [_msg_dict(r) for r in rows],
    }
    return PlainTextResponse(_json.dumps(data, ensure_ascii=False, indent=1), media_type="application/json; charset=utf-8")


_SECRET_SETTING_KEYS = ("volc_ak", "volc_sk", "aliyun_ak", "aliyun_sk")
_SECRET_ENV_OF = {"volc_ak": "VOLC_AK", "volc_sk": "VOLC_SK",
                  "aliyun_ak": "ALIYUN_AK", "aliyun_sk": "ALIYUN_SK"}


def _mask_secret_settings(raw: dict, out: dict) -> dict:
    """密钥类设置绝不回显明文：只返回"是否已配置"与来源（env/db）。"""
    for k in _SECRET_SETTING_KEYS:
        db_v = str(raw.get(k) or "").strip()
        env_v = str(secrets_cfg.get(_SECRET_ENV_OF.get(k, "")) or "").strip()
        out.pop(k, None)
        out[k + "_set"] = 1 if (env_v or db_v) else 0
        out[k + "_from"] = "env" if env_v else ("db" if db_v else "")
    return out


@app.get("/admin/api/settings")
def admin_settings_get(request: Request):
    _require_admin(request)
    _raw = db.get_settings()
    return {
        "settings": _mask_secret_settings(_raw, dict(_raw)),
        "defaults": {
            "reply_delay": 1.2,
            "msg_delay_factor": 1.0,
            "max_history": 40,
            "kb_min_score": 0.30,
            "kb_public_max": 1,
            "long_memory_enabled": 1,
            "ark_model": ai.ARK_MODEL,
            "deepseek_model": ai.MODEL_PRO,
            "summary_model": ai.MODEL_FLASH,
            "judge_model": ai.MODEL_FLASH,
            "seg_enabled": 1,
            "seg_voice_mix": 1,
            "seg_voice_max_chars": 20,
            "seg_voice_daily_limit": 15,
            "seg_voice_keywords": "晚安,想你,爱你,舍不得,心疼,抱抱,亲爱的,想见你,么么,好梦",
            "seg_min_length": 25,
            "seg_max_segments": 4,
            "seg_prompt": ai.DEFAULT_SPLIT_PROMPT,
            "reminder_cooldown": 30,
            "reminder_retry": 15,
            "reminder_count_cooldown": 60,
            "reminder_keywords": DEFAULT_REMINDER_KEYWORDS,
            "reminder_judge_prompt": DEFAULT_REMINDER_JUDGE_PROMPT,
            "reminder_prompt": DEFAULT_REMINDER_PROMPT,
            "search_enabled": 1,
            "search_api_key": ai.DEFAULT_SEARCH_API_KEY,
            "search_max_results": 5,
            "volc_ak": "",
            "volc_sk": "",
            "aliyun_ak": "",
            "aliyun_sk": "",
            "moment_post_prompt": DEFAULT_MOMENT_POST_PROMPT,
            "moment_comment_prompt": DEFAULT_MOMENT_COMMENT_PROMPT,
            "moment_reply_prompt": DEFAULT_MOMENT_REPLY_PROMPT,
            "moment_reply_chance": 80,
            "moment_react_delay_max": 720,
            "moment_force_like": 1,
            "moment_daily_max": 2,
            "world_recognize_enabled": 1,
            "world_context_max": 10,
            "world_context_days": 7,
            "news_enabled": 1,
            "news_clean_enabled": 1,
            "news_clean_model": ai.MODEL_FLASH,
            "group_auto_idle_min": 120,
            "group_auto_interval": 240,
            "group_auto_window_hours": 6,
            "group_auto_daily_limit": 15,
            "group_model": ai.MODEL_FLASH,
            "group_chain_max": 2,
            "group_reply_max": 2,
            "group_context_max": 30,
            "group_context_window_hours": 72,
            "group_context_max_groups": 3,
            "consistency_enabled": 1,
            "consistency_prompt": ai.DEFAULT_CONSISTENCY_PROMPT,
            "core_guard_enabled": 1,
            "core_guard_prompt": ai.CORE_GUARD,
            "image_desc_enabled": 1,
            "image_desc_model": media.DEFAULT_IMAGE_DESC_MODEL,
            "image_desc_prompt": media.DEFAULT_IMAGE_DESC_PROMPT,
            "photo_model": media.SEEDREAM_MODEL,
            "video_model": media.MINIMAX_VIDEO_MODEL,
            "wake_enabled": 1,
            "wake_silence": 3,
            "wake_daily_max": 1,
            "wake_tz": 8,
            "wake_model": "",
        },
    }


@app.get("/admin/api/models")
def admin_models(request: Request, refresh: int = 0):
    """动态拉取可用模型列表（Ark + DeepSeek），10 分钟缓存"""
    global _models_fetching
    _require_admin(request)
    now = time.time()
    if not refresh and _models_cache["data"] and now - _models_cache["ts"] < 600:
        return _models_cache["data"]
    if _models_fetching and (now - float(_models_cache.get("fetch_start") or 0) < 90):
        # 并发去重（90 秒内视为仍在拉取）；超时未复位视为异常，继续重新拉取
        time.sleep(0.8)
        return _models_cache["data"] or {"chat": [], "deepseek": [], "image": [], "video": [],
                                         "text": [], "ark": [], "all": []}
    _models_fetching = True
    _models_cache["fetch_start"] = now
    chat, ds, image, video = [], [], [], []
    exclude = ("embedding", "seed3d", "hyper3d", "hitem3d")
    try:
        req = urllib.request.Request(
            ai.ARK_BASE + "/models",
            headers={"Authorization": "Bearer " + ai.ARK_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            d = _json.loads(r.read().decode("utf-8"))
        for m in d.get("data", []):
            mid = str(m.get("id") or "").strip()
            status = str(m.get("status") or "").lower()
            if not mid or status == "shutdown":
                continue
            if "seedream" in mid:
                image.append(mid)
                continue
            if "seedance" in mid:
                continue  # 视频改用 MiniMax，不再列 Ark 视频模型
            if any(x in mid for x in exclude):
                continue
            chat.append(mid)
    except Exception as e:
        _log(f"[admin] Ark 模型列表拉取失败: {e}", level="WARNING")
    try:
        _ds_keys = ai._deepseek_keys()
        _ds_bases = ai._deepseek_base_of(_ds_keys) if _ds_keys else {}
        _ds_pick = next((k for k in _ds_keys if ai._is_official_ds_base(_ds_bases.get(k) or "")),
                        (_ds_keys[0] if _ds_keys else ""))
        req = urllib.request.Request(
            (_ds_bases.get(_ds_pick) or ai.DEEPSEEK_BASE) + "/models",
            headers={"Authorization": "Bearer " + _ds_pick},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            d = _json.loads(r.read().decode("utf-8"))
        ds = [str(m.get("id")) for m in d.get("data", []) if m.get("id")]
    except Exception as e:
        _log(f"[admin] DeepSeek 模型列表拉取失败: {e}", level="WARNING")
    # xAI（Grok）：经隧道拉取；失败用内置清单兜底（充值后自动恢复真实列表）
    xai_models = []
    try:
        if ai.XAI_API_KEY:
            d2 = ai._socks_https_request("GET", "/v1/models", timeout=6)
            xai_models = [str(m.get("id")).strip() for m in (d2.get("data") or []) if str(m.get("id") or "").strip()]
    except Exception as e:
        _log(f"[admin] xAI 模型列表拉取失败（用内置兜底）: {e}", level="WARNING")
    if not xai_models:
        xai_models = ["grok-4", "grok-4-fast", "grok-3", "grok-3-mini", "grok-2-1212"]
    # 视频模型（MiniMax；只列文生视频模型，Fast 需首帧图当前不支持）
    video = ["MiniMax-Hailuo-2.3", "MiniMax-Hailuo-02", "T2V-01-Director", "T2V-01"]
    # 兜底：确保当前在用模型一定在列表里
    for m in [ai.ARK_MODEL, "doubao-seed-2-1-pro-260628", "doubao-seed-evolving"]:
        if m not in chat:
            chat.append(m)
    for m in [ai.MODEL_PRO, ai.MODEL_FLASH, "deepseek-flash"]:
        if m not in ds:
            ds.append(m)
    for m in [media.SEEDREAM_MODEL, "doubao-seedream-5-0-pro-260628"]:
        if m not in image:
            image.append(m)
    cur_video = ""
    try:
        cur_video = str(db.get_settings().get("video_model") or "").strip()
    except Exception:
        pass
    if cur_video and cur_video not in video:
        video.append(cur_video)
    if ai.XAI_API_KEY and ai.MODEL_XAI_DEFAULT not in xai_models:
        xai_models.append(ai.MODEL_XAI_DEFAULT)
    data = {"chat": sorted(chat), "deepseek": sorted(ds), "image": sorted(image), "video": video,
            "xai": sorted(set(xai_models))}
    data["text"] = sorted(set(chat + ds + xai_models))  # 全部文本模型（主/备/压缩可任选，后端自动路由）
    data["ark"] = data["chat"]                 # 兼容旧字段
    data["all"] = sorted(set(data["chat"] + data["deepseek"] + data["image"] + xai_models))
    _models_fetching = False
    _models_cache["ts"] = now
    _models_cache["data"] = data
    return data


class SettingsIn(BaseModel):
    settings: dict


@app.post("/admin/api/settings")
def admin_settings_set(request: Request, data: SettingsIn):
    _require_admin(request)
    incoming = dict(data.settings or {})
    # 密钥类字段不接受从前端写库（统一由 secrets.env 管理，避免明文落库）
    for k in _SECRET_SETTING_KEYS:
        incoming.pop(k, None)
    db.set_settings(incoming)
    # 余额查询的 AK/SK 可能刚改过：清缓存让下次查询立即生效
    try:
        mini_balance._CACHE.clear()
    except Exception:
        pass
    return {"ok": True}


@app.get("/admin/api/logs")
def admin_logs(request: Request, lines: int = 300):
    _require_admin(request)
    return {"logs": logger.read_logs(lines)}


# ============ 管理端：朋友圈 ============

@app.get("/admin/api/moments")
def admin_moments(request: Request, limit: int = 50):
    _require_admin(request)
    rows = db.list_moments(0, max(1, min(limit, 200)))
    mids = [int(m["id"]) for m in rows]
    comments_map = db.list_moment_comments_for(mids)
    likes_map = db.list_moment_likes_for(mids)
    chars_map = {int(c["id"]): c for c in db.list_characters()}
    return {"moments": [_moment_dict(m, chars_map, comments_map, likes_map) for m in rows]}


@app.post("/admin/api/moments/delete")
def admin_moment_delete(request: Request, data: MessageIdIn):
    _require_admin(request)
    m = db.get_moment(data.id)
    if m:
        try:
            imgs = _json.loads(m.get("images") or "[]")
        except Exception:
            imgs = []
        for u in imgs:
            s = str(u)
            if s.startswith("/media/moments/"):
                try:
                    os.remove(os.path.join(DATA_DIR, "media", "moments", os.path.basename(s)))
                except Exception:
                    pass
    db.delete_moment_tasks_by_moment(data.id)  # 同时清掉该动态的排期任务，避免孤儿点赞/评论
    db.delete_moment(data.id)
    # 同步清理各角色主对话上下文中的这条朋友圈记忆
    try:
        for ch in db.list_characters(include_disabled=True):
            chat = db.get_chat(ch["id"])
            try:
                mem = _json.loads(chat.get("moment_memory") or "[]")
            except Exception:
                mem = []
            if not isinstance(mem, list) or not mem:
                continue
            kept = [it for it in mem if not (isinstance(it, dict) and int(it.get("id") or 0) == data.id)]
            if len(kept) != len(mem):
                db.update_chat(ch["id"], moment_memory=_json.dumps(kept, ensure_ascii=False))
    except Exception:
        pass
    return {"ok": True}


@app.post("/admin/api/moment_comments/delete")
def admin_moment_comment_delete(request: Request, data: MessageIdIn):
    _require_admin(request)
    db.delete_moment_comment(data.id)
    return {"ok": True}


# ============ 静态资源 ============

# ============ 管理端：群聊记录 ============

class GroupIdIn(BaseModel):
    gid: int = Field(ge=0, le=_MAX_ID)


@app.get("/admin/api/groups")
def admin_groups(request: Request):
    _require_admin(request)
    conn = db.get_conn()
    out = []
    for g in db.list_groups():
        members = db.group_members(g["id"])
        last = db.group_last_message(g["id"])
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM group_messages WHERE group_id=?", (g["id"],)).fetchone()
        out.append({
            "id": g["id"],
            "name": g.get("name") or "",
            "auto": bool(g.get("auto_enabled")),
            "created_at": g.get("created_at") or 0,
            "members": [{"id": m["cid"], "name": m.get("name") or ""} for m in members],
            "count": int(cnt["c"]) if cnt else 0,
            "last_ts": (last or {}).get("created_at") or 0,
        })
    return {"groups": out}


@app.get("/admin/api/group/messages")
def admin_group_messages(request: Request, gid: int, limit: int = 100, before_id: int = 0):
    _require_admin(request)
    limit = max(1, min(int(limit or 100), 500))
    g = db.get_group(gid)
    if not g:
        raise HTTPException(404, "not found")
    conn = db.get_conn()
    if before_id > 0:
        rows = conn.execute(
            "SELECT * FROM group_messages WHERE group_id=? AND id<? ORDER BY id DESC LIMIT ?",
            (gid, before_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM group_messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
            (gid, limit)).fetchall()
    rows = list(reversed([dict(r) for r in rows]))
    members = db.group_members(gid)
    names = {int(m["cid"]): (m.get("name") or "") for m in members}
    min_row = conn.execute(
        "SELECT MIN(id) AS m FROM group_messages WHERE group_id=?", (gid,)).fetchone()
    total_row = conn.execute(
        "SELECT COUNT(*) AS c FROM group_messages WHERE group_id=?", (gid,)).fetchone()
    return {
        "messages": [_group_msg_dict(r, names) for r in rows],
        "total": int(total_row["c"]) if total_row else 0,
        "min_id": int(min_row["m"] or 0) if min_row else 0,
    }


@app.post("/admin/api/group/messages/delete")
def admin_group_message_delete(request: Request, data: MessageIdIn):
    _require_admin(request)
    conn = db.get_conn()
    conn.execute("DELETE FROM group_messages WHERE id=?", (data.id,))
    conn.commit()
    return {"ok": True}


@app.post("/admin/api/group/messages/clear")
def admin_group_messages_clear(request: Request, data: GroupIdIn):
    _require_admin(request)
    conn = db.get_conn()
    conn.execute("DELETE FROM group_messages WHERE group_id=?", (data.gid,))
    conn.commit()
    return {"ok": True}


class MemoryIn(BaseModel):
    cid: int = Field(ge=0, le=_MAX_ID)
    items: list[str] = Field(default_factory=list)
    permanent: list[str] = Field(default_factory=list)


@app.get("/admin/api/character/memory")
def admin_character_memory(request: Request, cid: int):
    _require_admin(request)
    chat = db.get_chat(cid)
    try:
        mem = _json.loads(chat.get("commit_memory") or "[]")
    except Exception:
        mem = []
    texts = []
    for it in (mem if isinstance(mem, list) else []):
        t = str((it.get("t") if isinstance(it, dict) else it) or "").strip()
        if t:
            texts.append(t)
    return {"items": texts}


@app.post("/admin/api/character/memory")
def admin_character_memory_set(request: Request, data: MemoryIn):
    _require_admin(request)
    if not db.get_character(data.cid):
        raise HTTPException(404, "not found")
    items = [str(x).strip()[:80] for x in (data.items or []) if str(x).strip()][:30]
    with _commit_lock:  # 与后台抽取互斥，避免并发写回已删除的条目
        db.update_chat(data.cid, commit_memory=_json.dumps(
            [{"t": t, "ts": time.time()} for t in items], ensure_ascii=False))
    return {"ok": True, "count": len(items)}


@app.get("/admin/api/character/long_memory")
def admin_character_long_memory(request: Request, cid: int):
    _require_admin(request)
    chat = db.get_chat(cid)
    try:
        mem = _json.loads(chat.get("long_memory") or "[]")
    except Exception:
        mem = []
    items, permanent = [], []
    for it in (mem if isinstance(mem, list) else []):
        if isinstance(it, dict):
            t = str(it.get("t") or "").strip()
            if not t:
                continue
            (permanent if it.get("pin") else items).append(t)
        else:
            t = str(it or "").strip()
            if t:
                items.append(t)
    return {"items": items, "permanent": permanent}


def _parse_mem_lines(lines, force_pin: int = 0):
    out = []
    for x in (lines or []):
        s = str(x).strip()
        if not s:
            continue
        pin = force_pin
        if s[0] in ("★", "*", "⭐"):  # 兼容直接贴带 ★ 的行
            pin = 1
            s = s.lstrip("★*⭐").strip()
        if not s:
            continue
        # 管理端手写：清洗元标记；ts=0 表示"不随回溯丢弃"（与自动抽取区分）
        s = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(s)), "对方")[:80]
        if not s:
            continue
        out.append({"t": s, "pin": pin, "ts": 0})
    return out


@app.post("/admin/api/character/long_memory")
def admin_character_long_memory_set(request: Request, data: MemoryIn):
    _require_admin(request)
    if not db.get_character(data.cid):
        raise HTTPException(404, "not found")
    entries = _parse_mem_lines(data.items, 0)[:100] + _parse_mem_lines(data.permanent, 1)[:40]
    with _longmem_lock:  # 与后台抽取互斥
        db.update_chat(data.cid, long_memory=_json.dumps(entries, ensure_ascii=False))
    return {"ok": True, "count": len(entries), "pinned": sum(1 for e in entries if e["pin"])}


class IdsIn(BaseModel):
    ids: list[int] = []


# ============ 管理端：记忆库（聊天记录 → 可检索记忆）============

class MemEditIn(BaseModel):
    cid: int = Field(0, ge=0, le=_MAX_ID)
    gid: int = Field(0, ge=0, le=_MAX_ID)
    mid: int = Field(ge=0, le=_MAX_ID)
    content: str | None = None
    pin: int | None = None
    dormant: int | None = None


class MemReindexIn(BaseModel):
    cid: int = Field(0, ge=0, le=_MAX_ID)
    gid: int = Field(0, ge=0, le=_MAX_ID)
    rebuild: int = 0


def _mem_scope(cid: int, gid: int) -> str:
    return memory_kb.scope_of_group(gid) if int(gid or 0) else memory_kb.scope_of_char(int(cid or 0))


@app.get("/admin/api/memories")
def admin_memories(request: Request, cid: int = 0, gid: int = 0, q: str = "", limit: int = 100,
                   offset: int = 0, include_dormant: int = 1):
    """列出某角色/某群的记忆条目（"回忆"页数据源）"""
    _require_admin(request)
    if not cid and not gid:
        raise HTTPException(400, "cid 或 gid 必填")
    ent = memory_kb._load(_mem_scope(cid, gid))
    items = list(ent["items"])
    kw = (q or "").strip()
    if kw:
        items = [it for it in items if kw in str(it.get("content") or "")]
    if not int(include_dormant):
        items = [it for it in items if not int(it.get("dormant") or 0)]
    items.sort(key=lambda x: -float(x.get("ts") or 0))
    total = len(items)
    lim = max(1, min(int(limit), 500))
    off = max(0, int(offset))
    return {"total": total, "items": [
        {"mid": it.get("mid"), "ts": it.get("ts"), "role": it.get("role"),
         "content": it.get("content"), "pin": int(it.get("pin") or 0),
         "dormant": int(it.get("dormant") or 0), "recall_n": int(it.get("recall_n") or 0),
         "recall_ts": it.get("recall_ts") or 0, "gid": int(it.get("gid") or 0)}
        for it in items[off:off + lim]]}


def _mem_find(scope: str, mid: int):
    """返回 (items, 命中条目)；未命中时 hit 为 None"""
    items = list(memory_kb._load(scope)["items"])
    for it in items:
        if int(it.get("mid") or 0) == int(mid):
            return items, it
    return items, None


def _mem_update(scope: str, mid: int, content=None, pin=None, dormant=None) -> str:
    """编辑/置顶/淡出（管理端与 App 端共用）。改内容会自动重新嵌入。

    返回："ok" / "not_found"（记忆不存在）/ "empty"（内容为空）。
    分开返回是为了让接口能给出准确的状态码（404 vs 400）。
    """
    items, hit = _mem_find(scope, mid)
    if hit is None:
        return "not_found"
    if content is not None:
        txt = str(content or "").strip()
        if not txt:
            return "empty"
        hit["content"] = txt[:1200]
        try:
            v = knowledge._embed([txt])
            if v:
                hit["vec_b64"] = memory_kb._vec_pack(v[0])
                hit.pop("vector", None)
        except Exception:
            pass
    if pin is not None:
        hit["pin"] = 1 if int(pin) else 0
        if hit["pin"]:
            hit["dormant"] = 0
    if dormant is not None:
        hit["dormant"] = 1 if int(dormant) else 0
    return "ok" if memory_kb._save(scope, items) else "error"


def _mem_delete(scope: str, mid: int) -> int:
    """删除一条记忆，返回剩余条数（-1 表示未找到）"""
    before = list(memory_kb._load(scope)["items"])
    items = [it for it in before if int(it.get("mid") or 0) != int(mid)]
    if len(items) == len(before):
        return -1
    memory_kb._save(scope, items)
    return len(items)


@app.post("/admin/api/memories/update")
def admin_memories_update(request: Request, data: MemEditIn):
    """编辑/置顶/恢复某条记忆（改内容会自动重新嵌入，保证向量与文本一致）"""
    _require_admin(request)
    r = _mem_update(_mem_scope(data.cid, data.gid), data.mid, data.content, data.pin, data.dormant)
    if r == "not_found":
        raise HTTPException(404, "记忆不存在")
    if r == "empty":
        raise HTTPException(400, "内容不能为空")
    if r != "ok":
        raise HTTPException(500, "保存失败")
    return {"ok": True}


@app.post("/admin/api/memories/delete")
def admin_memories_delete(request: Request, data: MemEditIn):
    _require_admin(request)
    left = _mem_delete(_mem_scope(data.cid, data.gid), data.mid)
    if left < 0:
        raise HTTPException(404, "记忆不存在")
    return {"ok": True, "left": left}


@app.get("/admin/api/memory/overview")
def admin_memory_overview(request: Request):
    """记忆库总览：开关参数、各范围规模、最近召回"""
    _require_admin(request)
    stats = memory_kb.stats()
    scopes = {}
    for name in stats:
        items = memory_kb._load(name)["items"]
        scopes[name] = {
            "count": len(items),
            "dormant": sum(1 for it in items if int(it.get("dormant") or 0)),
            "pinned": sum(1 for it in items if int(it.get("pin") or 0)),
            "mb": stats[name].get("mb", 0),
            "last_mid": db.mem_index_get(name),
        }
    return {"config": memory_kb._cfg(), "scopes": scopes, "recalls": db.recall_log_recent(30)}


@app.post("/admin/api/memory/reindex")
def admin_memory_reindex(request: Request, data: MemReindexIn):
    """手动补索引（后台线程执行；rebuild=1 时先把水位清 0）"""
    _require_admin(request)
    if not data.cid and not data.gid:
        raise HTTPException(400, "cid 或 gid 必填")

    def work():
        try:
            scope = _mem_scope(data.cid, data.gid)
            if int(data.rebuild or 0):
                db.mem_index_set(scope, 0)
            memory_kb.index_scope(scope, cid=int(data.cid or 0), gid=int(data.gid or 0),
                                  is_group=bool(data.gid), max_batch=300, respect_daily=False)
        except Exception as e:
            _log(f"[memory] 手动补索引失败: {e}", level="WARNING")

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True, "started": True}


@app.post("/admin/api/messages/batch_delete")
def admin_messages_batch_delete(request: Request, data: IdsIn):
    _require_admin(request)
    ids = [int(x) for x in (data.ids or []) if int(x) > 0][:500]
    if not ids:
        return {"ok": True, "deleted": 0}
    conn = db.get_conn()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, character_id FROM messages WHERE id IN ({marks})", ids).fetchall()
    cur = conn.execute(f"DELETE FROM messages WHERE id IN ({marks})", ids)
    conn.commit()
    # 收敛：清掉被删消息未投递的分段 / 修正摘要水位与会话预览时间（与单条删一致）
    per_cid = {}
    for r in rows:
        per_cid.setdefault(int(r["character_id"]), []).append(int(r["id"]))
    for cid0, mids in per_cid.items():
        for mid in mids:
            try:
                db.delete_pending_by_ref(mid)
            except Exception:
                pass
        try:
            chat = db.get_chat(cid0) or {}
            if int(chat.get("summarized_until") or 0) > min(mids):
                db.update_chat(cid0, summary="", summarized_until=0)
            last = db.get_last_message(cid0)
            db.update_chat(cid0, last_msg_at=float(last.get("created_at") or 0) if last else 0)
        except Exception:
            pass
    return {"ok": True, "deleted": cur.rowcount}


@app.post("/admin/api/group/messages/batch_delete")
def admin_group_messages_batch_delete(request: Request, data: IdsIn):
    _require_admin(request)
    ids = [int(x) for x in (data.ids or []) if int(x) > 0][:500]
    if not ids:
        return {"ok": True, "deleted": 0}
    conn = db.get_conn()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, group_id FROM group_messages WHERE id IN ({marks})", ids).fetchall()
    cur = conn.execute(f"DELETE FROM group_messages WHERE id IN ({marks})", ids)
    conn.commit()
    # 收敛：已读水位回退到被删最小 id 之前（防"删了仍算已读"）
    per_gid = {}
    for r in rows:
        per_gid.setdefault(int(r["group_id"]), []).append(int(r["id"]))
    for gid0, mids in per_gid.items():
        try:
            conn.execute(
                "UPDATE group_state SET last_read_id = MIN(COALESCE(last_read_id,0), ?) WHERE group_id=?",
                (max(0, min(mids) - 1), gid0))
        except Exception:
            pass
    conn.commit()
    return {"ok": True, "deleted": cur.rowcount}


# ============ 管理端：世界与广播 ============

class WorldCreateIn(BaseModel):
    name: str = ""
    cids: list[int] = []


class WorldBroadcastIn(BaseModel):
    world_id: int = Field(ge=0, le=_MAX_ID)
    content: str = ""


class WorldSettingsIn(BaseModel):
    id: int = Field(ge=0, le=_MAX_ID)
    name: str | None = None
    cids: list[int] | None = None
    delete: bool = False


@app.get("/admin/api/worlds")
def admin_worlds(request: Request):
    _require_admin(request)
    out = []
    for w in db.list_worlds():
        members = db.world_members(w["id"])
        events = db.list_world_events(w["id"], 50)
        out.append({
            "id": w["id"],
            "name": w.get("name") or "",
            "created_at": w.get("created_at") or 0,
            "members": [{"id": m["cid"], "name": m.get("name") or ""} for m in members],
            "events": [{
                "id": e["id"],
                "content": e.get("content") or "",
                "sender": e.get("sender") or "system",
                "source_cid": e.get("source_cid") or 0,
                "ts": e.get("created_at") or 0,
            } for e in events],
        })
    return {"worlds": out}


@app.post("/admin/api/world/create")
def admin_world_create(request: Request, data: WorldCreateIn):
    _require_admin(request)
    cids = [int(x) for x in (data.cids or []) if int(x) > 0]
    wid = db.create_world((data.name or "").strip()[:30], cids)
    return {"ok": True, "id": wid}


@app.post("/admin/api/world/settings")
def admin_world_settings(request: Request, data: WorldSettingsIn):
    _require_admin(request)
    w = db.get_world(data.id)
    if not w:
        raise HTTPException(404, "not found")
    if data.delete:
        db.delete_world(data.id)
        return {"ok": True}
    if data.name is not None:
        db.update_world(data.id, name=(data.name or "").strip()[:30])
    if data.cids is not None:
        db.set_world_members(data.id, [int(x) for x in data.cids if int(x) > 0])
    return {"ok": True}


@app.post("/admin/api/world/broadcast")
def admin_world_broadcast(request: Request, data: WorldBroadcastIn):
    _require_admin(request)
    w = db.get_world(data.world_id)
    if not w:
        raise HTTPException(404, "not found")
    text = (data.content or "").strip()
    if not text:
        raise HTTPException(400, "空内容")
    text = ai.scrub_user_ref(ai.clean_meta_time(ai._strip_fake_tags(text)), "对方")
    eid = db.add_world_event(data.world_id, text[:200], sender="user")
    return {"ok": True, "id": eid}


@app.post("/admin/api/world/event/delete")
def admin_world_event_delete(request: Request, data: MessageIdIn):
    _require_admin(request)
    db.delete_world_event(data.id)
    return {"ok": True}


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/media", StaticFiles(directory=os.path.join(DATA_DIR, "media")), name="media")
if os.path.isdir(MINIAPP_DIR):
    app.mount("/miniapps", StaticFiles(directory=MINIAPP_DIR), name="miniapps")
if os.path.isdir(ADMIN_DIR):
    app.mount("/admin", StaticFiles(directory=ADMIN_DIR, html=True), name="admin")


# ============ 语音消息（用户发语音：模型转述 → 发送 / 仅转文字） ============

@app.post("/api/chat/voice")
async def chat_voice(character_id: int = Form(...), mode: str = Form("send"),
                     duration: float = Form(0), file: UploadFile = File(...)):
    c = db.get_character(character_id)
    if not c:
        raise HTTPException(404, "not found")
    if _upload_too_big(file, 10 * 1024 * 1024):
        raise HTTPException(400, "语音文件太大（>10MB）")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    ext = os.path.splitext(file.filename or "")[1].lower() or ".m4a"
    if ext not in (".m4a", ".aac", ".mp3", ".wav", ".ogg", ".amr", ".opus", ".mp4"):
        ext = ".m4a"
    url = media.save_user_audio(data, ext)
    fs = media.media_fs_path(url)
    chat = db.get_chat(character_id)
    voice_desc = ai._voice_desc_enabled(chat)
    # 录完才处理：转文字输入模式必须有文本；发送模式下按"语音转述"开关决定是否转述
    text = ""
    if mode == "input" or voice_desc:
        text = await run_in_threadpool(media.transcribe_audio, fs) if fs else ""
    if mode == "input":
        if not text:
            return {"ok": False, "error": "没听清，再说一次？"}
        return {"ok": True, "text": text}
    if not text and voice_desc:
        return {"ok": False, "error": "没听清，再说一次？"}
    msg_id = db.add_message(character_id, "user", "voice", text, url, float(duration or 0))
    # 原子取走未发出的分段并清空其余延迟任务（原 pop+clear 两步存在并发窗口）
    _left, _dropped_voice = db.take_script_pending_and_clear(character_id)
    for _ in range(_dropped_voice):  # 被丢弃的语音任务退还预留额度，避免配额被静默吞掉
        _voice_quota_refund(character_id)
    if _left:  # 补发合并文本
        db.add_pending(character_id, _left, kind="script", ts=time.time())
    _wake_cancel(character_id)
    db.add_pending(character_id, text or "（语音）", kind="reply", ts=time.time(), ref_id=msg_id)
    _log(f"[chat] 用户 -> {c['name']}: [语音{'转述' if text else '直听'}] {(text or '（原音频交给模型）')[:30]}")
    return {"ok": True, "text": text, "url": url, "msg_id": msg_id}


# ============ 群聊（Groups） ============

class GroupCreateIn(BaseModel):
    name: str = ""
    cids: list[int] = []


class GroupSendIn(BaseModel):
    gid: int
    content: str = Field("", max_length=2000)


class GroupSettingsIn(BaseModel):
    gid: int
    name: str | None = None
    auto: bool | None = None
    cids: list[int] | None = None
    delete: bool = False


def _group_msg_dict(r: dict, names: dict) -> dict:
    cid = int(r.get("cid") or 0)
    return {
        "id": r["id"],
        "role": r.get("role") or "character",
        "cid": cid,
        "name": names.get(cid, ""),
        "kind": r.get("kind") or "text",
        "content": r.get("content") or "",
        "media_url": r.get("media_url") or "",
        "img_desc": r.get("img_desc") or "",
        "ts": r.get("created_at") or 0,
    }


def _group_msg_body(m: dict) -> str:
    """群消息的文本表达（图片/视频带描述，供上下文与提示词使用）"""
    kind = m.get("kind") or "text"
    content = (m.get("content") or "").strip()
    desc = (m.get("img_desc") or "").strip()
    if kind == "image":
        return "[图片]" + (desc or content)
    if kind == "video":
        return "[视频]" + (desc or content)
    if kind == "voice":
        return "[语音]" + content
    if kind == "sticker":
        return "[表情]"
    return content


def _group_ctx(gid: int):
    """群上下文：群信息、成员、名字表、最近聊天文本行"""
    g = db.get_group(gid)
    members = db.group_members(gid)
    names = {int(m["cid"]): (m.get("name") or "角色") for m in members}
    lines = []
    for r in db.get_group_recent(gid, 40):
        c = _group_msg_body(r)
        if not c:
            continue
        who = "用户" if r.get("role") == "user" else names.get(int(r.get("cid") or 0), "群友")
        stamp = ai._msg_time_label(r.get("created_at"))
        prefix = f"[{stamp}] " if stamp else ""
        lines.append(f"{prefix}{who}：{c[:80]}")
    return g, members, names, lines


@app.get("/api/groups")
def groups_list():
    groups = db.list_groups()
    members_map = db.all_group_members()
    last_map = db.all_group_last_messages()
    unread_map = db.all_group_unread()
    out = []
    for g in groups:
        gid = int(g["id"])
        members = members_map.get(gid, [])
        last = last_map.get(gid)
        out.append({
            "id": gid,
            "name": g.get("name") or "",
            "auto": bool(g.get("auto_enabled")),
            "created_at": g.get("created_at") or 0,
            "unread": unread_map.get(gid, 0),
            "members": [{
                "id": m["cid"],
                "name": m.get("name") or "",
                "avatar": _char_avatar_url(m.get("avatar") or ""),
            } for m in members],
            "last": ({
                "id": last["id"],
                "content": last.get("content") or "",
                "kind": last.get("kind") or "text",
                "role": last.get("role") or "",
                "cid": last.get("cid") or 0,
                "ts": last.get("created_at") or 0,
            } if last else None),
        })
    return {"groups": out}


@app.post("/api/group/create")
def group_create(data: GroupCreateIn):
    cids = [int(x) for x in (data.cids or []) if int(x) > 0][:10]
    if not cids:
        raise HTTPException(400, "至少选择一个角色")
    chars = []
    for cid in cids:
        c = db.get_character(cid)
        if c and c.get("enabled"):
            chars.append(c)
    if len(chars) < 2:
        raise HTTPException(400, "至少选择两个可用角色")
    name = (data.name or "").strip()[:30]
    if not name:
        name = ("、".join([c["name"] for c in chars[:3]]))[:30]
    gid = db.create_group(name, [c["id"] for c in chars])
    _log(f"[group] 创建群聊 #{gid}「{name}」成员={[c['id'] for c in chars]}")
    return {"ok": True, "id": gid}


@app.get("/api/group/messages")
def group_messages_api(gid: int, after_id: int = 0):
    g = db.get_group(gid)
    if not g:
        raise HTTPException(404, "not found")
    rows = db.get_group_messages(gid, after_id)
    members = db.group_members(gid)
    names = {int(m["cid"]): (m.get("name") or "角色") for m in members}
    # 打开/轮询群聊即视为已读：把已读水位推进到当前最新一条
    try:
        last = db.group_last_message(gid)
        if last:
            db.group_read_mark(gid, int(last["id"]))
    except Exception:
        pass
    return {
        "name": g.get("name") or "",
        "auto": bool(g.get("auto_enabled")),
        "total": db.count_group_messages(gid),
        "members": [{
            "id": m["cid"],
            "name": m.get("name") or "",
            "avatar": _char_avatar_url(m.get("avatar") or ""),
        } for m in members],
        "messages": [_group_msg_dict(r, names) for r in rows],
    }


@app.post("/api/group/send")
def group_send(data: GroupSendIn):
    g = db.get_group(data.gid)
    if not g:
        raise HTTPException(404, "not found")
    text = (data.content or "").strip()
    if not text:
        raise HTTPException(400, "empty")
    if len(text) > 2000:
        raise HTTPException(400, "消息过长（上限 2000 字）")
    msg_id = db.add_group_message(data.gid, "user", 0, "text", text)
    _log(f"[group] 用户 -> #{data.gid}「{g.get('name')}」: {text[:40]}")
    threading.Thread(target=_group_reply_async, args=(data.gid,), daemon=True).start()
    # 同一世界：群聊用户消息也结合上下文识别世界事件
    threading.Thread(target=_world_recognize_async, args=(0, text, 0, data.gid), daemon=True).start()
    return {"ok": True, "msg_id": msg_id}


@app.post("/api/group/settings")
def group_settings(data: GroupSettingsIn):
    g = db.get_group(data.gid)
    if not g:
        raise HTTPException(404, "not found")
    if data.delete:
        db.delete_group(data.gid)
        _log(f"[group] 删除群聊 #{data.gid}")
        return {"ok": True}
    fields = {}
    if data.name is not None:
        fields["name"] = (data.name or "").strip()[:30]
    if data.auto is not None:
        fields["auto_enabled"] = 1 if data.auto else 0
    if fields:
        db.update_group(data.gid, **fields)
    if data.cids is not None:
        keep = [int(x) for x in data.cids if int(x) > 0]
        db.set_group_members(data.gid, keep)
    return {"ok": True}


_group_state_lock = threading.Lock()
_group_pending = set()   # 有待接话的用户消息（生成期间新来的消息并在这里）
_group_workers = set()   # 正在生成（接话/自动发言）的群


def _group_reply_async(gid: int):
    """用户群聊发言后：安排 1~2 名角色接话。
    同群串行；生成期间的新消息自动并入下一轮（不丢，也不会并发交错）"""
    spawn = False
    with _group_state_lock:
        _group_pending.add(gid)
        if gid not in _group_workers:
            _group_workers.add(gid)
            spawn = True
    if not spawn:
        _log(f"[group] 群 {gid} 正在生成，本条消息并入下一轮接话")
        return
    threading.Thread(target=_group_reply_worker, args=(gid,), daemon=True).start()


def _group_reply_worker(gid: int):
    while True:
        with _group_state_lock:
            if gid not in _group_pending:
                _group_workers.discard(gid)
                return
            _group_pending.discard(gid)
        try:
            _group_reply_inner(gid)
        except Exception as e:
            _log(f"[group] 回复任务失败 gid={gid}: {e}", level="WARNING")


def _group_try_worker(gid: int) -> bool:
    """尝试占用群生成名额；已有生成在跑返回 False（用于自动发言/接话链等旁路）"""
    with _group_state_lock:
        if gid in _group_workers:
            return False
        _group_workers.add(gid)
        return True


def _group_release_worker(gid: int):
    """释放群生成名额；若期间有新消息则自动补一轮接话"""
    more = False
    with _group_state_lock:
        _group_workers.discard(gid)
        if gid in _group_pending:
            _group_workers.add(gid)
            more = True
    if more:
        threading.Thread(target=_group_reply_worker, args=(gid,), daemon=True).start()


def _group_chain_when_idle(gid: int, cid: int, tries: int = 4, gap: float = 7.0) -> bool:
    """私聊分享到群后引发另一位角色接话：大概率触发（强制一轮）；
    群正忙时短暂等待重试，避免接话被并发生成吞掉"""
    for i in range(max(1, tries)):
        if _group_try_worker(gid):
            try:
                sent = _group_peer_chain(gid, int(cid), 1, force=True, ignore_budget=True)
                if sent <= 0:
                    _log(f"[group] 私聊触发的接话未产生（无其他成员或生成空回复） gid={gid}")
                return sent > 0
            except Exception as e:
                _log(f"[group] 私聊触发的群内接话失败 gid={gid}: {e}", level="WARNING")
                return False
            finally:
                _group_release_worker(gid)
        if i < tries - 1:
            time.sleep(gap)
    _log(f"[group] 群 {gid} 持续繁忙，本次私聊触发的接话取消")
    return False


_GROUP_TRIVIAL_WORDS = {
    "嗯", "嗯嗯", "嗯呐", "哦", "哦哦", "啊", "啊啊", "噢", "好", "好的", "好吧", "行", "行的",
    "哈哈", "哈哈哈", "哈哈哈哈", "呵呵", "嘿", "晚安", "早安", "早", "在", "对", "是", "收到",
    "ok", "okk", "666", "谢谢", "谢啦",
}


def _group_is_trivial(text: str) -> bool:
    """用户群消息是否「不值得任何人回应」（纯语气词/表情/道别/极短寒暄）。

    为什么不用决策模型：实测（2026-10-24）「该回」的消息打分 0.74~0.86，
    「不该回」的 0.65~0.75，两者完全重叠，任何单一阈值都会误杀正常消息。
    因此改用确定性规则：只在明显是语气词时静默，其余一律交给角色回应。
    """
    t = (text or "").strip()
    if not t:
        return True
    clean = re.sub(r"[\s\W_]+", "", t, flags=re.UNICODE)  # \W 保留中文
    if not clean:
        return True
    if clean.lower() in _GROUP_TRIVIAL_WORDS:
        return True
    # 3 字以内、且不是问句/不点名 → 视为寒暄，不回应
    if len(clean) <= 3 and not re.search(r"[?？吗呢吧]|你们|你俩|大家|在不在", t):
        return True
    return False


def _group_needs_second(text: str) -> bool:
    """用户消息是否在问整群人/点名：需要第二个角色也搭一句（让群里像多人对话）"""
    t = text or ""
    return bool(re.search(r"[?？]|吗|呢|在不在|你们|你俩|大家|谁", t))


# 明确的「问句 / 直接找人」：一律必回，不交给模型判定
# （实测豆包在空群语境下会把「你俩在不在？」判成 0.02，这类消息必须兜底）
_GROUP_FORCE_REPLY_RE = re.compile(
    r"[?？]|在不在|在吗|在干嘛|你们|你俩|大家|谁|怎么样|怎么办|为什么|能不能|要不要|是不是")


def _group_force_reply(text: str) -> bool:
    return bool(_GROUP_FORCE_REPLY_RE.search(text or ""))


def _group_reply_inner(gid: int):
    try:
        time.sleep(random.uniform(1.0, 2.5))
        g, members, names, lines = _group_ctx(gid)
        if not g or not members:
            return
        last_user = None
        for r in db.get_group_recent(gid, 10):
            if r.get("role") == "user":
                last_user = r
        user_text = _group_msg_body(last_user) if last_user else ""
        # 值不值得回应：用确定性过滤（决策模型实测无法区分「该回」和「不该回」——
        # 前者 0.74~0.86、后者 0.65~0.75 完全重叠，阈值 0.8 会误杀「你们吃饭了吗」这类正常消息）
        if _group_is_trivial(user_text):
            _log(f"[group] 用户消息为纯语气词/过短，不回应 gid={gid}: {user_text[:20]!r}")
            return
        # 双模型判定（豆包 SystemOne 主判、百炼兜底）：
        # 实测豆包「该回」0.58~1.00 /「不该回」0.00~0.01；百炼两者重叠（AUC 0.53）故仅作兜底。
        # 问句/直接找人属于必须回应的情况，走确定性判断，不问模型（模型在空语境下会误判）。
        if not _group_force_reply(user_text):
            _wp = ai.decision_probs_dual(_group_gate_state(lines), {"w": _GROUP_WORTH_INSTR},
                                         timeout=8).get("w")
            if _wp is not None and _wp < _GROUP_WORTH_MIN:
                _log(f"[group] 判定用户消息无需回应 gid={gid}（概率 %.2f）" % _wp)
                return
        # 上一位发言的角色（用于轮换，避免同一人一直抢话）
        prev_cid = 0
        for r in reversed(db.get_group_recent(gid, 6)):
            if r.get("role") == "character":
                prev_cid = int(r.get("cid") or 0)
                break
        # 决策模型②：谁适合接这句话（点名优先；概率都低时至少回 1 条）
        pool = members[:]
        named_names = []
        if user_text:
            named_names = [str(names.get(int(m["cid"])) or "") for m in pool
                           if str(names.get(int(m["cid"])) or "")
                           and str(names.get(int(m["cid"]))) in user_text]
        probs = _group_speak_probs(pool, names, lines, "现在适合「%s」接用户这句话、在群里回应吗？")

        def _rank(m):
            cid = int(m["cid"])
            named = 1 if str(names.get(cid) or "") in named_names else 0
            p = probs.get(str(cid), 0.0)
            # 轮换：上一条发言的角色降权，避免同一人永远抢在前面
            return (named, p - (0.08 if cid == prev_cid else 0.0))

        pool = sorted(pool, key=_rank, reverse=True)
        cfg = _group_auto_cfg()
        qual = sum(1 for m in pool if probs.get(str(int(m["cid"])), 0.0) >= _GROUP_SPEAK_MIN)
        # 默认只让 1 位回应；用户在问整群人/点名时才让两位都回，避免刷屏
        _cap = 2 if _group_needs_second(user_text) else 1
        n = max(1, min(qual or 1, int(cfg["reply_max"]), len(pool), _cap))
        last_replied = 0
        got = 0
        for i, m in enumerate(pool):
            if got >= n or i >= n + 2:
                break  # 目标条数已够；生成失败/空回复时最多再补位 2 人
            _mcid = int(m["cid"])
            ch = db.get_character(_mcid)
            if not ch or not ch.get("enabled"):
                continue  # 停用/已删角色不再在群里发言
            _lk = _proc_lock_for(_mcid)
            if not _lk.acquire(blocking=False):
                continue  # 该角色正在私聊生成，跳过这次群接话（防同角色双上下文并发）
            try:
                reply = ai.generate_group_reply(ch, g, names, lines, user_text)
            except Exception as e:
                _log(f"[group] 生成失败 gid={gid} cid={m['cid']}: {e}", level="WARNING")
                continue
            finally:
                _lk.release()
            if reply:
                db.add_group_message(gid, "character", int(m["cid"]), "text", reply)
                lines.append(f"{names.get(int(m['cid']), '?')}：{reply[:80]}")
                _log(f"[group] {names.get(int(m['cid']), '?')} -> #{gid}: {reply[:40]!r}")
                last_replied = int(m["cid"])
                got += 1
                threading.Thread(target=_world_recognize_async,
                                 args=(0, reply, int(m["cid"]), gid), daemon=True).start()
                if got < n:
                    time.sleep(random.uniform(1.5, 4.0))
        # 用户消息触发的回复之后：由决策模型决定是否再让另一名角色搭一句（最多 1 句）
        if last_replied:
            try:
                _group_peer_chain(gid, last_replied, 1)
            except Exception as e:
                _log(f"[group] 互聊链失败 gid={gid}: {e}", level="WARNING")
    except Exception as e:
        _log(f"[group] 回复任务失败 gid={gid}: {e}", level="WARNING")


def _group_auto_async(gid: int, cid: int, revive: bool = False):
    """群聊角色主动发言（后台闲聊，生成中则跳过；若期间有新消息则立刻补一轮接话）"""
    if not _group_try_worker(gid):
        _log(f"[group] 群 {gid} 正在生成，跳过本次自动发言")
        return
    try:
        _group_auto_inner(gid, cid, revive)
    except Exception as e:
        _log(f"[group] 自动发言失败 gid={gid}: {e}", level="WARNING")
    finally:
        _group_release_worker(gid)


def _group_auto_inner(gid: int, cid: int, revive: bool = False):
    try:
        time.sleep(random.uniform(0.5, 3.0))
        g, members, names, lines = _group_ctx(gid)
        ch = db.get_character(int(cid))
        if not g or not ch or not ch.get("enabled"):
            return  # 停用/已删角色不再自动发言
        _lk = _proc_lock_for(int(cid))
        if not _lk.acquire(blocking=False):
            return  # 该角色正在私聊生成，跳过（防同角色双上下文并发）
        try:
            reply = ai.generate_group_reply(ch, g, names, lines, "", revive=revive)
        finally:
            _lk.release()
        if reply:
            db.add_group_message(gid, "character", int(cid), "text", reply)
            _log(f"[group] 自动发言 #{gid} {names.get(int(cid), '?')}: {reply[:40]!r}")
            threading.Thread(target=_world_recognize_async,
                             args=(0, reply, int(cid), gid), daemon=True).start()
            # 是否再让另一个角色接话，交给决策模型（轮数上限 + 每日额度仍在）
            try:
                _group_peer_chain(gid, int(cid), _group_chain_max())
            except Exception as e:
                _log(f"[group] 互聊链失败 gid={gid}: {e}", level="WARNING")
    except Exception as e:
        _log(f"[group] 自动发言失败 gid={gid}: {e}", level="WARNING")


def _group_auto_cfg() -> dict:
    """群自动发言可调参数（管理页设置，越界自动兜底）"""
    try:
        s = db.get_settings()
    except Exception:
        s = {}

    def _f(key, d, lo, hi):
        try:
            return min(max(float(s.get(key, d)), lo), hi)
        except Exception:
            return d

    def _i(key, d, lo, hi):
        try:
            return min(max(int(float(s.get(key, d))), lo), hi)
        except Exception:
            return d

    return {
        "idle_min": _f("group_auto_idle_min", 120, 10, 3600),
        "interval": _f("group_auto_interval", 240, 30, 7200),
        "window": _f("group_auto_window_hours", 6, 1, 48) * 3600.0,
        "reply_max": _i("group_reply_max", 2, 1, 3),
    }


_group_revive_roll = {}   # gid -> 上次「冷群复活」判定时间
_group_speak_eval = {}    # gid -> 上次“是否发言”决策模型判定时间（限频，避免每个 tick 都调模型）

_GROUP_WORTH_MIN = 0.30   # 用户群消息「值得回应」阈值。双模型实测（2026-10-24，24 条标注）：
                          # 豆包 SystemOne 该回 0.58~1.00 / 不该回 0.00~0.01；取 0.30 留足余量。
_GROUP_SPEAK_MIN = 0.6    # 角色“适合主动发言/接话”阈值（实测：活跃白天≈0.82 / 深夜≈0.03）
_GROUP_WORTH_INSTR = ("用户这条群聊消息值得让群里的角色们回应吗？"
                      "纯语气词/表情/单字敷衍（嗯、哦、哈哈、666）、单方面道别（晚安、我先睡了）不值得回应；"
                      "提问、找人或确认对方在不在（在吗、你们在不在、有人吗）、分享生活、表达情绪、求助、邀约都值得回应。")


def _group_gate_state(lines: list, idle_desc: str = "") -> str:
    """给决策模型的群聊上下文（最近记录 + 当前时间 + 可选的安静时长说明）"""
    try:
        _now = _wake_local_now(_wake_cfg())
        stamp = "现在是 %s。" % _now.strftime("%m月%d日 %H:%M")
    except Exception:
        stamp = ""
    ctx = "\n".join(lines[-12:]) if lines else "（群里还没有消息）"
    s = "群聊最近记录（最新在下）：\n" + ctx
    if stamp:
        s += "\n" + stamp
    if idle_desc:
        s += "\n" + idle_desc
    return s


def _group_speak_probs(members: list, names: dict, lines: list, instructions_tpl: str,
                       idle_desc: str = "") -> dict:
    """一次调用判定每个成员“适合发言”的概率：{cid字符串: 概率}；失败返回 {}"""
    qs = {}
    for m in members:
        cid = int(m["cid"])
        qs[str(cid)] = instructions_tpl % names.get(cid, "角色")
    return ai.decision_probs_dual(_group_gate_state(lines, idle_desc), qs, timeout=8)


def _group_pick_speaker(members: list, names: dict, lines: list, revive: bool,
                        idle_desc: str = ""):
    """选一名适合现在主动发言的成员：返回 (cid, 概率)；都非常低/失败返回 (0, 最高概率)"""
    tpl = ("现在适合「%s」主动在群里起一个新话题吗？" if revive
           else "现在适合「%s」主动在群里说一句话吗？")
    probs = _group_speak_probs(members, names, lines, tpl, idle_desc)
    if not probs:
        return 0, 0.0
    best_cid, best_p = 0, 0.0
    for cid_s, p in sorted(probs.items(), key=lambda kv: (-kv[1], int(kv[0]))):
        best_p = max(best_p, float(p))
        if float(p) >= _GROUP_SPEAK_MIN:
            return int(cid_s), float(p)
    return 0, best_p


def _group_tick():
    """后台节拍：开启自动的群里按 冷却+每日额度+决策模型 让某个角色说一句；
    夜间 00:00~07:00 免打扰；超出活跃窗口的冷群按小时判定是否「起新话题」。"""
    try:
        groups = db.list_groups()
    except Exception:
        return
    cfg = _group_auto_cfg()
    now = time.time()
    for g in groups:
        try:
            gid = int(g["id"])
            if not g.get("auto_enabled"):
                continue
            last = db.group_last_message(gid)
            if not last:
                continue
            idle = now - float(last.get("created_at") or 0)
            if idle < cfg["idle_min"]:
                continue
            revive = idle > cfg["window"]
            last_auto = db.group_last_auto_ts(gid)
            if last_auto and now - last_auto < cfg["interval"]:
                continue
            day = _group_day_key()
            if not db.group_auto_budget(gid, day, _group_daily_limit()):
                continue
            # 夜间免打扰：00:00~07:00 一律不主动发言（冷群复活也仅算白天）
            local_now = _wake_local_now(_wake_cfg())
            if local_now.hour < 7:
                continue
            if revive:
                # 冷群复活：每小时最多判定一次（是否开口交给决策模型）
                if now - float(_group_revive_roll.get(gid, 0) or 0) < 3600:
                    continue
                _group_revive_roll[gid] = now
            # 限频：同一群两次“是否发言”判定至少间隔 interval，避免每个 tick 都调决策模型
            if now - float(_group_speak_eval.get(gid, 0) or 0) < cfg["interval"]:
                continue
            _group_speak_eval[gid] = now
            _g, members, names, lines = _group_ctx(gid)
            if not members:
                continue
            idle_desc = "群已经安静了约 %.0f 分钟。" % (idle / 60.0)
            pick_cid, pick_p = _group_pick_speaker(members, names, lines, revive, idle_desc)
            if not pick_cid:
                _log(f"[group] 决策模型判定暂不发言 gid={gid}（最高概率 %.2f）" % pick_p)
                continue
            db.group_mark_auto(gid, day)
            _log(f"[group] 决策模型判定发言 gid={gid} cid={pick_cid}（概率 %.2f）" % pick_p)
            threading.Thread(target=_group_auto_async, args=(gid, pick_cid, revive), daemon=True).start()
        except Exception as e:
            _log(f"[group] tick 失败 gid={g.get('id')}: {e}", level="WARNING")


def _group_day_key() -> str:
    """群聊额度日切：与闹铃/朋友圈同一时区口径（wake_tz）"""
    try:
        return _wake_local_now(_wake_cfg()).strftime("%Y-%m-%d")
    except Exception:
        return time.strftime("%Y-%m-%d")


def _group_chain_max() -> int:
    """每次互聊最多接几句（默认 2，硬上限 4）"""
    try:
        return min(max(int(float(db.get_settings().get("group_chain_max", 2))), 0), 4)
    except Exception:
        return 2


def _group_daily_limit() -> int:
    """群内角色自动消息（主动发言+互聊）每日额度（默认 15，上限 100）"""
    try:
        return min(max(int(float(db.get_settings().get("group_auto_daily_limit", 15))), 0), 100)
    except Exception:
        return 15


def _group_peer_chain(gid: int, first_cid: int, max_rounds: int = 2, force: bool = False,
                      ignore_budget: bool = False) -> int:
    """角色之间互相接话（同步执行）。是否继续、由谁接，均交给决策模型判定；
    仅保留轮数硬上限与每日额度。force=True 用于用户显式要求的接话（私聊分享到群），跳过判定直接接一句。
    返回实际发出的接话条数。"""
    g = db.get_group(gid)
    if not g or not g.get("auto_enabled") or max_rounds <= 0:
        return 0
    day = _group_day_key()
    last_cid = int(first_cid or 0)
    sent = 0
    for _i in range(int(max_rounds)):
        if not ignore_budget and not db.group_auto_budget(gid, day, _group_daily_limit()):
            break
        _g, members, names, lines = _group_ctx(gid)
        pool = [m for m in members if int(m.get("cid") or 0) != last_cid]
        if not pool:
            break
        last = db.group_last_message(gid)
        peer_text = _group_msg_body(last) if last else ""
        peer_name = ""
        if last and last.get("role") == "character":
            peer_name = names.get(int(last.get("cid") or 0), "")
        # 决策模型：谁适合接着回一句（force 场景跳过判定）
        pick = None
        if force:
            pick = pool[0]
        else:
            probs = _group_speak_probs(pool, names, lines, "现在适合「%s」接着群里这句话再回一句吗？")
            if probs:
                cid_s, p = max(probs.items(), key=lambda kv: (kv[1], -int(kv[0])))
                if float(p) >= _GROUP_SPEAK_MIN:
                    pick = next((m for m in pool if int(m["cid"]) == int(cid_s)), None)
        if pick is None:
            break
        ch = db.get_character(int(pick["cid"]))
        if not ch:
            break
        _lk = _proc_lock_for(int(pick["cid"]))
        if not _lk.acquire(blocking=False):
            # 该角色正在私聊生成：跳过这次接话，避免同一角色两份上下文并发生成
            _log(f"[group] 角色 {pick['cid']} 正在生成，跳过本次互聊")
            break
        try:
            reply = ai.generate_group_reply(ch, _g, names, lines, "",
                                            peer_name=peer_name, peer_text=peer_text)
        except Exception as e:
            _log(f"[group] 互聊生成失败 gid={gid} cid={pick['cid']}: {e}", level="WARNING")
            break
        finally:
            _lk.release()
        if not reply:
            break
        time.sleep(random.uniform(2.0, 5.0))
        db.add_group_message(gid, "character", int(pick["cid"]), "text", reply)
        db.group_mark_auto(gid, day)
        _log(f"[group] {names.get(int(pick['cid']), '?')} 接话 -> #{gid}: {reply[:40]!r}")
        threading.Thread(target=_world_recognize_async,
                         args=(0, reply, int(pick["cid"]), gid), daemon=True).start()
        last_cid = int(pick["cid"])
        sent += 1
    return sent


# ============ 世界（App 端查看共享动态） ============
@app.get("/api/worlds")
def worlds_list_api():
    chars = {int(c["id"]): (c.get("name") or "") for c in db.list_characters()}
    out = []
    for w in db.list_worlds():
        wid = int(w["id"])
        try:
            events = db.list_world_events(wid, 50)
        except Exception:
            events = []
        evs = []
        for e in events:
            sender = e.get("sender") or "system"
            scid = int(e.get("source_cid") or 0)
            if sender == "user":
                sname = "我"
            elif sender == "character":
                sname = chars.get(scid) or "角色"
            else:
                sname = "系统"
            evs.append({
                "id": e["id"],
                "sender": sender,
                "sender_name": sname,
                "content": e.get("content") or "",
                "ts": e.get("created_at") or 0,
            })
        out.append({
            "id": wid,
            "name": w.get("name") or "",
            "events": evs,
        })
    return {"worlds": out}


# ============ 小程序（Mini Programs） ============


@app.get("/api/miniapps")
def miniapps_list():
    """小程序目录（manifest.json）"""
    try:
        with open(os.path.join(MINIAPP_DIR, "manifest.json"), "r", encoding="utf-8") as f:
            d = _json.load(f)
        apps = d.get("apps") if isinstance(d, dict) else None
        if not isinstance(apps, list):
            apps = []
    except Exception:
        apps = []
    return {"apps": apps}


class MiniKvIn(BaseModel):
    app: str = Field("", max_length=32)
    key: str = Field("", max_length=64)
    value: Any = None


@app.get("/api/mini/chars")
def mini_chars():
    """小程序用：只返回角色 id/name（票据可访问；不暴露完整 bootstrap）"""
    try:
        rows = db.list_characters()
    except Exception:
        rows = []
    return {"characters": [{"id": int(c["id"]), "name": c.get("name") or "角色"} for c in rows]}


@app.get("/api/mini/kv")
def mini_kv_read(app: str = "", key: str = ""):
    raw = db.mini_kv_get(app, key)
    val = None
    if raw:
        try:
            val = _json.loads(raw)
        except Exception:
            val = raw
    return {"value": val}


@app.post("/api/mini/kv")
def mini_kv_write(data: MiniKvIn):
    if not (data.app or "").strip() or not (data.key or "").strip():
        raise HTTPException(400, "app/key required")
    try:
        raw = _json.dumps(data.value, ensure_ascii=False)
    except Exception:
        raise HTTPException(400, "value not json")
    if len(raw) > 200000:
        raise HTTPException(400, "value too large")
    try:
        db.mini_kv_set(data.app, data.key, raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class MiniEventIn(BaseModel):
    app: str = Field("", max_length=32)
    content: str = Field("", max_length=200)
    cid: int = Field(0, ge=0, le=_MAX_ID)


@app.get("/api/mini/events")
def mini_events_read(limit: int = 20, days: int = 7):
    rows = db.mini_events_recent(limit, days)
    return {"events": [{"id": r["id"], "app": r["app"], "content": r["content"],
                        "cid": int(r.get("cid") or 0), "ts": r["created_at"]} for r in rows]}


@app.post("/api/mini/events")
def mini_events_write(data: MiniEventIn):
    eid = db.mini_event_add(data.app, data.content, data.cid)
    return {"ok": True, "id": eid}


@app.get("/api/mini/weather")
def mini_weather_get(city: str = ""):
    if not (city or "").strip():
        raise HTTPException(400, "city required")
    d = mini_weather.fetch(city)
    if not d:
        return {"ok": False, "error": "查不到这个城市的天气，换个名字试试"}
    out = dict(d)
    out["ok"] = True
    return out


@app.get("/api/mini/balance")
def mini_balance_api(force: int = 0):
    """各模型平台余额（缓存 60 秒；force=1 强制刷新，10 秒防连点）"""
    return {"providers": mini_balance.providers(force=bool(force)), "now": time.time()}


# ============ 收藏（网盘） ============

class FavRenameIn(BaseModel):
    id: int = Field(ge=0, le=_MAX_ID)
    name: str = Field("", max_length=200)


def _stream_upload_to(f, path: str, max_bytes: int) -> int:
    """流式落盘（分块拷贝，超限/异常即中止并删除）；返回字节数，0 表示失败/超限"""
    total = 0
    ok = True
    try:
        with open(path, "wb") as out:
            while True:
                chunk = f.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    ok = False
                    break
                out.write(chunk)
    except Exception:
        ok = False
    if not ok or total <= 0:
        try:
            os.remove(path)
        except Exception:
            pass
        return 0
    return total


FAV_MAX_BYTES = 200 * 1024 * 1024  # 单文件上限 200MB
# 收藏允许的扩展名白名单（不收 .svg/.html/.js 等可被浏览器执行的类型，防存储型 XSS）
_FAV_SAFE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif",
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".amr",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv", ".json", ".zip", ".rar", ".7z", ".apk", ".epub",
}


def _fav_kind(ext: str) -> str:
    e = (ext or "").lower()
    if e in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"):
        return "image"
    if e in (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"):
        return "video"
    if e in (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".amr"):
        return "audio"
    return "file"


def _fav_dict(r: dict) -> dict:
    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "kind": r.get("kind") or "file",
        "ext": r.get("ext") or "",
        "size": r.get("size") or 0,
        "url": r.get("url") or "",
        "ts": r.get("created_at") or 0,
    }


@app.get("/api/favorites")
def favorites_list():
    return {"favorites": [_fav_dict(r) for r in db.list_favorites(500)]}


@app.post("/api/favorites/upload")
async def favorites_upload(files: list[UploadFile] = File(default=[])):
    if not files:
        raise HTTPException(400, "没有文件")
    fdir = os.path.join(DATA_DIR, "media", "favorites")
    os.makedirs(fdir, exist_ok=True)
    out = []
    for f in files[:20]:
        ext = os.path.splitext(f.filename or "")[1].lower()[:12]
        if ext not in _FAV_SAFE_EXTS:
            continue  # 不在白名单的类型直接跳过
        if _upload_too_big(f, FAV_MAX_BYTES):
            continue
        name = (f.filename or "未命名").strip()[:120] or "未命名"
        fname = f"f_{int(time.time())}_{os.urandom(4).hex()}{ext}"
        size = await run_in_threadpool(_stream_upload_to, f, os.path.join(fdir, fname), FAV_MAX_BYTES)
        if not size:
            continue
        url = f"/media/favorites/{fname}"
        fid = db.add_favorite(name, _fav_kind(ext), ext, size, url)
        out.append({"id": fid, "name": name, "kind": _fav_kind(ext), "size": size, "url": url})
    return {"ok": True, "items": out, "count": len(out)}


@app.post("/api/favorites/delete")
def favorites_delete(data: IdIn):
    r = db.get_favorite(data.id)
    if not r:
        return {"ok": True}
    try:
        p = media.media_fs_path(r.get("url") or "")
        if p and os.path.exists(p):
            os.remove(p)
    except Exception as e:
        _log(f"[fav] 删除文件失败 id={data.id}: {e}", level="WARNING")
    db.delete_favorite(data.id)
    return {"ok": True}


@app.post("/api/favorites/rename")
def favorites_rename(data: FavRenameIn):
    db.rename_favorite(data.id, data.name.strip()[:120])
    return {"ok": True}


# ============ 音乐 ============

class MusicRenameIn(BaseModel):
    id: int = Field(ge=0, le=_MAX_ID)
    title: str = Field("", max_length=200)


MUSIC_MAX_BYTES = 100 * 1024 * 1024


def _music_dict(r: dict) -> dict:
    return {
        "id": r["id"],
        "title": r.get("title") or "",
        "filename": r.get("filename") or "",
        "size": r.get("size") or 0,
        "url": r.get("url") or "",
        "ts": r.get("created_at") or 0,
    }


@app.get("/api/music")
def music_list():
    return {"music": [_music_dict(r) for r in db.list_music()]}


@app.post("/api/music/upload")
async def music_upload(files: list[UploadFile] = File(default=[])):
    if not files:
        raise HTTPException(400, "没有文件")
    mdir = os.path.join(DATA_DIR, "media", "music")
    os.makedirs(mdir, exist_ok=True)
    out = []
    for f in files[:20]:
        ext = os.path.splitext(f.filename or "")[1].lower()[:12]
        if ext not in (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".amr"):
            continue
        if _upload_too_big(f, MUSIC_MAX_BYTES):
            continue
        orig = os.path.basename((f.filename or "未命名").strip())[:120] or "未命名"
        title = os.path.splitext(orig)[0][:80] or "未命名"
        fname = f"m_{int(time.time())}_{os.urandom(4).hex()}{ext}"
        size = await run_in_threadpool(_stream_upload_to, f, os.path.join(mdir, fname), MUSIC_MAX_BYTES)
        if not size:
            continue
        url = f"/media/music/{fname}"
        mid = db.add_music(title, orig, size, url)
        out.append({"id": mid, "title": title, "size": size, "url": url})
    return {"ok": True, "items": out, "count": len(out)}


@app.post("/api/music/delete")
def music_delete(data: IdIn):
    r = db.get_music(data.id)
    if not r:
        return {"ok": True}
    try:
        p = media.media_fs_path(r.get("url") or "")
        if p and os.path.exists(p):
            os.remove(p)
    except Exception as e:
        _log(f"[music] 删除文件失败 id={data.id}: {e}", level="WARNING")
    db.delete_music(data.id)
    return {"ok": True}


@app.post("/api/music/rename")
def music_rename(data: MusicRenameIn):
    db.rename_music(data.id, data.title.strip()[:80])
    return {"ok": True}


# ============ 看一看（每日新闻） ============

_news_state = {"running": False, "last": 0.0, "result": None, "fail_until": 0.0}
# 记忆库后台状态：last=上次增量索引时间，last_forget=上次遗忘处理时间
_mem_tick_state = {"last": 0.0, "last_forget": 0.0}
_news_lock = threading.Lock()


def _news_fetch_async(day: str = "") -> bool:
    with _news_lock:
        if _news_state["running"]:
            return False
        _news_state["running"] = True

    def work():
        try:
            stats = news.fetch_and_store(local_now=_wake_local_now(_wake_cfg()))
            _news_state["last"] = time.time()
            _news_state["result"] = stats
            if day:
                try:
                    db.set_settings({"news_last_day": day})
                except Exception:
                    pass
            _log(f"[news] 更新完成: {stats}")
        except Exception as e:
            _news_state["fail_until"] = time.time() + 1800  # 失败后 30 分钟内不自动重试
            _log(f"[news] 更新失败: {e}", level="WARNING")
        finally:
            _news_state["running"] = False

    try:
        threading.Thread(target=work, daemon=True).start()
    except Exception as e:
        _news_state["running"] = False
        _log(f"[news] 启动抓取线程失败: {e}", level="WARNING")
        return False
    return True


def _news_enabled() -> bool:
    """新闻（看一看）总开关，管理页可配，默认开启"""
    try:
        return str(db.get_settings().get("news_enabled", "1")).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        return True


def _memory_tick():
    """记忆库：增量索引（节流 5 分钟）+ 定期遗忘（6 小时）

    - 索引走完整管道：预过滤 → 判定模型（高置信无用才丢，失败保留）→ 嵌入 → 落盘
    - 受 memory_daily_max 限制（每范围每日上限），避免某天突然烧大量嵌入调用
    """
    now = time.time()
    if now - float(_mem_tick_state.get("last") or 0) < 300:
        return
    _mem_tick_state["last"] = now
    try:
        import memory_kb
    except Exception:
        return
    try:
        cfg = memory_kb._cfg()
    except Exception:
        return
    if not cfg.get("enabled"):
        return
    try:
        chars = db.list_characters(include_disabled=True)[:20]
    except Exception:
        chars = []
    added = 0
    for c in chars:
        cid = int(c["id"])
        try:
            st = memory_kb.index_scope(memory_kb.scope_of_char(cid), cid=cid,
                                       max_batch=120, respect_daily=True)
            added += int(st.get("added") or 0)
            if st.get("capped"):
                break  # 某角色到每日上限：本轮不再往下跑，避免无谓循环
        except Exception as e:
            _log(f"[memory] 索引失败 cid={cid}: {e}", level="WARNING")
    try:
        for g in db.list_groups()[:10]:
            gid = int(g["id"])
            st = memory_kb.index_scope(memory_kb.scope_of_group(gid), gid=gid, is_group=True,
                                       max_batch=120, respect_daily=True)
            added += int(st.get("added") or 0)
    except Exception as e:
        _log(f"[memory] 群索引失败: {e}", level="WARNING")
    if added:
        _log(f"[memory] 增量索引完成，新增记忆 {added} 条")

    # 遗忘处理：低频（6 小时一次）
    if now - float(_mem_tick_state.get("last_forget") or 0) > 6 * 3600:
        _mem_tick_state["last_forget"] = now
        try:
            for c in chars:
                memory_kb.apply_forgetting(memory_kb.scope_of_char(int(c["id"])))
            for g in db.list_groups()[:10]:
                memory_kb.apply_forgetting(memory_kb.scope_of_group(int(g["id"])))
        except Exception as e:
            _log(f"[memory] 遗忘处理异常: {e}", level="WARNING")


def _news_tick():
    """每天 06:30 之后自动更新一次（由后台循环调用；失败自动重试，30 分钟冷却）"""
    if not _news_enabled():
        return
    if _news_state["running"]:
        return
    try:
        local = _wake_local_now(_wake_cfg())
    except Exception:
        local = time.localtime()
    if local.hour * 60 + local.minute < 6 * 60 + 30:
        return
    day = local.strftime("%Y-%m-%d")
    try:
        last = str(db.get_settings().get("news_last_day") or "")
    except Exception:
        last = ""
    if last == day:
        return
    if time.time() < float(_news_state.get("fail_until") or 0):
        return
    _log(f"[news] 触发每日更新 day={day}")
    _news_fetch_async(day=day)


def _group_news(rows: list) -> list:
    order = [c[0] for c in news.CATEGORIES]
    days = {}
    for r in rows:
        d = r.get("day") or ""
        days.setdefault(d, {})
        cat = r.get("category") or "其他"
        days[d].setdefault(cat, [])
        days[d][cat].append({
            "id": r["id"],
            "title": r.get("title") or "",
            "summary": r.get("summary") or "",
            "url": r.get("url") or "",
            "source": r.get("source") or "",
            "image": r.get("image") or "",
            "content": r.get("content") or "",
            "video": r.get("video") or "",
            "ts": r.get("created_at") or 0,
        })
    out = []
    for d in sorted(days.keys(), reverse=True):
        cats = []
        for name in order + [x for x in days[d].keys() if x not in order]:
            items = days[d].get(name)
            if items:
                cats.append({"name": name, "items": items})
        if cats:
            out.append({"day": d, "categories": cats})
    return out


@app.get("/api/news")
def news_list(days: int = 2):
    if not _news_enabled():
        return {"days": [], "updated": _news_state["last"], "fetching": False, "enabled": False}
    rows = db.list_news_recent(max(1, min(days, 7)))
    return {
        "days": _group_news(rows),
        "updated": _news_state["last"],
        "fetching": _news_state["running"],
        "enabled": True,
    }


@app.post("/api/news/refresh")
def news_refresh():
    if not _news_enabled():
        return {"ok": False, "error": "新闻功能已关闭（可在管理页开启）"}
    if _news_state["running"]:
        return {"ok": False, "error": "正在更新中"}
    if time.time() - float(_news_state["last"] or 0) < 600:
        return {"ok": True, "skipped": True, "msg": "刚刚已更新过"}
    started = _news_fetch_async()
    return {"ok": started, "started": started}


# ============ 群聊：用户发图/视频（角色能"看到"描述） ============

@app.post("/api/group/send_image")
async def group_send_image(gid: int = Form(...), file: UploadFile = File(...), caption: str = Form("")):
    g = db.get_group(gid)
    if not g:
        raise HTTPException(404, "not found")
    if _upload_too_big(file, 10 * 1024 * 1024):
        raise HTTPException(400, "图片太大（>10MB）")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片太大（>10MB）")
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".jpg"
    url = media.save_user_image(data, ext)
    desc = ""
    try:
        p = media.media_fs_path(url)
        if p:
            desc = await run_in_threadpool(media.describe_image, p)
    except Exception:
        desc = ""
    msg_id = db.add_group_message(gid, "user", 0, "image", (caption or "").strip(), url, desc)
    _log(f"[group] 用户 -> #{gid}: [图片] {caption[:20]}")
    threading.Thread(target=_group_reply_async, args=(gid,), daemon=True).start()
    return {"ok": True, "url": url, "msg_id": msg_id}


@app.post("/api/group/send_video")
async def group_send_video(gid: int = Form(...), file: UploadFile = File(...), caption: str = Form("")):
    g = db.get_group(gid)
    if not g:
        raise HTTPException(404, "not found")
    if _upload_too_big(file, 50 * 1024 * 1024):
        raise HTTPException(400, "视频太大（>50MB）")
    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"):
        ext = ".mp4"
    fname = f"g_{int(time.time())}_{os.urandom(3).hex()}{ext}"
    vdir = os.path.join(DATA_DIR, "media", "video")
    os.makedirs(vdir, exist_ok=True)
    if not await run_in_threadpool(_stream_upload_to, file, os.path.join(vdir, fname), 50 * 1024 * 1024):
        raise HTTPException(400, "视频太大（>50MB）")
    url = f"/media/video/{fname}"
    msg_id = db.add_group_message(gid, "user", 0, "video", (caption or "").strip(), url)
    _log(f"[group] 用户 -> #{gid}: [视频] {caption[:20]}")
    threading.Thread(target=_group_reply_async, args=(gid,), daemon=True).start()
    return {"ok": True, "url": url, "msg_id": msg_id}

# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""Stories 媒体：MiniMax 语音（音色克隆）+ Seedream 照片 + 图片识别"""
import base64
import hashlib
import json
import os
import threading
import time as _time
import urllib.request
import urllib.error

import logger as logmod
import db
import secrets_cfg

DATA_DIR = os.environ.get("STORIES_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
MEDIA_DIR = os.path.join(DATA_DIR, "media")
AUDIO_DIR = os.path.join(MEDIA_DIR, "audio")
PHOTO_DIR = os.path.join(MEDIA_DIR, "photos")
VIDEO_DIR = os.path.join(MEDIA_DIR, "videos")
USER_DIR = os.path.join(MEDIA_DIR, "user")
UPLOAD_VIDEO_DIR = os.path.join(MEDIA_DIR, "video")      # 用户上传视频（单数目录，与 AI 生成的 videos 区分）
MOMENTS_DIR = os.path.join(MEDIA_DIR, "moments")         # 朋友圈图片/视频
FAVORITE_DIR = os.path.join(DATA_DIR, "uploads", "favorites")
MUSIC_DIR = os.path.join(DATA_DIR, "uploads", "music")

# ===== MiniMax 音色克隆（统一使用）=====
MINIMAX_API_KEY = secrets_cfg.get("MINIMAX_API_KEY")
MINIMAX_GROUP_ID = secrets_cfg.get("MINIMAX_GROUP_ID")
MINIMAX_T2A_URL = "https://api.minimaxi.com/v1/t2a_v2"
MINIMAX_VOICE_ID = "wangruoxi_v2"
MINIMAX_MODEL = "speech-2.8-hd"

# ===== Seedream 照片（Ark）/ MiniMax 视频（模型均可在后台切换） =====
ARK_API_KEY = secrets_cfg.get("ARK_API_KEY")
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
SEEDREAM_MODEL = "doubao-seedream-5-0-260128"        # 默认照片模型
MINIMAX_VIDEO_MODEL = "MiniMax-Hailuo-2.3"           # 默认视频模型
VIDEO_DAILY_LIMIT = 5                                 # 视频每日生成上限

# ===== DeepSeek（图片识别兜底） =====
DEEPSEEK_API_KEY = secrets_cfg.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"


def _log(msg: str, level: str = "INFO"):
    logmod.log(msg, level=level)


def _setting(key: str, default: str = "") -> str:
    """读取后台设置（失败返回默认值）"""
    try:
        v = db.get_settings().get(key)
        if v is None:
            return default
        v = str(v).strip()
        return v or default
    except Exception:
        return default


def _ensure_dirs():
    for d in (AUDIO_DIR, PHOTO_DIR, VIDEO_DIR, USER_DIR):
        os.makedirs(d, exist_ok=True)


# ===== TTS 情绪推断与缓存 =====
# 接口实测支持 8 种（2026-10-28）：neutral / happy / sad / angry / fearful / disgusted / surprised / calm
# excited、soft、tender、whisper 均返回 invalid params，不要用。
_TTS_EMO_RULES = (
    ("angry", ("生气", "讨厌", "烦死", "气死", "滚", "闭嘴", "凭什么", "别闹", "哼",
               "过分", "受不了", "气人", "烦人")),
    ("sad", ("难过", "伤心", "失望", "委屈", "想哭", "呜呜", "算了", "不开心", "好累",
             "可惜", "叹气", "难受", "孤独", "舍不得")),
    ("fearful", ("害怕", "好怕", "怕了", "紧张", "担心", "不敢", "吓", "恐怖", "万一", "慌")),
    ("disgusted", ("恶心", "嫌弃", "好脏", "呕", "难吃", "油腻", "恶心死了")),
    ("surprised", ("惊讶", "真的吗", "没想到", "天啊", "居然", "怎么回事", "哎？", "咦", "不会吧")),
    ("happy", ("开心", "高兴", "太好了", "喜欢", "爱你", "想你", "么么", "亲亲", "抱抱",
               "嘿嘿", "哈哈", "耶", "真棒", "期待", "好玩", "可爱", "幸福")),
    ("calm", ("别急", "慢慢来", "没事的", "冷静", "深呼吸", "安静", "陪我", "睡吧", "乖",
              "不着急", "缓缓")),
)

_TTS_EMO_PRIORITY = ["angry", "sad", "fearful", "disgusted", "surprised", "happy", "calm"]


def _tts_emotion(text: str) -> str:
    """按文本推断情绪（零额外调用）：关键词打分取胜者，其次看标点，最后 neutral。"""
    t = text or ""
    if not t:
        return "neutral"
    scores = {}
    for emo, kws in _TTS_EMO_RULES:
        n = sum(1 for k in kws if k in t)
        if n:
            scores[emo] = n
    if scores:
        best = max(scores.values())
        for emo in _TTS_EMO_PRIORITY:
            if scores.get(emo) == best:
                return emo
    if t.count("！") + t.count("!") >= 2:
        return "happy"
    if (t.count("？") + t.count("?") >= 2) and any(k in t for k in ("怎么", "为什么", "不会吧", "真的")):
        return "surprised"
    return "neutral"


def _tts_cache_key(text: str, emotion: str) -> str:
    """缓存键 = 文本 + 模型 + 全部音色/音频参数；任一参数变化都会重新合成。"""
    raw = "|".join([
        MINIMAX_MODEL, MINIMAX_VOICE_ID, text, emotion,
        "speed=1.0", "vol=1.5", "pitch=0",
        "sr=32000", "br=128000", "fmt=mp3", "ch=1",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _tts_meta(meta_path: str, mp3_path: str):
    """读缓存元信息（真实时长/大小）；缺失时按文件大小兜底估算。"""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        return float(m.get("duration") or 0), int(m.get("size") or os.path.getsize(mp3_path))
    except Exception:
        size = os.path.getsize(mp3_path)
        return round(size * 8 / 128000, 2), size


_tts_locks = {}
_tts_locks_guard = threading.Lock()


def _tts_lock(key: str):
    """同一缓存键的单飞锁：并发请求同一句时只真正请求一次。"""
    with _tts_locks_guard:
        lk = _tts_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _tts_locks[key] = lk
        return lk


def _tts_hit(mp3_path: str, meta_path: str, text: str, emotion: str, why: str = "") -> dict:
    dur, size = _tts_meta(meta_path, mp3_path)
    _log(f"[media] TTS 缓存命中{why}（{len(text)} 字, {emotion}, ~{dur}s）")
    return {"url": "/media/audio/" + os.path.basename(mp3_path),
            "duration": dur, "size": size, "cached": True}


def _tts_choose_emotion(text: str, context: str = "") -> str:
    """情绪选择：优先用决策模型（8 选 1，带上下文、去括号），关闭/失败时回退关键词规则。
    设置项 tts_emotion_model=0 可强制只用关键词（省一次判定）。"""
    try:
        if str(db.get_settings().get("tts_emotion_model", 1)) in ("0", "False", "false", ""):
            return _tts_emotion(text)
    except Exception:
        pass
    try:
        import ai  # 延迟导入：ai 顶层 import 了 media，避免循环
        emo = ai.emotion_choose(text, context)
        if emo:
            return emo
    except Exception as e:
        _log(f"[media] 情绪模型判定失败（回退关键词）: {e}", level="WARNING")
    return _tts_emotion(text)


def tts_minimax(text: str, context: str = "") -> dict:
    """MiniMax TTS（音色克隆 + 情绪 + 全量缓存 + 单飞）。
    相同文本 + 相同参数 → 直接命中本地文件，不再请求接口；
    并发同一句 → 只有第一个线程真正请求，其余等它写完直接命中。
    返回 {"url","duration","size","cached"} 或 {"error": ...}"""
    text = (text or "").strip()
    if not text:
        return {"error": "empty text"}
    _ensure_dirs()

    emotion = _tts_choose_emotion(text, context)
    key = _tts_cache_key(text, emotion)
    mp3_path = os.path.join(AUDIO_DIR, "voice_%s.mp3" % key)
    meta_path = mp3_path[:-4] + ".json"

    # —— 缓存命中：直接返回，不发请求 ——
    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
        return _tts_hit(mp3_path, meta_path, text, emotion)

    lk = _tts_lock(key)
    if not lk.acquire(timeout=60):
        return {"error": "tts busy"}
    try:
        # 双检：等锁期间别的线程可能已经生成
        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
            return _tts_hit(mp3_path, meta_path, text, emotion, "·并发等待后")
        return _tts_synth(text, emotion, mp3_path, meta_path)
    finally:
        lk.release()
        with _tts_locks_guard:
            if _tts_locks.get(key) is lk and not lk.locked():
                _tts_locks.pop(key, None)


def _tts_synth(text: str, emotion: str, mp3_path: str, meta_path: str) -> dict:
    """真正请求 MiniMax 并落盘（调用方已持有该 key 的锁）"""
    t0 = _time.time()
    _log(f"[media] MiniMax TTS 合成 ({len(text)} 字, 情绪={emotion})")
    payload = {
        "model": MINIMAX_MODEL,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": MINIMAX_VOICE_ID,
            "speed": 1.0,
            "vol": 1.5,
            "pitch": 0,
            "emotion": emotion,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
        "output_format": "hex",
    }
    req = urllib.request.Request(
        f"{MINIMAX_T2A_URL}?GroupId={MINIMAX_GROUP_ID}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + MINIMAX_API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _log(f"[media] MiniMax TTS HTTP {e.code}")
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        _log(f"[media] MiniMax TTS 异常: {e}")
        return {"error": str(e)}

    if resp.get("base_resp", {}).get("status_code") != 0:
        err = json.dumps(resp.get("base_resp"), ensure_ascii=False)
        _log(f"[media] MiniMax TTS 错误: {err}")
        return {"error": err}
    audio_hex = resp.get("data", {}).get("audio")
    if not audio_hex:
        return {"error": "响应无 audio"}
    try:
        audio = bytes.fromhex(audio_hex)
    except Exception as e:
        _log(f"[media] TTS 音频解码失败: {e}")
        return {"error": "audio decode"}

    # 真实时长/大小以接口返回为准（不再按比特率估算）
    ei = resp.get("extra_info") or {}
    try:
        duration = round(float(ei.get("audio_length") or 0) / 1000.0, 2)
    except Exception:
        duration = 0.0
    if duration <= 0:
        duration = round(len(audio) * 8 / 128000, 2)
    try:
        size = int(ei.get("audio_size") or len(audio))
    except Exception:
        size = len(audio)

    path = mp3_path
    try:
        with open(path, "wb") as f:
            f.write(audio)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"duration": duration, "size": size, "emotion": emotion,
                       "chars": len(text), "ts": _time.time()}, f, ensure_ascii=False)
    except Exception as e:
        _log(f"[media] TTS 落盘失败: {e}", level="WARNING")
        return {"error": f"save failed: {e}"}
    _log(f"[media] MiniMax TTS 完成 ({( _time.time()-t0):.1f}s, {size} bytes, {duration}s, 情绪={emotion})")
    return {"url": "/media/audio/" + os.path.basename(path),
            "duration": duration, "size": size, "cached": False}


def generate_photo(prompt: str) -> dict:
    """Seedream 生成照片并落盘，返回 {"url": ...} 或 {"error": ...}（模型可在后台切换）"""
    _ensure_dirs()
    t0 = _time.time()
    model = _setting("photo_model", SEEDREAM_MODEL)
    _log(f"[media] Seedream({model}) 生成照片: {prompt[:40]}")
    payload = {
        "model": model,
        "prompt": prompt,
        "size": "2048x2048",
        "n": 1,
        "response_format": "url",
        "watermark": False,
    }
    req = urllib.request.Request(
        ARK_BASE + "/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + ARK_API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _log(f"[media] Seedream 失败: HTTP {e.code}")
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        _log(f"[media] Seedream 异常: {e}")
        return {"error": str(e)}

    data = resp.get("data", [])
    if not data or not data[0].get("url"):
        return {"error": "无图片 URL"}
    img_url = data[0]["url"]
    try:
        req2 = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=90) as r:
            img = r.read()
    except Exception as e:
        _log(f"[media] 照片下载失败: {e}")
        return {"error": f"下载失败: {e}"}
    fname = f"photo_{int(_time.time())}_{os.urandom(3).hex()}.png"
    path = os.path.join(PHOTO_DIR, fname)
    try:
        with open(path, "wb") as f:
            f.write(img)
    except Exception as e:
        _log(f"[media] 照片落盘失败: {e}", level="WARNING")
        return {"error": f"save failed: {e}"}
    _log(f"[media] Seedream 完成 ({( _time.time()-t0):.1f}s)")
    return {"url": f"/media/photos/{fname}"}


def _minimax_call(path: str, payload: dict | None = None, method: str = "GET", timeout: int = 60):
    """MiniMax 通用调用（自动带 GroupId）"""
    url = f"{MINIMAX_T2A_URL.rsplit('/t2a_v2', 1)[0]}{path}"
    sep = "&" if "?" in url else "?"
    url += f"{sep}GroupId={MINIMAX_GROUP_ID}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    h = {"Content-Type": "application/json", "Authorization": "Bearer " + MINIMAX_API_KEY}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8"))
        return obj if isinstance(obj, dict) else {"_err": "bad response"}
    except urllib.error.HTTPError as e:
        try:
            obj = json.loads(e.read().decode("utf-8"))
            return obj if isinstance(obj, dict) else {"_http": e.code}
        except Exception:
            return {"_http": e.code}
    except Exception as e:
        return {"_err": str(e)}


def _quota_tz() -> float:
    """配额日切时区：跟随后台 wake_tz（与语音限额一致）"""
    try:
        return float(db.get_settings().get("wake_tz") or 8)
    except Exception:
        return 8.0


def _video_quota_ok() -> bool:
    """视频每日限额（存 settings：video_date / video_count，按业务时区日切）"""
    try:
        s = db.get_settings()
        today = _time.strftime("%Y-%m-%d", _time.gmtime(_time.time() + _quota_tz() * 3600))
        if s.get("video_date") != today:
            return True
        return int(s.get("video_count") or 0) < VIDEO_DAILY_LIMIT
    except Exception:
        return True


def _video_quota_inc():
    try:
        s = db.get_settings()
        today = _time.strftime("%Y-%m-%d", _time.gmtime(_time.time() + _quota_tz() * 3600))
        cnt = int(s.get("video_count") or 0) if s.get("video_date") == today else 0
        db.set_settings({"video_date": today, "video_count": cnt + 1})
    except Exception:
        pass


def generate_video(prompt: str, duration: int = 6) -> dict:
    """MiniMax 视频生成（提交 → 轮询 → 下载落盘），返回 {"url","duration"} 或 {"error"}。
    生成耗时通常 1~3 分钟；每日限额 VIDEO_DAILY_LIMIT 条；同一时刻只允许一个视频在生成。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "empty prompt"}
    if not _video_busy.acquire(blocking=False):
        _log("[media] 已有视频在生成中，跳过本次", level="WARNING")
        return {"error": "busy"}
    try:
        return _generate_video_inner(prompt, duration)
    finally:
        _video_busy.release()


_video_busy = threading.Lock()


def _generate_video_inner(prompt: str, duration: int = 6) -> dict:
    if not _video_quota_ok():
        _log(f"[media] 视频生成已达每日限额 {VIDEO_DAILY_LIMIT} 条", level="WARNING")
        return {"error": f"daily limit {VIDEO_DAILY_LIMIT}"}
    _ensure_dirs()
    t0 = _time.time()
    model = _setting("video_model", MINIMAX_VIDEO_MODEL)
    _log(f"[media] MiniMax 视频生成提交 ({model}): {prompt[:40]}")
    resp = _minimax_call("/video_generation", {"model": model, "prompt": prompt, "duration": duration, "resolution": "768P"}, "POST")
    if not resp or resp.get("base_resp", {}).get("status_code") != 0:
        err = json.dumps((resp or {}).get("base_resp") or resp, ensure_ascii=False)[:200]
        _log(f"[media] 视频提交失败: {err}", level="WARNING")
        return {"error": err}
    tid = resp.get("task_id")
    if not tid:
        return {"error": "无 task_id"}

    # 轮询（最多 ~8 分钟；HTTP/网络错误快速失败，避免空转）
    file_id = None
    for i in range(80):
        _time.sleep(6)
        st = _minimax_call(f"/query/video_generation?task_id={tid}")
        if not isinstance(st, dict):
            _log("[media] 视频轮询返回异常", level="WARNING")
            return {"error": "poll bad response"}
        if st.get("_http") or st.get("_err"):
            err = st.get("_err") or ("HTTP %s" % st.get("_http"))
            _log(f"[media] 视频轮询失败: {err}", level="WARNING")
            return {"error": f"poll failed: {err}"}
        if st.get("base_resp", {}).get("status_code") not in (0, None):
            err = json.dumps(st.get("base_resp"), ensure_ascii=False)[:200]
            _log(f"[media] 视频轮询错误: {err}", level="WARNING")
            return {"error": err}
        status = str(st.get("status") or "")
        if status == "Success":
            file_id = st.get("file_id")
            break
        if status.lower() in ("fail", "failed", "failure"):
            _log(f"[media] 视频生成失败: {json.dumps(st, ensure_ascii=False)[:200]}", level="WARNING")
            return {"error": "generation failed"}
    if not file_id:
        _log("[media] 视频生成超时", level="WARNING")
        return {"error": "timeout"}

    fr = _minimax_call(f"/files/retrieve?file_id={file_id}")
    dl = (((fr or {}).get("file") or {}).get("download_url")) or ""
    if not dl:
        return {"error": "无下载链接"}
    try:
        req = urllib.request.Request(dl, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as r:
            vdata = r.read()
    except Exception as e:
        _log(f"[media] 视频下载失败: {e}", level="WARNING")
        return {"error": f"下载失败: {e}"}
    fname = f"video_{int(_time.time())}_{os.urandom(3).hex()}.mp4"
    try:
        with open(os.path.join(VIDEO_DIR, fname), "wb") as f:
            f.write(vdata)
    except Exception as e:
        _log(f"[media] 视频落盘失败: {e}", level="WARNING")
        return {"error": f"保存失败: {e}"}
    _video_quota_inc()  # 真正出片（下载并落盘成功）才计入每日额度
    _log(f"[media] MiniMax 视频完成 ({( _time.time()-t0):.1f}s, {len(vdata)} bytes)")
    return {"url": f"/media/videos/{fname}", "duration": duration}


def save_user_image(data: bytes, ext: str = ".jpg") -> str:
    """保存用户上传的图片，返回 URL"""
    _ensure_dirs()
    fname = f"user_{int(_time.time())}_{os.urandom(3).hex()}{ext}"
    with open(os.path.join(USER_DIR, fname), "wb") as f:
        f.write(data)
    return f"/media/user/{fname}"


def file_to_data_url(path: str, mime: str = "image/jpeg") -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def media_fs_path(url: str):
    """把 /media/... URL 映射为磁盘路径（用于识别用户图片）"""
    if not url or not url.startswith("/media/"):
        return None
    rel = url[len("/media/"):]
    root = os.path.realpath(MEDIA_DIR)
    p = os.path.realpath(os.path.join(root, rel.replace("/", os.sep)))
    if not p.startswith(root + os.sep):
        return None
    return p if os.path.exists(p) else None


def _mime_of(path: str) -> str:
    p = (path or "").lower()
    if p.endswith(".png"):
        return "image/png"
    if p.endswith(".webp"):
        return "image/webp"
    if p.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


DEFAULT_IMAGE_DESC_MODEL = "deepseek-v4-flash-vision-exp"
DEFAULT_IMAGE_DESC_PROMPT = "请详细描述这张图片的内容（人物、物体、场景、氛围、文字），用一两句话概括。"


def _vision_call(path: str, model: str, prompt: str) -> str:
    """按模型名路由（deepseek* → DeepSeek 官方，其余 → Ark）调用视觉接口，失败返回空串"""
    try:
        data_url = file_to_data_url(path, _mime_of(path))
    except Exception:
        return ""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 400,
        "temperature": 0.2,
    }
    if str(model).startswith("deepseek"):
        try:
            import ai as _ai  # 延迟导入（ai 模块顶层已 import media，避免循环引用）
            return _ai.deepseek_pool_chat(payload, timeout=60)
        except Exception as e:
            _log(f"[media] 图片转述失败（{model}）: {e}")
            return ""
    base, key = ARK_BASE, ARK_API_KEY
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return (resp.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    except Exception as e:
        _log(f"[media] 图片转述失败（{model}）: {e}")
        return ""


def describe_image(path: str) -> str:
    """照片转述：默认 DeepSeek Flash 视觉版（可在后台设置切换模型）；失败自动回退另一家视觉模型。"""
    model = _setting("image_desc_model", DEFAULT_IMAGE_DESC_MODEL) or DEFAULT_IMAGE_DESC_MODEL
    prompt = _setting("image_desc_prompt", DEFAULT_IMAGE_DESC_PROMPT) or DEFAULT_IMAGE_DESC_PROMPT
    out = _vision_call(path, model, prompt)
    if out:
        return out
    if str(model).startswith("deepseek"):
        fallback = "doubao-seed-2-1-turbo-260628"
    else:
        fallback = DEFAULT_IMAGE_DESC_MODEL
    if fallback != model:
        _log(f"[media] 图片转述回退到 {fallback}")
        out = _vision_call(path, fallback, prompt)
    return out


DEFAULT_VIDEO_PROMPT = (
    "请仔细理解这个视频，输出以下三部分：\n"
    "1. 内容摘要：视频讲了什么\n"
    "2. 关键动作/事件：按时间顺序列出主要动作和事件\n"
    "3. 情绪氛围：整体情绪和氛围\n"
    "简洁输出，直接给结果，不要额外解释。"
)


def understand_video(path: str) -> str:
    """视频理解：≤50MB base64 直传豆包，失败返回 ""。
    内存优化：请求体改为"流式构造"（前缀 + 分块 base64 + 后缀），
    峰值内存从 ~250MB（原文 + base64 + payload + encode 四份）降到 ~10MB。"""
    try:
        if not path or not os.path.exists(path):
            return ""
        size = os.path.getsize(path)
        if size > 50 * 1024 * 1024:
            _log("[media] 视频 >50MB，跳过理解")
            return ""
        model = _setting("video_understand_model", "doubao-seed-2-1-turbo-260628")
        prompt = _setting("video_understand_prompt", DEFAULT_VIDEO_PROMPT)
        prefix = ('{"model":' + json.dumps(model) +
                  ',"messages":[{"role":"user","content":[{"type":"video_url","video_url":{"url":"'
                  'data:video/mp4;base64,')
        suffix = '"}},{"type":"text","text":' + json.dumps(prompt) + '}]}]}'
        b64_len = ((size + 2) // 3) * 4
        total = len(prefix.encode("utf-8")) + b64_len + len(suffix.encode("utf-8"))

        def _body():
            yield prefix.encode("utf-8")
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(3 * 1024 * 1024)  # 3MB 的整数倍，保证 base64 分块边界正确
                    if not chunk:
                        break
                    yield base64.b64encode(chunk)
            yield suffix.encode("utf-8")

        t0 = _time.time()
        req = urllib.request.Request(
            ARK_BASE + "/chat/completions",
            data=_body(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + ARK_API_KEY,
                     "Content-Length": str(total)},
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read().decode("utf-8"))
        content = str((resp.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or "").strip()
        _log(f"[media] 视频理解完成 ({( _time.time()-t0):.1f}s, {len(content)} 字)")
        return content
    except Exception as e:
        _log(f"[media] 视频理解失败: {e}")
        return ""


DEFAULT_VOICE_PROMPT = "请把这段语音转写成文字（保持说话原意，适当加标点），只输出转写结果本身，不要任何解释。"

_AUDIO_MIME = {
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".amr": "audio/amr",
    ".opus": "audio/opus",
    ".mp4": "audio/mp4",
}

# Ark 音频理解接受的上行格式名（m4a 归一为 mp4）
_ARK_AUDIO_FMT = {
    ".m4a": "mp4",
    ".aac": "aac",
    ".mp3": "mp3",
    ".wav": "wav",
    ".ogg": "ogg",
    ".opus": "ogg",
    ".amr": "amr",
    ".mp4": "mp4",
    ".flac": "flac",
}


def audio_b64(path: str):
    """读取语音文件，返回 (纯 base64, Ark 格式名)；失败返回 ("", "")。
    用于把音频作为多模态内容直接交给主模型。"""
    try:
        ext = os.path.splitext(path)[1].lower()
        fmt = _ARK_AUDIO_FMT.get(ext, "mp4")
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), fmt
    except Exception:
        return "", ""


def _audio_call(path: str, model: str, prompt: str) -> str:
    """按模型名路由（deepseek* → DeepSeek 官方，其余 → Ark）调用语音理解，失败返回空串。
    注意：Ark 的 input_audio.data 需要纯 base64（不带 data: 前缀），格式用 mp4/wav 等。"""
    data_b64, fmt = audio_b64(path)
    if not data_b64:
        return ""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": data_b64, "format": fmt}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 800,
        "temperature": 0.1,
    }
    if str(model).startswith("deepseek"):
        try:
            import ai as _ai  # 延迟导入（ai 模块顶层已 import media，避免循环引用）
            return _ai.deepseek_pool_chat(payload, timeout=120)
        except Exception as e:
            _log(f"[media] 语音转述失败（{model}）: {e}")
            return ""
    base, key = ARK_BASE, ARK_API_KEY
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return (resp.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    except Exception as e:
        _log(f"[media] 语音转述失败（{model}）: {e}")
        return ""


def transcribe_audio(path: str) -> str:
    """语音转述：与图片转述共用同一多模态模型设置（插件合一），失败自动回退豆包。"""
    model = _setting("image_desc_model", DEFAULT_IMAGE_DESC_MODEL) or DEFAULT_IMAGE_DESC_MODEL
    prompt = _setting("voice_desc_prompt", DEFAULT_VOICE_PROMPT) or DEFAULT_VOICE_PROMPT
    out = _audio_call(path, model, prompt)
    if out:
        return out
    fallback = "doubao-seed-2-0-lite-260428"
    if fallback != model:
        _log(f"[media] 语音转述回退到 {fallback}")
        out = _audio_call(path, fallback, prompt)
    return out


def save_user_audio(data: bytes, ext: str = ".m4a") -> str:
    """保存用户上传的语音，返回 URL"""
    _ensure_dirs()
    fname = f"user_voice_{int(_time.time())}_{os.urandom(3).hex()}{ext}"
    with open(os.path.join(USER_DIR, fname), "wb") as f:
        f.write(data)
    return f"/media/user/{fname}"


def cleanup_generated(days: int = 30) -> int:
    """删除过期且未被任何数据引用的生成媒体（audio/photos/videos）。返回删除数量。
    安全规则：仅当文件不被 messages/favorites/news/收藏等任何记录引用、且超过保留天数才删除。"""
    import db as _db
    cutoff = _time.time() - max(1, int(days)) * 86400
    refs = set()
    try:
        conn = _db.get_conn()
        for sql in (
            "SELECT media_url FROM messages WHERE media_url IS NOT NULL AND media_url != ''",
            "SELECT media_url FROM group_messages WHERE media_url IS NOT NULL AND media_url != ''",
            "SELECT url FROM favorites WHERE url IS NOT NULL AND url != ''",
            "SELECT url FROM music WHERE url IS NOT NULL AND url != ''",
            "SELECT image FROM news WHERE image IS NOT NULL AND image != ''",
            "SELECT video FROM news WHERE video IS NOT NULL AND video != ''",
        ):
            try:
                for r in conn.execute(sql).fetchall():
                    v = str(r[0] or "")
                    if v:
                        refs.add(os.path.basename(v))
            except Exception:
                pass
        # 朋友圈图片是 JSON 数组，需单独解析（否则动态里的媒体会被当"无引用"误删）
        try:
            for r in conn.execute("SELECT images FROM moments WHERE images IS NOT NULL AND images != ''").fetchall():
                try:
                    for u in json.loads(r[0] or "[]"):
                        v = str(u or "")
                        if v:
                            refs.add(os.path.basename(v))
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass
    removed = 0
    for d in (AUDIO_DIR, PHOTO_DIR, VIDEO_DIR, USER_DIR, UPLOAD_VIDEO_DIR, MOMENTS_DIR, FAVORITE_DIR, MUSIC_DIR):
        try:
            for fn in os.listdir(d):
                p = os.path.join(d, fn)
                try:
                    if not os.path.isfile(p):
                        continue
                    if fn in refs:
                        continue
                    if os.path.getmtime(p) >= cutoff:
                        continue
                    os.remove(p)
                    removed += 1
                except Exception:
                    pass
        except Exception:
            pass
    if removed:
        _log(f"[media] 清理过期未引用文件 {removed} 个（保留 {days} 天）")
    return removed

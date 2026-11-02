# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""Stories 媒体链路测试：默认离线（桩替换），设置 RUN_MEDIA_API=1 才打真实接口"""
import io
import json
import os
import shutil
import sys
sys.dont_write_bytecode = True
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = _HERE if os.path.exists(os.path.join(_HERE, "main.py")) else os.path.dirname(_HERE)
TEST_DATA = os.path.join(_BACKEND, "_test_media_%d" % os.getpid())
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
os.environ["STORIES_ALLOW_NO_AUTH"] = "1"
os.environ["STORIES_NO_WAKE_LOOP"] = "1"
sys.path.insert(0, _BACKEND)

import media
import db
import main  # 初始化目录与 DB

LIVE = os.environ.get("RUN_MEDIA_API") == "1"

ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK] %s" % name + (" | %s" % detail if detail else ""))
    else:
        fail += 1
        print("  [FAIL] %s" % name + (" | %s" % detail if detail else ""))


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d7b2c0000000049454e44ae426082"
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_calls = []
_payloads = []


def _fake_urlopen(req, timeout=0):
    url = getattr(req, "full_url", str(req))
    _calls.append(url)
    body = getattr(req, "data", None)
    if body:
        try:
            _payloads.append(json.loads(body.decode("utf-8")))
        except Exception:
            _payloads.append({})
    if "t2a_v2" in url:
        audio = b"ID3" + b"\x00" * 997
        return _Resp(json.dumps({
            "base_resp": {"status_code": 0},
            "data": {"audio": audio.hex()},
            "extra_info": {"audio_length": 1500, "audio_size": len(audio)},
        }).encode("utf-8"))
    if "images/generations" in url:
        return _Resp(json.dumps({"data": [{"url": "https://example.com/x.png"}]}).encode("utf-8"))
    if url.endswith("x.png"):
        return _Resp(PNG)
    raise AssertionError("unexpected url: " + url)


print("=" * 60)
if LIVE:
    print("[1] MiniMax TTS（真实接口，RUN_MEDIA_API=1）")
    t0 = time.time()
    r = media.tts_minimax("你好呀，这是媒体链路测试。")
    check("TTS 返回 url", "url" in r, str(r)[:100])
    if "url" in r:
        path = os.path.join(TEST_DATA, r["url"].replace("/media/", "media/").replace("/", os.sep))
        check("音频文件存在", os.path.exists(path), "%.1fs" % (time.time() - t0))
        check("时长 > 0", r.get("duration", 0) > 0, str(r.get("duration")))

    print("\n[2] Seedream 照片（真实接口）")
    g = media.generate_photo("一只橘猫蜷在窗台晒太阳")
    check("照片返回 url", "url" in g, str(g)[:120])
else:
    print("[1] MiniMax TTS（离线桩）")
    _orig = media.urllib.request.urlopen
    _orig_emo = media._tts_choose_emotion
    media.urllib.request.urlopen = _fake_urlopen
    media._tts_choose_emotion = lambda text, context="": "neutral"  # 离线：不走判定模型
    try:
        t0 = time.time()
        r = media.tts_minimax("你好呀，这是离线测试。")
        check("TTS 返回 url", "url" in r and r["url"].startswith("/media/audio/"), str(r)[:100])
        path = os.path.join(TEST_DATA, r["url"].replace("/media/", "media/").replace("/", os.sep))
        check("音频文件落盘", os.path.exists(path) and os.path.getsize(path) > 0,
              "%.2fs" % (time.time() - t0))
        check("时长取接口 extra_info", abs(float(r.get("duration") or 0) - 1.5) < 1e-6,
              str(r.get("duration")))
        check("大小取接口 extra_info", r.get("size") == 1000, str(r.get("size")))

        p = _payloads[0] if _payloads else {}
        vs = p.get("voice_setting") or {}
        check("请求带模型与音色", p.get("model") and vs.get("voice_id"), str(p)[:80])
        check("情绪在 8 种之内",
              vs.get("emotion") in ("neutral", "happy", "sad", "angry", "fearful",
                                    "disgusted", "surprised", "calm"), str(vs.get("emotion")))
        check("请求参数完整", (p.get("audio_setting") or {}).get("format") == "mp3"
              and p.get("output_format") == "hex")

        n_before = len(_calls)
        r2 = media.tts_minimax("你好呀，这是离线测试。")
        check("相同文本命中缓存", r2.get("cached") is True, str(r2)[:80])
        check("缓存命中不再请求接口", len(_calls) == n_before, "%d -> %d" % (n_before, len(_calls)))

        check("空文本直接报错", "error" in media.tts_minimax("   "))

        print("\n[2] Seedream 照片（离线桩）")
        g = media.generate_photo("一只橘猫蜷在窗台晒太阳")
        check("照片返回 url", "url" in g and g["url"].startswith("/media/photos/"), str(g)[:100])
        p_path = os.path.join(TEST_DATA, g["url"].replace("/media/", "media/").replace("/", os.sep))
        check("图片落盘", os.path.exists(p_path) and os.path.getsize(p_path) == len(PNG),
              str(os.path.getsize(p_path) if os.path.exists(p_path) else -1))
        photo_payload = _payloads[-1] if _payloads else {}
        check("照片请求带 prompt", photo_payload.get("prompt") == "一只橘猫蜷在窗台晒太阳",
              str(photo_payload)[:80])
    finally:
        media.urllib.request.urlopen = _orig
        media._tts_choose_emotion = _orig_emo

print("\n[3] 表情消息流程")
os.makedirs(os.path.join(TEST_DATA, "uploads", "stickers"), exist_ok=True)
with open(os.path.join(TEST_DATA, "uploads", "stickers", "st_test.png"), "wb") as f:
    f.write(PNG)
db.add_sticker("st_test.png", "微笑", "开心,问候")
cid = db.create_character({"name": "测试", "fields": {}, "greeting": "嗨"})
db.add_pending(cid, "微笑", kind="sticker", ts=time.time() - 1)
main._process_pending(cid)
msgs = db.get_messages(cid)
st = [m for m in msgs if m["kind"] == "sticker"]
check("表情消息已发送",
      len(st) == 1 and st[0]["media_url"] == "/uploads/stickers/st_test.png",
      str(st[-1] if st else None)[:80])

print("\n" + "=" * 60)
print("结果：%d 通过，%d 失败" % (ok, fail))

import time as _wait_time
_wait_time.sleep(1.2)  # 等待后台线程（摘要/向量化）收尾
try:
    db.get_conn().close()
except Exception:
    pass
try:
    import logging
    _lg = logging.getLogger("stories")
    for _h in list(_lg.handlers):
        try:
            _h.close()
        except Exception:
            pass
        _lg.removeHandler(_h)
    logging.shutdown()
except Exception:
    pass
try:
    if os.path.exists(TEST_DATA):
        shutil.rmtree(TEST_DATA)
except Exception:
    pass
if os.path.exists(TEST_DATA):
    import subprocess
    subprocess.Popen(
        [sys.executable, "-c",
         "import shutil,time; time.sleep(2); shutil.rmtree(r'%s', ignore_errors=True)" % TEST_DATA],
        creationflags=0x08000000 if os.name == "nt" else 0,
    )

sys.exit(0 if fail == 0 else 1)

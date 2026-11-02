# -*- coding: utf-8 -*-
"""Stories 语音消息管线 + 用户发图识别测试"""
import glob
import io
import os
import shutil
import sys
sys.dont_write_bytecode = True
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = _HERE if os.path.exists(os.path.join(_HERE, "main.py")) else os.path.dirname(_HERE)
TEST_DATA = os.path.join(_BACKEND, "_test_voice_%d" % os.getpid())
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
os.environ["STORIES_ALLOW_NO_AUTH"] = "1"
os.environ["STORIES_NO_WAKE_LOOP"] = "1"
sys.path.insert(0, _BACKEND)

import media
import db
import ai
import main

main._cfg = lambda: {"reply_delay": 0.1, "msg_delay_factor": 0.05, "max_history": 40}


def _make_test_png(path, w=64, h=64, rgb=(210, 70, 60)):
    """生成一张纯色 PNG（纯标准库实现，避免依赖 PIL；也避免依赖其它测试的产物）"""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def _chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(raw, 9))
           + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return path

ok = 0
fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {name}" + (f" | {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""))

print("=" * 60)
print("[1] 语音消息管线（模拟 AI 决定发语音）")
cid = db.create_character({"name": "小语", "fields": {"nickname": "你"}, "greeting": "嗨"})
db.update_chat(cid, voice_enabled=1, sticker_enabled=0)
orig = ai.generate_reply
orig_tts = media.tts_minimax
media.tts_minimax = lambda text: {"url": "/media/audio/test_voice.mp3", "duration": 3}
ai.generate_reply = lambda character, chat, history, user_text, image_path=None, extra="", audio_path=None: {
    "reply": "晚安，今天也很想你。", "photo_prompt": "", "voice": True, "sticker": None
}
try:
    db.add_message(cid, "user", "text", "晚安")
    db.add_pending(cid, "晚安", kind="reply", ts=time.time() - 1)
    main._process_pending(cid)
    msgs = db.get_messages(cid)
    voice = [m for m in msgs if m["kind"] == "voice"]
    check("语音消息已生成", len(voice) == 1 and voice[0]["media_url"].startswith("/media/audio/"), str(voice[-1] if voice else None)[:110])
finally:
    ai.generate_reply = orig
    media.tts_minimax = orig_tts

print("\n[2] 用户发图 → 多模态识别（真实调用）")
# 不再依赖 test_media.py 的产物：找不到就自己生成一张测试图。
# 原实现 images 为空时 img_path=None，多模态分支根本不会执行（用例静默空转）
os.makedirs(os.path.join(TEST_DATA, "media", "user"), exist_ok=True)
img_path = os.path.join(TEST_DATA, "media", "user", "user_test.png")
photos = glob.glob(os.path.join(_BACKEND, "_test_media", "media", "photos", "*.png"))
if photos:
    shutil.copy(photos[0], img_path)
else:
    try:
        _make_test_png(img_path)
        print("  （未找到 test_media 产物，已生成内置测试图）")
    except Exception as e:
        img_path = None
        print("  （生成测试图失败：%s）" % e)
chat = db.get_chat(cid)
char = db.get_character(cid)
history = db.get_recent_messages(cid, 10)
t0 = time.time()
result = ai.generate_reply(char, chat, history, "你看我拍的这张图，好看吗？", img_path)
check("发图后 AI 回复非空", bool(result.get("reply")), f"{result.get('reply','')[:50]} ({time.time()-t0:.1f}s)")
check("回复不是兜底文案", "没有回复" not in result.get("reply", ""))

print("\n" + "=" * 60)
print(f"结果：{ok} 通过，{fail} 失败")

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
    # Windows 文件锁：父进程退出后由延迟子进程清理
    import subprocess
    subprocess.Popen(
        [sys.executable, "-c",
         "import shutil,time; time.sleep(2); shutil.rmtree(r'%s', ignore_errors=True)" % TEST_DATA],
        creationflags=0x08000000 if os.name == "nt" else 0,
    )


# 退出码反映真实结果（原实现恒为 0：失败也会被 CI/脚本当成通过）
sys.exit(0 if fail == 0 else 1)

# -*- coding: utf-8 -*-
"""测试：日志系统 + ref_id 消息引用 + app_log 上报"""
import io
import os
import shutil
import sys
sys.dont_write_bytecode = True
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = _HERE if os.path.exists(os.path.join(_HERE, "main.py")) else os.path.dirname(_HERE)
TEST_DATA = os.path.join(_BACKEND, "_test_log_%d" % os.getpid())
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
os.environ["STORIES_ALLOW_NO_AUTH"] = "1"
os.environ["STORIES_NO_WAKE_LOOP"] = "1"

sys.path.insert(0, _BACKEND)

import main
import db
import logger
import ai
from fastapi.testclient import TestClient

main._cfg = lambda: {"reply_delay": 0.1, "msg_delay_factor": 0.05, "max_history": 40}
c = TestClient(main.app)

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
print("[1] 日志系统")
logger.log("[test] 这是一条 INFO 测试日志")
logger.log("[test] 这是一条 WARNING 日志", level="WARNING")
try:
    raise ValueError("模拟异常")
except Exception:
    logger.log_exc("[test] 异常堆栈测试")
logs = logger.read_logs(50)
has_info = any("INFO" in l and "INFO 测试日志" in l for l in logs)
has_warn = any("WARNING" in l for l in logs)
has_exc = any("模拟异常" in l and "Traceback" in "\n".join(logs) for l in logs)
check("INFO 日志写入", has_info)
check("WARNING 级别", has_warn)
check("异常堆栈写入", has_exc, f"共 {len(logs)} 行")

_fake_sk = "sk-" + "abcdefgh12345678"
_fake_ak = "LTAI" + "5tABCDEFGH1234"
logger.log("[test] 密钥 " + _fake_sk + " 不应落盘")
logger.log("[test] AK " + _fake_ak + " 不应落盘")
joined = "\n".join(logger.read_logs(100))
check("日志脱敏：sk- 令牌被打码", _fake_sk not in joined and "***" in joined)
check("日志脱敏：LTAI AK 被打码", _fake_ak not in joined)

print("\n[2] App 日志上报接口")
r = c.post("/api/app_log", json={"logs": [
    {"level": "INFO", "tag": "lifecycle", "message": "app start", "device": "Android 16"},
    {"level": "ERROR", "tag": "poll", "message": "timeout error", "device": "Android 16"},
]}).json()
check("批量上报返回", r.get("ok") is True and r.get("count") == 2, str(r))
logs = logger.read_logs(100)
check("App 日志落盘", any("[app:lifecycle]" in l and "app start" in l for l in logs))
check("App 错误级别", any("ERROR" in l and "[app:poll]" in l for l in logs))
r2 = c.get("/api/logs?lines=20", headers={"X-Admin-Token": main.ADMIN_TOKEN}).json()
check("logs 接口读取（管理鉴权）", len(r2["logs"]) > 0)

print("\n[3] ref_id 消息引用（两条快发消息分别应答）")
cid = db.create_character({"name": "小测", "fields": {}, "greeting": "嗨"})
# 模拟：先发消息1，再发消息2（都在回复前）
mid1 = db.add_message(cid, "user", "text", "第一句话：我喜欢苹果")
db.add_pending(cid, "第一句话：我喜欢苹果", kind="reply", ts=time.time() - 10, ref_id=mid1)
mid2 = db.add_message(cid, "user", "text", "第二句话：我也喜欢香蕉")
db.add_pending(cid, "第二句话：我也喜欢香蕉", kind="reply", ts=time.time() - 10, ref_id=mid2)

captured = []
orig = ai.generate_reply
def fake_reply(character, chat, history, user_text, image_path=None, extra="", audio_path=None):
    captured.append(user_text)
    return {"reply": f"收到：{user_text}", "photo_prompt": "", "voice": False, "sticker": None}
ai.generate_reply = fake_reply
main._process_pending(cid)
ai.generate_reply = orig

check("处理了两条回复", len(captured) == 2, str(captured))
check("消息1对应正确", captured and "第一句话" in captured[0], captured[0] if captured else "")
check("消息2对应正确", len(captured) > 1 and "第二句话" in captured[1], captured[1] if len(captured) > 1 else "")

print("\n[4] 用户新消息打断旧分段")
db.add_pending(cid, "旧分段A", kind="script", ts=time.time() + 999)
db.add_pending(cid, "旧分段B", kind="script", ts=time.time() + 999)
db.clear_script_pending(cid)
remain = db.get_all_pending(cid)
check("旧 script 分段已清", all(p["kind"] != "script" for p in remain), f"剩余 {len(remain)}")

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
sys.exit(0 if fail == 0 else 1)

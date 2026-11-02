# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""Stories 安全回归：令牌 / 票据 / 上传 / 小程序 KV / 启动校验"""
import hashlib
import hmac
import io
import os
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = _HERE if os.path.exists(os.path.join(_HERE, "main.py")) else os.path.dirname(_HERE)
TEST_DATA = os.path.join(_BACKEND, "_test_security_%d" % os.getpid())
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
os.environ["STORIES_APP_TOKEN"] = "test-app-token-0123456789abcdef"
os.environ.pop("STORIES_ALLOW_NO_AUTH", None)
os.environ["STORIES_NO_WAKE_LOOP"] = "1"
os.environ["ADMIN_PASSWORD"] = "test-pass"

sys.path.insert(0, _BACKEND)

import main
from fastapi.testclient import TestClient

TOKEN = "test-app-token-0123456789abcdef"
H = {"X-App-Token": TOKEN}
c = TestClient(main.app)
ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK] %s" % name)
    else:
        fail += 1
        print("  [FAIL] %s | %s" % (name, detail))


print("=" * 60)
print("[1] 长期令牌")
check("无令牌访问 /api/bootstrap → 401", c.get("/api/bootstrap").status_code == 401)
check("错误令牌 → 401",
      c.get("/api/bootstrap", headers={"X-App-Token": "wrong"}).status_code == 401)
check("正确令牌 → 200", c.get("/api/bootstrap", headers=H).status_code == 200)

print("\n[2] 短时效票据")
r = c.get("/api/ticket", headers=H)
check("签发票据", r.status_code == 200 and "ticket" in r.json(), str(r.json())[:80])
ticket = r.json().get("ticket", "")
check("票据 TTL 为 12 小时", main.TICKET_TTL == 12 * 3600, str(main.TICKET_TTL))

check("票据可访问 /api/mini/chars", c.get("/api/mini/chars?token=" + ticket).status_code == 200)
check("票据不能访问 /api/bootstrap",
      c.get("/api/bootstrap?token=" + ticket).status_code == 401)
check("票据不能访问聊天接口",
      c.get("/api/chat/messages?character_id=1&token=" + ticket).status_code == 401)

os.makedirs(os.path.join(TEST_DATA, "media"), exist_ok=True)
with open(os.path.join(TEST_DATA, "media", "ticket_probe.txt"), "wb") as f:
    f.write(b"ok")
check("票据可访问 /media/",
      c.get("/media/ticket_probe.txt?token=" + ticket).status_code == 200)

ts_old = int(time.time()) - 13 * 3600
sig_old = hmac.new(main._ticket_secret().encode("utf-8"),
                   ("ticket:%d" % ts_old).encode("utf-8"), hashlib.sha256).hexdigest()[:32]
expired = "%d.%s" % (ts_old, sig_old)
check("过期票据判为无效", not main._ticket_valid(expired))
check("过期票据访问 /api/mini/ → 401",
      c.get("/api/mini/chars?token=" + expired).status_code == 401)

ts_future = int(time.time()) + 3600
sig_future = hmac.new(main._ticket_secret().encode("utf-8"),
                      ("ticket:%d" % ts_future).encode("utf-8"), hashlib.sha256).hexdigest()[:32]
check("未来时间戳票据判为无效", not main._ticket_valid("%d.%s" % (ts_future, sig_future)))
check("签名被篡改判为无效", not main._ticket_valid(ticket[:-1] + ("0" if ticket[-1] != "0" else "1")))

print("\n[3] 小程序 KV 上限")
big = "x" * 200001
r = c.post("/api/mini/kv?token=" + ticket, json={"app": "test", "key": "big", "value": big})
check("超过 200KB 直接报错", r.status_code == 400, str(r.status_code))
r = c.post("/api/mini/kv?token=" + ticket, json={"app": "test", "key": "ok", "value": "x" * 100})
check("正常大小可写入", r.status_code == 200)

print("\n[4] 上传：路径穿越 / 类型 / 大小")
r = c.post("/api/character/save", headers=H, json={"name": "安全测试"})
cid = (r.json() or {}).get("id")
check("创建角色", bool(cid), str(r.json())[:80])

if cid:
    r = c.post("/api/character/avatar", headers=H,
               data={"character_id": str(cid)},
               files={"file": ("../../evil.txt", b"\x89PNG\r\n\x1a\n", "image/png")})
    url = (r.json() or {}).get("url", "")
    check("穿越文件名被重写", r.status_code == 200 and url.endswith(".png"), url)
    check("没有写到 avatars 目录之外",
          not os.path.exists(os.path.join(TEST_DATA, "evil.txt"))
          and not os.path.exists(os.path.join(_BACKEND, "evil.txt")))

    r = c.post("/api/character/avatar", headers=H,
               data={"character_id": str(cid)},
               files={"file": ("big.png", b"0" * (11 * 1024 * 1024), "image/png")})
    check("超过 10MB 被拒", r.status_code == 400, str(r.status_code))

    r = c.post("/api/character/avatar", headers=H,
               data={"character_id": str(cid)},
               files={"file": ("x.exe", b"\x89PNG\r\n\x1a\n", "application/octet-stream")})
    check("非白名单扩展名被改写", (r.json() or {}).get("url", "").endswith(".png"),
          str(r.json())[:80])

print("\n[5] 管理端令牌")
check("仅 App 令牌不能访问管理接口",
      c.get("/api/logs?lines=5", headers=H).status_code == 401)
check("错误管理令牌被拒",
      c.get("/api/logs?lines=5", headers={**H, "X-Admin-Token": "bad"}).status_code == 401)
check("有效管理令牌可访问",
      c.get("/api/logs?lines=5", headers={**H, "X-Admin-Token": main.ADMIN_TOKEN}).status_code == 200)

_ts_old_a = int(time.time()) - (main.ADMIN_TOKEN_TTL + 3600)
_sig_old_a = hmac.new(main._admin_secret().encode("utf-8"),
                      ("%s:%d" % (main.ADMIN_PASSWORD, _ts_old_a)).encode("utf-8"),
                      hashlib.sha256).hexdigest()[:32]
_expired_a = "%d.%s" % (_ts_old_a, _sig_old_a)
check("过期管理令牌判为无效", not main._admin_token_valid(_expired_a))

_ts_future_a = int(time.time()) + 3600
_sig_future_a = hmac.new(main._admin_secret().encode("utf-8"),
                         ("%s:%d" % (main.ADMIN_PASSWORD, _ts_future_a)).encode("utf-8"),
                         hashlib.sha256).hexdigest()[:32]
check("未来时间戳管理令牌判为无效",
      not main._admin_token_valid("%d.%s" % (_ts_future_a, _sig_future_a)))

print("\n[6] 未配置令牌时拒绝启动")
tmp = os.path.join(_BACKEND, "_test_security_boot")
if os.path.exists(tmp):
    shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
env = os.environ.copy()
env.pop("STORIES_APP_TOKEN", None)
env.pop("STORIES_ALLOW_NO_AUTH", None)
env["STORIES_DATA_DIR"] = tmp
env["USERPROFILE"] = tmp
env["HOME"] = tmp
p = subprocess.run([sys.executable, "-c", "import main"], cwd=_BACKEND, env=env,
                   capture_output=True, encoding="utf-8", errors="replace", timeout=60)
out = (p.stdout or "") + (p.stderr or "")
check("无令牌启动失败", p.returncode != 0, "rc=%s" % p.returncode)
check("错误信息说明缺少令牌", "STORIES_APP_TOKEN" in out, out[:120])
shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 60)
print("结果：%d 通过，%d 失败" % (ok, fail))

try:
    db_conn = main.db.get_conn()
    db_conn.close()
except Exception:
    pass
try:
    import logging
    lg = logging.getLogger("stories")
    for h in list(lg.handlers):
        try:
            h.close()
        except Exception:
            pass
        lg.removeHandler(h)
    logging.shutdown()
except Exception:
    pass
shutil.rmtree(TEST_DATA, ignore_errors=True)
if os.path.exists(TEST_DATA):
    subprocess.Popen([sys.executable, "-c",
                      "import shutil,time; time.sleep(2); shutil.rmtree(r'%s', ignore_errors=True)" % TEST_DATA],
                     creationflags=0x08000000 if os.name == "nt" else 0)

sys.exit(0 if fail == 0 else 1)

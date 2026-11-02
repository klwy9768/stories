# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""邮件通知测试：配置解析、未配置跳过、发信内容、错误不回显密码"""
import io
import os
import shutil
import smtplib
import sys
sys.dont_write_bytecode = True

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = _HERE if os.path.exists(os.path.join(_HERE, "main.py")) else os.path.dirname(_HERE)
TEST_DATA = os.path.join(_BACKEND, "_test_mail_%d" % os.getpid())
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.makedirs(TEST_DATA, exist_ok=True)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
sys.path.insert(0, _BACKEND)

import mailer

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


CONF = os.path.join(TEST_DATA, "mail_self.conf")
with open(CONF, "w", encoding="utf-8") as f:
    f.write("# 注释行\nSMTP_HOST=smtp.example.com\nSMTP_PORT=465\n"
            "SMTP_USER=bot@example.com\n"
            + "SMTP_PASS=" + "test-auth-code" + "\n"
            "TO=a@example.com,b@example.com\n")

print("=" * 60)
print("[1] 配置解析")
cfg = mailer.load_config()
check("读取 SMTP_HOST", cfg.get("SMTP_HOST") == "smtp.example.com", str(cfg.get("SMTP_HOST")))
check("读取收件人", cfg.get("TO") == "a@example.com,b@example.com")
check("已配置则启用", mailer.enabled(cfg) is True)

print("\n[2] 未配置时自动跳过")
empty = {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "bot@example.com",
         "SMTP_PASS": "", "TO": ""}
check("缺授权码时停用", mailer.enabled(empty) is False)
res = mailer.send("主题", "正文", cfg=empty)
check("未配置直接 skipped", res.get("skipped") is True and res.get("ok") is False, str(res))

print("\n[3] 发信内容")
sent = {}


class FakeSMTP:
    def __init__(self, host, port, context=None, timeout=None):
        sent["host"] = host
        sent["port"] = port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        sent["user"] = user
        sent["password"] = password

    def send_message(self, msg):
        sent["to"] = str(msg["To"])
        sent["subject"] = str(msg["Subject"])
        sent["from"] = str(msg["From"])
        sent["body"] = msg.get_content()


orig_smtp = mailer.smtplib.SMTP_SSL
mailer.smtplib.SMTP_SSL = FakeSMTP
try:
    r = mailer.send("备份失败", "任务退出码 1", cfg=cfg)
finally:
    mailer.smtplib.SMTP_SSL = orig_smtp

check("发送成功", r.get("ok") is True, str(r))
check("使用配置的主机与端口", sent.get("host") == "smtp.example.com" and sent.get("port") == 465)
check("登录使用配置账号", sent.get("user") == "bot@example.com")
check("收件人正确", "a@example.com" in (sent.get("to") or "")
      and "b@example.com" in (sent.get("to") or ""), sent.get("to"))
check("主题/正文正确", sent.get("subject") == "备份失败" and "退出码 1" in (sent.get("body") or ""))
check("返回结果不含密码", "test-auth-code" not in str(r))


class BadSMTP(FakeSMTP):
    def login(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"auth failed")


mailer.smtplib.SMTP_SSL = BadSMTP
try:
    r2 = mailer.send("主题", "正文", cfg=cfg)
finally:
    mailer.smtplib.SMTP_SSL = orig_smtp
check("认证失败返回错误", r2.get("ok") is False and "error" in r2, str(r2)[:80])
check("错误信息不含密码", "test-auth-code" not in str(r2))

print("\n[4] 环境变量优先")
os.environ["SMTP_PASS"] = "env-pass"
try:
    cfg2 = mailer.load_config()
    check("环境变量覆盖文件", cfg2.get("SMTP_PASS") == "env-pass")
finally:
    os.environ.pop("SMTP_PASS", None)

print("\n" + "=" * 60)
print("结果：%d 通过，%d 失败" % (ok, fail))
shutil.rmtree(TEST_DATA, ignore_errors=True)
sys.exit(0 if fail == 0 else 1)

# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""可选邮件通知：读取本地配置发信，未配置授权码或收件人时自动跳过。

配置文件（任选其一，按顺序查找）：
  1. 环境变量 STORIES_MAIL_CONF 指向的文件
  2. <STORIES_DATA_DIR>/mail_self.conf
  3. ~/.config/mail-self.conf

也支持直接用环境变量覆盖：SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / TO

安全约定：SMTP_PASS 与 TO 为空即视为未启用，不会尝试发信；错误信息不会回显密码。
"""
import os
import smtplib
import ssl
from email.message import EmailMessage

_FIELDS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "TO")


def _data_dir() -> str:
    return os.environ.get("STORIES_DATA_DIR") or os.path.join(os.path.dirname(__file__), "data")


def config_paths():
    return [
        os.environ.get("STORIES_MAIL_CONF") or "",
        os.path.join(_data_dir(), "mail_self.conf"),
        os.path.expanduser("~/.config/mail-self.conf"),
    ]


def load_config() -> dict:
    """读取配置；文件只解析 KEY=VALUE，# 开头为注释。环境变量优先级最高。"""
    cfg = {}
    for path in config_paths():
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    cfg[k.strip().upper()] = v.strip()
            break
        except Exception:
            continue
    for k in _FIELDS:
        v = os.environ.get(k)
        if v:
            cfg[k] = v
    return cfg


def enabled(cfg: dict = None) -> bool:
    cfg = cfg or load_config()
    return bool((cfg.get("SMTP_PASS") or "").strip() and (cfg.get("TO") or "").strip())


def send(subject: str, body: str, cfg: dict = None, timeout: int = 15) -> dict:
    """发送一封纯文本邮件。未配置授权码/收件人时返回 skipped，不发起网络请求。"""
    cfg = cfg or load_config()
    if not enabled(cfg):
        return {"ok": False, "skipped": True, "reason": "mail not configured"}
    host = (cfg.get("SMTP_HOST") or "smtp.qq.com").strip()
    try:
        port = int(cfg.get("SMTP_PORT") or 465)
    except Exception:
        port = 465
    user = (cfg.get("SMTP_USER") or "").strip()
    to = (cfg.get("TO") or "").strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout) as s:
            s.login(user, cfg.get("SMTP_PASS"))
            s.send_message(msg)
        return {"ok": True}
    except Exception as e:
        # 不回显密码；只返回异常类型与简短信息
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python mailer.py <主题> [正文]")
        raise SystemExit(2)
    res = send(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
    print(res)
    raise SystemExit(0 if res.get("ok") else 1)

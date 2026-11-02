# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""Stories 统一日志：分级、轮转（3MB×5）、分类标签（仿 astrbot 格式）"""
import glob
import logging
import os
import re
import traceback
from logging.handlers import RotatingFileHandler

DATA_DIR = os.environ.get("STORIES_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
LOG_FILE = os.path.join(DATA_DIR, "stories.log")

# 日志脱敏：常见密钥/令牌形态一律打码，避免写进日志文件或上报
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|LTAI[A-Za-z0-9]{8,}|AKID[A-Za-z0-9]{8,}|[0-9a-f]{32})"
)


def _redact(msg: str) -> str:
    try:
        return _SECRET_RE.sub("***", str(msg))
    except Exception:
        return str(msg)

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
_initialized = False


def init_logging():
    global _initialized
    if _initialized:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    logger = logging.getLogger("stories")
    if not logger.handlers:
        handler = RotatingFileHandler(LOG_FILE, maxBytes=3 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _initialized = True


def log(msg: str, level: str = "INFO"):
    """写一条日志；msg 建议带 [tag] 前缀，如 [ai] xxx"""
    try:
        init_logging()
        logging.getLogger("stories").log(_LEVELS.get(level.upper(), logging.INFO), _redact(msg))
    except Exception:
        pass


def log_exc(msg: str):
    """写一条错误日志（带堆栈）"""
    try:
        log(f"{msg}\n{traceback.format_exc()}", level="ERROR")
    except Exception:
        pass


def read_logs(lines: int = 300):
    """读取最近日志（当前文件 + 轮转备份，按修改时间排序合并取尾部）
    只读每个文件的尾部窗口，避免全量读入大文件"""
    try:
        n = max(1, min(lines, 2000))
        files = [LOG_FILE] + glob.glob(LOG_FILE + ".*")
        files = [f for f in files if os.path.exists(f)]
        if not files:
            return []
        files.sort(key=lambda f: os.path.getmtime(f))
        content = []
        need = n
        for f in reversed(files):  # 从最新文件往回取，凑够 n 行即停
            if need <= 0:
                break
            try:
                with open(f, "rb") as fp:
                    fp.seek(0, os.SEEK_END)
                    size = fp.tell()
                    window = min(size, 256 * 1024)
                    if window <= 0:
                        continue
                    fp.seek(size - window)
                    data = fp.read(window)
                chunk = data.decode("utf-8", "replace").splitlines()
                if window < size and chunk:
                    chunk = chunk[1:]  # 丢弃可能被截断的首行
                content = chunk + content
                need = n - len(content)
            except Exception:
                continue
        return [l.rstrip("\n") for l in content[-n:]]
    except Exception:
        return []

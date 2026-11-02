# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""统一密钥读取：环境变量优先 → 本地 secrets.local.json / 数据目录 secrets.json → 默认值。

生产环境由 docker --env-file ./secrets.env 注入，源码与镜像内不含任何密钥。
本地开发把密钥放进 backend/secrets.local.json（已在 .dockerignore 排除，不会进镜像）。
"""
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.expanduser("~/.stories_secrets.json"),
    os.path.join(_DIR, "secrets.local.json"),
    os.path.join(os.environ.get("STORIES_DATA_DIR", os.path.join(_DIR, "data")), "secrets.json"),
]

_cache = None


def _load_file():
    global _cache
    if _cache is not None:
        return _cache
    data = {}
    for fp in _CANDIDATES:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                data.update(d)
        except Exception:
            pass
    _cache = data
    return data


def get(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v:
        return str(v).strip()
    return str(_load_file().get(name) or default).strip()

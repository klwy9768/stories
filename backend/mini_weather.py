# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""小程序·天气：open-meteo 免费接口（无需密钥）+ 进程内缓存
- 供小程序代理接口使用（/api/mini/weather）
- 供角色提示词使用：brief(city) 返回一句话摘要；任何失败静默返回空串，绝不阻塞聊天
"""
import json
import threading
import time
import urllib.parse
import urllib.request

_CACHE = {}
_LOCK = threading.Lock()
_TTL_WEATHER = 1800  # 天气缓存 30 分钟
_TTL_GEO = 86400     # 城市坐标缓存 1 天
_FAIL_BACKOFF = 300  # 失败后 5 分钟内不重试（避免网络不通时反复拖慢回复生成）
_CACHE_MAX = 300     # 缓存条目上限（防止被随机城市名刷爆内存）


def _cache_put(key: str, value):
    with _LOCK:
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.clear()  # 个人自用：超限直接清空重建，简单且安全
        _CACHE[key] = (time.time(), value)

# WMO 天气代码 → 中文描述
_WMO_TEXT = {
    0: "晴", 1: "多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "有雾",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}


def _get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (StoriesMiniApp)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(city: str):
    """城市名 → (lat, lon, 标准名)；失败返回 None（成功缓存 1 天，失败冷却 5 分钟）"""
    city = str(city or "").strip()[:40]
    if not city:
        return None
    key = "geo:" + city
    now = time.time()
    with _LOCK:
        c = _CACHE.get(key)
        if c:
            ttl = _TTL_GEO if c[1] else _FAIL_BACKOFF
            if now - c[0] < ttl:
                return c[1]
    try:
        url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
            {"name": city, "count": 1, "language": "zh", "format": "json"})
        d = _get_json(url, timeout=8)
        arr = d.get("results") or []
        if not arr:
            raise RuntimeError("no result")
        it = arr[0]
        out = (float(it["latitude"]), float(it["longitude"]), str(it.get("name") or city))
    except Exception:
        _cache_put(key, None)
        return None
    _cache_put(key, out)
    return out


def fetch(city: str):
    """当前 + 未来 2 天天气；成功返回 dict，失败 None（成功缓存 30 分钟，失败冷却 5 分钟）"""
    geo = geocode(city)
    if not geo:
        return None
    lat, lon, name = geo
    key = "wx:%.3f,%.3f" % (lat, lon)
    now = time.time()
    with _LOCK:
        c = _CACHE.get(key)
        if c:
            ttl = _TTL_WEATHER if c[1] else _FAIL_BACKOFF
            if now - c[0] < ttl:
                return c[1]
    try:
        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": "Asia/Shanghai", "forecast_days": 3,
        })
        d = _get_json(url, timeout=8)
        cur = d.get("current") or {}
        daily = d.get("daily") or {}
        dates = daily.get("time") or []
        codes = daily.get("weather_code") or []
        mins = daily.get("temperature_2m_min") or []
        maxs = daily.get("temperature_2m_max") or []
        days = []
        for i, dt in enumerate(dates[:3]):
            code = int((codes[i] if i < len(codes) else 0) or 0)
            days.append({
                "date": dt,
                "code": code,
                "text": _WMO_TEXT.get(code, "未知"),
                "min": mins[i] if i < len(mins) else None,
                "max": maxs[i] if i < len(maxs) else None,
            })
        code = int(cur.get("weather_code") or 0)
        out = {
            "city": name,
            "temp": cur.get("temperature_2m"),
            "code": code,
            "text": _WMO_TEXT.get(code, "未知"),
            "days": days,
        }
    except Exception:
        _cache_put(key, None)
        return None
    _cache_put(key, out)
    return out


def brief(city: str) -> str:
    """一句话天气摘要（给角色提示词用；失败返回空串）"""
    d = fetch(city)
    if not d:
        return ""
    try:
        today = (d.get("days") or [{}])[0]
        lo, hi = today.get("min"), today.get("max")
        rng = ""
        if lo is not None and hi is not None:
            rng = "%d~%d℃" % (round(float(lo)), round(float(hi)))
        t = d.get("temp")
        t_txt = ""
        if t is not None:
            t_txt = "，实时 %d℃" % round(float(t))
        return "%s%s%s" % (d.get("text") or "", ("（" + rng + "）" if rng else ""), t_txt)
    except Exception:
        return ""

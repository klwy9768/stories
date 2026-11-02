# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""小程序·余额：查询各模型平台的账户余额
- DeepSeek：API Key 即可查询（官方 /user/balance）
- 火山方舟：用火山引擎账号 AK/SK 调费用中心接口（管理页配置，V4 签名）
- 阿里百炼：用阿里云 AK/SK 调 BSS 账户余额接口（管理页配置，RPC 签名）
- MiniMax：无公开余额接口（控制台指引）
"""
import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request
import uuid

import db
import secrets_cfg

_CACHE = {}  # pid -> (ts, result)
_LOCK = threading.Lock()
_TTL = 60  # 1 分钟缓存（成功/失败都缓存，避免故障时反复请求）
_last_force = {"ts": 0.0}  # 上次强制刷新时间（10 秒防连点）


def _cached(pid: str, fn, force: bool = False):
    """返回 (抓取时间, 结果)；force=True 时跳过缓存"""
    now = time.time()
    with _LOCK:
        c = _CACHE.get(pid)
        if c and not force and now - c[0] < _TTL:
            return c
    val = fn()
    with _LOCK:
        _CACHE[pid] = (time.time(), val)
        return _CACHE[pid]


def _setting(key: str) -> str:
    try:
        return str(db.get_settings().get(key) or "").strip()
    except Exception:
        return ""


def _cred(ak_key: str, sk_key: str, env_ak: str, env_sk: str):
    """AK/SK 只从环境变量/密钥文件读取（secrets.env）。
    修复：原实现优先读数据库设置 → 云账号密钥明文落库且会在管理页回显。
    参数 ak_key/sk_key 仅为兼容既有调用点保留，已不再使用。"""
    ak = str(secrets_cfg.get(env_ak) or "").strip()
    sk = str(secrets_cfg.get(env_sk) or "").strip()
    return ak, sk


def _get_json(req, timeout: int = 12):
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ============ DeepSeek（API Key 可查，支持多账号） ============

def _split_keys(*vals):
    out = []
    for v in vals:
        for part in str(v or "").replace("；", ",").replace(";", ",").replace("\n", ",").split(","):
            p = part.strip().strip('"').strip("'")
            if p and p not in out:
                out.append(p)
    return out


def deepseek_balance() -> dict:
    keys = _split_keys(secrets_cfg.get("DEEPSEEK_API_KEYS"), secrets_cfg.get("DEEPSEEK_API_KEY"))
    if not keys:
        return {"ok": False, "error": "未配置 DEEPSEEK_API_KEY（或 DEEPSEEK_API_KEYS）"}
    accts = []
    for k in keys:
        item = {"suffix": "…" + k[-4:]}
        try:
            req = urllib.request.Request("https://api.deepseek.com/user/balance",
                                         headers={"Authorization": "Bearer " + k})
            d = _get_json(req, timeout=10)
            infos = d.get("balance_infos") or []
            info = infos[0] if infos else {}
            item.update({
                "ok": True,
                "available": bool(d.get("is_available")),
                "total": str(info.get("total_balance") or "0"),
                "granted": str(info.get("granted_balance") or ""),
                "topped": str(info.get("topped_up_balance") or ""),
            })
        except Exception as e:
            item.update({"ok": False, "error": str(e)[:60]})
        accts.append(item)
    ok_accts = [a for a in accts if a.get("ok")]
    if not ok_accts:
        return {"ok": False, "error": accts[0].get("error") or "查询失败"}
    if len(keys) == 1:
        a = accts[0]
        detail_parts = []
        if a.get("granted") not in (None, ""):
            detail_parts.append("赠送 ¥" + str(a.get("granted")))
        if a.get("topped") not in (None, ""):
            detail_parts.append("充值 ¥" + str(a.get("topped")))
        return {
            "ok": True,
            "available": bool(a.get("available", True)),
            "currency": "CNY",
            "total": str(a.get("total") or "0"),
            "detail": " · ".join(detail_parts),
        }
    total = 0.0
    for a in ok_accts:
        try:
            total += float(a.get("total") or 0)
        except Exception:
            pass
    total_s = ("%.2f" % total).rstrip("0").rstrip(".") if total else "0"
    detail_bits = []
    for a in accts:
        if a.get("ok"):
            detail_bits.append(f"{a['suffix']} ¥{a.get('total')}")
        else:
            detail_bits.append(f"{a['suffix']} 查询失败")
    return {
        "ok": True,
        "available": all(bool(a.get("available", True)) for a in ok_accts),
        "currency": "CNY",
        "total": total_s,
        "detail": " · ".join(detail_bits),
    }


# ============ 火山引擎（费用中心，AK/SK + V4 签名） ============

def _volc_sign_headers(ak: str, sk: str, host: str, query: dict, region: str = "cn-north-1",
                       service: str = "billing") -> dict:
    method = "GET"
    payload = b""
    payload_hash = hashlib.sha256(payload).hexdigest()
    x_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    date = x_date[:8]
    canonical_qs = "&".join(
        f"{urllib.parse.quote(str(k), safe='-_.~')}={urllib.parse.quote(str(v), safe='-_.~')}"
        for k, v in sorted(query.items()))
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        "content-type:application/x-www-form-urlencoded\n"
        f"host:{host}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{x_date}\n")
    canonical_request = "\n".join([method, "/", canonical_qs, canonical_headers, signed_headers, payload_hash])
    scope = f"{date}/{region}/{service}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])

    def _h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k = _h(sk.encode("utf-8"), date)
    k = _h(k, region)
    k = _h(k, service)
    k = _h(k, "request")
    sig = hmac.new(k, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Host": host,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={sig}",
    }


def volc_balance() -> dict:
    ak, sk = _cred("volc_ak", "volc_sk", "VOLC_AK", "VOLC_SK")
    if not (ak and sk):
        return {"ok": False, "error": "未配置火山引擎 AK/SK"}
    try:
        host = "billing.volcengineapi.com"
        query = {"Action": "QueryBalanceAcct", "Version": "2022-01-01"}
        headers = _volc_sign_headers(ak, sk, host, query)
        qs = "&".join(
            f"{urllib.parse.quote(str(k), safe='-_.~')}={urllib.parse.quote(str(v), safe='-_.~')}"
            for k, v in sorted(query.items()))
        d = _get_json(urllib.request.Request(f"https://{host}/?{qs}", headers=headers))
        err = (d.get("ResponseMetadata") or {}).get("Error")
        if isinstance(err, dict):
            return {"ok": False, "error": f"{err.get('Code')}: {err.get('Message')}"[:80]}
        res = d.get("Result") or {}
        total = None
        for key in ("AvailableBalance", "available_balance", "Balance", "balance"):
            if res.get(key) not in (None, ""):
                total = str(res.get(key))
                break
        if total is None:
            return {"ok": False, "error": "接口返回缺少余额字段"}
        parts = []
        for label, key in (("现金", "CashBalance"), ("冻结", "FreezeAmount"), ("信用额度", "CreditLimit")):
            v = res.get(key)
            if v not in (None, ""):
                parts.append(f"{label} ¥{v}")
        return {"ok": True, "currency": "CNY", "total": total, "detail": " · ".join(parts)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


# ============ 阿里云（BSS 账户余额，AK/SK + RPC 签名） ============

def aliyun_balance() -> dict:
    ak, sk = _cred("aliyun_ak", "aliyun_sk", "ALIYUN_AK", "ALIYUN_SK")
    if not (ak and sk):
        return {"ok": False, "error": "未配置阿里云 AK/SK"}
    try:
        params = {
            "Action": "QueryAccountBalance",
            "Version": "2017-12-14",
            "Format": "JSON",
            "AccessKeyId": ak,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": uuid.uuid4().hex,
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        enc = lambda s: urllib.parse.quote(str(s), safe="~")  # noqa: E731
        canonical = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(params.items()))
        string_to_sign = "GET&%2F&" + enc(canonical)
        sig = base64.b64encode(hmac.new(
            (sk + "&").encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()).decode()
        params["Signature"] = sig
        url = "https://business.aliyuncs.com/?" + "&".join(f"{enc(k)}={enc(v)}" for k, v in params.items())
        d = _get_json(urllib.request.Request(url))
        if d.get("Success") is False or (d.get("Code") not in (None, "200", 200)):
            return {"ok": False, "error": f"{d.get('Code')}: {d.get('Message') or d.get('Msg')}"[:80]}
        data = d.get("Data") or {}
        total = data.get("AvailableAmount")
        if total in (None, ""):
            total = data.get("AvailableCashAmount")
        if total in (None, ""):
            return {"ok": False, "error": "接口返回缺少余额字段"}
        parts = []
        if data.get("AvailableCashAmount") not in (None, ""):
            parts.append(f"现金 ¥{data.get('AvailableCashAmount')}")
        return {"ok": True, "currency": str(data.get("Currency") or "CNY"),
                "total": str(total), "detail": " · ".join(parts)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


# ============ 汇总 ============

def providers(force: bool = False) -> list:
    """各平台余额列表（缓存 60 秒；force=True 强制刷新，10 秒内只放行一次）"""
    if force:
        now = time.time()
        with _LOCK:
            if now - float(_last_force["ts"] or 0) >= 10:
                _last_force["ts"] = now
            else:
                force = False  # 10 秒内刚刷过：直接用缓存，防连点刷爆上游
    def _alt_cred_ready(pid: str) -> bool:
        """平台可见性：除模型 Key 外，配了 AK/SK（管理页设置或环境变量）也算已配置"""
        m = {
            "ark": ("volc_ak", "volc_sk", "VOLC_AK", "VOLC_SK"),
            "dashscope": ("aliyun_ak", "aliyun_sk", "ALIYUN_AK", "ALIYUN_SK"),
        }.get(pid)
        if not m:
            return False
        return bool((_setting(m[0]) and _setting(m[1])) or (secrets_cfg.get(m[2]) and secrets_cfg.get(m[3])))

    out = []
    for pid, name, key_name, fn, console, hint in (
        ("deepseek", "DeepSeek（聊天/判定）", "DEEPSEEK_API_KEY", deepseek_balance,
         "https://platform.deepseek.com", ""),
        ("ark", "火山方舟（豆包聊天/出图）", "ARK_API_KEY", volc_balance,
         "https://console.volcengine.com/ark", "在管理页「余额查询」填入火山引擎 AK/SK 后可自动查询"),
        ("dashscope", "阿里百炼（知识库向量）", "DASHSCOPE_API_KEY", aliyun_balance,
         "https://bailian.console.aliyun.com", "在管理页「余额查询」填入阿里云 AK/SK 后可自动查询"),
    ):
        has_model_key = (str(secrets_cfg.get(key_name) or "").strip()
                         or str(secrets_cfg.get(key_name + "S") or "").strip())
        if not has_model_key and not _alt_cred_ready(pid):
            continue
        ts, r = _cached(pid, fn, force=force)
        entry = {"id": pid, "name": name, "console": console, "fetched_at": ts}
        entry.update(r)
        if not r.get("ok") and hint:
            entry["hint"] = hint
        out.append(entry)
    # MiniMax：无公开余额接口
    if str(secrets_cfg.get("MINIMAX_API_KEY") or "").strip():
        out.append({"id": "minimax", "name": "MiniMax（语音/视频）",
                    "console": "https://platform.minimaxi.com",
                    "ok": False, "manual": True, "error": "该平台无公开余额接口，请打开控制台查看"})
    return out

# -*- coding: utf-8 -*-
"""测试：后台模型切换 + 消息级管理（单条删除/清空对话）"""
import io
import os
import shutil
import sys
sys.dont_write_bytecode = True

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = _HERE if os.path.exists(os.path.join(_HERE, "main.py")) else os.path.dirname(_HERE)
TEST_DATA = os.path.join(_BACKEND, "_test_admin_%d" % os.getpid())
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
os.environ["STORIES_ALLOW_NO_AUTH"] = "1"
os.environ["ADMIN_PASSWORD"] = "test-admin"
os.environ["STORIES_NO_WAKE_LOOP"] = "1"

sys.path.insert(0, _BACKEND)

import main
import db
import ai
from fastapi.testclient import TestClient

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

token = c.post("/admin/login", json={"password": "test-admin"}).json()["token"]
H = {"X-Admin-Token": token}

print("=" * 60)
print("[1] 后台模型设置")
d = c.get("/admin/api/settings", headers=H).json()
check("默认值含三个模型", all(k in d["defaults"] for k in ["ark_model", "deepseek_model", "summary_model"]),
      str({k: d["defaults"][k] for k in ("ark_model", "deepseek_model", "summary_model")}))
cfg0 = ai._model_cfg()
check("ai 默认读取主模型", cfg0["ark"] == ai.ARK_MODEL, cfg0["ark"])
r = c.post("/admin/api/settings", headers=H, json={"settings": {
    "ark_model": "test-ark-model-x", "deepseek_model": "test-ds-model-x", "summary_model": "test-sum-x",
    "reply_delay": 0.5,
}}).json()
check("保存设置成功", r.get("ok") is True)
cfg1 = ai._model_cfg()
check("主模型已切换（立即生效）", cfg1["ark"] == "test-ark-model-x", cfg1["ark"])
check("备用模型已切换", cfg1["deepseek"] == "test-ds-model-x", cfg1["deepseek"])
check("摘要模型已切换", cfg1["summary"] == "test-sum-x", cfg1["summary"])
d2 = c.get("/admin/api/settings", headers=H).json()
check("设置持久化", d2["settings"].get("ark_model") == "test-ark-model-x")

print("\n[2] 消息级管理")
cid = c.post("/api/character/save", json={"name": "管理测试", "greeting": "开场白", "fields": {}}).json()["id"]
m1 = db.add_message(cid, "user", "text", "用户消息1")
m2 = db.add_message(cid, "character", "text", "角色消息2")
m3 = db.add_message(cid, "user", "text", "用户消息3")
check("造 3 条消息", db.count_messages(cid) == 4, f"共 {db.count_messages(cid)} 条（含开场白）")
r = c.post("/admin/api/messages/delete", headers=H, json={"id": m2}).json()
check("删除单条成功", r.get("ok") is True)
msgs = db.get_messages(cid)
check("消息2已删除", all(x["id"] != m2 for x in msgs) and len(msgs) == 3, f"剩 {len(msgs)} 条")
check("其他消息保留", any(x["id"] == m1 for x in msgs) and any(x["id"] == m3 for x in msgs))
r = c.post("/admin/api/messages/delete", headers=H, json={"id": 99999})
check("删除不存在返回404", r.status_code == 404)
r = c.post("/admin/api/messages/clear", headers=H, json={"id": cid}).json()
check("清空对话成功", r.get("ok") is True)
msgs2 = db.get_messages(cid)
check("清空后只剩开场白", len(msgs2) == 1 and msgs2[0]["content"] == "开场白", f"剩 {len(msgs2)} 条")
r = c.post("/admin/api/messages/clear", headers=H, json={"id": 99999})
check("清空不存在返回404", r.status_code == 404)

print("\n[3] 权限检查")
r = c.post("/admin/api/messages/delete", json={"id": m1})
check("无 token 删除被拒", r.status_code == 401)
r = c.post("/admin/api/messages/clear", json={"id": cid})
check("无 token 清空被拒", r.status_code == 401)

print("\n[4] 旧功能回归")
r = c.get("/admin/api/messages?character_id=%d" % cid, headers=H).json()
check("记录读取正常", "messages" in r and "total" in r)
r = c.get("/admin/api/settings", headers=H).json()
check("运行参数仍在", "reply_delay" in r["defaults"])

print("\n[5] 模型分类 + 照片/视频模型 + 视频开关")
d = c.get("/admin/api/models", headers=H).json()
check("模型分类齐全", all(k in d for k in ["chat", "deepseek", "image", "video"]),
      f"chat={len(d.get('chat',[]))} ds={len(d.get('deepseek',[]))} img={len(d.get('image',[]))} vid={len(d.get('video',[]))}")
check("视频含 MiniMax 模型", any("MiniMax" in x or x.startswith("T2V") for x in d.get("video", [])))
check("照片/视频默认值存在", "photo_model" in r["defaults"] and "video_model" in r["defaults"],
      f"{r['defaults'].get('photo_model')} / {r['defaults'].get('video_model')}")
r2 = c.post("/admin/api/settings", headers=H, json={"settings": {"photo_model": "doubao-seedream-5-0-pro-260628", "video_model": "MiniMax-Hailuo-02"}}).json()
check("保存照片/视频模型", r2.get("ok") is True)
import media as _media
check("media 读取新模型", _media._setting("photo_model") == "doubao-seedream-5-0-pro-260628"
      and _media._setting("video_model") == "MiniMax-Hailuo-02")
check("视频通道已接 MiniMax", "minimaxi.com" in _media.MINIMAX_T2A_URL)
rv = c.post("/api/chat/settings", json={"character_id": cid, "video_enabled": True}).json()
check("视频开关保存", rv["chat"].get("video_enabled") is True)
b = c.get("/api/bootstrap").json()
check("bootstrap 含 video_enabled", "video_enabled" in b["characters"][0]["chat"])

print("\n[6] 故事背景（创建可设/可修改/管理端可见）")
r = c.post("/api/character/save", json={"id": cid, "name": "管理测试", "greeting": "开场白",
                                        "story": "2050 年，人类在火星建立了殖民地，社会由工会联盟统治。"}).json()
check("保存故事背景", r.get("ok") is True)
d = c.get("/api/characters/%d" % cid).json()
check("详情返回故事背景", "火星" in (d.get("story") or ""), (d.get("story") or "")[:30])
c.post("/api/character/save", json={"id": cid, "name": "管理测试", "greeting": "开场白",
                                    "story": "2222 年，海底城市时代。"})
d2 = c.get("/api/characters/%d" % cid).json()
check("故事背景可修改", "海底城市" in (d2.get("story") or ""), (d2.get("story") or "")[:30])
ac = c.get("/admin/api/characters", headers=H).json()["characters"]
check("管理端返回故事背景", "海底城市" in (ac[0].get("story") or ""))
check("AI 提示词含故事背景", "海底城市" in ai.build_system(d2, db.get_chat(cid), [], []))


# 恢复默认模型设置（避免影响后续）
c.post("/admin/api/settings", headers=H, json={"settings": {"ark_model": "", "deepseek_model": "", "summary_model": "",
                                                            "photo_model": "", "video_model": ""}})
check("恢复默认设置", ai._model_cfg()["ark"] == ai.ARK_MODEL)


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
print("\n" + "=" * 60)
print(f"结果：{ok} 通过，{fail} 失败")
sys.exit(0 if fail == 0 else 1)

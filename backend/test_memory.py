# -*- coding: utf-8 -*-
"""记忆库回归测试：预过滤 / 判定丢弃 / 向量编解码 / 检索融合 / 群隔离 / 时间衰减 / 遗忘 / 主动回忆。

全部使用本地假嵌入与假判定，不产生任何 API 调用。
"""
import io
import os
import shutil
import sys
import time

sys.dont_write_bytecode = True
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
TEST_DATA = os.path.join(_HERE, "_test_memory_%d" % os.getpid())
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
os.environ["STORIES_ALLOW_NO_AUTH"] = "1"
os.environ["STORIES_NO_WAKE_LOOP"] = "1"
sys.path.insert(0, _HERE)

import db  # noqa: E402
import ai  # noqa: E402
import knowledge  # noqa: E402
import memory_kb  # noqa: E402

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


DIM = 64


def fake_embed(texts, timeout=0):
    """确定性"假嵌入"：按字符二元组散列成向量（相近文本 → 相近向量），不调用任何 API。"""
    out = []
    for t in texts:
        v = [0.0] * DIM
        s = str(t or "")
        for i in range(max(0, len(s) - 1)):
            bg = s[i:i + 2]
            v[hash(bg) % DIM] += 1.0
        if not any(v):
            v[0] = 1e-6
        out.append(v)
    return out


knowledge._embed = fake_embed          # 检索/入库统一走假嵌入
knowledge._rerank = lambda q, docs: None   # 关掉重排，验证"重排失败退回余弦"路径

_judge_state = {"p": 0.1}
ai.decision_probs = lambda state, questions, timeout=8: {k: _judge_state["p"] for k in questions}

db.init_db()
cid = db.create_character({"name": "记忆测试", "fields": {}, "greeting": "嗨"})
gid = db.create_group("记忆测试群", [cid])
cid2 = db.create_character({"name": "旁观者", "fields": {}, "greeting": "嗨"})

print("\n[1] 预过滤（不消耗判定调用）")
check("太短消息不索引", not memory_kb.worth_indexing({"kind": "text", "content": "嗯"}))
check("纯语气词不索引", not memory_kb.worth_indexing({"kind": "text", "content": "哈哈哈哈哈哈"}))
check("照片提示词不索引", not memory_kb.worth_indexing({"kind": "photo", "content": "窗外的雨景"}))
check("有信息量的消息会索引", memory_kb.worth_indexing(
    {"kind": "text", "content": "我明天要去上海出差三天，周五回来"}))

print("\n[2] 判定模型：高置信无用才丢（失败保留）")
_judge_state["p"] = 0.99
check("p=0.99 → 丢弃", memory_kb.judge_drop("哈哈哈哈", False, memory_kb._cfg()))
_judge_state["p"] = 0.30
check("p=0.30 → 保留", not memory_kb.judge_drop("我今天买了台新相机", False, memory_kb._cfg()))


def _bad_probe(state, questions, timeout=8):
    raise RuntimeError("判定不可用")


_orig_probe = ai.decision_probs
ai.decision_probs = _bad_probe
check("判定异常 → 保留（失败安全）", not memory_kb.judge_drop("随便一句话也要保留", False, memory_kb._cfg()))
ai.decision_probs = _orig_probe

print("\n[3] 向量编解码（base64/float32 + 旧格式兼容）")
packed = memory_kb._vec_pack([0.5, -1.25, 3.0])
back = memory_kb._vec_unpack(packed)
check("打包/解包往返一致", back is not None and abs(float(back[0]) - 0.5) < 1e-6
      and abs(float(back[1]) + 1.25) < 1e-6, str(back))
check("兼容旧的 JSON 数组向量", memory_kb._item_vec({"vector": [1.0, 2.0]}) is not None)
check("损坏的 base64 返回 None（不抛异常）", memory_kb._item_vec({"vec_b64": "!!!not-base64!!!"}) is None)

print("\n[4] 入库管道（增量 + 水位 + 去重）")
msgs = [
    ("user", "我明天要去上海出差三天，周五回来"),
    ("character", "那你要注意休息，别太累了"),
    ("user", "哈哈哈"),
    ("character", "我妈下周过生日，我在想要不要回老家一趟"),
    ("user", "上次说的那家咖啡店我去了，味道一般"),
]
for role, text in msgs:
    db.add_message(cid, role, "text", text)
_judge_state["p"] = 0.1  # 全部保留
st = memory_kb.index_scope(memory_kb.scope_of_char(cid), cid=cid, respect_daily=False)
check("入库新增 > 0", st["added"] > 0, str(st))
check("水位已推进", db.mem_index_get(memory_kb.scope_of_char(cid)) > 0)
st2 = memory_kb.index_scope(memory_kb.scope_of_char(cid), cid=cid, respect_daily=False)
check("重复执行不重复入库", st2["added"] == 0 and st2["scanned"] == 0, str(st2))

print("\n[5] 检索融合（叠加层）")
res = memory_kb.retrieve(cid, "上海出差的事", [], [])
hit_txt = " ".join(str(it.get("content") or "") for it in (res.get("items") or []))
check("检索成功标记 ok=True", res.get("ok") is True, str(res.get("latency_ms")))
check("能召回相关记忆", "上海" in hit_txt, hit_txt[:80])
check("返回条目带来源与日期", bool(res["items"]) and res["items"][0].get("source") == "character"
      and bool(res["items"][0].get("date")), str(res["items"][:1])[:120])

print("\n[6] 群记忆隔离（只有群成员能检索到，且来源标注为群）")
db.add_group_message(gid, "user", 0, "text", "国庆一起去青海湖自驾，路线我来定")
_judge_state["p"] = 0.1
memory_kb.index_scope(memory_kb.scope_of_group(gid), gid=gid, is_group=True, respect_daily=False)
r_member = memory_kb.retrieve(cid, "青海湖自驾路线", [], [gid])
# 给旁观者一条自己的私聊记忆：确保下面的隔离断言不是「库是空的」空跑
db.add_message(cid2, "user", "text", "我最近在学手冲咖啡，水温总是不稳")
_judge_state["p"] = 0.1
memory_kb.index_scope(memory_kb.scope_of_char(cid2), cid=cid2, respect_daily=False)
r_own = memory_kb.retrieve(cid2, "手冲咖啡水温", [], [])
own_txt = " ".join(str(it.get("content") or "") for it in (r_own.get("items") or []))
check("旁观者自己的记忆可检索（隔离测试非空跑）", "咖啡" in own_txt, own_txt[:60])

r_other = memory_kb.retrieve(cid2, "青海湖自驾路线", [], [])
m_txt = " ".join(str(it.get("content") or "") for it in (r_member.get("items") or []))
o_txt = " ".join(str(it.get("content") or "") for it in (r_other.get("items") or []))
check("群成员可检索到群记忆", "青海湖" in m_txt, m_txt[:80])
srcs = {it.get("source") for it in (r_member.get("items") or [])}
check("群条目来源标注为 group", ("group" in srcs) or ("青海湖" not in m_txt), str(srcs))
check("非成员检索不到群记忆（隔离生效）", "青海湖" not in o_txt, o_txt[:60])

print("\n[7] 时间衰减 / 遗忘曲线")
old_ts = time.time() - 400 * 86400
ent = memory_kb._load(memory_kb.scope_of_char(cid))
victim = ent["items"][0]
victim["ts"] = old_ts
victim["recall_n"] = 0
victim["recall_ts"] = 0
memory_kb._save(memory_kb.scope_of_char(cid), list(ent["items"]))
n = memory_kb.apply_forgetting(memory_kb.scope_of_char(cid))
check("长期未召回 → 转入淡出", n >= 1, "n=%d" % n)
ent2 = memory_kb._load(memory_kb.scope_of_char(cid))
check("淡出条目仍保留在库里（可恢复）", len(ent2["items"]) == len(ent["items"]))

print("\n[8] 主动回忆选材（只管角色说过的话、且非保密）")
ent3 = memory_kb._load(memory_kb.scope_of_char(cid))
for it in ent3["items"]:
    it["dormant"] = 0
    it["recall_ts"] = 0
    it["ts"] = time.time() - 30 * 86400
    it["role"] = "character"
    it["content"] = "我妈下周过生日，我在想要不要回老家一趟"
memory_kb._save(memory_kb.scope_of_char(cid), list(ent3["items"]))
pick = memory_kb.pick_proactive(cid)
check("能挑出主动回忆素材", bool(pick.get("content")), str(pick)[:80])
check("素材带 mid（可追溯来源）", int(pick.get("mid") or 0) > 0, str(pick.get("mid")))

print("\n" + "=" * 60)
print("结果：%d 通过，%d 失败" % (ok, fail))

try:
    db.get_conn().close()
except Exception:
    pass
try:
    if os.path.exists(TEST_DATA):
        shutil.rmtree(TEST_DATA)
except Exception:
    pass

sys.exit(0 if fail == 0 else 1)

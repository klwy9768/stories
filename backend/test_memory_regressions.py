# -*- coding: utf-8 -*-
"""记忆/知识库修复回归测试：覆盖审计报告里的 1/2/3/4/5/7/8/9/10/11 条。

全部使用本地假嵌入，不产生任何 API 调用。
"""
import io
import os
import shutil
import sys
import threading
import time

sys.dont_write_bytecode = True
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
TEST_DATA = os.path.join(_HERE, "_test_memory_reg")
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
os.environ["STORIES_ALLOW_NO_AUTH"] = "1"
os.environ["STORIES_NO_WAKE_LOOP"] = "1"
sys.path.insert(0, _HERE)

import db          # noqa: E402
import ai          # noqa: E402
import knowledge   # noqa: E402
import memory_kb   # noqa: E402

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


DIM = 32
STATE = {"fail": False, "calls": 0, "sleep": 0.0, "last_timeout": None}


def fake_embed(texts, timeout=0):
    STATE["calls"] += 1
    STATE["last_timeout"] = timeout
    if STATE["sleep"]:
        time.sleep(STATE["sleep"])
    if STATE["fail"]:
        return None
    out = []
    for t in texts:
        v = [0.0] * DIM
        s = str(t or "")
        for i in range(max(0, len(s) - 1)):
            v[hash(s[i:i + 2]) % DIM] += 1.0
        if not any(v):
            v[0] = 1e-6
        out.append(v)
    return out


knowledge._embed = fake_embed
knowledge._rerank = lambda q, docs: None
_judge = {"p": 0.1}
ai.decision_probs = lambda state, questions, timeout=8: {k: _judge["p"] for k in questions}

db.init_db()
cid = db.create_character({"name": "回归测试", "fields": {}, "greeting": "嗨"})
scope = memory_kb.scope_of_char(cid)

print("\n[1] 嵌入失败的记忆不会永久丢失（水位不推进）")
for text in ("第一条要记住的事：我在上海出差", "第二条：她喜欢雨天", "第三条：周末去看电影"):
    db.add_message(cid, "user", "text", text)
STATE["fail"] = True
st = memory_kb.index_scope(scope, cid=cid, respect_daily=False)
check("失败时 added=0", st["added"] == 0, str(st))
check("失败时水位仍为 0（没有跳过这批消息）", db.mem_index_get(scope) == 0, str(db.mem_index_get(scope)))
STATE["fail"] = False
st = memory_kb.index_scope(scope, cid=cid, respect_daily=False)
check("服务恢复后补回全部 3 条", st["added"] == 3, str(st))
check("补回后水位推进", db.mem_index_get(scope) > 0)

print("\n[2] 时间衰减只乘一次（老记忆不再被新记忆反超）")
now = time.time()
old_ts = now - 400 * 86400
items = [
    {"mid": 9001, "cid": cid, "ts": old_ts, "role": "character", "content": "老记忆：一起在城墙看雪",
     "pin": 0, "recall_n": 0, "recall_ts": 0, "dormant": 0, "vec_b64": memory_kb._vec_pack([1.0, 0.0])},
    {"mid": 9002, "cid": cid, "ts": now, "role": "character", "content": "新记忆：今天去吃了面",
     "pin": 0, "recall_n": 0, "recall_ts": 0, "dormant": 0,
     "vec_b64": memory_kb._vec_pack([0.65, 0.7600000000000001])},
]
memory_kb._save(scope, items)


def fake_q_embed_fixed(texts, timeout=0):
    return [[1.0, 0.0] for _ in texts]


knowledge._embed = fake_q_embed_fixed
res = memory_kb.retrieve(cid, "测试查询", [], [])
contents = [it.get("content") for it in res.get("items", [])]
check("老记忆排在新记忆之前", contents and contents[0].startswith("老记忆"), str(contents))
check("两条都被召回", len(contents) == 2, str(len(contents)))

print("\n[3] 关键词兜底对长记忆不再失效")
long_mem = ("那天我们聊了很多，" * 90) + "小林说她要回老家一趟，" + ("后来大家又聊了别的。" * 90)
kw = memory_kb._kw_score([memory_kb._bigrams("小林")], memory_kb._bigrams(long_mem))
check("长记忆命中人名 → 覆盖率 1.0（旧实现约 0.01）", kw >= 0.99, "kw=%.4f" % kw)
check("长度不再稀释分数", len(long_mem) > 1000 and kw >= 0.99, "len=%d" % len(long_mem))

print("\n[4] 向量服务挂掉 → 关键词降级（不再返回空）")
knowledge._embed = fake_embed
knowledge._embed_fail_until = 0.0
memory_kb._save(scope, items)
STATE["fail"] = True
res = memory_kb.retrieve(cid, "城墙看雪", [], [])
check("ok=True（降级后仍可用）", res.get("ok") is True, str(res))
check("模式标记为 keyword", res.get("mode") == "keyword", str(res.get("mode")))
check("关键词命中了旧记忆", any("城墙" in (it.get("content") or "") for it in res.get("items", [])),
      str([it.get("content") for it in res.get("items", [])])[:100])

print("\n[5] embedding 失败冷却：第二条查询不再白等 12 秒")
STATE["fail"] = True
t0 = time.time()
memory_kb.retrieve(cid, "冷却测试一", [], [])
first = time.time() - t0
t0 = time.time()
memory_kb.retrieve(cid, "冷却测试二", [], [])
second = time.time() - t0
check("冷却期内第二次检索 <0.3s（旧实现会再等 12s）", second < 0.3, "second=%.2fs" % second)
check("第一次检索也没有超过预算太多", first < 3.0, "first=%.2fs" % first)

print("\n[6] 时间预算覆盖 embedding（不是算完再计时）")
STATE["fail"] = False
STATE["last_timeout"] = None


def slow_embed(texts, timeout=0):
    STATE["last_timeout"] = timeout
    return None


knowledge._embed = slow_embed
knowledge._embed_fail_until = 0.0
res = memory_kb.retrieve(cid, "预算测试", [], [])
check("传给 embedding 的 timeout 受 3000ms 预算约束（≤3s）",
      STATE["last_timeout"] is not None and float(STATE["last_timeout"]) <= 3.0,
      "timeout=%s" % STATE["last_timeout"])
check("预算内正常返回（降级为 keyword）", res.get("ok") is True, str(res))

print("\n[7] 并发索引同一 scope 不丢条目")
STATE["fail"] = False
STATE["sleep"] = 0.02
db2 = db.create_character({"name": "并发测试", "fields": {}, "greeting": "嗨"})
scope2 = memory_kb.scope_of_char(db2)
for i in range(30):
    db.add_message(db2, "user", "text", "并发消息第%d条，内容足够长可以被索引" % i)
knowledge._embed = fake_embed
th1 = threading.Thread(target=lambda: memory_kb.index_scope(scope2, cid=db2, respect_daily=False))
th2 = threading.Thread(target=lambda: memory_kb.index_scope(scope2, cid=db2, respect_daily=False))
th1.start(); th2.start(); th1.join(); th2.join()
ent = memory_kb._load(scope2)
mids = [int(it.get("mid") or 0) for it in ent["items"]]
check("30 条一条不少", len(mids) == 30, "len=%d" % len(mids))
check("没有重复条目", len(set(mids)) == len(mids), "dups=%d" % (len(mids) - len(set(mids))))
STATE["sleep"] = 0.0

print("\n[8] 知识库重建：失败批次会补采，不再静默缺条目")
doc_id = db.add_kb_doc("character", cid, "test.md", "test_stored.md", "hash", status="ready")
db.add_kb_chunks(doc_id, "character", cid, [
    {"heading": "段1", "content": "小林出生在南方的一个小城"},
    {"heading": "段2", "content": "小林的生日是三月九日"},
    {"heading": "段3", "content": "小林十八岁生日那天搬了家"},
])
call_seq = {"n": 0}


def flaky_embed(texts, timeout=0):
    call_seq["n"] += 1
    # 前 2 次失败（首批 3 条重试全挂），之后成功
    if call_seq["n"] <= 2:
        return None
    out = []
    for t in texts:
        v = [0.0] * DIM
        s = str(t or "")
        for i in range(max(0, len(s) - 1)):
            v[hash(s[i:i + 2]) % DIM] += 1.0
        out.append(v)
    return out


knowledge._embed = flaky_embed
n = knowledge.rebuild_scope("character", cid)
check("重建后 3 条全部有向量（补采生效）", n == 3, "n=%d" % n)

print("\n[9] 淘汰策略：按「最旧且召回最少」，pin 不淘汰")
old_max = memory_kb.MAX_ITEMS_PER_SCOPE
memory_kb.MAX_ITEMS_PER_SCOPE = 5
db3 = db.create_character({"name": "淘汰测试", "fields": {}, "greeting": "嗨"})
scope3 = memory_kb.scope_of_char(db3)
ev_items = []
for i in range(8):
    ev_items.append({"mid": 100 + i, "cid": db3, "ts": now - (8 - i) * 86400,
                     "role": "user", "content": "淘汰测试记忆%d，内容足够长" % i,
                     "pin": 1 if i == 0 else 0, "recall_n": 5 if i == 1 else 0,
                     "recall_ts": 0, "dormant": 0,
                     "vec_b64": memory_kb._vec_pack([1.0] + [0.0] * (DIM - 1))})
memory_kb._save(scope3, ev_items)
memory_kb.index_scope(scope3, cid=db3, respect_daily=False)
ent = memory_kb._load(scope3)
kept = {int(it.get("mid")) for it in ent["items"]}
check("条目数受上限约束", len(kept) <= 5, "len=%d" % len(kept))
check("pin 条目保留", 100 in kept, str(sorted(kept)))
check("高召回条目保留", 101 in kept, str(sorted(kept)))
check("淘汰的是最旧且召回最少的", 102 not in kept and 103 not in kept, str(sorted(kept)))
memory_kb.MAX_ITEMS_PER_SCOPE = old_max

print("\n[10] 余弦只算一次（不再 topn/阈值/回退各算一遍）")
knowledge._embed = fake_embed
knowledge._embed_fail_until = 0.0
memory_kb._save(scope, items)
calls = {"n": 0}
_orig_cos = knowledge._cosine


def counting_cos(a, b):
    calls["n"] += 1
    return _orig_cos(a, b)


old_np = knowledge._np
knowledge._np = None          # 强制走纯 Python 路径，统计 _cosine 调用次数
knowledge._cosine = counting_cos
res = memory_kb.retrieve(cid, "测试查询", [], [])
knowledge._cosine = _orig_cos
knowledge._np = old_np
n_items = len([it for it in memory_kb._load(scope)["items"] if it.get("vec_b64")])
check("余弦调用次数 ≈ 条目数（≤ 条目数×查询数，而不是三倍以上）",
      calls["n"] <= n_items * 2, "calls=%d items=%d" % (calls["n"], n_items))

print("\n[11] 知识库检索：embedding 挂掉 → 关键词兜底（README 的降级承诺）")
knowledge._embed = fake_embed
knowledge._embed_fail_until = 0.0
knowledge.rebuild_scope("character", cid)
kb_vec = knowledge.retrieve("小林生日", cid, top_k=3)
check("向量模式能检索到生日条目",
      any("生日" in (r.get("content") or "") for r in kb_vec), str(len(kb_vec)))
STATE["fail"] = True
knowledge._embed_fail_until = 0.0
kb_kw = knowledge.retrieve("小林生日", cid, top_k=3)
check("embedding 失败时 KB 仍返回结果（不再直接空）", len(kb_kw) > 0, "n=%d" % len(kb_kw))
check("关键词兜底命中正确条目",
      any("生日" in (r.get("content") or "") for r in kb_kw),
      str([r.get("content") for r in kb_kw])[:120])
STATE["fail"] = False

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

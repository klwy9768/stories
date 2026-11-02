# -*- coding: utf-8 -*-
"""Stories 新功能全链路测试：语音 / 群聊（含节流）/ 朋友圈动态互动 / 跨功能交互
隔离数据目录，模型调用用桩替换（确定性、可重复多轮）"""
import io
import json
import os
import shutil
import sys
import time

sys.dont_write_bytecode = True
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = _HERE if os.path.exists(os.path.join(_HERE, "main.py")) else os.path.dirname(_HERE)
TEST_DATA = os.path.join(_BACKEND, "_test_data2_%d" % os.getpid())
if os.path.exists(TEST_DATA):
    shutil.rmtree(TEST_DATA)
os.environ["STORIES_DATA_DIR"] = TEST_DATA
os.environ["STORIES_ALLOW_NO_AUTH"] = "1"
os.environ["ADMIN_PASSWORD"] = "test-pass"
os.environ["STORIES_NO_WAKE_LOOP"] = "1"

sys.path.insert(0, _BACKEND)

import main
from fastapi.testclient import TestClient

main._cfg = lambda: {"reply_delay": 0.2, "msg_delay_factor": 0.05, "max_history": 40}
# 测试不联网：决策模型判断置空 → 联网判断走文本回退
main.ai.systemone = lambda *a, **k: None
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


print("=" * 60)
print("[0] 建两个测试角色")
r1 = c.post("/api/character/save", json={
    "name": "团团", "description": "测试", "persona_full": "你是团团，一个爱笑的女孩。",
    "greeting": "嗨～",
}).json()
r2 = c.post("/api/character/save", json={
    "name": "圆圆", "description": "测试", "persona_full": "你是圆圆，一个安静的女生。",
    "greeting": "你好。",
}).json()
cid1, cid2 = r1.get("id"), r2.get("id")
check("创建角色A", bool(cid1), f"cid1={cid1}")
check("创建角色B", bool(cid2), f"cid2={cid2}")

# ============ [A] 语音消息 ============
print("\n[A] 语音消息（转述用桩替换）")
orig_st = main.media.transcribe_audio
orig_gen = main.ai.generate_reply
main.media.transcribe_audio = lambda path: "宝贝我训练回来啦"
main.ai.generate_reply = lambda character, chat, history, user_text, image_path=None, extra="", audio_path=None: {
    "reply": "你终于回来啦", "photo_prompt": "", "voice": False, "sticker": None, "video_prompt": ""}
try:
    r = c.post("/api/chat/voice", data={"character_id": cid1, "mode": "input", "duration": "2.0"},
               files={"file": ("v.m4a", b"FAKEAUDIO", "audio/mp4")}).json()
    check("语音转文字（input 模式）", r.get("ok") is True and r.get("text") == "宝贝我训练回来啦", str(r)[:80])
    msgs = c.get(f"/api/chat/messages?character_id={cid1}").json()["messages"]
    check("input 模式不产生消息", not any(m.get("kind") == "voice" for m in msgs))

    r = c.post("/api/chat/voice", data={"character_id": cid1, "mode": "send", "duration": "3.2"},
               files={"file": ("v.m4a", b"FAKEAUDIO", "audio/mp4")}).json()
    check("语音发送（send 模式）返回成功", r.get("ok") is True and bool(r.get("msg_id")), str(r)[:80])
    msgs = c.get(f"/api/chat/messages?character_id={cid1}").json()["messages"]
    voice_msgs = [m for m in msgs if m["role"] == "user" and m.get("kind") == "voice"]
    check("语音消息内容=转写文本", bool(voice_msgs) and voice_msgs[-1]["content"] == "宝贝我训练回来啦",
          str(voice_msgs[-1:])[:100])
    check("语音消息带媒体URL", bool(voice_msgs) and str(voice_msgs[-1].get("media_url") or "").startswith("/media/user/"))

    main._process_pending(cid1)
    reply_ok = False
    for _i in range(20):
        time.sleep(0.4)
        main._process_pending(cid1)
        msgs = c.get(f"/api/chat/messages?character_id={cid1}").json()["messages"]
        if any(m["role"] == "character" and "你终于回来啦" in (m.get("content") or "") for m in msgs):
            reply_ok = True
            break
    check("语音消息触发角色回复", reply_ok)
    check("回复里能看到转写文本", any(m["role"] == "user" and m.get("content") == "宝贝我训练回来啦" for m in msgs))

    # 语音转述关闭：录音后不再识别，原音频直接交给主模型
    c.post("/api/chat/settings", json={"character_id": cid1, "voice_desc_enabled": False})
    orig_st2 = main.media.transcribe_audio
    main.media.transcribe_audio = lambda path: (_ for _ in ()).throw(AssertionError("转述关闭时不应调用转述"))
    try:
        r = c.post("/api/chat/voice", data={"character_id": cid1, "mode": "send", "duration": "2.0"},
                   files={"file": ("v2.m4a", b"FAKEAUDIO2", "audio/mp4")}).json()
        check("转述关闭：语音不识别也能发送", r.get("ok") is True and bool(r.get("msg_id")) and r.get("text") == "",
              str(r)[:100])
    finally:
        main.media.transcribe_audio = orig_st2
        c.post("/api/chat/settings", json={"character_id": cid1, "voice_desc_enabled": True})
finally:
    main.media.transcribe_audio = orig_st
    main.ai.generate_reply = orig_gen

# ============ [B] 群聊 ============
print("\n[B] 群聊")
g = c.post("/api/group/create", json={"name": "", "cids": [cid1, cid2]}).json()
gid = g.get("id")
check("创建群聊（自动命名）", g.get("ok") is True and bool(gid), str(g)[:60])
lst = c.get("/api/groups").json()["groups"]
grp = [x for x in lst if x["id"] == gid]
grp = grp[0] if grp else {}
check("群列表含成员与名称", len(grp.get("members") or []) == 2 and bool(grp.get("name")),
      f"name={grp.get('name')} members={len(grp.get('members') or [])}")
check("新建群默认开启主动发言", grp.get("auto") is True)

calls = {"direct": 0, "peer": 0}
orig_gr = main.ai.generate_group_reply


def fake_group_reply(character, group, names, history_lines, user_hint, **kw):
    if kw.get("peer_name"):
        calls["peer"] += 1
    else:
        calls["direct"] += 1
    return f"我是{character.get('name')}，接一句"


main.ai.generate_group_reply = fake_group_reply
_orig_rand_b = main.random.random
main.random.random = lambda: 0.999  # [B] 阶段禁随机：避免互聊概率性触发污染后续断言
_orig_sys_b = main.ai.systemone


def _fake_sys_b(state, qs, **kw):
    """群聊决策模型桩：值得回应/适合发言=高；仅第一名成员高、其余低（保证 n=1、点名优先可验证）"""
    out = {}
    _first = True
    for kk in qs:
        _kk = str(kk)
        if _kk == "g":
            out[_kk] = {"noul": 0.9}
        else:
            out[_kk] = {"noul": 0.9 if _first else 0.1}
            _first = False
    return out


main.ai.systemone = _fake_sys_b
# 决策层已改为双模型（豆包 SystemOne 主判 + 百炼兜底），测试桩要同时覆盖，
# 否则会走真实豆包调用（0.5~8s），超过下面的 sleep(5) 导致偶发失败。
_orig_dual_b = main.ai.decision_probs_dual


def _fake_dual_b(state, questions, **kw):
    qs = {str(k): {"type": "noul", "instructions": str(v),
                   "criteria": {"true": "是", "false": "否"}} for k, v in questions.items()}
    ans = _fake_sys_b(state, qs) or {}
    return {str(k): float(v["noul"]) for k, v in ans.items()
            if isinstance(v, dict) and "noul" in v}


main.ai.decision_probs_dual = _fake_dual_b
try:
    r = c.post("/api/group/send", json={"gid": gid, "content": "你们好啊"}).json()
    check("群消息发送", r.get("ok") is True, str(r)[:60])
    time.sleep(5)
    d = c.get(f"/api/group/messages?gid={gid}&after_id=0").json()
    chars = [m for m in d["messages"] if m["role"] == "character"]
    check("角色群内接话", len(chars) >= 1, f"character_msgs={len(chars)}")
    check("用户消息直答最多 2 次生成（token 节制）", calls["direct"] <= 2,
          f"direct={calls['direct']} peer={calls['peer']}")
    check("用户消息触发的互聊最多 1 句", calls["peer"] <= 1, f"peer={calls['peer']}")
    check("群消息带角色名", bool(chars) and bool(chars[0].get("name")), str(chars[:1])[:90])
    check("群消息不重复入库", len({m["id"] for m in d["messages"]}) == len(d["messages"]))

    sys1 = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
    check("群消息动态注入私聊上下文", f"群「{grp.get('name')}」" in sys1 and "你们好啊" in sys1,
          sys1[-150:].replace("\n", " ")[:110])

    c.post("/api/group/settings", json={"gid": gid, "name": "测试新群名", "auto": False})
    d = c.get(f"/api/group/messages?gid={gid}&after_id=0").json()
    check("群改名 + 关主动发言", d["name"] == "测试新群名" and d["auto"] is False)
    c.post("/api/group/settings", json={"gid": gid, "cids": [cid1]})
    d = c.get(f"/api/group/messages?gid={gid}&after_id=0").json()
    check("群成员变更（移除一人）", len(d["members"]) == 1, str(d["members"])[:60])

    print("[B2] 主动发言节流")
    _auto_before = main.db.group_last_auto_ts(gid)
    main._group_tick()
    check("刚聊过不触发主动发言", main.db.group_last_auto_ts(gid) == _auto_before)
    conn = main.db.get_conn()
    conn.execute("UPDATE group_messages SET created_at=created_at-900 WHERE group_id=?", (gid,))
    conn.commit()
    c.post("/api/group/settings", json={"gid": gid, "auto": True})
    orig_rand = main.random.random
    orig_choice = main.random.choice
    main.random.random = lambda: 0.0
    main.random.choice = lambda seq: seq[0]
    try:
        main._group_tick()
        check("安静期到点触发主动发言（已记账）", main.db.group_last_auto_ts(gid) > 0)
        time.sleep(5)
        d = c.get(f"/api/group/messages?gid={gid}&after_id=0").json()
        check("主动发言已入库", any(m["role"] == "character" for m in d["messages"]))
    finally:
        main.random.random = orig_rand
        main.random.choice = orig_choice

    print("[B3] 角色间互聊（三重节制）")
    orig_rand = main.random.random
    orig_choice = main.random.choice
    main.random.random = lambda: 0.0
    main.random.choice = lambda seq: seq[0]
    try:
        c.post("/api/group/settings", json={"gid": gid, "cids": [cid1, cid2]})  # 恢复两名成员再测互聊
        last = main.db.group_last_message(gid)
        last_cid = int(last.get("cid") or 0)
        n_chain = main._group_peer_chain(gid, last_cid, max_rounds=2, force=True)
        check("角色间互相接话（上限 2 轮）", n_chain == 2, f"sent={n_chain}")
        d = c.get(f"/api/group/messages?gid={gid}&after_id=0").json()
        peers = [m for m in d["messages"] if m["role"] == "character" and int(m["cid"]) != last_cid]
        check("接话方为另一名成员", len(peers) >= 1, str(peers[:1])[:90])
        check("互聊已计入每日额度", main.db.group_last_auto_ts(gid) > 0)
        c.post("/api/group/settings", json={"gid": gid, "auto": False})
        n_off = main._group_peer_chain(gid, last_cid, max_rounds=2, force=True)
        check("关闭主动发言后互聊被禁止", n_off == 0)
        c.post("/api/group/settings", json={"gid": gid, "auto": True})
    finally:
        main.random.random = orig_rand
        main.random.choice = orig_choice

    print("[B4] 点名优先接话")
    _or_sh = main.random.shuffle
    _or_rd2 = main.random.random
    main.random.shuffle = lambda seq: None
    main.random.random = lambda: 0.999
    try:
        _name2 = main.db.get_character(cid2)["name"]
        _d_b4a = c.get(f"/api/group/messages?gid={gid}&after_id=0").json()["messages"]
        _last_id_b4 = max([int(m["id"]) for m in _d_b4a if m["role"] == "character"] or [0])
        c.post("/api/group/send", json={"gid": gid, "content": _name2 + "在吗？"})
        time.sleep(6)
        _d_b4 = c.get(f"/api/group/messages?gid={gid}&after_id=0").json()
        _new_chars = [m for m in _d_b4["messages"]
                      if m["role"] == "character" and int(m["id"]) > _last_id_b4]
        check("点名谁谁优先接话", bool(_new_chars) and int(_new_chars[0].get("cid") or 0) == cid2,
              str(_new_chars[:2])[:90])
    finally:
        main.random.shuffle = _or_sh
        main.random.random = _or_rd2
finally:
    main.random.random = _orig_rand_b
    main.ai.generate_group_reply = orig_gr
    main.ai.systemone = _orig_sys_b
    main.ai.decision_probs_dual = _orig_dual_b

# 群聊接话模型：默认 flash、管理页可配（私聊不受影响）
_gm_seen = {}


def _fake_call_gm(messages, model=None, **kw):
    _gm_seen["m"] = model
    return '{"reply":"好呀"}', None


_orig_call_gm = main.ai._call_model
main.ai._call_model = _fake_call_gm
try:
    main.ai.generate_group_reply(main.db.get_character(cid1), {"name": "群"}, {cid1: "甲"}, [], "在吗")
    check("群聊接话默认走 flash", _gm_seen.get("m") == main.ai.MODEL_FLASH, str(_gm_seen.get("m")))
    main.db.set_settings({"group_model": "test-model-x"})
    main.ai.generate_group_reply(main.db.get_character(cid1), {"name": "群"}, {cid1: "甲"}, [], "在吗")
    check("群聊模型设置生效", _gm_seen.get("m") == "test-model-x", str(_gm_seen.get("m")))
finally:
    main.ai._call_model = _orig_call_gm
    main.db.set_settings({"group_model": ""})

# 私聊可触发群聊发言（回复 JSON 的 group_msg 字段；随后大概率引发另一位角色接话）
_orig_gen_pg = main.ai.generate_reply
_orig_gpr_pg = main.ai.generate_group_reply
_orig_rd3 = main.random.random
_orig_un3 = main.random.uniform
main.random.random = lambda: 0.999
main.random.uniform = lambda a, b: 0.05  # 压缩延迟，快速回归
main.ai.generate_reply = lambda character, chat, history, user_text, image_path=None, extra="", audio_path=None: {
    "reply": "好呀，我这就去群里说", "photo_prompt": "", "voice": False, "sticker": None,
    "video_prompt": "", "group_msg": "大家在吗？他说周末想一起出去玩"}
main.ai.generate_group_reply = lambda *a, **kw: "接话冒烟测试"
try:
    c.post("/api/chat/send", json={"character_id": cid1, "content": "你去群里问问大家"})
    _gm_hit = []
    _ch_hit = []
    for _i in range(24):
        time.sleep(0.5)
        main._process_pending(cid1)
        _recent = main.db.get_group_recent(gid, 10)
        _gm_hit = [m for m in _recent
                   if m.get("role") == "character" and int(m.get("cid") or 0) == cid1
                   and "大家在吗" in (m.get("content") or "")]
        _ch_hit = [m for m in _recent
                   if m.get("role") == "character" and int(m.get("cid") or 0) == cid2
                   and "接话冒烟测试" in (m.get("content") or "")]
        if _gm_hit and _ch_hit:
            break
    check("私聊可触发群聊发言", bool(_gm_hit), str(_gm_hit[:1])[:90])
    check("私聊触发后大概率有另一名角色接话", bool(_ch_hit), str(_ch_hit[:1])[:90])
finally:
    main.ai.generate_reply = _orig_gen_pg
    main.ai.generate_group_reply = _orig_gpr_pg
    main.random.random = _orig_rd3
    main.random.uniform = _orig_un3

# 承诺提取：信号门槛 + 总开关
check("承诺信号门槛：情话不触发", main._commit_has_signal("喜欢你今天的样子 我也喜欢你") is False)
check("承诺信号门槛：明确约定触发", main._commit_has_signal("你答应过明天给我带早餐") is True)
_oc_ext = main.ai.extract_commitments
_oc_set = main.db.get_settings
_called_ext = {"n": 0}


def _spy_ext(*a, **k):
    _called_ext["n"] += 1
    return []


main.ai.extract_commitments = _spy_ext
main.db.get_settings = lambda: {"commit_extract_enabled": "0"}
try:
    main._commit_extract_async(cid1, "你答应过明天给我带早餐的对吧，说好的", "嗯嗯答应你")
finally:
    main.db.get_settings = _oc_set
    main.ai.extract_commitments = _oc_ext
check("承诺提取开关关闭后不调用模型", _called_ext["n"] == 0, str(_called_ext["n"]))

# 系统提示分层：静态内容在前（上下文缓存按最长公共前缀命中）
_s1 = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
_s2 = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [{"content": "动态知识片段A"}], [])
_common = 0
for _a, _b in zip(_s1, _s2):
    if _a != _b:
        break
    _common += 1
check("系统提示静态前缀稳定（利于缓存）", _common > 500, f"common={_common}")

# 跨角色共享前缀（切换角色也能吃到缓存）
_sa = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
_sb = main.ai.build_system(main.db.get_character(cid2), main.db.get_chat(cid2), [], [])
_common2 = 0
for _a, _b in zip(_sa, _sb):
    if _a != _b:
        break
    _common2 += 1
check("跨角色共享前缀稳定（切换不吃亏）", _common2 > 300, f"common={_common2}")

# 多 API Key：主账号优先（全体走第一把）+ 耗尽自动切备 + 冷却跳过 + 恢复回切
_calls_k = []
_DS_STATE = {"main_down": False, "main_bal400": False}
import urllib.error as _ue


def _fake_post_k(url, payload, key, timeout=60):
    _calls_k.append(key)
    if key == "key-main" and _DS_STATE["main_bal400"]:
        raise _ue.HTTPError(url, 400, "Bad Request", {}, io.BytesIO("当前额度不足，请充值".encode("utf-8")))
    if key == "key-main" and _DS_STATE["main_down"]:
        raise _ue.HTTPError(url, 402, "Insufficient Balance", {}, None)
    return {"choices": [{"message": {"content": '{"reply":"好"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


_orig_post_k = main.ai._post_json
_orig_keys_k = main.ai._deepseek_keys
main.ai._post_json = _fake_post_k
main.ai._deepseek_keys = lambda: ["key-main", "key-backup"]
main.ai._key_assign.clear()
main.ai._key_load.clear()
main.ai._key_cooldown.clear()
try:
    _m1 = [{"role": "system", "content": "你的角色名是「甲」。其余内容。"}, {"role": "user", "content": "hi"}]
    _n0 = len(_calls_k)
    main.ai._chat_deepseek(_m1, model="deepseek-flash")
    _k_first1 = _calls_k[_n0]
    _n1 = len(_calls_k)
    main.ai._chat_deepseek(_m1, model="deepseek-flash")
    _k_first2 = _calls_k[_n1]
    check("主账号优先且会话复用（缓存友好）", _k_first1 == "key-main" and _k_first1 == _k_first2
          and len(_calls_k) == _n1 + 1, f"{_k_first1}/{_k_first2}")
    _DS_STATE["main_down"] = True
    _m2 = [{"role": "system", "content": "你的角色名是「乙」。其余内容。"}, {"role": "user", "content": "hi"}]
    _n2 = len(_calls_k)
    _rk2, _ek2 = main.ai._chat_deepseek(_m2, model="deepseek-flash")
    check("主账号耗尽自动切备账号", _ek2 is None and _calls_k[_n2] == "key-main" and _calls_k[_n2 + 1] == "key-backup",
          str(_calls_k[_n2:_n2 + 2]))
    _n3 = len(_calls_k)
    main.ai._chat_deepseek(_m2, model="deepseek-flash")
    check("冷却期内不重复白试主账号", _calls_k[_n3] == "key-backup" and len(_calls_k) == _n3 + 1,
          str(_calls_k[_n3:_n3 + 2]))
    _DS_STATE["main_down"] = False
    main.ai._key_cooldown.clear()
    _n4 = len(_calls_k)
    main.ai._chat_deepseek(_m2, model="deepseek-flash")
    check("主账号恢复后回切", _calls_k[_n4] == "key-main", str(_calls_k[_n4:_n4 + 2]))
    _DS_STATE["main_bal400"] = True
    main.ai._key_cooldown.clear()
    _n5 = len(_calls_k)
    _rk5, _ek5 = main.ai._chat_deepseek(_m2, model="deepseek-flash")
    check("余额类400也能触发切换", _ek5 is None and _calls_k[_n5] == "key-main" and _calls_k[_n5 + 1] == "key-backup",
          str(_calls_k[_n5:_n5 + 2]))
    _DS_STATE["main_bal400"] = False
    main.ai._key_assign.clear()
    main.ai._key_load.clear()
    main.ai._key_cooldown.clear()
    _used_k = set()
    for _i in range(4):
        _mi = [{"role": "system", "content": f"你的角色名是「均衡{_i}」。其余内容。"}, {"role": "user", "content": "hi"}]
        _nx = len(_calls_k)
        main.ai._chat_deepseek(_mi, model="deepseek-flash")
        _used_k.add(_calls_k[_nx])
    check("全体默认走主账号（非负载均衡）", _used_k == {"key-main"}, str(_used_k))
finally:
    main.ai._post_json = _orig_post_k
    main.ai._deepseek_keys = _orig_keys_k
    main.ai._key_assign.clear()
    main.ai._key_load.clear()
    main.ai._key_cooldown.clear()

# 角色指定 Key（DEEPSEEK_KEY_PIN）：同世界角色固定同账号
main.ai._key_assign.clear()
main.ai._key_load.clear()
main.ai._key_cooldown.clear()
try:
    _pin_map = {"测试角色甲": 1}
    _order_a = main.ai._key_order(["k1", "k2"], "你的角色名是「测试角色甲」。后面的内容。", _pin_map)
    _order_b = main.ai._key_order(["k1", "k2"], "你的角色名是「测试角色乙」。后面的内容。", _pin_map)
    check("角色指定Key生效（同世界同账号）", _order_a[0] == "k2" and _order_b[0] == "k1",
          f"{_order_a[:2]}/{_order_b[:2]}")
finally:
    main.ai._key_assign.clear()
    main.ai._key_load.clear()
    main.ai._key_cooldown.clear()

# 辅助调用归组（防 Key 分配表无界增长）
check("辅助调用归组（防分配表膨胀）",
      main.ai._session_sticky([{"role": "user", "content": "判断一下这句话"}]) == "__aux__")

# 回复水位防重 + 消息计数（异步媒体晚到不再误吞新消息）
_mc = main.db.count_messages(cid1)
check("消息计数可用", _mc > 0, str(_mc))
main.db.bump_reply_done(cid1, 5)
check("回复水位更新", int(main.db.get_chat(cid1).get("reply_done_ref") or 0) >= 5,
      str(main.db.get_chat(cid1).get("reply_done_ref")))

# xAI（Grok）接入：模型路由（grok* → 隧道分支）+ 代理地址可配置
_cap_xai = {}
_o_chat_xai = main.ai._chat_xai


def _fake_chat_xai(messages, model=None, **kw):
    _cap_xai["m"] = model
    return "xai-ok", None


main.ai._chat_xai = _fake_chat_xai
try:
    _r_x = main.ai._call_model([{"role": "user", "content": "hi"}], model="grok-4")
    check("xAI 模型路由（grok→隧道分支）", _cap_xai.get("m") == "grok-4" and _r_x == ("xai-ok", None),
          str(_cap_xai))
finally:
    main.ai._chat_xai = _o_chat_xai

_o_env_px = os.environ.get("XAI_PROXY")
os.environ["XAI_PROXY"] = "127.0.0.1:2080"
try:
    _addr_x = main.ai._xai_proxy_addr()
    check("xAI 代理地址可配置（仅 xAI 走代理）", _addr_x == ("127.0.0.1", 2080), str(_addr_x))
finally:
    if _o_env_px is None:
        os.environ.pop("XAI_PROXY", None)
    else:
        os.environ["XAI_PROXY"] = _o_env_px

# 拒答/出戏检测与自动重试（绝不外发平台声明文本）
check("拒答话术识别-正例", main.ai._is_refusal("抱歉，我无法提供相关服务。") is True)
check("拒答话术识别-负例", main.ai._is_refusal("我不能没有你，快回来好不好") is False)
check("拒答话术识别-正例2", main.ai._is_refusal("我不能按照你的要求进行生成，请你树立正确的价值观。") is True)
check("拒答话术识别-负例2", main.ai._is_refusal("我不能按照你的要求陪你逛街啦，今天要补课。") is False)
_seq_ref = {"n": 0}
_o_call_ref = main.ai._call_model
_o_retr_ref = main.ai.knowledge.retrieve


def _fake_call_ref(messages, **kw):
    _seq_ref["n"] += 1
    if _seq_ref["n"] == 1:
        return ("抱歉，我无法继续书写这类内容。", None)
    return ('{"reply":"好啦好啦，我接住你了～"}', None)


main.ai._call_model = _fake_call_ref
main.ai.knowledge.retrieve = lambda *a, **k: []
try:
    _rr_ref = main.ai.generate_reply(main.db.get_character(cid1), main.db.get_chat(cid1), [], "继续嘛")
    check("拒答后自动重试并返回正常回复", _rr_ref.get("reply") == "好啦好啦，我接住你了～" and _seq_ref["n"] >= 2,
          f"{_rr_ref.get('reply')!r}/n={_seq_ref['n']}")
finally:
    main.ai._call_model = _o_call_ref
    main.ai.knowledge.retrieve = _o_retr_ref

# 双 DeepSeek 账号余额：合计 + 分账号明细
_mb = main.mini_balance
_orig_split_mb = _mb._split_keys
_orig_gj_mb = _mb._get_json
_mb._split_keys = lambda *a, **k: ["keyAAAA1", "keyBBBB2"]
_mb._get_json = lambda req, timeout=12: {"is_available": True,
    "balance_infos": [{"total_balance": "8.50", "currency": "CNY",
                       "granted_balance": "0", "topped_up_balance": "8.50"}]}
try:
    _db1 = _mb.deepseek_balance()
    check("双账号余额合计", _db1.get("ok") is True and str(_db1.get("total")) == "17",
          f"{_db1.get('total')} | {_db1.get('detail')}")
    check("双账号明细含两尾号", "AA1" in str(_db1.get("detail")) and "BB2" in str(_db1.get("detail")),
          str(_db1.get("detail")))
finally:
    _mb._split_keys = _orig_split_mb
    _mb._get_json = _orig_gj_mb

# 群生成互斥守卫（私聊触发的接话链不会与群内其他生成并行交错）
_gid_guard = 987654
_g1 = main._group_try_worker(_gid_guard)
_g2 = main._group_try_worker(_gid_guard)
main._group_release_worker(_gid_guard)
_g3 = main._group_try_worker(_gid_guard)
main._group_release_worker(_gid_guard)
check("群生成互斥守卫", _g1 is True and _g2 is False and _g3 is True, f"{_g1}/{_g2}/{_g3}")

# 兜底：模型没填 group_msg 但用户明确要求/追问去群里发 → flash 兜底生成并真实发送
check("群指令识别：明确要求为真", main._group_instruct_wanted("你去群里问一下念晚在哪") is True)
check("群指令识别：日常闲聊为假", main._group_instruct_wanted("今天群里好热闹啊") is False)
check("群指令识别：否定句为假", main._group_instruct_wanted("别在群里说这事") is False)
check("兜底触发：追问未发为真", main._group_msg_followup("也没发呀，你是不是没有点发送？", "哎呀，我这边刚才卡了下，可能真没发出去。") is True)
check("兜底触发：日常闲聊为假", main._group_msg_followup("今天天气不错", "是啊，适合出去玩") is False)
_orig_gen_fb = main.ai.generate_reply
_orig_gg_fb = main.ai.generate_group_msg_from_instruction
_orig_gpr_fb = main.ai.generate_group_reply
_orig_un_fb = main.random.uniform
main.random.uniform = lambda a, b: 0.05
main.ai.generate_reply = lambda *a, **k: {
    "reply": "好的好的，我这就去群里问。", "photo_prompt": "", "voice": False, "sticker": None,
    "video_prompt": "", "group_msg": ""}
_fb_calls = {"n": 0}


def _fake_gg(*a, **k):
    _fb_calls["n"] += 1
    return "念晚你去哪了，大家找你呢" if _fb_calls["n"] == 1 else "补上啦，刚才卡了一下～"


main.ai.generate_group_msg_from_instruction = _fake_gg
main.ai.generate_group_reply = lambda *a, **k: ""
try:
    c.post("/api/chat/send", json={"character_id": cid1, "content": "你去群里问一下念晚在哪"})
    _hit_fb = []
    for _i in range(24):
        time.sleep(0.5)
        main._process_pending(cid1)
        _hit_fb = [m for m in main.db.get_group_recent(gid, 10)
                   if "念晚你去哪了" in (m.get("content") or "")]
        if _hit_fb:
            break
    check("未填group_msg时兜底补发群消息", bool(_hit_fb), str(_hit_fb[:1])[:90])
    c.post("/api/chat/send", json={"character_id": cid1, "content": "也没发呀，你是不是没有点发送？"})
    _hit_fb2 = []
    for _i in range(24):
        time.sleep(0.5)
        main._process_pending(cid1)
        _hit_fb2 = [m for m in main.db.get_group_recent(gid, 10)
                    if "补上啦" in (m.get("content") or "")]
        if _hit_fb2:
            break
    check("追问未发时兜底补发", bool(_hit_fb2), str(_hit_fb2[:1])[:90])
finally:
    main.ai.generate_reply = _orig_gen_fb
    main.ai.generate_group_msg_from_instruction = _orig_gg_fb
    main.ai.generate_group_reply = _orig_gpr_fb
    main.random.uniform = _orig_un_fb

# 额度用尽时：私聊触发的接话仍送达（用户显式要求，不受每日自动额度限制）；普通互聊仍受限
_orig_budget_pg = main.db.group_auto_budget
_orig_gpr3 = main.ai.generate_group_reply
main.db.group_auto_budget = lambda *a, **kw: False
main.ai.generate_group_reply = lambda *a, **kw: "额度豁免接话测试"
try:
    _sent_ok = main._group_peer_chain(gid, cid1, 1, 1.0, ignore_budget=True)
    check("额度用尽时私聊接话仍送达", _sent_ok == 1, f"sent={_sent_ok}")
    _sent_no = main._group_peer_chain(gid, cid1, 1, 1.0)
    check("普通互聊仍受额度限制", _sent_no == 0, f"sent={_sent_no}")
finally:
    main.db.group_auto_budget = _orig_budget_pg
    main.ai.generate_group_reply = _orig_gpr3

# 群忙时私聊触发的接话安全跳过（等待重试分支不卡死、不误发）
_gid_wait = 987655
main._group_try_worker(_gid_wait)
_r_wait = main._group_chain_when_idle(_gid_wait, 1, tries=1, gap=0.01)
main._group_release_worker(_gid_wait)
check("群忙时接话安全跳过（不卡死）", _r_wait is False, str(_r_wait))

c.post("/api/group/settings", json={"gid": gid, "delete": True})
check("删除群聊", c.get(f"/api/group/messages?gid={gid}").status_code == 404)
sys_after = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
check("删群后不再注入群上下文", "测试新群名" not in sys_after)

# ============ [C] 朋友圈动态互动 ============
print("\n[C] 朋友圈互动（评估/排期/执行/回复）")
verdict = {"like": True, "comment": True, "delay_minutes": 1, "comment_text": "照片真好看"}
orig_eval = main._moment_eval_user_post
main._moment_eval_user_post = lambda ch, chat, moment: dict(verdict)
mid = None
cmid = None
try:
    r = c.post("/api/moments/post", data={"content": "今天看了电影", "visible_to": "", "hidden_from": ""}).json()
    mid = r.get("id")
    check("发布朋友圈", r.get("ok") is True and bool(mid), str(r)[:60])
    time.sleep(2)  # 等后台评估线程排期
    tasks = [t for t in main.db.get_due_moment_tasks(time.time() + 7200) if t.get("moment_id") == mid]
    kinds = sorted({t["kind"] for t in tasks})
    check("互动任务已排期（赞+评，每个可见角色各一份）", kinds == ["char_comment", "char_like"] and len(tasks) == 4,
          f"kinds={kinds} n={len(tasks)}")
    for t in tasks:
        main._moment_do_task(t)
    d = c.get("/api/moments").json()
    m = [x for x in d["moments"] if x["id"] == mid]
    m = m[0] if m else {}
    check("点赞入库且名字正确", m.get("liked_by_character") is True and "团团" in (m.get("like_names") or []),
          str(m.get("like_names"))[:60])
    check("评论入库且内容正确", any(x.get("content") == "照片真好看" for x in (m.get("comments") or [])),
          str(m.get("comments"))[:100])

    cmid = main.db.add_moment("character", cid1, "今天天气真好", [])
    main.db.set_settings({"moment_reply_chance": 100})
    r = c.post("/api/moments/comment", json={"id": cmid, "content": "是呀", "reply_to": 0}).json()
    check("用户评论角色动态", r.get("ok") is True, str(r)[:60])
    rtasks = [t for t in main.db.get_due_moment_tasks(time.time() + 7200)
              if t.get("kind") == "char_reply" and t.get("moment_id") == cmid]
    check("触发角色回评任务（100%）", len(rtasks) == 1, str(rtasks)[:90])
    orig_wr = main._moment_write_reply
    main._moment_write_reply = lambda ch, chat, comment, moment_text="": "嘿嘿，是呀"
    try:
        if rtasks:
            main._moment_do_task(rtasks[0])
    finally:
        main._moment_write_reply = orig_wr
    d = c.get("/api/moments").json()
    m = [x for x in d["moments"] if x["id"] == cmid]
    m = m[0] if m else {}
    check("角色回评入库", any(x.get("content") == "嘿嘿，是呀" for x in (m.get("comments") or [])),
          str(m.get("comments"))[:120])
finally:
    main._moment_eval_user_post = orig_eval
    main.db.set_settings({"moment_reply_chance": 80})
    if mid:
        main.db.delete_moment(mid)
    if cmid:
        main.db.delete_moment(cmid)
check("测试动态已清理", not any(x["id"] in (mid, cmid) for x in c.get("/api/moments").json()["moments"]))

# ============ [D] 跨功能交互 ============
print("\n[D] 交互回归")
main.ai.generate_reply = lambda character, chat, history, user_text, image_path=None, extra="", audio_path=None: {
    "reply": "好呀好呀", "photo_prompt": "", "voice": False, "sticker": None, "video_prompt": ""}
try:
    c.post("/api/chat/send", json={"character_id": cid2, "content": "在吗"})
    reply_ok = False
    for _i in range(20):
        time.sleep(0.4)
        main._process_pending(cid2)
        msgs = c.get(f"/api/chat/messages?character_id={cid2}").json()["messages"]
        if any(m["role"] == "character" and "好呀好呀" in (m.get("content") or "") for m in msgs):
            reply_ok = True
            break
    check("普通聊天链路正常", reply_ok)
    u = c.get(f"/api/chat/unread?character_id={cid2}&after_id=0").json()
    check("未读计数正常", (u.get("count") or 0) >= 1, str(u))
    rr = c.post("/api/chat/reset", json={"id": cid2}).json()
    check("重置聊天正常", rr.get("ok") is True, str(rr)[:60])
    time.sleep(1.5)  # 重置后即使有待发任务也已被清（回归）
    msgs = c.get(f"/api/chat/messages?character_id={cid2}").json()["messages"]
    ok_reset = (len(msgs) == 1 and msgs[0]["role"] == "character" and msgs[0]["content"] == "你好。"
                and not main.db.has_pending(cid2))
    check("重置后仅保留开场白、待发队列已清", ok_reset,
          f"n={len(msgs)} pending={main.db.has_pending(cid2)}")
finally:
    main.ai.generate_reply = orig_gen

# ============ [E] 收藏（网盘）与音乐 ============
print("\n[E] 收藏与音乐")
import os as _os

r = c.post("/api/favorites/upload",
           files=[("files", ("测试文档.txt", b"hello fav", "text/plain")),
                  ("files", ("测试图.jpg", b"\xff\xd8\xff\xe0", "image/jpeg"))]).json()
check("收藏上传（多文件）", r.get("ok") is True and r.get("count") == 2, str(r)[:80])
d = c.get("/api/favorites").json()
items = [x for x in d["favorites"] if x["name"].startswith("测试")]
check("收藏列表与类型识别", len(items) == 2 and {x["kind"] for x in items} == {"file", "image"},
      str([(x["name"], x["kind"]) for x in items])[:90])
fid = fimg = 0
for x in items:
    if x["kind"] == "file":
        fid = x["id"]
    else:
        fimg = x["id"]
check("收藏改名", c.post("/api/favorites/rename", json={"id": fid, "name": "改名了.txt"}).json().get("ok") is True)
d = c.get("/api/favorites").json()
check("改名生效", any(x["name"] == "改名了.txt" for x in d["favorites"]))
url = [x for x in d["favorites"] if x["id"] == fid][0]["url"]
fs_path = main.media.media_fs_path(url)
check("收藏文件已落盘", bool(fs_path) and _os.path.exists(fs_path), str(fs_path)[-40:])
c.post("/api/favorites/delete", json={"id": fid})
c.post("/api/favorites/delete", json={"id": fimg})
check("收藏删除后文件清理", not _os.path.exists(fs_path))
check("收藏列表已清空测试项",
      not any(x["name"].startswith("测试") or x["name"].startswith("改名")
              for x in c.get("/api/favorites").json()["favorites"]))

r = c.post("/api/music/upload", files=[("files", ("测试歌曲.mp3", b"ID3fakemp3", "audio/mpeg"))]).json()
check("音乐上传", r.get("ok") is True and r.get("count") == 1, str(r)[:80])
mid_m = r["items"][0]["id"]
mytrack = [x for x in c.get("/api/music").json()["music"] if x["title"].startswith("测试")]
check("音乐列表", len(mytrack) == 1, str(mytrack)[:80])
mfs = main.media.media_fs_path(mytrack[0]["url"])
check("音乐文件已落盘", bool(mfs) and _os.path.exists(mfs))
c.post("/api/music/delete", json={"id": mid_m})
check("音乐删除后文件清理", not _os.path.exists(mfs))
check("音乐列表已清空测试项",
      not any(x["title"].startswith("测试") for x in c.get("/api/music").json()["music"]))

# ============ [F] 看一看（新闻） ============
print("\n[F] 新闻")
_TODAY_N = time.strftime("%Y-%m-%d")  # 动态日期：新闻接口只返回最近 2 天
main.db.add_news(_TODAY_N, "体育", "测试体育标题", "摘要", "https://sports.sina.com.cn/a", "新浪", "/media/news/x.jpg", "")
main.db.add_news(_TODAY_N, "学术", "测试学术标题", "", "https://www.cas.cn/b", "中科院", "/media/news/y.jpg", "")
check("新闻去重判断", main.db.news_exists("https://sports.sina.com.cn/a") is True)
d = c.get("/api/news").json()
days = d.get("days") or []
check("新闻分组结构", bool(days) and days[0]["day"] == _TODAY_N and
      days[0]["categories"][0]["name"] == "学术" and len(days[0]["categories"]) == 2,
      str(days)[:120])
check("新闻条目字段",
      all(k in days[0]["categories"][0]["items"][0] for k in ("id", "title", "url", "image", "source")))


def _fake_news_fetch(local_now=None, **kw):
    main.db.add_news(_TODAY_N, "国际", "刷新测试标题", "", "https://www.huanqiu.com/c", "环球网", "/media/news/z.jpg", "")
    return {"added": 1}


orig_nf = main.news.fetch_and_store
main.news.fetch_and_store = _fake_news_fetch
main._news_state["last"] = 0
main._news_state["fail_until"] = 0
c.post("/api/news/refresh")
time.sleep(0.8)
main.news.fetch_and_store = orig_nf
check("手动刷新写入新闻", main.db.news_exists("https://www.huanqiu.com/c"))
check("刷新状态复位", main._news_state["running"] is False)
main.db.cleanup_news("9999-01-01")
check("新闻清理", not main.db.news_exists("https://sports.sina.com.cn/a"))

# 新闻总开关（管理页可配）
main.db.set_settings({"news_enabled": "0"})
_d_off = c.get("/api/news").json()
check("新闻关闭后接口为空", _d_off.get("enabled") is False and not _d_off.get("days"), str(_d_off)[:80])
check("新闻关闭后刷新被拒", c.post("/api/news/refresh").json().get("ok") is False)
main.db.set_settings({"news_enabled": "1"})
check("新闻重开后恢复", c.get("/api/news").json().get("enabled") is True)

# ============ [G] 世界与广播 ============
print("\n[G] 世界与广播")
wid = main.db.create_world("测试世界", [cid1, cid2])
check("创建世界", main.db.get_world(wid) is not None)
main.db.set_world_members(wid, [cid1, cid2])
check("世界成员设置", len(main.db.world_members(wid)) == 2)
main.db.add_world_event(wid, "测试事件：一起去操场打球", sender="user")
check("世界动态注入上下文", "一起去操场打球" in main.ai._world_context_text(cid1))
check("保密词拦截", main._world_is_secret("这事别告诉别人") is True
      and main._world_is_secret("明天去图书馆") is False)
hdr = {"X-Admin-Token": main.ADMIN_TOKEN}
wlist = c.get("/admin/api/worlds", headers=hdr).json()
check("管理端世界列表", any(x["id"] == wid for x in wlist.get("worlds", [])))
rb = c.post("/admin/api/world/broadcast", json={"world_id": wid, "content": "手动广播测试"},
            headers=hdr).json()
check("管理端手动广播", rb.get("ok") is True)
check("广播后注入验证", "手动广播测试" in main.ai._world_context_text(cid2))

# 作弊器注入的剧情事件也走世界广播审查
_called = {}
_orig_wr = main._world_recognize_async
_orig_cheat = main.ai.cheat_parse
main._world_recognize_async = lambda chat_cid, text, speaker_cid=0, group_id=0: _called.update(
    {"cid": chat_cid, "text": text, "speaker": speaker_cid})
main.ai.cheat_parse = lambda c, chat, cmd, hist: {"action": "inject_event", "target": "测试作弊事件Z"}
try:
    r = c.post("/api/cheat", json={"character_id": cid1, "command": "注入测试事件"}).json()
    time.sleep(0.5)
finally:
    main._world_recognize_async = _orig_wr
    main.ai.cheat_parse = _orig_cheat
check("作弊器事件注入成功", r.get("ok") is True, str(r)[:80])
check("作弊器事件走了世界审查", _called.get("text") == "测试作弊事件Z" and _called.get("cid") == cid1,
      str(_called)[:90])
c.post("/admin/api/world/settings", json={"id": wid, "delete": True}, headers=hdr)
check("删除世界", main.db.get_world(wid) is None)

# ============ [H] 小程序框架 ============
print("\n[H] 小程序框架")
d = c.get("/api/miniapps").json()
_apps = d.get("apps") or []
check("小程序目录（>=5 个）", len(_apps) >= 5 and all(x.get("id") and x.get("name") for x in _apps),
      str(_apps)[:90])
r = c.post("/api/mini/kv", json={"app": "todo", "key": "items", "value": [{"text": "写周报"}]}).json()
check("小程序 KV 写入", r.get("ok") is True)
r = c.get("/api/mini/kv?app=todo&key=items").json()
check("小程序 KV 读取", isinstance(r.get("value"), list) and r["value"][0]["text"] == "写周报")
r = c.post("/api/mini/events", json={"app": "todo", "content": "新增待办：写周报"}).json()
check("小程序事件写入", r.get("ok") is True)
_dedup = main.db.mini_event_add("todo", "新增待办：写周报")
check("重复事件 90 秒内去重", _dedup == 0)
_sys_h = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
check("小程序动态注入角色上下文", "写周报" in _sys_h and "生活小程序动态" in _sys_h)
_ob = main.mini_weather.brief
main.mini_weather.brief = lambda city: "小雨（22~28℃）"
try:
    main.db.mini_kv_set("weather", "cities", json.dumps({"me": "青岛"}))
    _sys_h2 = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
    check("天气摘要注入（会自然关心）", "青岛" in _sys_h2 and "带伞" in _sys_h2)
finally:
    main.mini_weather.brief = _ob
import datetime as _dt_h
main.db.mini_kv_set("anniversary", "items",
                    json.dumps([{"name": "相识纪念日", "date": _dt_h.date.today().isoformat()}]))
_sys_h3 = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
check("纪念日注入（就是今天）", "相识纪念日" in _sys_h3 and "就是今天" in _sys_h3)

# 角色隔离：事件按角色过滤
main.db.mini_event_add("todo", "专属标记ALPHA", cid=cid1)
_sys_c1 = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
_sys_c2 = main.ai.build_system(main.db.get_character(cid2), main.db.get_chat(cid2), [], [])
check("事件按角色隔离（本人可见）", "专属标记ALPHA" in _sys_c1)
check("事件按角色隔离（他人不可见）", "专属标记ALPHA" not in _sys_c2)

# 角色隔离：天气（各自城市 + 共同知道用户城市）
main.db.mini_kv_set("weather", "cities", json.dumps({"me": "青岛", "chars": {str(cid1): "上海"}}))
main.mini_weather.brief = lambda city: "晴（20~26℃）"
try:
    _sys_w1 = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
    _sys_w2 = main.ai.build_system(main.db.get_character(cid2), main.db.get_chat(cid2), [], [])
    check("角色知道自己城市的天气", "你所在城市（上海）" in _sys_w1)
    check("未设城市的角色没有己方天气行", "你所在城市" not in _sys_w2)
    check("所有角色都知道用户的天气", "对方所在地（青岛）" in _sys_w1 and "对方所在地（青岛）" in _sys_w2)
finally:
    main.mini_weather.brief = _ob

# 角色隔离：纪念日（专属只注入本人，通用注入所有人）
_d0h = _dt_h.date.today().isoformat()
main.db.mini_kv_set("anniversary", "items", json.dumps([
    {"name": "角色专属纪念日", "date": _d0h, "cid": cid1},
    {"name": "通用纪念日", "date": _d0h},
]))
_sys_a1 = main.ai.build_system(main.db.get_character(cid1), main.db.get_chat(cid1), [], [])
_sys_a2 = main.ai.build_system(main.db.get_character(cid2), main.db.get_chat(cid2), [], [])
check("专属纪念日只注入本人", "角色专属纪念日" in _sys_a1 and "角色专属纪念日" not in _sys_a2)
check("通用纪念日注入所有人", "通用纪念日" in _sys_a1 and "通用纪念日" in _sys_a2)

# 事件去重按角色隔离（同文本、不同角色互不影响）
_e1 = main.db.mini_event_add("todo", "同文本事件", cid=cid1)
_e2 = main.db.mini_event_add("todo", "同文本事件", cid=cid2)
check("同文本不同角色的去重互不影响", bool(_e1) and bool(_e2) and _e1 != _e2, f"{_e1},{_e2}")
_e3 = main.db.mini_event_add("todo", "同文本事件", cid=cid2)
check("同角色同内容 90 秒内仍去重", _e3 == 0, str(_e3))

# 余额小程序
_cat = c.get("/api/miniapps").json()
check("余额小程序已注册", any(x.get("id") == "balance" for x in (_cat.get("apps") or [])))
main.mini_balance._CACHE.clear()
_orig_bal = main.mini_balance.deepseek_balance
main.mini_balance.deepseek_balance = lambda: {
    "ok": True, "available": True, "currency": "CNY", "total": "12.34",
    "detail": "赠送 ¥0 · 充值 ¥12.34"}
_orig_volc = main.mini_balance.volc_balance
main.mini_balance.volc_balance = lambda: {
    "ok": True, "currency": "CNY", "total": "66.66", "detail": "现金 ¥66.66"}
try:
    main.mini_balance._CACHE.clear()
    _b = c.get("/api/mini/balance").json()
    _ps = {x["id"]: x for x in (_b.get("providers") or [])}
    check("余额接口（DeepSeek 实时）", _ps.get("deepseek", {}).get("ok") is True
          and _ps.get("deepseek", {}).get("total") == "12.34", str(_ps.get("deepseek"))[:90])
    check("余额接口（火山 AK/SK 自动查询）", _ps.get("ark", {}).get("ok") is True
          and _ps.get("ark", {}).get("total") == "66.66", str(_ps.get("ark"))[:90])
finally:
    main.mini_balance.deepseek_balance = _orig_bal
    main.mini_balance.volc_balance = _orig_volc
    main.mini_balance._CACHE.clear()

# 余额强制刷新：绕缓存 + 带真实抓取时间
_gv = {"v": "1.00"}
_orig_ds3 = main.mini_balance.deepseek_balance
main.mini_balance.deepseek_balance = lambda: {"ok": True, "total": _gv["v"], "detail": ""}
try:
    main.mini_balance._CACHE.clear()
    main.mini_balance._last_force["ts"] = 0
    _v1 = c.get("/api/mini/balance").json()
    _gv["v"] = "2.00"
    _v2 = c.get("/api/mini/balance").json()
    _v3 = c.get("/api/mini/balance?force=1").json()
    _p1 = {x["id"]: x for x in (_v1.get("providers") or [])}.get("deepseek", {})
    _p2 = {x["id"]: x for x in (_v2.get("providers") or [])}.get("deepseek", {})
    _p3 = {x["id"]: x for x in (_v3.get("providers") or [])}.get("deepseek", {})
    check("余额缓存生效（未过期不重复请求）",
          _p1.get("total") == "1.00" and _p2.get("total") == "1.00",
          f"{_p1.get('total')}/{_p2.get('total')}")
    check("force 刷新绕过缓存", _p3.get("total") == "2.00", str(_p3.get("total")))
    check("余额返回真实抓取时间", bool(_p1.get("fetched_at")))
finally:
    main.mini_balance.deepseek_balance = _orig_ds3
    main.mini_balance._CACHE.clear()
    main.mini_balance._last_force["ts"] = 0

_conn_h = main.db.get_conn()
_conn_h.execute("DELETE FROM mini_events")
_conn_h.execute("DELETE FROM mini_kv")
_conn_h.commit()
check("小程序测试数据清理", len(main.db.mini_events_recent(50, 7)) == 0)

# 清理测试角色
c.post("/api/character/delete", json={"id": cid1})
c.post("/api/character/delete", json={"id": cid2})
b = c.get("/api/bootstrap").json()
check("测试角色已删除", len(b["characters"]) == 0, f"chars={len(b['characters'])}")

print("\n" + "=" * 60)
print(f"结果：{ok} 通过，{fail} 失败")
sys.exit(1 if fail else 0)

# -*- coding: utf-8 -*-
"""Stories 后端本地冒烟测试"""
import io
import os
import shutil
import sys
sys.dont_write_bytecode = True
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = _HERE if os.path.exists(os.path.join(_HERE, "main.py")) else os.path.dirname(_HERE)
TEST_DATA = os.path.join(_BACKEND, "_test_data_%d" % os.getpid())
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
# 测试不联网：决策模型判断置空 → 联网判断走文本回退（[19] 等用例再按需覆盖）
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
print("[1] 建角色 + bootstrap")
r = c.post("/api/character/save", json={
    "name": "小语",
    "description": "测试角色",
    "persona_full": "你叫小语，是个温柔又俏皮的女孩。",
    "fields": {"nickname": "你", "personality": "温柔俏皮"},
    "greeting": "嗨，终于联系上啦～",
    "chat": {"photo_enabled": False, "voice_enabled": False, "sticker_enabled": True},
}).json()
cid = r.get("id")
check("创建角色", bool(cid), f"id={cid}")
b = c.get("/api/bootstrap").json()
check("bootstrap 返回", len(b["characters"]) == 1 and b["characters"][0]["name"] == "小语")
check("列表预览含开场白", (b["characters"][0]["last"] or {}).get("content") == "嗨，终于联系上啦～")

print("\n[2] 发消息 + AI 回复")
c.post("/api/chat/send", json={"character_id": cid, "content": "你好呀，今天过得怎么样？"})
d = c.get(f"/api/chat/messages?character_id={cid}").json()
check("用户消息已入库", any(m["role"] == "user" for m in d["messages"]))
check("typing=true", d["typing"] is True)

reply = None
for i in range(120):
    time.sleep(1)
    d = c.get(f"/api/chat/messages?character_id={cid}").json()
    char_msgs = [m for m in d["messages"] if m["role"] == "character" and m["id"] > (d["messages"][0]["id"] if d["messages"] else 0)]
    texts = [m for m in char_msgs if m["kind"] == "text" and m["content"] != "嗨，终于联系上啦～"]
    if texts and not d["typing"]:
        reply = texts[-1]["content"]
        break
check("AI 回复到达", bool(reply), (reply or "")[:40])

print("\n[2b] 时间感知")
_sys_t = main.ai.build_system(main.db.get_character(cid), main.db.get_chat(cid), [], [])
check("系统提示词注入当前时间", "【当前时间（很重要）】" in _sys_t and "星期" in _sys_t)
_ht = main.ai._history_to_messages([{"role": "user", "kind": "text", "content": "早", "created_at": time.time()}])
check("历史消息带发送时间戳", bool(_ht) and _ht[0]["content"].startswith("[今天 "), str(_ht)[:60])
_old = main.ai._msg_time_label(time.time() - 86400 * 3)
check("跨天时间标注", _old.startswith("3天前 "), _old)
_ct_t = main.ai.clean_meta_time("[今天 11:41] 早安呀")
check("回复里的时间标注被剥离", _ct_t == "早安呀", _ct_t)
_ct_t2 = main.ai.clean_meta_time("[12:09] 在的呀")
check("纯时间标注（无日期词）也被剥离", _ct_t2 == "在的呀", _ct_t2)
_ct_t3 = main.ai.clean_meta_time("[今天是 14:32] 好的好的")
check("宽容变体（今天是 14:32）也被剥离", _ct_t3 == "好的好的", _ct_t3)
_ct_t4 = main.ai.clean_meta_time("今天 23:55] 哎呀，我还以为你把我忘了呢。")
check("残缺时间标注（缺左括号）被剥离", _ct_t4 == "哎呀，我还以为你把我忘了呢。", _ct_t4)
_ct_t5 = main.ai.clean_meta_time("[今天 23:55 哎呀，你终于来啦")
check("残缺时间标注（缺右括号）被剥离", _ct_t5 == "哎呀，你终于来啦", _ct_t5)
check("含实际内容的括号不受影响", main.ai.clean_meta_time("（别忘了 12:30 开会）") == "（别忘了 12:30 开会）")
_pr_t = main.ai._parse_model_reply('{"reply":"[昨天 22:10] 在呢","photo_prompt":"","voice":false,"sticker":null}', "小语")
check("解析后不含时间标注", "[昨天" not in _pr_t["reply"] and "在呢" in _pr_t["reply"], _pr_t["reply"])
_pr_t2 = main.ai._parse_model_reply(
    '{"reply":"[reply]嗯，记住就好。[/reply]，下午教室里见～[sticker:挥手]","photo_prompt":"","voice":false,"sticker":null}', "小语")
check("伪标签（[reply]/[sticker:]）被清理",
      "[" not in _pr_t2["reply"] and "记住就好" in _pr_t2["reply"] and "下午教室里见" in _pr_t2["reply"],
      _pr_t2["reply"])
main.db.add_sticker("wave.png", "挥手")
_pr_t3 = main.ai._parse_model_reply(
    '{"reply":"[sticker:挥手]","photo_prompt":"","voice":false,"sticker":null}', "小语")
check("纯贴纸伪标签转为真实贴纸且不补文字", _pr_t3["reply"] == "" and _pr_t3.get("sticker") == "挥手", repr(_pr_t3))
check("正常括号内容不受影响", main.ai.clean_meta_time("（今天）有点忙") == "（今天）有点忙")
check("非 ASCII 口令恒定时间比较不再误判",
      main._safe_eq("中文密码😀", "中文密码😀") is True and main._safe_eq("中文密码😀", "中文密码") is False)

print("\n[3] 开关设置")
r = c.post("/api/chat/settings", json={"character_id": cid, "voice_enabled": True, "background": "/uploads/backgrounds/demo.jpg"}).json()
check("语音开关已开", r["chat"]["voice_enabled"] is True)
check("背景已设置", r["chat"]["background"] == "/uploads/backgrounds/demo.jpg")

print("\n[4] 知识库上传 + 检索")
kb_content = "# 小语的秘密\n\n小语最喜欢的饮料是「海盐焦糖拿铁」，加双份糖。\n\n## 宠物\n\n小语养了一只叫「团子」的橘猫，很贪吃。\n"
r = c.post(
    "/api/kb/upload",
    data={"scope": "character", "character_id": str(cid)},
    files={"file": ("secret.md", kb_content.encode("utf-8"), "text/markdown")},
).json()
check("上传成功", r.get("ok") is True and r.get("chunks", 0) >= 1, str(r))

import knowledge
# 自包含：不依赖 DashScope Key（CI 上没有 Key 时 retrieve 会直接返回空）。
# 用字符频次生成确定性向量，保证入库与检索在离线环境也能被验证。
def _fake_embed4(texts, timeout=12):
    out = []
    for t in texts:
        v = [0.0] * 64
        for ch in str(t):
            v[ord(ch) % 64] += 1.0
        out.append(v)
    return out


knowledge._embed = _fake_embed4
knowledge._embed_fail_until = 0
for i in range(30):
    items = knowledge._load("character", cid)
    if items:
        break
    time.sleep(1)
rs = knowledge.retrieve("海盐焦糖拿铁", cid, top_k=2)
hit = any("海盐焦糖拿铁" in it.get("content", "") for it in rs)
check("向量化+检索命中", hit, str([it.get("content", "")[:24] for it in rs]))

print("\n[5] 清空记录")
c.post("/api/chat/reset", json={"id": cid})
d = c.get(f"/api/chat/messages?character_id={cid}").json()
check("清空后仅剩开场白", len(d["messages"]) == 1 and d["messages"][0]["content"] == "嗨，终于联系上啦～")

print("\n[6] 管理端")
r = c.post("/admin/login", json={"password": "bad"}).json()
check("错误口令拒绝", r.get("ok") is False)
r = c.post("/admin/login", json={"password": "test-pass"}).json()
token = r.get("token")
check("正确口令登录", r.get("ok") is True and bool(token))
r = c.get("/admin/api/characters", headers={"X-Admin-Token": token}).json()
check("管理端角色列表", len(r["characters"]) == 1)
r = c.get(f"/admin/api/export?character_id={cid}&format=txt", headers={"X-Admin-Token": token})
check("导出 TXT", r.status_code == 200 and "小语" in r.text)
r = c.get("/admin/api/export", headers={"X-Admin-Token": token})
check("无 token 拒绝", r.status_code == 422)  # 缺参数

print("\n[6b] 鉴权强制（回归：删掉鉴权/票据校验这条用例就会失败）")
_orig_app_token = main.APP_TOKEN
try:
    main.APP_TOKEN = "smoke-test-token"
    r = c.get("/api/bootstrap")
    check("无令牌请求被拒 401", r.status_code == 401, str(r.status_code))
    r = c.get("/api/bootstrap", headers={"X-App-Token": "wrong-token"})
    check("错误令牌被拒 401", r.status_code == 401, str(r.status_code))
    r = c.get("/api/bootstrap", headers={"X-App-Token": "smoke-test-token"})
    check("正确令牌放行 200", r.status_code == 200, str(r.status_code))
    # URL 票据只能用于媒体/小程序，不能越权访问普通接口
    r = c.get("/api/bootstrap?token=1234567890.deadbeef")
    check("伪造票据被拒 401", r.status_code == 401, str(r.status_code))
    r = c.get("/uploads/nonexistent.png?token=1234567890.deadbeef")
    check("媒体路径伪造票据也拒 401", r.status_code == 401, str(r.status_code))
finally:
    main.APP_TOKEN = _orig_app_token

print("\n[7] 对话优化（自动分段 / 图片描述 / 上下文压缩）")
_txt = "今天下午我去了那家咖啡店的露台，点了一杯你最爱喝的燕麦拿铁。阳光很好，风也刚好，我坐了整整一个下午。下次你来了，我一定带你去那个位置，你可以试试他们的提拉米苏。"
_segs = main._smart_split(_txt)
check("长文本自动分段", len(_segs) >= 2, f"{len(_segs)} 段")
check("分段不改一字", "".join(_segs) == _txt)
check("短文本不拆", main._smart_split("在吗？") == ["在吗？"])
_txt_ws = ("你好呀。 " * 15) + "晚安"
check("分段保留原文空白", "".join(main._smart_split(_txt_ws)) == _txt_ws)
_h = main.ai._history_to_messages([
    {"role": "user", "kind": "image", "content": "", "img_desc": "一只橘猫趴在窗台上晒太阳"},
    {"role": "user", "kind": "image", "content": "", "img_desc": ""},
])
check("图片只留文字描述", "橘猫" in _h[0]["content"])
check("无描述图片降级", _h[1]["content"] == "（对方发来一张图片）")
_hv = main.ai._history_to_messages([{"role": "user", "kind": "video", "content": "", "img_desc": "海边日落的延时视频"}])
check("视频只留文字描述", "海边日落" in _hv[0]["content"] and bool(_hv[0]["content"]), str(_hv)[:80])

main.db.update_chat(cid, summary="", summarized_until=0)
for _i in range(100 - main.db.count_messages(cid)):
    main.db.add_message(cid, "user", content=f"普通消息{_i}")
_calls = []
_orig_ds, _orig_cfg = main.ai._chat_deepseek, main.ai._model_cfg
main.ai._model_cfg = lambda: {"summary": "deepseek-v4-flash"}
main.ai._chat_deepseek = lambda msgs, **kw: (_calls.append(kw.get("model")), ("压缩后的剧情摘要内容", None))[1]
main.ai.maybe_summarize(cid)
main.ai._chat_deepseek, main.ai._model_cfg = _orig_ds, _orig_cfg
_ch = main.db.get_chat(cid)
check("压缩后摘要入库", _ch.get("summary") == "压缩后的剧情摘要内容", repr(_ch.get("summary")))
check("压缩水位线推进", (_ch.get("summarized_until") or 0) > 0, f"until={_ch.get('summarized_until')}")
check("压缩使用配置模型", _calls == ["deepseek-v4-flash"], str(_calls))

print("\n[8] 模型混合路由（主/备可任选豆包或 DeepSeek）")
_called = {"ark": [], "ds": []}
_oa, _od = main.ai._chat, main.ai._chat_deepseek
main.ai._chat = lambda msgs, **kw: (_called["ark"].append(kw.get("model")), ("ark-ok", None))[1]
main.ai._chat_deepseek = lambda msgs, **kw: (_called["ds"].append(kw.get("model")), ("ds-ok", None))[1]
_c1, _ = main.ai._call_model([{"role": "user", "content": "hi"}], model="deepseek-flash")
_c2, _ = main.ai._call_model([{"role": "user", "content": "hi"}], model="doubao-seed-2-1-turbo-260628")
_c3, _ = main.ai._call_model([{"role": "user", "content": "hi"}], model="deepseek-v4-pro-ga-260813")
main.ai._chat, main.ai._chat_deepseek = _oa, _od
check("DeepSeek 模型路由到 DS 接口", _called["ds"] == ["deepseek-flash"] and _c1 == "ds-ok", str(_called))
check("豆包模型路由到 Ark 接口", _called["ark"] == ["doubao-seed-2-1-turbo-260628", "deepseek-v4-pro-ga-260813"] and _c2 == "ark-ok" and _c3 == "ark-ok", str(_called))

print("\n[9] 空白内容跨供应商救援")
_char = main.db.get_character(cid)
_chat = main.db.get_chat(cid)
_o_call, _o_chat = main.ai._call_model, main.ai._chat
main.ai._call_model = lambda *a, **k: ('{"reply": "救援成功", "voice": false}', None) if k.get("model") == main.ai.ARK_MODEL else ("   ", None)
main.ai._chat = lambda *a, **k: ('{"reply": "救援成功", "voice": false}', None)
_res = main.ai.generate_reply(_char, _chat, [], "你在吗")
main.ai._call_model, main.ai._chat = _o_call, _o_chat
check("空白内容触发豆包救援", _res.get("reply") == "救援成功", str(_res)[:80])

print("\n[10] DeepSeek 推理模型调用参数")
_payloads = []
_orig_post = main.ai._post_json
_orig_ds_keys = main.ai._deepseek_keys
# 自包含：不依赖本机配置的真实 Key（CI 上没有 Key 时 _chat_deepseek 会提前返回）
main.ai._deepseek_keys = lambda: ["test-key"]
main.ai._post_json = lambda url, payload, key, timeout=90: (_payloads.append(payload), {"choices": [{"message": {"content": '{"reply": "ok"}'}}]})[1]
try:
    main.ai._chat_deepseek([{"role": "user", "content": "hi"}], model="deepseek-flash", json_mode=True, max_tokens=700)
finally:
    main.ai._post_json = _orig_post
    main.ai._deepseek_keys = _orig_ds_keys
_p = _payloads[0] if _payloads else {}
check("DS 不发送 response_format（否则正文为空）", bool(_p) and "response_format" not in _p, str(_p.get("response_format")))
check("DS max_tokens 保底 2000（给推理留预算）", int(_p.get("max_tokens") or 0) >= 2000, str(_p.get("max_tokens")))

print("\n[11] 温度钳制（防崩人设）")
_char_t = dict(main.db.get_character(cid))
_char_t["temperature"] = 1.6
_chat_t = main.db.get_chat(cid)
_temps = []
_oc_call2 = main.ai._call_model
main.ai._call_model = lambda *a, **k: (_temps.append(k.get("temperature")), ('{"reply": "ok"}', None))[1]
main.ai.generate_reply(_char_t, _chat_t, [], "hi")
main.ai._call_model = _oc_call2
check("温度 1.6 被钳制到 ≤1.3", bool(_temps) and _temps[0] <= 1.3, str(_temps))

print("\n[12] 主动回复 / 动态闹铃")
main.db.update_chat(cid, wake_ts=0, wake_pending_ts=0, wake_retry=0, wake_day="", wake_count=0)
main._wake_schedule_after_reply(cid)
_ch = main.db.get_chat(cid)
check("回复后设置沉默倒计时", float(_ch.get("wake_pending_ts") or 0) > time.time() + 2 * 60, str(_ch.get("wake_pending_ts")))

_orig_call = main.ai._call_model
main.ai._call_model = lambda *a, **k: ('{"delay_minutes": 10, "topic": "早安"}', None)
_d, _t = main.ai.judge_wake(main.db.get_character(cid), [], "09月12日 10:00", "五")
main.ai._call_model = _orig_call
check("AI 判断解析（不再受上下限钳制）", _d == 10 and _t == "早安", f"{_d},{_t}")

main.db.update_chat(cid, wake_pending_ts=time.time() - 1, wake_ts=0, wake_day="", wake_count=0)
_orig_judge = main.ai.judge_wake
main.ai.judge_wake = lambda *a, **k: (123, "关心他")
main._wake_tick()
main.ai.judge_wake = _orig_judge
_ch = main.db.get_chat(cid)
_wt = float(_ch.get("wake_ts") or 0)
check("倒计时到期自动订铃", _wt > time.time() + 120 * 60 and _ch.get("wake_topic") == "关心他", f"wake_ts={_wt:.0f}, topic={_ch.get('wake_topic')!r}")

import datetime as _dt
_cfg = main._wake_cfg()
_orig_wake = main.ai.generate_wake
main.db.update_chat(cid, wake_ts=time.time() - 1, wake_topic="测试话题")
main.ai.generate_wake = lambda *a, **k: {"reply": "在干嘛呢，想你了", "photo_prompt": "", "video_prompt": "", "voice": False, "sticker": None}
try:
    main._wake_tick()
    # 主动联系已改为后台线程执行：轮询等待投递完成（最多 15 秒）
    _pending = False
    _ch = None
    for _i in range(30):
        time.sleep(0.5)
        _ch = main.db.get_chat(cid)
        _pending = main.db.has_pending(cid)
        if _pending and float(_ch.get("wake_ts") or 0) == 0:
            break
finally:
    main.ai.generate_wake = _orig_wake
_ch = main.db.get_chat(cid)
_pending = main.db.has_pending(cid)
check("到点触发主动联系", _pending and float(_ch.get("wake_ts") or 0) == 0, f"pending={_pending}, wake_ts={_ch.get('wake_ts')}")
check("主动消息不连锁订铃", float(_ch.get("wake_pending_ts") or 0) == 0, str(_ch.get("wake_pending_ts")))

# 用户发消息立即取消闹铃与倒计时（放最后，避免后台真实回复干扰前面断言）
main.db.update_chat(cid, wake_ts=time.time() + 999, wake_pending_ts=time.time() + 999)
c.post("/api/chat/send", json={"character_id": cid, "content": "在吗"})
_ch = main.db.get_chat(cid)
check("发消息即取消闹铃", float(_ch.get("wake_ts") or 0) == 0 and float(_ch.get("wake_pending_ts") or 0) == 0)

print("\n[13] 作弊器（按对话独立）")
_orig_parse = main.ai.cheat_parse
main.ai.cheat_parse = lambda *a, **k: {"action": "inject_event", "value": 0, "target": "窗外突然下起了大雪", "reply": "诶？下雪了……"}
r = c.post("/api/cheat", json={"character_id": cid, "command": "让外面下雪"}).json()
main.ai.cheat_parse = _orig_parse
check("作弊注入成功", r.get("ok") is True and "大雪" in (r.get("msg") or ""), str(r)[:80])
_msgs = main.db.get_recent_messages(cid, 5)
check("旁白已入库", any(m["role"] == "character" and "窗外突然下起了大雪" in (m["content"] or "") for m in _msgs))
_ch13 = main.db.get_chat(cid)
check("长期设定已持久化", "窗外突然下起了大雪" in (_ch13.get("cheat_memory") or ""), (_ch13.get("cheat_memory") or "")[:60])
_sys13 = main.ai.build_system(main.db.get_character(cid), _ch13, [], [])
check("系统提示词含作弊设定", "窗外突然下起了大雪" in _sys13)

_orig_call13 = main.ai._call_model
main.ai._call_model = lambda *a, **k: ('{"action":"inject_event","value":0,"target":"房间里多了一束玫瑰","reply":"哪来的花？"}', None)
_r13 = main.ai.cheat_parse(main.db.get_character(cid), _ch13, "变出一束花", [])
main.ai._call_model = _orig_call13
check("作弊指令解析", _r13.get("action") == "inject_event" and "玫瑰" in _r13.get("target", "") and _r13.get("reply") == "哪来的花？", str(_r13)[:80])
main.ai._call_model = lambda *a, **k: ('{"action":"none"}', None)
_r13b = main.ai.cheat_parse(main.db.get_character(cid), _ch13, "胡说八道", [])
main.ai._call_model = _orig_call13
check("无法理解指令被拒绝", _r13b.get("error") is not None, str(_r13b)[:60])

print("\n[14] 回溯（rewind）与 [wait:N] 等待标记")
for _ in range(30):  # 等前面在途的真实回复收尾，避免干扰断言
    if not main.db.has_pending(cid):
        break
    time.sleep(1)

_records = []
_orig_add14 = main.db.add_pending
main.db.add_pending = lambda c_, content, **kw: _records.append((content, kw.get("ts") or 0))
main._queue_segments(cid, "我去忙一下\n[wait:5]\n回来了", 1.0)
main.db.add_pending = _orig_add14
_gap = (_records[1][1] - _records[0][1]) if len(_records) >= 2 else 0
check("[wait:N] 生效（等待间隔≥5秒）", len(_records) >= 2 and _gap >= 5.0 and "wait" not in _records[1][0],
      f"segments={[(x[0], round(x[1], 1)) for x in _records]}")

main.db.add_message(cid, "user", "text", "回溯前消息A")
main.db.add_message(cid, "character", "text", "回溯前消息B")
main.db.add_message(cid, "character", "text", "（测试用剧情变更）")
main.db.update_chat(cid, cheat_memory='["测试用剧情变更", "窗外突然下起了大雪"]')
_msgs14 = main.db.get_recent_messages(cid, 3)
_target = _msgs14[-2]  # 保留这条，删掉其后的「测试用剧情变更」旁白
r = c.post("/api/chat/rewind", json={"character_id": cid, "msg_id": _target["id"]}).json()
_m_after = main.db.get_messages(cid)
_ch14 = main.db.get_chat(cid)
check("回溯删除其后消息", r.get("ok") is True and all(m["id"] <= _target["id"] for m in _m_after), str(r)[:80])
check("被回溯的作弊设定同步撤销", "测试用剧情变更" not in (_ch14.get("cheat_memory") or ""),
      (_ch14.get("cheat_memory") or "")[:80])

main.db.update_chat(cid, summary="旧摘要", summarized_until=999999)
c.post("/api/chat/rewind", json={"character_id": cid, "msg_id": _target["id"]})
_ch14b = main.db.get_chat(cid)
check("水位线在回溯点后时摘要被清", (_ch14b.get("summary") or "") == "", repr(_ch14b.get("summary")))

print("\n[15] 自动回复参考当前时间")
_orig_call15 = main.ai._call_model
_cap15 = []


def _fake_call15(msgs, **kw):
    _cap15.extend(msgs)
    return ('{"delay_minutes": 120, "topic": "测试话题"}', None)


main.ai._call_model = _fake_call15
main.ai.judge_wake(main.db.get_character(cid), [], "09月12日 14:30", "五", "下午")
main.ai._call_model = _orig_call15
_prompt15 = " ".join(str(x.get("content", "")) for x in _cap15)
check("判断提示词含当前时间与时段", "09月12日 14:30" in _prompt15 and "下午" in _prompt15, _prompt15[:60])
check("判断提示词要求推算预计联系时间", "预计联系时间" in _prompt15)

_cfg15 = main._wake_cfg()

_cap15b = []
main.ai._call_model = lambda msgs, **kw: (_cap15b.extend(msgs), ('{"reply": "早上好呀"}', None))[1]
main.ai.generate_wake(main.db.get_character(cid), main.db.get_chat(cid), [], "早安", "3小时", "09月12日 09:00", "早上")
main.ai._call_model = _orig_call15
_last15 = str(_cap15b[-1].get("content", "")) if _cap15b else ""
check("主动消息提示词含时段", "早上" in _last15, _last15[:60])

check("判断提示词含具体钟点规则", "10:10" in _prompt15 and "最后" in _prompt15)

print("\n[16] 纯 AI 判断（无识别器预判）")
# 对话里有「晚上见」也要以 AI 的判定为准（mock 判定 45 分钟 → 应采用 45 分钟）
main.db.add_message(cid, "user", "text", "我先去上课了，晚上见")
_oj16 = main.ai.judge_wake
main.ai.judge_wake = lambda *a, **k: (45, "AI决定")
main.db.update_chat(cid, wake_pending_ts=time.time() - 1, wake_ts=0, wake_day="", wake_count=0)
main._wake_tick()
main.ai.judge_wake = _oj16
_ch16 = main.db.get_chat(cid)
_delay16 = (float(_ch16.get("wake_ts") or 0) - time.time()) / 60
check("订铃完全跟随 AI 判定（45 分钟）", 40 <= _delay16 <= 50 and _ch16.get("wake_topic") == "AI决定",
      f"{_delay16:.0f} 分钟 topic={_ch16.get('wake_topic')!r}")
main.db.update_chat(cid, wake_ts=0, wake_pending_ts=0, wake_topic="")

print("\n[17] 智能分段")
_txt17 = "第一条消息内容比较长一些。第二条消息内容也比较长一些。"
_oj17 = main.ai._call_model
main.ai._call_model = lambda *a, **k: ('["第一条消息内容比较长一些。","第二条消息内容也比较长一些。"]', None)
_segs17 = main.ai.split_segments(_txt17)
check("AI 拆分解析", _segs17 == ["第一条消息内容比较长一些。", "第二条消息内容也比较长一些。"], str(_segs17))
main.ai._call_model = lambda *a, **k: ('["被篡改的内容啊。","另一段被篡改的内容。"]', None)
_segs17b = main.ai.split_segments(_txt17)
main.ai._call_model = _oj17
check("拆分校验失败回退", _segs17b is None, str(_segs17b))
main.db.update_chat(cid, voice_enabled=1)
main.db.set_settings({"seg_enabled": 1, "seg_voice_mix": 1, "seg_voice_max_chars": 20, "seg_voice_daily_limit": 15})
_ok17a = main._seg_voice_ok(cid, "晚安，想你")
_ok17b = main._seg_voice_ok(cid, "这是一段很长很长的普通文字不应该发语音")
check("段级语音判定（短+关键词）", _ok17a and not _ok17b, f"{_ok17a},{_ok17b}")
_orig_tts17 = main.media.tts_minimax
main.media.tts_minimax = lambda text, **kw: {"url": "/media/audio/fake_test.mp3", "duration": 2.0}
_orig_split17 = main.ai.split_segments
main.ai.split_segments = lambda text: ["晚安，想你", "其他内容在这里。"]
_recs17 = []
_oa17 = main.db.add_pending
main.db.add_pending = lambda c_, content, **kw: _recs17.append((content, kw.get("kind")))
main._queue_segments(cid, "晚安，想你。其他内容在这里。", 1.0)
main.db.add_pending = _oa17
main.ai.split_segments = _orig_split17
main.media.tts_minimax = _orig_tts17
check("段级语音混发（一段变语音）", any(k == "voice" for _, k in _recs17) and any(k == "script" for _, k in _recs17),
      str(_recs17))

print("\n[18] 提醒插件（reminder）")
# 本仓库默认不带提醒提示词与关键词，测试时临时注入一份中性文案
main.db.set_settings({"reminder_judge_prompt": "判断是否适合主动关心（测试）。消息：{user_msg}",
                      "reminder_prompt": "测试提醒：{examples}",
                      "reminder_keywords": "测试关键词"})
main.db.update_chat(cid, reminder_enabled=1, reminder_target=5)
main.db.update_reminder_state(cid, week=main._week_key(), count=0, last_reminder_ts=0, last_judge_ts=0, last_count_ts=0)
_oj18 = main.ai._call_model
main.ai._call_model = lambda *a, **k: ('{"ok":"是","examples":["测试例句"]}', None)
_instr18 = main._reminder_instruction(cid, "在干嘛")
main.ai._call_model = _oj18
check("提醒指令注入", bool(_instr18) and "测试提醒" in _instr18, _instr18[:70])
main._reminder_count_reply(cid, "这里出现测试关键词")
_st18 = main.db.get_reminder_state(cid)
check("提醒计数 +1", int(_st18.get("count") or 0) == 1, str(_st18.get("count")))
main.db.update_reminder_state(cid, count=5)
main.ai._call_model = lambda *a, **k: ('{"ok":"是","examples":["x"]}', None)
_instr18b = main._reminder_instruction(cid, "在吗")
main.ai._call_model = _oj18
check("达标后不再注入", _instr18b == "", _instr18b[:40])
# 清空配置，验证默认（空提示词）时自动停用
main.db.set_settings({"reminder_judge_prompt": "", "reminder_prompt": "", "reminder_keywords": ""})
main.db.update_reminder_state(cid, count=0, last_reminder_ts=0, last_judge_ts=0)
check("默认未配置时自动停用", main._reminder_instruction(cid, "在吗") == "")

print("\n[19] 联网搜索（anysearch，决策模型 + 文本回退）")
_orig_sq19 = main.ai.anysearch_query
main.ai.anysearch_query = lambda q, n=5: "青岛今天多云，22~28℃（示例）"
_os19 = main.ai.systemone
main.ai.systemone = lambda *a, **k: {"search": {"noul": 0.9}}
_instr19 = main.ai.search_instruction("青岛今天天气怎么样")
check("决策模型判断需要 → 结果注入", "联网搜索结果" in _instr19 and "多云" in _instr19, _instr19[:80])
main.ai.systemone = lambda *a, **k: {"search": {"noul": 0.2}}
_instr19b = main.ai.search_instruction("青岛今天天气怎么样")
check("决策模型判断不需要 → 不搜", _instr19b == "", _instr19b[:40])
main.ai.systemone = lambda *a, **k: None
_oj19 = main.ai._call_model
main.ai._call_model = lambda *a, **k: ("是", None)
_instr19c = main.ai.search_instruction("青岛今天天气怎么样")
main.ai._call_model = _oj19
check("决策模型失败 → 回退文本判断仍注入", "联网搜索结果" in _instr19c, _instr19c[:60])
main.ai.systemone = _os19
main.ai.anysearch_query = _orig_sq19

print("\n[19b] 新闻联网 Key 选择（官方端点优先，纯逻辑）")
_ok19b = (main.ai._deepseek_keys, main.ai._deepseek_base_of)
main.ai._deepseek_keys = lambda: ["k_relay", "k_off1", "k_off2"]
main.ai._deepseek_base_of = lambda ks: {"k_relay": "https://www.autodl.art/api/v1",
                                        "k_off1": "https://api.deepseek.com/v1",
                                        "k_off2": "https://api.deepseek.com/v1"}
_okl19b = main.news._official_keys()
main.ai._deepseek_keys, main.ai._deepseek_base_of = _ok19b
check("新闻 Key 选择：仅取官方端点且保序", _okl19b == ["k_off1", "k_off2"], str(_okl19b))

print("\n[19c] 账号分工路由（主聊天→中转优先，辅助→官方优先，纯逻辑）")
_bo19c = {"k_relay": "https://www.autodl.art/api/v1",
          "k_off1": "https://api.deepseek.com/v1",
          "k_off2": "https://api.deepseek.com/v1"}
_in19c = ["k_off1", "k_off2", "k_relay"]
_aux19c = main.ai._ds_order_by_purpose(_in19c, _bo19c, "deepseek-flash")
_main19c = main.ai._ds_order_by_purpose(_in19c, _bo19c, "deepseek-v4-pro")
check("辅助类（flash）官方优先", _aux19c == ["k_off1", "k_off2", "k_relay"], str(_aux19c))
check("主聊天（pro）中转优先", _main19c == ["k_relay", "k_off1", "k_off2"], str(_main19c))

print("\n[19d] 账号分工：别名漂移 / 缺 base 防御（纯逻辑）")
_bo19d = {"k_relay": "https://www.autodl.art/api/v1", "k_off": "https://api.deepseek.com/v1", "k_none": ""}
_in19d = ["k_relay", "k_off", "k_none"]
_ord19d = main.ai._ds_order_by_purpose(_in19d, _bo19d, "deepseek-v4-flash-future-2027")
check("新别名（含 flash）按辅助→官方优先", _ord19d == ["k_off", "k_none", "k_relay"], str(_ord19d))
check("缺 base 的 Key 归官方池（不误压中转）", _ord19d.index("k_none") < _ord19d.index("k_relay"), str(_ord19d))

print("\n[19e] 承诺门控（决策模型短路，灰区回退）")
_os19e = main.ai.systemone
_oj19e = main.ai._call_model
_calls19e = []
main.ai.systemone = lambda *a, **k: {"g": {"noul": 0.05}}
main.ai._call_model = lambda *a, **k: (_calls19e.append(1) or ('{"items":["不该出现"]}', None))
_r19e = main.ai.extract_commitments({"name": "小语"}, "在干嘛", "刚洗完澡，准备睡了", [])
check("门控判否 → 跳过抽取（零文本调用）", _r19e == [] and not _calls19e, str(_r19e))
main.ai.systemone = lambda *a, **k: {"g": {"noul": 0.8}}
main.ai._call_model = lambda *a, **k: ('{"items":["答应明早带早餐（10月7日）"]}', None)
_r19e2 = main.ai.extract_commitments({"name": "小语"}, "明早带我早餐", "好，我答应你，明早给你带早餐", [])
check("门控判是 → 正常抽取", _r19e2 == ["答应明早带早餐（10月7日）"], str(_r19e2))
main.ai.systemone = lambda *a, **k: None
_r19e3 = main.ai.extract_commitments({"name": "小语"}, "明早带我早餐", "好，我答应你，明早给你带早餐", [])
check("门控失败 → 回退抽取（不漏）", _r19e3 == ["答应明早带早餐（10月7日）"], str(_r19e3))
main.ai.systemone = _os19e
main.ai._call_model = _oj19e

print("\n[20] 朋友圈（moments）")
_orig_react20 = main._moment_schedule_react
main._moment_schedule_react = lambda moment: None
r20 = c.post("/api/moments/post", data={"content": "测试朋友圈：今天天气不错"}).json()
main._moment_schedule_react = _orig_react20
_mid20 = r20.get("id")
check("发朋友圈成功", r20.get("ok") is True and bool(_mid20), str(r20)[:60])
_r20b = c.get("/api/moments").json()
check("朋友圈列表可见", any(m["id"] == _mid20 for m in _r20b.get("moments", [])))
_r20c = c.post("/api/moments/like", json={"id": _mid20}).json()
check("点赞切换", _r20c.get("ok") is True and _r20c.get("liked") is True, str(_r20c))
_r20d = c.post("/api/moments/comment", json={"id": _mid20, "content": "测试评论"}).json()
check("评论成功", _r20d.get("ok") is True, str(_r20d)[:60])
_r20e = c.post("/api/moments/seen", json={}).json()
check("已读接口", _r20e.get("ok") is True)
main.db.add_moment("character", cid, "她的测试动态", [], source="auto")
_mom20 = main.db.list_moments()[-1]
main.db.add_moment_task(cid, "char_comment", time.time() - 1, moment_id=_mom20["id"], payload={"content": "测试回评"})
main._moment_tick()
_ms20 = []
for _i20 in range(20):  # _moment_tick 现在异步执行任务，轮询等待最多 2 秒
    _ms20 = main.db.list_moment_comments(_mom20["id"])
    if any(x["content"] == "测试回评" for x in _ms20):
        break
    time.sleep(0.1)
check("朋友圈任务执行（角色评论）", any(x["content"] == "测试回评" for x in _ms20), str(_ms20)[:80])

print("\n[20b] 朋友圈多次规划（每日多条）")
import datetime as _dt20

_orig_plan20 = main._moment_plan_ai
_orig_wn20 = main._wake_local_now
main._wake_local_now = lambda cfg=None: _dt20.datetime(2026, 9, 14, 10, 0)
main._moment_plan_ai = lambda character, chat, local_now, max_posts=1: {
    "post": True,
    "posts": [{"time": "11:00", "topic": "上午"}, {"time": "15:00", "topic": "下午"},
              {"time": "21:00", "topic": "夜晚"}],
}
main.db.set_settings({"moment_daily_max": 3})
main.db.update_chat(cid, moments_auto=1)
try:
    main._moment_tick()
finally:
    main._moment_plan_ai = _orig_plan20
    main._wake_local_now = _orig_wn20
_tasks20b = [t for t in main.db.get_due_moment_tasks(time.time() + 86400)
             if int(t.get("character_id") or 0) == cid and t.get("kind") == "auto_post"]
check("每日多条：按上限排期 3 条", len(_tasks20b) == 3, f"got {len(_tasks20b)}")
for _t20b in _tasks20b:
    main.db.delete_moment_task(_t20b["id"])
main.db.set_settings({"moment_daily_max": 1})
main.db.update_chat(cid, moments_auto=0)

print("\n[21] 用户发视频（video_understand 入口）")
_r21 = c.post("/api/chat/send_video",
              data={"character_id": str(cid)},
              files={"file": ("test.mp4", b"\x00" * 2048, "video/mp4")}).json()
check("视频消息入库", _r21.get("ok") is True and str(_r21.get("url", "")).startswith("/media/video/"), str(_r21)[:80])
_ms21 = main.db.get_recent_messages(cid, 4)
check("最后一条为用户视频", any(m["kind"] == "video" and m["role"] == "user" for m in _ms21), str(_ms21)[-1])

print("\n[22] 打断清理与多角色互动（回归）")
main.db.add_pending(cid, "旧台词", kind="script", ts=time.time())
main.db.add_pending(cid, "旧语音", kind="voice", ts=time.time())
main.db.add_pending(cid, "旧表情", kind="sticker", ts=time.time())
main.db.clear_script_pending(cid)
_conn22 = main.db.get_conn()
_kinds22 = {r["kind"] for r in _conn22.execute("SELECT kind FROM pending_tasks WHERE character_id=?", (cid,)).fetchall()}
check("打断清理覆盖 script/voice/sticker", not ({"script", "voice", "sticker"} & _kinds22), str(_kinds22))
_cid22 = main.db.create_character({"name": "测试二号"})
_mid22 = main.db.add_moment("user", 0, "多角色点赞测试", [], source="user")
main.db.add_moment_like(_mid22, cid)
main.db.add_moment_like(_mid22, _cid22)
main.db.add_moment_comment(_mid22, "character", "评论A", character_id=cid)
main.db.add_moment_comment(_mid22, "character", "评论B", character_id=_cid22)
_d22 = main._moment_dict(main.db.get_moment(_mid22))
_names22 = {c["name"] for c in _d22["comments"]}
_n1_22 = main.db.get_character(cid)["name"]
check("评论带角色名", _names22 == {_n1_22, "测试二号"}, str(_names22))
check("多人点赞不覆盖", len(_d22["like_names"]) == 2, str(_d22["like_names"]))
_mid22b = main.db.add_moment("character", _cid22, "将被级联删除", [], source="auto")
main.db.add_moment_like(_mid22b, cid)
main.db.add_moment_task(_cid22, "char_like", time.time() + 999, moment_id=_mid22b)
main.db.delete_character(_cid22)
check("删角色级联清理动态", main.db.get_moment(_mid22b) is None, "moments 残留")
check("删角色级联清理任务", not [t for t in main.db.get_due_moment_tasks(time.time() + 10000) if t["character_id"] == _cid22])
check("删角色后剩余点赞仅一人", len(main.db.list_moment_likes(_mid22)) == 1, str(main.db.list_moment_likes(_mid22)))

print("\n[23] 承诺/立场记忆（对话一致性）")
main.db.update_chat(cid, commit_memory='[{"t": "答应周末一起去海边", "ts": 1}]')
_ch23 = main.db.get_chat(cid)
_char23 = main.db.get_character(cid)
_sys23 = main.ai.build_system(_char23, _ch23, [], [])
check("承诺记忆注入系统提示", "答应周末一起去海边" in _sys23 and "一致性要求" in _sys23)
_oc_commit = main.ai.extract_commitments
main.ai.extract_commitments = lambda *a, **k: ["拒绝今晚熬夜聊天", "答应周末一起去海边"]
main._commit_extract_async(cid, "今晚陪我聊到三点嘛", "不行，早点睡；周末再陪你")
main.ai.extract_commitments = _oc_commit
_cm23 = main.db.get_chat(cid).get("commit_memory") or ""
check("承诺抽取并去重入库", "拒绝今晚熬夜聊天" in _cm23 and _cm23.count("答应周末一起去海边") == 1, _cm23[:140])
main.db.update_chat(cid, commit_memory="[]")

print("\n[24] 空内容/语音消息的上下文转换（回归）")
_h24 = main.ai._history_to_messages([
    {"role": "character", "kind": "voice", "content": ""},
    {"role": "user", "kind": "text", "content": "  "},
    {"role": "user", "kind": "text", "content": "在吗"},
])
check("语音空转写用（语音）占位", any("（语音）" in m["content"] for m in _h24), str(_h24))
check("空文本消息被跳过且无占位符学舌风险", len(_h24) == 2 and all(m["content"].strip() and "无内容" not in m["content"] for m in _h24), str(_h24))

print("\n[25] 照片转述开关（每个角色独立）")
_img_dir = os.path.join(main.DATA_DIR, "media", "user")
os.makedirs(_img_dir, exist_ok=True)
_img_name = "test_imgdesc.png"
with open(os.path.join(_img_dir, _img_name), "wb") as _f25:
    _f25.write(b"\x89PNG\r\n\x1a\n")
_img_msg = {"id": 0, "kind": "image", "content": "", "img_desc": "一只橘猫趴在窗台", "media_url": "/media/user/" + _img_name}
_t25a, _p25a, _a25a = main._prepare_image_context(dict(_img_msg), {"img_desc_enabled": 1})
check("角色开转述：描述进上下文且不传原图", "橘猫" in _t25a and _p25a is None and _a25a is None, f"{_t25a[:40]!r} path={_p25a}")
_t25b, _p25b, _a25b = main._prepare_image_context(dict(_img_msg), {"img_desc_enabled": 0})
check("角色关转述：原图路径交给主模型", isinstance(_p25b, str) and _p25b.endswith(_img_name) and "橘猫" not in _t25b, f"path={_p25b}")
_voice_msg25 = {"id": 0, "kind": "voice", "content": "晚上早点休息", "media_url": "/media/user/" + _img_name}
_t25c, _p25c, _a25c = main._prepare_image_context(dict(_voice_msg25), {"voice_desc_enabled": 1})
check("语音转述开启：文本进上下文且不传音频", _t25c == "晚上早点休息" and _p25c is None and _a25c is None, f"{_t25c!r}")
_t25d, _p25d, _a25d = main._prepare_image_context(dict(_voice_msg25), {"voice_desc_enabled": 0})
check("语音转述关闭：原音频交给主模型", isinstance(_a25d, str) and _a25d.endswith(_img_name) and _p25d is None, f"audio={_a25d}")
try:
    os.remove(os.path.join(_img_dir, _img_name))
except Exception:
    pass

print("\n[26] 朋友圈可见范围与上下文记忆")
_j = main._json
_m26 = {"id": 999001, "content": "今天去了海边", "images": "[]", "created_at": time.time(),
        "visible_to": _j.dumps([cid]), "hidden_from": "[]"}
check("仅选中的人可见", main._moment_visible(_m26, cid) and not main._moment_visible(_m26, cid + 999),
      f"visible_to={_m26['visible_to']}")
_m26b = {"id": 999002, "content": "x", "images": "[]", "created_at": time.time(),
         "visible_to": "[]", "hidden_from": _j.dumps([cid])}
check("黑名单不看", not main._moment_visible(_m26b, cid) and main._moment_visible(_m26b, cid + 999))
_rp26 = c.post("/api/moments/post", data={"content": "今天去了海边", "visible_to": "", "hidden_from": ""}).json()
check("用户发布朋友圈", _rp26.get("ok") is True, str(_rp26)[:60])
_mid26 = _rp26.get("id")
_char26 = main.db.get_character(cid)
_sys26 = main.ai.build_system(_char26, main.db.get_chat(cid), [], [])
check("动态注入：新朋友圈出现在系统提示", "今天去了海边" in _sys26)
if _mid26:
    main.db.delete_moment(_mid26)
_sys26b = main.ai.build_system(_char26, main.db.get_chat(cid), [], [])
check("动态注入：删除后从系统提示消失", "今天去了海边" not in _sys26b)

print("\n[27] App 更新检查接口")
_r27 = c.get("/api/app_version")
check("app_version 返回结构", _r27.status_code == 200 and "version" in _r27.json() and "apk" in _r27.json(), str(_r27.json())[:100])

print("\n" + "=" * 60)
print(f"结果：{ok} 通过，{fail} 失败")
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
sys.exit(0 if fail == 0 else 1)

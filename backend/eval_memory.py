# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""记忆检索评测：命中率 / 延迟基线（改检索参数前后对比用）

用法：
    python eval_memory.py --gen 30            # 从已入库记忆里自动生成 30 条评测样例
    python eval_memory.py                     # 跑评测（默认用例文件 eval/memory_cases.json）
    python eval_memory.py --topn 5            # 临时改召回条数对比
    python eval_memory.py --verbose           # 打印每条明细

样例格式（可手工补充）：
    {"cid": 13, "query": "那家咖啡店", "expect_any": ["咖啡"], "note": "手工补充"}
"""
import argparse
import io
import json
import os
import random
import sys
import time

sys.dont_write_bytecode = True
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import db  # noqa: E402
import memory_kb  # noqa: E402

EVAL_DIR = os.path.join(os.environ.get("STORIES_DATA_DIR", os.path.join(_HERE, "data")), "eval")
CASES = os.path.join(EVAL_DIR, "memory_cases.json")


def gen_cases(n: int, seed: int = 0):
    """自检索样例：拿已入库记忆的片段当查询，期望能召回它自己所在的那条记忆"""
    db.init_db()
    rnd = random.Random(seed)
    scopes = []
    try:
        for c in db.list_characters(include_disabled=True):
            scopes.append((int(c["id"]), 0, memory_kb.scope_of_char(int(c["id"])), c.get("name") or ""))
        for g in db.list_groups():
            scopes.append((0, int(g["id"]), memory_kb.scope_of_group(int(g["id"])), g.get("name") or ""))
    except Exception:
        pass
    pool = []
    for cid, gid, scope, name in scopes:
        for it in memory_kb._load(scope)["items"]:
            txt = str(it.get("content") or "").strip()
            if len(txt) < 10 or int(it.get("dormant") or 0):
                continue
            pool.append((cid, gid, name, txt, int(it.get("mid") or 0)))
    if not pool:
        print("!! 还没有已入库的记忆（先跑 backfill_memory.py）")
        return []
    rnd.shuffle(pool)
    cases = []
    for cid, gid, name, txt, mid in pool[:n]:
        # 取片段作为查询：去掉首尾语气，保留有信息量的 8~16 字
        q = txt[2:18] if len(txt) > 20 else txt[:12]
        # 期望关键词：取片段里最长的连续中文片段（去掉标点）
        import re
        frags = [f for f in re.split(r"[，。！？、,.!?~\s]+", txt) if len(f) >= 4]
        expect = [max(frags, key=len)[:12]] if frags else [txt[:8]]
        cases.append({"cid": cid, "gid": gid, "query": q, "expect_any": expect,
                      "mid": mid, "note": "auto(%s)" % name})
    os.makedirs(EVAL_DIR, exist_ok=True)
    with io.open(CASES, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(cases, ensure_ascii=False, indent=1))
    print("已生成 %d 条评测样例 → %s" % (len(cases), CASES))
    return cases


def run(args):
    if not os.path.isfile(CASES):
        print("!! 找不到用例文件：%s（先跑 --gen）" % CASES)
        return 1
    cases = json.loads(io.open(CASES, "r", encoding="utf-8").read())
    if args.topn:
        # 临时覆盖召回条数
        _orig = memory_kb._cfg

        def _cfg_patched():
            c = _orig()
            c["topn"] = args.topn
            return c

        memory_kb._cfg = _cfg_patched
    hit = 0
    lat = []
    rows = []
    for i, cs in enumerate(cases, 1):
        cid = int(cs.get("cid") or 0)
        gid = int(cs.get("gid") or 0)
        q = str(cs.get("query") or "")
        expects = [str(x) for x in (cs.get("expect_any") or [])]
        t0 = time.time()
        res = memory_kb.retrieve(cid, q, [], [gid] if gid else [])
        dt = time.time() - t0
        lat.append(dt)
        got = " ｜ ".join(str(it.get("content") or "")[:60] for it in (res.get("items") or []))
        ok = any(e and e in got for e in expects)
        hit += 1 if ok else 0
        rows.append((i, ok, q, expects, got, res.get("latency_ms") or int(dt * 1000)))
        if args.verbose:
            print("[%02d] %s q=%s 期望~%s\n     召回: %s" % (i, "命中" if ok else "未中", q, expects, got))
        time.sleep(args.pause)
    n = len(cases)
    lat_sorted = sorted(lat)
    p50 = lat_sorted[n // 2] if n else 0
    print("\n" + "=" * 60)
    print("评测结果：%d/%d 命中（命中率 %.1f%%）" % (hit, n, (hit * 100.0 / n) if n else 0))
    print("延迟：平均 %.2fs，P50 %.2fs，最大 %.2fs" % (
        (sum(lat) / n) if n else 0, p50, max(lat) if lat else 0))
    if not args.verbose:
        miss = [r for r in rows if not r[1]]
        if miss:
            print("\n未命中样例（最多 8 条）：")
            for i, _ok, q, exp, got, ms in miss[:8]:
                print("  [%02d] q=%s 期望~%s\n       召回=%s" % (i, q, exp, got or "（空）"))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--topn", type=int, default=0)
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if args.gen:
        gen_cases(args.gen, args.seed)
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

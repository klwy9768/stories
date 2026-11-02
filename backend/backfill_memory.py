# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""全量回填：把历史消息按记忆库管道索引进向量库（可中断、可续跑、可分批）。

用法（在 backend 目录下）：
    python backfill_memory.py                 # 全部角色 + 全部群
    python backfill_memory.py --cid 13        # 只回填某个角色
    python backfill_memory.py --group 3       # 只回填某个群
    python backfill_memory.py --batch 200     # 每轮取多少条（默认 300）
    python backfill_memory.py --limit 1000    # 每个范围最多处理多少条（0=不限）
    python backfill_memory.py --rebuild       # 清空水位，从头重建（会重新计算，已入库的不重复嵌入）
    python backfill_memory.py --dry           # 只统计，不调用模型

特性：
- 依靠 mem_index 水位续跑：中断后重新执行会接着上次的位置
- 已入库内容会按 mid 去重；重复执行安全
- 每日上限自动绕过（respect_daily=False）
"""
import argparse
import io
import os
import sys
import time

sys.dont_write_bytecode = True
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import db  # noqa: E402
import memory_kb  # noqa: E402


def _fmt(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 60:
        return "%ds" % sec
    if sec < 3600:
        return "%dm%02ds" % (sec // 60, sec % 60)
    return "%dh%02dm" % (sec // 3600, (sec % 3600) // 60)


def _total(scope: str, cid: int, gid: int) -> int:
    conn = db.get_conn()
    try:
        if gid:
            r = conn.execute("SELECT COUNT(*) AS c FROM group_messages WHERE group_id=?", (gid,)).fetchone()
        else:
            r = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE character_id=?", (cid,)).fetchone()
        return int(r["c"] or 0)
    except Exception:
        return 0


def run_scope(label: str, scope: str, cid: int, gid: int, args) -> dict:
    total = _total(scope, cid, gid)
    done = db.mem_index_get(scope)
    print("\n=== %s ===" % label)
    print("  消息总数 %d，当前水位 %d" % (total, done))
    if args.dry:
        return {"scanned": 0, "added": 0, "dropped": 0, "skipped": 0}

    agg = {"scanned": 0, "added": 0, "dropped": 0, "skipped": 0}
    t0 = time.time()
    loops = 0
    while True:
        loops += 1
        st = memory_kb.index_scope(scope, cid=cid, gid=gid, is_group=bool(gid),
                                   max_batch=args.batch, respect_daily=False)
        for k in agg:
            agg[k] += int(st.get(k) or 0)
        if st.get("scanned", 0) == 0:
            break
        if args.limit and agg["scanned"] >= args.limit:
            print("  达到 --limit %d，本轮停止（下次继续）" % args.limit)
            break
        el = time.time() - t0
        rate = agg["scanned"] / el if el > 0 else 0
        left = max(0, total - done - agg["scanned"])
        eta = left / rate if rate > 0 else 0
        print("  [%d] 已扫描 %d / 新增 %d / 丢弃 %d / 跳过 %d  用时 %s  预计剩余 %s"
              % (loops, agg["scanned"], agg["added"], agg["dropped"], agg["skipped"],
                 _fmt(el), _fmt(eta)), flush=True)
        if loops > 20000:
            print("  循环上限保护，停止")
            break
        time.sleep(args.pause)

    print("  ✅ 完成：扫描 %d，新增 %d，判定丢弃 %d，预过滤跳过 %d，用时 %s"
          % (agg["scanned"], agg["added"], agg["dropped"], agg["skipped"], _fmt(time.time() - t0)))
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=300)
    ap.add_argument("--pause", type=float, default=0.2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cid", type=int, default=0)
    ap.add_argument("--group", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    db.init_db()  # 确保新表（mem_index / recall_log）存在
    chars = [c for c in db.list_characters(include_disabled=True)]
    groups = db.list_groups()
    if args.cid:
        chars = [c for c in chars if int(c["id"]) == args.cid]
    if args.group:
        groups = [g for g in groups if int(g["id"]) == args.group]

    if args.rebuild:
        for c in chars:
            db.mem_index_set(memory_kb.scope_of_char(int(c["id"])), 0)
        for g in groups:
            db.mem_index_set(memory_kb.scope_of_group(int(g["id"])), 0)
        print("已重置水位（向量库保留，已入库内容不会重复嵌入）")

    print("记忆库回填开始：角色 %d 个，群 %d 个（batch=%d）" % (len(chars), len(groups), args.batch))
    tot = {"scanned": 0, "added": 0, "dropped": 0, "skipped": 0}
    t0 = time.time()
    for c in chars:
        cid = int(c["id"])
        r = run_scope("角色 %s（#%d）" % (c.get("name") or "", cid),
                      memory_kb.scope_of_char(cid), cid, 0, args)
        for k in tot:
            tot[k] += r.get(k, 0)
    for g in groups:
        gid = int(g["id"])
        r = run_scope("群 %s（#%d）" % (g.get("name") or "", gid),
                      memory_kb.scope_of_group(gid), 0, gid, args)
        for k in tot:
            tot[k] += r.get(k, 0)

    print("\n" + "=" * 60)
    print("回填总览：扫描 %d，新增 %d，判定丢弃 %d，预过滤跳过 %d，总用时 %s"
          % (tot["scanned"], tot["added"], tot["dropped"], tot["skipped"], _fmt(time.time() - t0)))
    print("向量库规模：")
    for name, s in sorted(memory_kb.stats().items()):
        print("  %-16s %8.2f MB" % (name, s.get("mb", 0)))


if __name__ == "__main__":
    main()

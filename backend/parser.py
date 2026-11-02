# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""Stories 文档解析：md/txt → 文本 → 分块（标题优先，512 字/50 重叠）"""
import re

CHUNK_SIZE = 512
CHUNK_OVERLAP = 50


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def parse_document(filename: str, data: bytes) -> str:
    """解析 md/txt，返回纯文本（保留标题结构）"""
    text = decode_text(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_long(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    """长文本按段落/句子切分，超长强制切片"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    pieces = []
    # 先按空行分段
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    buf = ""
    for p in paras:
        # 单段超长：按句子拆
        if len(p) > size:
            if buf:
                pieces.append(buf)
                buf = ""
            sentences = re.split(r"(?<=[。！？!?；;])", p)
            sbuf = ""
            for s in sentences:
                if not s:
                    continue
                if len(sbuf) + len(s) > size and sbuf:
                    pieces.append(sbuf)
                    sbuf = sbuf[-overlap:] if overlap else ""
                sbuf += s
            if sbuf:
                pieces.append(sbuf)
            continue
        if len(buf) + len(p) + 2 > size and buf:
            pieces.append(buf)
            buf = buf[-overlap:] if overlap else ""
        buf += p + "\n"
    if buf.strip():
        pieces.append(buf.strip())
    # 兜底：仍然超长的硬切
    out = []
    for p in pieces:
        if len(p) <= size:
            out.append(p.strip())
        else:
            step = max(1, size - overlap)
            for i in range(0, len(p), step):
                seg = p[i:i + size].strip()
                if seg:
                    out.append(seg)
                if i + size >= len(p):
                    break
    return [p for p in out if p]


def chunk_document(text: str):
    """按 Markdown 标题优先分块，返回 [{heading, content}]"""
    text = text.strip()
    if not text:
        return []

    # 按标题切段（# ~ ######）
    sections = []
    cur_heading = ""
    cur_lines = []
    for line in text.split("\n"):
        m = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
        if m:
            if cur_lines and "".join(cur_lines).strip():
                sections.append((cur_heading, "\n".join(cur_lines).strip()))
            cur_heading = m.group(2).strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_lines and "".join(cur_lines).strip():
        sections.append((cur_heading, "\n".join(cur_lines).strip()))

    if not sections:
        return [{"heading": "", "content": c} for c in _split_long(text)]

    chunks = []
    for heading, body in sections:
        if not body:
            if heading:
                chunks.append({"heading": heading, "content": heading})
            continue
        for piece in _split_long(body):
            chunks.append({"heading": heading, "content": piece})
    return chunks

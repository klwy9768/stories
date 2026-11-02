# -*- coding: utf-8 -*-
# Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
# SPDX-License-Identifier: MIT
"""每日新闻：DeepSeek 联网搜索（Anthropic 兼容端点）→ 抓页面元信息（配图/摘要）→ 入库

设计要点：
- 搜索走 DeepSeek 服务端 web_search，能拿到结构化结果（标题 + URL）
- 域名过滤：白名单权威媒体优先，聚合号/广告站直接排除（去广告）
- 配图：抓取文章页 og:image 并下载到本地（/media/news/），外链失效也不怕
- 去重：同一 URL 只入库一次；旧的（>7 天）自动清理
"""
import html as _html
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request

import db
import media
import logger as _logger

try:
    from logger import _log  # type: ignore
except Exception:
    def _log(msg, level="INFO"):
        try:
            _logger.log(msg, level=level)
        except Exception:
            pass


DATA_DIR = os.environ.get("STORIES_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
NEWS_DIR = os.path.join(DATA_DIR, "media", "news")
NEWS_VIDEO_DIR = os.path.join(NEWS_DIR, "videos")

CATEGORIES = [
    ("学术", "中国高校、科研院所与科技企业的最新科研突破（如《自然》《科学》论文、中科院成果、科技日报报道）"),
    ("体育", "体育赛事、比赛结果、运动会与体育界动态"),
    ("军事", "国防军事动态、军队、装备与演习新闻"),
    ("政治", "中国时政要闻、政策发布、重要会议与领导人活动"),
    ("国际", "国际局势、外交关系、海外重大事件"),
]

# 权威媒体优先（命中靠前排序）；同时作为可信来源白名单
PREFER_DOMAINS = (
    "xinhuanet.com", "news.cn", "people.com.cn", "cctv.com", "news.cctv.com",
    "chinanews.com.cn", "thepaper.cn", "huanqiu.com", "guancha.cn", "caixin.com",
    "jiemian.com", "bjnews.com.cn", "yicai.com", "21jingji.com", "stcn.com",
    "cls.cn", "cnr.cn", "gmw.cn", "china.com.cn", "eastday.com", "ynet.com",
    "takefoto.cn", "sina.com.cn", "news.sina.com.cn", "sports.sina.com.cn",
    "163.com", "news.163.com", "sports.163.com", "qq.com", "news.qq.com",
    "sports.qq.com", "ifeng.com", "news.ifeng.com", "dongqiudi.com", "zhibo8.com",
    "xinhuanet", "cgtn.com", "globaltimes.cn", "chinadaily.com.cn", "stdaily.com",
    "cas.cn", "nsfc.gov.cn", "moe.gov.cn", "sports.gov.cn", "mod.gov.cn",
    "cankaoxiaoxi.com", "cri.cn", "81.cn", "wenge.gov.cn", "sciencenet.cn",
)

# 官方中文媒体的英文版：允许英文标题（如 China Daily、CGTN、Global Times 等）
EN_OK_DOMAINS = (
    "chinadaily.com.cn", "cgtn.com", "globaltimes.cn", "xinhuanet.com", "news.cn",
    "people.com.cn", "china.org.cn", "cri.cn", "ecns.cn", "chinaplus.cri.cn",
)

# 聚合号 / 自媒体 / 疑似广告与低质内容站点（直接排除）
BLOCK_DOMAINS = (
    "baijiahao.baidu.com", "mbd.baidu.com", "word.baidu.com", "zhidao.baidu.com",
    "toutiao.com", "www.toutiao.com", "zhihu.com", "weibo.com", "weibo.cn",
    "douyin.com", "kuaishou.com", "bilibili.com", "xiaohongshu.com", "xhs.cn",
    "csdn.net", "jianshu.com", "taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
    "58.com", "ganji.com", "zhaopin.com", "lagou.com", "douban.com", "tieba.baidu.com",
    "mp.weixin.qq.com", "sohu.com", "souhu.com", "sogou.com", "hao123.com", "wenku.baidu.com",
    "docin.com", "doc88.com", "chinaz.com", "oschina.net", "51cto.com", "cnblogs.com",
)

SOURCE_NAMES = (
    ("xinhuanet.com", "新华社"), ("news.cn", "新华社"), ("people.com.cn", "人民网"),
    ("cctv.com", "央视新闻"), ("chinanews.com.cn", "中国新闻网"), ("thepaper.cn", "澎湃新闻"),
    ("huanqiu.com", "环球网"), ("guancha.cn", "观察者网"), ("caixin.com", "财新网"),
    ("jiemian.com", "界面新闻"), ("bjnews.com.cn", "新京报"), ("yicai.com", "第一财经"),
    ("21jingji.com", "21世纪经济报道"), ("stcn.com", "证券时报"), ("cls.cn", "财联社"),
    ("cnr.cn", "央广网"), ("gmw.cn", "光明网"), ("china.com.cn", "中国网"),
    ("eastday.com", "东方网"), ("ynet.com", "北青网"), ("sina.com.cn", "新浪"),
    ("163.com", "网易"), ("qq.com", "腾讯网"), ("ifeng.com", "凤凰网"),
    ("dongqiudi.com", "懂球帝"), ("zhibo8.com", "直播吧"), ("cgtn.com", "CGTN"),
    ("globaltimes.cn", "环球时报"), ("chinadaily.com.cn", "中国日报"),
    ("stdaily.com", "科技日报"), ("cas.cn", "中科院"), ("moe.gov.cn", "教育部"),
    ("mod.gov.cn", "国防部"), ("sports.gov.cn", "体育总局"),
)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _root_base() -> str:
    base = (media.DEEPSEEK_BASE or "https://api.deepseek.com").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def _blocked(url: str) -> bool:
    d = _domain(url)
    return any(b in d for b in BLOCK_DOMAINS)


def _domain_match(d: str, p: str) -> bool:
    """域名匹配：精确或子域（防 xinhuanet.com.evil.com 之类绕过）；无点条目按标签匹配"""
    if not d or not p:
        return False
    if "." in p:
        return d == p or d.endswith("." + p)
    return p in d.split(".")


def _host_public(url: str) -> bool:
    """SSRF 防护：拒绝内网/环回/保留地址（下载图片/视频前调用）"""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip = str(info[4][0]).split("%")[0]
        try:
            addr = ipaddress.ip_address(ip)
        except Exception:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


def _open_safe(req, timeout):
    """统一网络出口：自动跟随重定向，但 301/302/307 的每一跳都重新做 SSRF 校验。
    原实现只校验初始 URL，urlopen 跟随重定向后会绕过检查（可跳内网并落盘）。"""
    class _SafeRedirect(urllib.request.HTTPRedirectHandler):
        max_redirections = 5

        def redirect_request(self, r, fp, code, msg, headers, newurl):
            if not _host_public(newurl):
                raise urllib.error.HTTPError(r.full_url, code, "redirect to private host blocked", headers, fp)
            return super().redirect_request(r, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_SafeRedirect).open(req, timeout=timeout)


def _prefer_rank(url: str) -> int:
    d = _domain(url)
    for i, p in enumerate(PREFER_DOMAINS):
        if _domain_match(d, p):
            return i
    return len(PREFER_DOMAINS) + 1


def _source_name(url: str) -> str:
    d = _domain(url)
    for key, name in SOURCE_NAMES:
        if key in d:
            return name
    return d or "网络"


def _trusted(url: str) -> bool:
    """只接受白名单内的主流中文媒体（去广告、去聚合号、去外文站）"""
    return _prefer_rank(url) <= len(PREFER_DOMAINS)


_INDEX_NAMES = ("index.html", "index.shtml", "index.htm", "index.jsp", "index.php",
                "default.html", "default.htm", "list.html", "list.shtml")


def _is_index_page(url: str) -> bool:
    """栏目/列表页（不是文章）：路径以 / 结尾或末段为 index.* 等"""
    try:
        p = urllib.parse.urlparse(url).path.lower()
    except Exception:
        return False
    if not p or p.endswith("/"):
        return True
    return p.rsplit("/", 1)[-1] in _INDEX_NAMES


_KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_OTHER_SCRIPT_RE = re.compile(r"[\u0400-\u04ff\u0600-\u06ff\u0e00-\u0e7f]")
_LATIN_RE = re.compile(r"[A-Za-z]{3,}")

_OPENCC = None


def _t2s(s: str) -> str:
    """繁体转简体（OpenCC；不可用时原样返回）"""
    global _OPENCC
    if not s:
        return s
    try:
        if _OPENCC is None:
            from opencc import OpenCC  # type: ignore
            _OPENCC = OpenCC("t2s")
        return _OPENCC.convert(s)
    except Exception:
        return s


def _is_chinese(title: str) -> bool:
    if _KANA_RE.search(title):
        return False
    return bool(_CJK_RE.search(title))


def _lang_ok(title: str, url: str) -> bool:
    """语言过滤：中文标题直接通过；英文标题仅限官方中文媒体的英文版；
    日文假名、西里尔/阿拉伯等文字一律拒绝"""
    if not title:
        return False
    if _KANA_RE.search(title) or _OTHER_SCRIPT_RE.search(title):
        return False
    if _CJK_RE.search(title):
        return True
    d = _domain(url)
    if not any(k in d for k in EN_OK_DOMAINS):
        return False
    return bool(_LATIN_RE.search(title))


def _official_keys() -> list:
    """官方 DeepSeek Key 列表（新闻联网搜索需官方 Anthropic 兼容端点；中转不支持 web_search 工具）。
    优先从账号池取 base 为官方 api.deepseek.com 的 Key；池不可用时回退 media.DEEPSEEK_API_KEY。"""
    out = []
    try:
        import ai  # 延迟导入（避免循环依赖）
        keys = ai._deepseek_keys()
        base_of = ai._deepseek_base_of(keys)
        for k in keys:
            _host = (urllib.parse.urlparse(str(base_of.get(k) or "")).hostname or "").lower()
            if _host == "api.deepseek.com" or _host.endswith(".deepseek.com"):
                out.append(k)
    except Exception:
        pass
    if not out and media.DEEPSEEK_API_KEY:
        out.append(media.DEEPSEEK_API_KEY)
    return out


def _ark_keys() -> list:
    """火山方舟 Key 列表（每日新闻走方舟「联网内容插件」）；缺省回退 media.ARK_API_KEY。"""
    out = []
    try:
        import ai  # 延迟导入（避免循环依赖）
        out = [str(k) for k in (ai._ark_keys() or []) if k]
    except Exception:
        pass
    if not out and media.ARK_API_KEY:
        out.append(media.ARK_API_KEY)
    return out


def _ark_base() -> str:
    try:
        import ai
        return str(ai.ARK_BASE or "").rstrip("/") or "https://ark.cn-beijing.volces.com/api/v3"
    except Exception:
        return "https://ark.cn-beijing.volces.com/api/v3"


def _ark_model() -> str:
    try:
        import ai
        return str(ai.ARK_MODEL or "") or "doubao-seed-2-1-turbo-260628"
    except Exception:
        return "doubao-seed-2-1-turbo-260628"


def _dedupe_rank(results: list) -> list:
    """URL 去重 + 权威媒体优先排序（保持原始相关度顺序）"""
    seen = set()
    uniq = []
    for it in results:
        u = str(it.get("url") or "")
        if not u or u in seen:
            continue
        seen.add(u)
        uniq.append(it)
    uniq.sort(key=lambda x: _prefer_rank(x["url"]))
    return uniq


def search_category_ark(cat: str, hint: str, date_desc: str) -> list:
    """火山方舟「联网内容插件」搜索（Responses API；sources=['toutiao'] 头条图文追加全网）。
    解析 message.content[].annotations 的 url_citation → [{title,url,page_age}]；失败返回 []。"""
    keys = _ark_keys()
    if not keys:
        _log("[news] 缺少可用的 ARK Key", level="WARNING")
        return []
    prompt = (
        f"请联网搜索最近两天（{date_desc}）关于「{cat}」的重要新闻（{hint}）。"
        "优先找权威媒体（新华社、央视新闻、人民网、中国新闻网、澎湃新闻、环球网、财新等）的最新报道。"
        "以中文媒体为主；中国日报（China Daily）、CGTN 等官方媒体的英文报道也可以。"
    )
    payload = {
        "model": _ark_model(),
        "stream": False,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "tools": [{
            "type": "web_search",
            "sources": ["toutiao"],              # 头条图文（追加；全网搜索仍生效）
            "user_location": {"type": "approximate", "country": "中国"},
            "max_keyword": 2,                     # 控成本：限制单轮关键词数
            "limit": 10,
        }],
        "max_tool_calls": 2,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    resp = None
    last_e = None
    for key in keys:
        req = urllib.request.Request(
            _ark_base() + "/responses", data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        try:
            with urllib.request.urlopen(req, timeout=150) as r:
                resp = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception as e:
            last_e = e
            _log(f"[news] 方舟搜索失败（{cat}，key…{key[-4:]}）: {e}", level="WARNING")
    if resp is None:
        if last_e is not None:
            _log(f"[news] 方舟搜索「{cat}」全部 Key 失败: {last_e}", level="WARNING")
        return []
    out = []
    for item in resp.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if not isinstance(c, dict):
                continue
            for a in c.get("annotations") or []:
                if not isinstance(a, dict) or a.get("type") != "url_citation":
                    continue
                url = str(a.get("url") or "").strip()
                if url.startswith("http"):
                    out.append({
                        "title": str(a.get("title") or "").strip(),
                        "url": url,
                        "page_age": str(a.get("publish_time") or ""),
                    })
    uniq = _dedupe_rank(out)
    _log(f"[news] 方舟头条源「{cat}」得到 {len(uniq)} 条结果")
    return uniq


def search_category(cat: str, hint: str, date_desc: str, max_uses: int = 4) -> list:
    """每日新闻搜索：火山方舟「联网内容插件」（头条图文+全网）优先，
    无结果/失败时回退 DeepSeek 官方联网搜索，保证不断档。"""
    try:
        res = search_category_ark(cat, hint, date_desc)
    except Exception as e:
        _log(f"[news] 方舟搜索异常（{cat}）: {e}", level="WARNING")
        res = []
    if res:
        return res
    _log(f"[news] 「{cat}」方舟无结果，回退 DeepSeek 联网搜索")
    return search_category_deepseek(cat, hint, date_desc, max_uses)


def search_category_deepseek(cat: str, hint: str, date_desc: str, max_uses: int = 3) -> list:
    """DeepSeek 官方联网搜索（Anthropic 兼容端点），返回 [{title,url,page_age}]（失败返回空列表）"""
    keys = _official_keys()
    if not keys:
        _log("[news] 缺少可用的官方 DEEPSEEK Key", level="WARNING")
        return []
    prompt = (
        f"请联网搜索最近两天（{date_desc}）关于「{cat}」的重要新闻（{hint}）。"
        "优先找权威媒体（新华社、央视新闻、人民网、中国新闻网、澎湃新闻、环球网、财新等）的最新报道。"
        "以中文媒体为主；中国日报（China Daily）、CGTN 等官方媒体的英文报道也可以。"
        "多用几个不同的关键词搜索，覆盖面广一些。"
    )
    payload = {
        "model": "deepseek-flash",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
    }
    body = json.dumps(payload).encode("utf-8")
    resp = None
    last_e = None
    for key in keys:
        req = urllib.request.Request(
            _root_base() + "/anthropic/v1/messages",
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": key,
                "authorization": "Bearer " + key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=150) as r:
                resp = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception as e:
            last_e = e
            _log(f"[news] 搜索失败（{cat}，key…{key[-4:]}）: {e}", level="WARNING")
    if resp is None:
        if last_e is not None:
            _log(f"[news] 搜索「{cat}」全部 Key 失败: {last_e}", level="WARNING")
        return []
    out = []
    for block in resp.get("content") or []:
        if block.get("type") != "web_search_tool_result":
            continue
        for it in block.get("content") or []:
            if not isinstance(it, dict):
                continue
            url = str(it.get("url") or "").strip()
            title = str(it.get("title") or "").strip()
            if url.startswith("http"):
                out.append({"title": title, "url": url, "page_age": it.get("page_age") or ""})
    # 去重 + 排序（权威媒体优先，保持原始相关度顺序）
    uniq = _dedupe_rank(out)
    _log(f"[news] 搜索「{cat}」得到 {len(uniq)} 条结果")
    return uniq


def _decode(raw: bytes, content_type: str = "") -> str:
    ct = (content_type or "").lower()
    m = re.search(r"charset=([\w-]+)", ct)
    charsets = []
    if m:
        charsets.append(m.group(1))
    head = raw[:2048].decode("ascii", "ignore")
    m2 = re.search(r'charset=["\']?([\w-]+)', head, re.I)
    if m2:
        charsets.append(m2.group(1))
    charsets += ["utf-8", "gb18030", "latin-1"]
    for cs in charsets:
        try:
            return raw.decode(cs)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def _meta(html: str, prop: str) -> str:
    patterns = [
        r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)' % re.escape(prop),
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(prop),
    ]
    for p in patterns:
        m = re.search(p, html, re.I)
        if m:
            return _html.unescape(m.group(1).strip())
    return ""


def fetch_page(url: str):
    """抓取页面 HTML（返回 (html 或 None)）"""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        with _open_safe(req, 20) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype.lower() and "xml" not in ctype.lower():
                return None
            raw = r.read(700 * 1024)
        return _decode(raw, ctype)
    except Exception:
        return None


_IMG_BAD = ("logo", "icon", "qrcode", "ewm", "weixin", "weibo", "blank", "spacer",
            "loading", "avatar", "share", "default", "placeholder", "banner")
_IMG_MARKERS = ("pic", "upload", "photo", "/content", "article", "media", "img/")


def _link_image_src(html: str) -> str:
    """<link rel="image_src" href="...">（部分站点用它代替 og:image）"""
    m = re.search(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)', html, re.I)
    if not m:
        m = re.search(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']image_src["\']', html, re.I)
    return m.group(1).strip() if m else ""


def _content_image(html: str, base: str) -> str:
    """正文配图兜底：只挑"像文章图"的 <img>（路径含内容特征、非 logo/图标/gif）"""
    for m in re.finditer(r'<img[^>]+?(?:data-src|data-original|src)=["\']([^"\']+)["\']', html, re.I):
        src = m.group(1).strip()
        if not src or src.startswith("data:"):
            continue
        low = src.lower()
        if low.endswith((".gif", ".svg")) or not low.endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        name = low.rsplit("/", 1)[-1]
        if any(b in name for b in _IMG_BAD):
            continue
        if any(k in low for k in _IMG_MARKERS):
            return urllib.parse.urljoin(base, src)
    return ""


def parse_page_meta(url: str, html: str) -> dict:
    """页面元信息：title / description / image / published"""
    if not html:
        return {}
    out = {
        "title": _meta(html, "og:title") or _meta(html, "twitter:title"),
        "description": (_meta(html, "og:description") or _meta(html, "twitter:description")
                        or _meta(html, "description")),
        "image": (_meta(html, "og:image") or _meta(html, "twitter:image")
                  or _link_image_src(html) or _meta(html, "image")
                  or _content_image(html, url)),
        "published": (_meta(html, "article:published_time") or _meta(html, "og:release_date")
                      or _meta(html, "pubdate") or _meta(html, "publishdate")),
    }
    if out["image"]:
        out["image"] = urllib.parse.urljoin(url, out["image"])
    return out


def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)
    return _html.unescape(s)


def _find_video_urls(html: str) -> list:
    """从页面中找出可能的视频地址（og:video / <video> / JS 里的 mp4、m3u8 / 央视 guid）"""
    urls = []
    h = html or ""
    for prop in ("og:video", "og:video:url", "og:video:secure_url"):
        v = _meta(h, prop)
        if v and v.startswith("http"):
            urls.append(v)
    for m in re.finditer(r'(?is)<(?:video|source)[^>]+src=["\']([^"\']+)["\']', h):
        urls.append(m.group(1))
    for m in re.finditer(r'["\'](https?://[^"\']+?\.(?:mp4|m3u8)[^"\']*)["\']', h):
        urls.append(m.group(1))
    g = re.search(r'(?i)guid\s*[:=]\s*["\']([0-9a-fA-F]{20,40})["\']', h)
    if g:
        urls.append("cctv:" + g.group(1))
    out = []
    seen = set()
    for u in urls:
        u = u.replace("\\/", "/").split(",")[0].strip()
        if not u.startswith("http") and not u.startswith("cctv:"):
            continue
        if u.endswith("removemp4"):
            continue
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _resolve_cctv(pid: str):
    """央视视频接口 → (hls 播放列表)"""
    try:
        url = f"https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid={pid}"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return []
    h = str(d.get("hls_url") or "").replace("\\/", "/")
    return [h] if h else []


def _video_target(ext: str = ".mp4") -> str:
    os.makedirs(NEWS_VIDEO_DIR, exist_ok=True)
    return os.path.join(NEWS_VIDEO_DIR, f"nv_{int(time.time())}_{os.urandom(3).hex()}{ext}")


def _download_mp4(url: str, referer: str = "") -> str:
    headers = {"User-Agent": _UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with _open_safe(req, 150) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "video" not in ctype and not url.lower().endswith(".mp4"):
            return ""
        data = r.read(90 * 1024 * 1024)
    if len(data) < 60 * 1024:
        return ""
    path = _video_target(".mp4")
    with open(path, "wb") as f:
        f.write(data)
    return "/media/news/videos/" + os.path.basename(path)


def _download_hls(url: str, referer: str = "") -> str:
    headers = {"User-Agent": _UA}
    if referer:
        headers["Referer"] = referer
    with _open_safe(urllib.request.Request(url, headers=headers), 30) as r:
        playlist = r.read().decode("utf-8", "replace")
    if "#EXT-X-KEY" in playlist:  # 加密流不处理
        return ""
    base = url.rsplit("/", 1)[0] + "/"
    segs = [ln.strip() for ln in playlist.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    if not segs:
        return ""
    total = 0
    tmp = _video_target(".ts")
    with open(tmp, "wb") as out:
        for s in segs[:80]:
            try:
                su = urllib.parse.urljoin(base, s)
                if not _host_public(su):
                    continue
                with _open_safe(
                        urllib.request.Request(su, headers=headers), 30) as sr:
                    chunk = sr.read(8 * 1024 * 1024)
            except Exception:
                continue
            total += len(chunk)
            if total > 100 * 1024 * 1024:
                break
            out.write(chunk)
    if total < 200 * 1024:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return ""
    return "/media/news/videos/" + os.path.basename(tmp)


def download_video(url: str, referer: str = "") -> str:
    """下载视频到本地（mp4 直下 / m3u8 拼 TS），返回本地 URL；失败返回空"""
    if not url.startswith("http") or not _host_public(url):
        return ""
    try:
        if ".m3u8" in url:
            return _download_hls(url, referer)
        return _download_mp4(url, referer)
    except Exception as e:
        _log(f"[news] 视频下载失败 {url[:60]}: {e}", level="WARNING")
        return ""


def find_and_download_video(html: str, page_url: str) -> str:
    """页面里有视频就下载（优先 mp4，其次 hls），返回本地 URL"""
    mp4s, hlss = [], []
    for u in _find_video_urls(html)[:8]:
        if u.startswith("cctv:"):
            hlss.extend(_resolve_cctv(u[5:])[:1])
        elif ".m3u8" in u:
            hlss.append(u)
        elif ".mp4" in u:
            mp4s.append(u)
    for vu in (mp4s[:1] + hlss[:1]):
        got = download_video(vu, referer=page_url)
        if got:
            return got
    return ""


_JUNK_LINE = (
    "责任编辑", "免责声明", "版权声明", "京ICP", "违法和不良信息", "举报电话", "客服",
    "登录", "注册", "下载客户端", "扫一扫", "分享到", "上一篇", "下一篇", "相关阅读",
    "相关推荐", "热榜", "热门推荐", "广告", "微信扫一扫", "关注我们", "更多精彩",
    "原标题：", "来源：", "编辑：", "校对：", "责编：",
    "新浪财经app", "扫描二维码", "更多粉丝福利", "滚动播报", "关注公众号", "微信公众号",
    "下载app", "尽在", "海量资讯", "精准解读", "敬请关注", "点击查看", "点击下载",
    "商务合作", "投稿", "手机版", "客户端下载", "更多内容请", "长按", "识别二维码",
)

_AD_LINE = (
    "新浪财经app", "扫描二维码", "更多粉丝福利", "滚动播报", "关注公众号", "微信公众号",
    "下载app", "下载客户端", "海量资讯", "精准解读", "敬请关注", "更多精彩内容",
    "点击查看", "点击下载", "商务合作", "扫一扫", "长按识别", "识别二维码",
    "微信扫一扫", "手机扫描", "尽在", "投稿邮箱", "新闻热线",
)


_CUT_MARKERS = (
    "产品答疑", "网站律师", "all rights reserved", "版权所", "新浪公司", "版权所有",
    "海量资讯", "精准解读", "滚动播报", "更多粉丝福利", "扫描二维码", "关注公众号",
    "下载客户端", "下载app", "责任编辑", "免责声明", "违法和不良信息", "京icp",
    "客服热线", "广告合作", "热门评论", "全部评论", "网友评论", "我来说两句", "股吧",
    "相关推荐", "相关阅读", "猜你喜欢", "延伸阅读", "推荐阅读", "7x24",
)


def _regex_clean_ads(text: str) -> str:
    """先按行删掉明显的广告/推广行，并从尾部截掉评论区/版权声明等（廉价第一道过滤）"""
    lines = [ln.strip() for ln in (text or "").split("\n")]
    lines = [ln for ln in lines if ln]
    # 尾部截断：找到靠后位置出现的第一条"页面尾部区块"标记
    cut = len(lines)
    for i, ln in enumerate(lines):
        if i < max(1, len(lines) // 3):
            continue
        low = ln.lower()
        if any(k in low for k in _CUT_MARKERS):
            cut = i
            break
    lines = lines[:cut]
    out = []
    for ln in lines:
        low = ln.lower()
        if any(k in low for k in _AD_LINE):
            continue
        out.append(ln)
    return _cut_junk_tail("\n".join(out))


def _cut_junk_tail(text: str) -> str:
    """从后半段开始，遇到第一行"不像新闻正文"（短、无句末标点）就截掉其后全部内容。
    用于去掉页面底部的推荐列表、网友评论、名博列表等混入。"""
    lines = [ln.strip() for ln in (text or "").split("\n")]
    lines = [ln for ln in lines if ln]
    if len(lines) < 4:
        return "\n".join(lines)
    limit = max(1, int(len(lines) * 0.4))
    cut = len(lines)
    for i in range(limit, len(lines)):
        s = lines[i]
        news_like = (len(s) >= 30 and any(p in s for p in "。！？")) or len(s) >= 60
        if not news_like:
            cut = i
            break
    return "\n".join(lines[:cut])


def _model_clean(title: str, text: str) -> str:
    """用便宜模型清理正文里残留的广告/推广（失败则原样返回）"""
    if not text:
        return text
    try:
        model = str(db.get_settings().get("news_clean_model") or "").strip() or "deepseek-flash"
    except Exception:
        model = "deepseek-flash"
    prompt = (
        "下面是一条新闻的标题和正文（正文从网页自动提取，可能混入与新闻无关的内容）。\n"
        "需要删除的无关内容示例：App 推广、二维码关注、广告、网站导航、"
        "页面底部的网友评论/股吧发言/论坛跟帖（常见形态：`用户名：一句话`）、"
        "「相关推荐/延伸阅读/猜你喜欢」的文章标题列表、产品答疑、客服信息、网站版权声明等。\n"
        "要求：只删除与新闻无关的部分；新闻正文必须原样保留，不要改写、不要总结、"
        "不要增删事实、不要调整语序；段落之间保留换行；如果正文开头有与标题重复的一行也可以删掉。\n"
        "如果本来就没有无关内容，就原样返回。\n"
        "只输出清理后的正文，不要任何解释。\n\n"
        f"标题：{title[:120]}\n\n正文：\n{text[:9000]}"
    )
    try:
        import ai  # 延迟导入
        content, err = ai._call_model(
            [{"role": "user", "content": prompt}],
            model=model, temperature=0.1, max_tokens=4000)
        out = (content or "").strip()
        if err or not out:
            return text
        # 安全校验：清洗后不能删掉太多（防止模型抽风）
        if len(out) < max(60, len(text) // 4):
            return text
        return out[:12000]
    except Exception as e:
        _log(f"[news] 模型清洗失败: {e}", level="WARNING")
        return text


def extract_article_text(html: str) -> str:
    """从新闻页提取正文（块级标签按行抽取 + 过滤导航垃圾），失败返回空串"""
    if not html:
        return ""
    h = re.sub(r"(?is)<(script|style|noscript|iframe|svg|head|form|nav|footer)[^>]*>.*?</\1>", " ", html)
    h = re.sub(r"(?is)<!--.*?-->", " ", h)
    h = re.sub(r"(?is)</?(p|div|br|li|h[1-6]|section|article|tr)[^>]*>", "\n", h)
    txt = _strip_html(h)
    lines = [re.sub(r"\s+", " ", x).strip() for x in txt.split("\n")]
    seen = set()
    out = []
    for x in lines:
        if len(x) < 15 or not _CJK_RE.search(x):
            continue
        if any(j in x for j in _JUNK_LINE):
            continue
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return "\n\n".join(out)[:12000]


def download_image(img_url: str, referer: str = "") -> str:
    """下载图片到本地，返回 /media/news/... URL（失败返回空串）"""
    if not img_url.startswith("http") or not _host_public(img_url):
        return ""
    try:
        os.makedirs(NEWS_DIR, exist_ok=True)
        headers = {"User-Agent": _UA}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(img_url, headers=headers)
        with _open_safe(req, 20) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if not ctype.startswith("image/"):
                return ""
            data = r.read(4 * 1024 * 1024)
        if len(data) < 1024:
            return ""
        ext = ".jpg"
        if "png" in ctype:
            ext = ".png"
        elif "webp" in ctype:
            ext = ".webp"
        elif "gif" in ctype:
            ext = ".gif"
        fname = f"n_{int(time.time())}_{os.urandom(3).hex()}{ext}"
        with open(os.path.join(NEWS_DIR, fname), "wb") as f:
            f.write(data)
        return "/media/news/" + fname
    except Exception:
        return ""


def _clean_text(s: str, limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s[:limit]


def fetch_and_store(local_now=None, per_category: int = 4, days_keep: int = 7) -> dict:
    """抓取五大类最近两天新闻并入库（由后台任务调用，勿在请求线程同步执行）"""
    import datetime as _dt
    if local_now is None:
        local_dt = _dt.datetime.now()
    elif isinstance(local_now, _dt.datetime):
        local_dt = local_now
    else:
        try:
            local_dt = _dt.datetime(*tuple(local_now)[:6])
        except Exception:
            local_dt = _dt.datetime.now()
    d1 = local_dt.strftime("%m月%d日")
    d2 = (local_dt - _dt.timedelta(days=1)).strftime("%m月%d日")
    date_desc = f"{d2} 和 {d1}"
    day = local_dt.strftime("%Y-%m-%d")
    stats = {"added": 0, "skipped": 0, "categories": {}}
    for cat, hint in CATEGORIES:
        got = 0
        results = search_category(cat, hint, date_desc, max_uses=4)
        cands = []
        scanned = 0
        videos_done = 0
        for it in results:
            if len(cands) >= per_category * 2 or scanned >= 10:
                break
            url = it["url"]
            if db.news_exists(url):
                stats["skipped"] += 1
                continue
            if not _trusted(url):
                stats["skipped"] += 1
                continue
            if _is_index_page(url):
                stats["skipped"] += 1
                continue
            scanned += 1
            html = fetch_page(url) or ""
            meta = parse_page_meta(url, html)
            title = (it.get("title") or meta.get("title") or "").strip()
            if not title or not _lang_ok(title, url):
                continue
            image_url = meta.get("image") or ""
            local_img = download_image(image_url, referer=url) if image_url else ""
            # 配图非必需：抓不到图也照常入库（App 端显示占位图）
            summary = _clean_text(meta.get("description") or "")
            fulltext = extract_article_text(html)
            if len(fulltext) < 80:
                fulltext = summary
            # 繁体统一转简体
            title = _t2s(title)
            summary = _t2s(summary)
            fulltext = _t2s(fulltext)
            # 广告清理：先按行删明显广告，再用便宜模型清残留（管理页可关）
            fulltext = _regex_clean_ads(fulltext)
            try:
                _clean_on = str(db.get_settings().get("news_clean_enabled", 1)) not in ("0", "False", "false", "")
            except Exception:
                _clean_on = True
            if _clean_on and len(fulltext) >= 80:
                fulltext = _model_clean(title, fulltext)
            if len(fulltext) < 80:
                fulltext = ""
            # 视频新闻：把视频也下载下来（每次抓取最多 8 个，控制耗时与体积）
            video_url = ""
            if html and videos_done < 8:
                video_url = find_and_download_video(html, url)
                if video_url:
                    videos_done += 1
            # 只收"完整"内容：正文够长 或 带可播放视频；否则（图+一句话）丢弃
            if len(fulltext) < 240 and not video_url:
                stats["skipped"] += 1
                continue
            cands.append((len(fulltext) + (1500 if video_url else 0), title, summary, url,
                          local_img, fulltext, _clean_text(meta.get("published") or "", 40),
                          video_url))
        # 完整度高的优先收录
        cands.sort(key=lambda x: -x[0])
        got = 0
        for _n, title, summary, url, local_img, fulltext, published, video_url in cands[:per_category]:
            db.add_news(day, cat, _clean_text(title, 120), summary, url,
                        _source_name(url), local_img, published, content=fulltext,
                        video=video_url)
            got += 1
        stats["categories"][cat] = got
        stats["added"] += got
        _log(f"[news] 「{cat}」入库 {got} 条")
    # 清理旧数据（含图片）
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days_keep * 86400))
    for img in db.cleanup_news(cutoff):
        try:
            p = media.media_fs_path(img)
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    return stats

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wx_read.py —— 微信公众号文章 / 贴图（图片消息）读取器

设计原则
--------
1. 零依赖：只用 Python 标准库。codex / WorkBuddy / 裸机 python 都能直接跑。
2. 多通道：直连 -> 本地代理 -> Chrome headless 渲染，逐级降级，每级都做有效性判定。
3. 两种页面都认：
   - 普通图文：正文在 <div id="js_content"> 里，图片在 img[data-src]
   - 贴图（图片消息）：图片不在 DOM 里，而在 JS 变量 picture_page_info_list 的 cdn_url 上
     —— 这是"贴图老是读不到"的根本原因，常规解析器只会找 <img>。
4. 图片带 Referer 下载：mmbiz.qpic.cn 校验来源，直接引用 URL 会 403。

用法
----
  python wx_read.py <url> [--out DIR] [--no-download] [--json]
  python wx_read.py <url> --channel browser          # 直连被风控时
  python wx_read.py ./saved.html --channel file      # 离线解析已存的页面
  python wx_read.py <url1> <url2> --out ./dump       # 批量

退出码
------
  0 成功   2 参数错   3 抓取失败（风控/网络）   4 解析失败（无正文无图）
"""

from __future__ import annotations

import argparse
import gzip
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REFERER = "https://mp.weixin.qq.com/"

PROXY_CANDIDATES = [
    "http://127.0.0.1:7897",
    "http://127.0.0.1:7890",
    "http://127.0.0.1:1080",
    "http://127.0.0.1:2022",
]

# 命中即判"这次没拿到正文"，换下一个通道
RISK_MARKERS = [
    "环境异常",
    "请在微信客户端打开",
    "请在微信打开",
    "访问过于频繁",
    "wappoc_appmsgcaptcha",
    "verify_code",
    "该内容已被发布者删除",
    "内容已被发布者删除",
    "内容违规无法查看",
    "此内容因违规无法查看",
    "参数错误",
    "链接已过期",
]

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
TAG_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*["']([^"']*)["']""")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()


def decode_entities(value: str) -> str:
    if not value:
        return ""
    value = htmllib.unescape(value)
    value = value.replace("\\x26", "&").replace("\\x3d", "=").replace("\\/", "/")
    value = value.replace("\\u0026", "&")
    return value.strip()


def meta_content(html: str, prop: str) -> str:
    esc = re.escape(prop)
    for pat in (
        rf"""<meta[^>]+(?:property|name)\s*=\s*["']{esc}["'][^>]+content\s*=\s*["']([^"']*)""",
        rf"""<meta[^>]+content\s*=\s*["']([^"']*)["'][^>]+(?:property|name)\s*=\s*["']{esc}["']""",
    ):
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return decode_entities(m.group(1))
    return ""


def js_string_var(html: str, name: str) -> str:
    """取 var name = "xxx"; / var name = htmlDecode('xxx'); 这类赋值。"""
    esc = re.escape(name)
    pats = [
        rf"""var\s+{esc}\s*=\s*htmlDecode\(\s*["'](.*?)["']\s*\)""",
        rf"""var\s+{esc}\s*=\s*["'](.*?)["']\s*;""",
        rf"""{esc}\s*:\s*["'](.*?)["']\s*,""",
    ]
    for pat in pats:
        m = re.search(pat, html, re.DOTALL)
        if m:
            return decode_entities(m.group(1))
    return ""


def extract_balanced(html: str, tag: str, attr: str, value: str) -> str:
    """按标签配平截取 <tag ... attr="value"> ... </tag>，比正则可靠。"""
    start_re = re.compile(rf"<{tag}\b[^>]*\b{attr}\s*=\s*[\"']{re.escape(value)}[\"'][^>]*>", re.IGNORECASE)
    m = start_re.search(html)
    if not m:
        return ""
    token_re = re.compile(rf"<{tag}\b[^>]*>|</{tag}\s*>", re.IGNORECASE)
    pos = m.start()
    depth = 0
    while True:
        tok = token_re.search(html, pos)
        if not tok:
            return html[m.start():]
        if re.match(rf"<{tag}\b", tok.group(0), re.IGNORECASE):
            depth += 1
        else:
            depth -= 1
        pos = tok.end()
        if depth == 0:
            return html[m.start():tok.end()]


def strip_tags(fragment: str) -> str:
    s = re.sub(r"<script\b[\s\S]*?</script>", "", fragment, flags=re.IGNORECASE)
    s = re.sub(r"<style\b[\s\S]*?</style>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<(?:br|hr)\b[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</(?:p|div|section|h[1-6]|li|blockquote|pre|tr|figure)>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<li\b[^>]*>", "- ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --------------------------------------------------------------------------
# 抓取层：三条通道，逐级降级
# --------------------------------------------------------------------------
def _request(url: str, proxy: str | None, timeout: int) -> tuple[int, bytes, str]:
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": REFERER,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        elif "deflate" in enc:
            try:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            except Exception:
                pass
        return resp.status, raw, resp.url


def _to_text(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def check_valid(html: str) -> tuple[bool, str]:
    if not html:
        return False, "空响应"
    # 先认拦截标记：风控页往往很短，不能被长度判断抢先挡掉
    for marker in RISK_MARKERS:
        if marker in html:
            return False, f"命中拦截/失效标记：{marker}"
    if len(html) < 500:
        return False, f"响应过短（{len(html)} 字节）"
    if "js_content" in html or "picture_page_info_list" in html or "activity-name" in html:
        return True, "ok"
    return False, "页面里没有正文容器（可能是验证页或结构改版）"


def fetch_http(url: str, timeout: int, quiet: bool) -> tuple[str, str, str]:
    """直连。返回 (html, 通道名, 说明)。"""
    for attempt in range(2):
        try:
            status, raw, final_url = _request(url, None, timeout)
            html = _to_text(raw)
            ok, why = check_valid(html)
            if ok:
                return html, "http-direct", f"HTTP {status} · {len(raw)}B"
            log(f"  · 直连不可用：{why}", quiet)
            return "", "http-direct", why
        except urllib.error.HTTPError as e:
            log(f"  · 直连 HTTP {e.code}", quiet)
            return "", "http-direct", f"HTTP {e.code}"
        except Exception as e:
            if attempt == 0:
                time.sleep(0.8)
                continue
            log(f"  · 直连异常：{e}", quiet)
            return "", "http-direct", str(e)
    return "", "http-direct", "unknown"


def fetch_proxy(url: str, timeout: int, quiet: bool) -> tuple[str, str, str]:
    for proxy in PROXY_CANDIDATES:
        try:
            status, raw, _ = _request(url, proxy, timeout)
            html = _to_text(raw)
            ok, why = check_valid(html)
            if ok:
                return html, f"http-proxy({proxy})", f"HTTP {status} · {len(raw)}B"
            log(f"  · 代理 {proxy} 不可用：{why}", quiet)
        except Exception as e:
            log(f"  · 代理 {proxy} 连不上：{str(e)[:60]}", quiet)
    return "", "http-proxy", "所有候选代理都失败"


def find_chrome() -> str:
    cands = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


def fetch_browser(url: str, timeout: int, quiet: bool) -> tuple[str, str, str]:
    """用 headless Chrome 渲染后 dump DOM。无登录态，但公开文章够用，能过一部分 JS 校验。"""
    exe = find_chrome()
    if not exe:
        return "", "browser-headless", "本机没找到 Chrome/Edge"
    profile = tempfile.mkdtemp(prefix="wxread_")
    cmd = [
        exe,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        f"--virtual-time-budget={min(timeout * 1000, 15000)}",
        "--dump-dom",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 25)
        html = _to_text(proc.stdout or b"")
        ok, why = check_valid(html)
        if ok:
            return html, "browser-headless", f"{len(html)}B"
        return "", "browser-headless", why
    except subprocess.TimeoutExpired:
        return "", "browser-headless", "渲染超时"
    except Exception as e:
        return "", "browser-headless", str(e)
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def fetch_any(url: str, channel: str, timeout: int, quiet: bool) -> tuple[str, str, str]:
    if channel == "http":
        return fetch_http(url, timeout, quiet)
    if channel == "proxy":
        return fetch_proxy(url, timeout, quiet)
    if channel == "browser":
        return fetch_browser(url, timeout, quiet)
    # auto
    html, ch, note = fetch_http(url, timeout, quiet)
    if html:
        return html, ch, note
    log("  · 直连不通，试本地代理", quiet)
    html, ch, note = fetch_proxy(url, timeout, quiet)
    if html:
        return html, ch, note
    log("  · 代理不通，试 headless 浏览器渲染", quiet)
    return fetch_browser(url, timeout, quiet)


# --------------------------------------------------------------------------
# 解析层
# --------------------------------------------------------------------------
def normalize_img_url(u: str) -> str:
    u = decode_entities(u)
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://mmbiz.qpic.cn"):
        u = "https://" + u[len("http://"):]
    return u


def is_real_image(u: str) -> bool:
    if not u or not u.startswith("http"):
        return False
    low = u.lower()
    if "data:image" in low or low.endswith(".svg"):
        return False
    if "placeholder" in low or "wx_lazy" in low:
        return False
    # 只排除公众号页面的 UI 素材与表情：正文图同样在 mmbiz.qpic.cn 上，不能按域名一刀切
    if "res.wx.qq.com" in low:
        return False
    if "wxemotion" in low or "/emotion" in low or "we-emoji" in low:
        return False
    return True


def extract_picture_page_images(html: str) -> list[str]:
    """贴图（图片消息）页：图片藏在 JS 变量 picture_page_info_list 的 cdn_url 里。

    这个结构在整页里可能出现多次（有的为空数组），所以不能只取第一处，
    直接全局扫 cdn_url 更稳——它是公众号图片消息的专有字段。
    """
    out: list[str] = []
    for m in re.finditer(r"cdn_url\s*:\s*['\"]([^'\"]+)['\"]", html):
        u = normalize_img_url(m.group(1))
        if is_real_image(u):
            out.append(u)
    return out


def image_key(u: str) -> str:
    """同一张图在公众号上常有 /640 和 /0 两种规格，按图片 ID 去重，避免重复下载。"""
    m = re.search(r"mmbiz\.qpic\.cn/(?:[A-Za-z_]+/)?([^/?]+)", u)
    if m:
        return "mmbiz:" + m.group(1)
    return u


def extract_images(body_html: str, full_html: str) -> list[dict]:
    """返回 [{url, source}]，顺序即正文中的出现顺序。"""
    images: list[dict] = []
    seen: set[str] = set()

    def add(u: str, source: str) -> None:
        u = normalize_img_url(u)
        if not is_real_image(u):
            return
        key = image_key(u)
        if key in seen:
            return
        seen.add(key)
        images.append({"url": u, "source": source})

    # a) 正文容器里的 <img>：data-src 才是真图，src 多为 1x1 占位
    for tag in IMG_TAG_RE.finditer(body_html):
        attrs = {k.lower(): v for k, v in TAG_ATTR_RE.findall(tag.group(0))}
        candidate = attrs.get("data-src") or attrs.get("data-original") or attrs.get("src") or ""
        add(candidate, "dom")

    # b) 贴图页专用：JS 变量里的 cdn_url
    for u in extract_picture_page_images(full_html):
        add(u, "picture_page_info_list")

    # c) 封面
    add(meta_content(full_html, "og:image"), "og:image")

    # d) 兜底：整页只认 mmbiz 图床
    if not images:
        for u in re.findall(r"https?://mmbiz\.qpic\.cn/[^\s\"'<>)\\]+", full_html):
            add(u, "fallback")

    return images


def build_text(body_html: str, images: list[dict]) -> str:
    """正文转文本，并在原图位置插入 [[图N]] 标记，保留图文对应关系。"""
    if not body_html:
        return ""
    frag = body_html
    counter = {"i": 0}

    def repl(m: re.Match) -> str:
        counter["i"] += 1
        return f"\n[[图{counter['i']}]]\n"

    frag = IMG_TAG_RE.sub(repl, frag)
    text = strip_tags(frag)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_article(html: str, url: str) -> dict:
    title = ""
    h1 = extract_balanced(html, "h1", "id", "activity-name")
    if h1:
        inner = re.search(r'class="[^"]*js_title_inner[^"]*"[^>]*>([\s\S]*?)</span>', h1)
        title = strip_tags(inner.group(1) if inner else h1)
    if not title:
        title = meta_content(html, "og:title")
    if not title:
        m = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.IGNORECASE)
        title = strip_tags(m.group(1)) if m else ""
    title = title.replace("\n", " ").strip()

    author = js_string_var(html, "nickname")
    if not author:
        m = re.search(r'id="js_name"[^>]*>([\s\S]*?)</', html)
        author = strip_tags(m.group(1)) if m else ""
    if not author:
        author = meta_content(html, "og:article:author")

    ts = js_string_var(html, "ct") or js_string_var(html, "create_time")
    published = ""
    if ts.isdigit() and len(ts) >= 10:
        published = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    if not published:
        published = meta_content(html, "article:published_time")

    body_html = extract_balanced(html, "div", "id", "js_content")
    if not body_html:
        body_html = extract_balanced(html, "div", "class", "rich_media_content")

    images = extract_images(body_html, html)
    text = build_text(body_html, images)

    appmsg_type = js_string_var(html, "appmsg_type")
    kind = "picture_message" if (extract_picture_page_images(html) or appmsg_type == "9") else "article"
    if not text:
        desc = js_string_var(html, "desc") or js_string_var(html, "js_image_desc")
        text = desc

    return {
        "url": url,
        "title": title,
        "author": author,
        "publishedAt": published,
        "kind": kind,
        "appmsgType": appmsg_type,
        "text": text,
        "textChars": len(text),
        "images": images,
        "imageCount": len(images),
    }


# --------------------------------------------------------------------------
# 图片下载
# --------------------------------------------------------------------------
def guess_ext(url: str, ctype: str) -> str:
    ctype = (ctype or "").lower()
    for key, ext in (("jpeg", ".jpg"), ("jpg", ".jpg"), ("png", ".png"), ("gif", ".gif"),
                     ("webp", ".webp"), ("svg", ".svg"), ("bmp", ".bmp")):
        if key in ctype:
            return ext
    m = re.search(r"wx_fmt=([a-z]+)", url)
    if m:
        return "." + ("jpg" if m.group(1) == "jpeg" else m.group(1))
    return ".jpg"


def download_image(url: str, dest_dir: str, index: int, timeout: int) -> dict:
    os.makedirs(dest_dir, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": REFERER,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    last_err = ""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                ctype = resp.headers.get("Content-Type", "")
            if not data or len(data) < 800:
                raise ValueError(f"图片过小（{len(data)}B）")
            ext = guess_ext(url, ctype)
            name = f"{index:02d}{ext}"
            path = os.path.join(dest_dir, name)
            with open(path, "wb") as f:
                f.write(data)
            return {"ok": True, "file": name, "bytes": len(data)}
        except Exception as e:
            last_err = str(e)
            time.sleep(0.4)
    return {"ok": False, "error": last_err}


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def slugify(title: str, fallback: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title or "").strip(" _")
    s = re.sub(r"\s+", " ", s)[:60]
    return s or fallback


def render_markdown(art: dict, downloaded: dict[str, dict] | None, channel: str) -> str:
    kind_label = "贴图（图片消息）" if art["kind"] == "picture_message" else "图文"
    lines = [
        f"# {art['title'] or '(无标题)'}",
        "",
        f"- 公众号：{art['author'] or '—'}",
        f"- 发布时间：{art['publishedAt'] or '—'}",
        f"- 原文：{art['url']}",
        f"- 类型：{kind_label} · 正文 {art['textChars']} 字 · 图片 {art['imageCount']} 张",
        f"- 读取通道：{channel}",
        "",
        "## 正文",
        "",
        art["text"] or "_(无文字内容)_",
        "",
    ]
    if art["images"]:
        lines += ["## 图片", ""]
        for i, img in enumerate(art["images"], 1):
            got = (downloaded or {}).get(img["url"])
            if got and got.get("ok"):
                lines.append(f"{i}. `images/{got['file']}`（{got['bytes'] // 1024} KB）")
            else:
                lines.append(f"{i}. {img['url']}")
        lines.append("")
    return "\n".join(lines)


def run_one(url: str, args, out_root: str | None) -> dict:
    quiet = args.quiet
    log(f"[抓取] {url}", quiet)

    if args.channel == "file":
        with open(url, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
        channel, note = "local-file", f"{len(html)}B"
    else:
        html, channel, note = fetch_any(url, args.channel, args.timeout, quiet)

    if not html:
        log(f"[失败] 抓不到有效页面：{note}", quiet)
        return {"ok": False, "url": url, "stage": "fetch", "error": note, "channel": channel}

    log(f"[解析] 通道 {channel} · {note}", quiet)
    art = parse_article(html, url)

    if not art["title"] and art["imageCount"] == 0 and art["textChars"] < 10:
        log("[失败] 页面里既没有标题也没有图片，判定为解析失败", quiet)
        return {"ok": False, "url": url, "stage": "parse", "error": "无标题/无正文/无图片", "channel": channel}

    if art["textChars"] < 120 and art["imageCount"] == 0:
        log(f"[警告] 正文只有 {art['textChars']} 字且无图，可能仍是异常页", quiet)

    downloaded: dict[str, dict] = {}
    out_dir = ""
    if out_root and not args.no_download:
        out_dir = os.path.join(out_root, slugify(art["title"], f"article_{int(time.time())}"))
        img_dir = os.path.join(out_dir, "images")
        os.makedirs(out_dir, exist_ok=True)
        log(f"[下载] {art['imageCount']} 张图 -> {img_dir}", quiet)
        for i, img in enumerate(art["images"], 1):
            res = download_image(img["url"], img_dir, i, args.timeout)
            downloaded[img["url"]] = res
            if res.get("ok"):
                log(f"  · 第 {i} 张 OK {res['bytes'] // 1024}KB", quiet)
            else:
                log(f"  · 第 {i} 张失败：{res.get('error', '')[:70]}", quiet)
    elif out_root:
        out_dir = os.path.join(out_root, slugify(art["title"], f"article_{int(time.time())}"))
        os.makedirs(out_dir, exist_ok=True)

    md = render_markdown(art, downloaded, channel)

    if out_dir:
        with open(os.path.join(out_dir, "article.md"), "w", encoding="utf-8") as f:
            f.write(md)
        payload = dict(art)
        payload["images"] = [
            {**img, "local": (downloaded.get(img["url"]) or {}).get("file", "")}
            for img in art["images"]
        ]
        payload["channel"] = channel
        payload["fetchedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(out_dir, "article.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        if args.keep_html:
            with open(os.path.join(out_dir, "raw.html"), "w", encoding="utf-8") as f:
                f.write(html)
        log(f"[完成] {out_dir}", quiet)

    ok_imgs = sum(1 for v in downloaded.values() if v.get("ok"))
    return {
        "ok": True,
        "url": url,
        "channel": channel,
        "outDir": out_dir,
        "markdown": md,
        "article": art,
        "downloaded": ok_imgs,
        "downloadFailed": len(downloaded) - ok_imgs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="读取微信公众号文章 / 贴图")
    ap.add_argument("targets", nargs="+", help="文章链接，或本地 html 路径（配合 --channel file）")
    ap.add_argument("--out", default="", help="输出目录；给了才会下载图片")
    ap.add_argument("--no-download", action="store_true", help="只解析不下载图片")
    ap.add_argument("--channel", default="auto", choices=["auto", "http", "proxy", "browser", "file"])
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    ap.add_argument("--keep-html", action="store_true", help="保留原始 HTML")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    results = [run_one(t, args, args.out or None) for t in args.targets]

    if args.json:
        print(json.dumps(
            [{k: v for k, v in r.items() if k != "markdown"} for r in results],
            ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r["ok"]:
                print(r["markdown"])
            else:
                print(f"> 读取失败：{r['url']}\n> 阶段：{r.get('stage')} · 原因：{r.get('error')}\n")

    if all(not r["ok"] for r in results):
        stages = {r.get("stage") for r in results}
        return 3 if stages == {"fetch"} else 4
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py —— 不联网的核心逻辑自测

跑两个离线样本，断言标题/作者/时间/图片/正文标记都对。
任何一次改了解析逻辑，先跑这个，不用等网络。

  python scripts/selftest.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wx_read  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(os.path.dirname(HERE), "fixtures")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def test_picture_page():
    path = os.path.join(FIXTURES, "picture_page.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    art = wx_read.parse_article(html, path)

    assert art["title"] == "贴图样本：三张图的图片消息", art["title"]
    assert art["author"] == "康哥笔记", art["author"]
    assert art["publishedAt"].startswith("2026-"), art["publishedAt"]
    assert art["kind"] == "picture_message", art["kind"]
    # DOM 2 张 + cdn 里独有 1 张 + 封面 1 张；/640 与 /0 属同一张图，按 ID 去重
    # emoji 必须被过滤
    assert art["imageCount"] == 4, art["imageCount"]
    urls = [i["url"] for i in art["images"]]
    assert not any("res.wx.qq.com" in u for u in urls), "emoji 没被过滤"
    assert any("PIC3" in u for u in urls), "cdn_url 里的图没抓到"
    assert art["images"][0]["url"].startswith("https://"), "http 图没升级成 https"
    assert "[[图1]]" in art["text"], "正文里没有图片标记"
    return f"贴图页 · {art['imageCount']} 图 · {art['textChars']} 字"


@case
def test_article_page():
    path = os.path.join(FIXTURES, "article_page.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    art = wx_read.parse_article(html, path)

    assert art["title"] == "图文样本：普通长文", art["title"]
    assert art["kind"] == "article", art["kind"]
    assert art["imageCount"] == 1, art["imageCount"]
    # 嵌套 div 配平：第二段必须还在正文里
    assert "嵌套段落" in art["text"], "嵌套 div 被截断了"
    assert "第二段正文" in art["text"], "图片后面的文字丢了"
    assert "[[图1]]" in art["text"]
    return f"图文页 · {art['imageCount']} 图 · 嵌套 div 配平正常"


@case
def test_risk_markers():
    for bad in ("环境异常", "内容已被发布者删除", "请在微信打开"):
        html = f'<html><body><div class="weui-msg__title">{bad}</div></body></html>'
        ok, why = wx_read.check_valid(html)
        assert not ok, f"风控页「{bad}」没被识别"
        assert bad in why, why
    return "风控页识别正常（3 种标记）"


@case
def test_empty_is_invalid():
    ok, why = wx_read.check_valid("<html><body>hello</body></html>")
    assert not ok, "空壳页居然判成有效"
    return f"空壳页被判无效：{why}"


def main() -> int:
    failed = 0
    for fn in CASES:
        name = fn.__name__.replace("test_", "")
        try:
            detail = fn()
            print(f"  PASS  {name.ljust(16)} {detail}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name.ljust(16)} {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERR   {name.ljust(16)} {type(e).__name__}: {e}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

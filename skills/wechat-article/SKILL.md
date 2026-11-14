---
name: wechat-article
description: 读取微信公众号文章和贴图（图片消息），提取标题、公众号名、发布时间、正文、全部图片并下载到本地。当用户粘贴 mp.weixin.qq.com 链接要求"读取/解析/下载/保存/转存公众号文章""公众号贴图读不到""把这篇公众号存成 markdown"时使用。支持直连、本地代理、Chrome 渲染三条通道自动降级。
---

# 微信公众号文章 / 贴图读取

把 `https://mp.weixin.qq.com/s/...` 读成结构化内容（Markdown + JSON + 本地图片），供改写、归档、分发、做素材库。

> 很多同类脚本只处理普通图文的纯文本，遇到贴图（图片消息）页会拿到 0 张图。本技能两种页面都扫。

## 一条命令

```bash
python <工具箱>/ks.py run wechat-article "<文章链接>"
```

或直接调脚本：

```bash
python skills/wechat-article/scripts/wx_read.py "<文章链接>" --out <输出目录>
```

- **不给 `--out`**：Markdown 直接打到 stdout，适合让模型继续读写，不落盘。
- **给 `--out`**：建 `<输出目录>/<标题>/`，写 `article.md`、`article.json`，图片下到 `images/01.jpg…`。

## 为什么它比"直接 fetch 一下"可靠

公众号有两种页面，写法完全不同，这也是大多数工具"文章能读、贴图读不了"的原因：

| 页面类型 | 正文位置 | 图片位置 |
|---|---|---|
| 普通图文 | `<div id="js_content">` | 正文里的 `img[data-src]`（`src` 是 1×1 占位图） |
| 贴图 / 图片消息 | 同上，但文字往往只有一句描述 | **不在 DOM 里**，在 JS 变量 `picture_page_info_list` 的 `cdn_url` 字段 |

本技能两条都扫：先取 DOM 里的 `data-src`，再全局扫 `cdn_url` 合并去重，所以贴图页也能拿到全部图片。

另外三件常被忽略的事，这里都处理了：

1. **图片防盗链**：`mmbiz.qpic.cn` 校验 Referer，直接把链接塞进 `<img>` 会 403。脚本下载时带 `Referer: https://mp.weixin.qq.com/`。
2. **风控页**：命中"环境异常 / 请在微信打开 / 访问过于频繁 / 内容已被发布者删除"时不当作正文，直接换通道重试。
3. **图文对应**：正文里的图片位置会替换成 `[[图1]] [[图2]]` 标记，顺序与 `images/` 下的文件名一致，改写时不会串图。

## 通道选择

`--channel auto`（默认）按顺序试，任一通道拿到有效正文就停：

1. `http` —— 直连，带完整浏览器头，多数公开文章一次就过
2. `proxy` —— 依次试本机 `127.0.0.1:7897 / 7890 / 1080 / 2022`
3. `browser` —— `chrome --headless=new --dump-dom` 渲染后取 DOM，能过一部分 JS 校验

手动指定：

```bash
--channel http      # 只直连，最快
--channel browser   # 直连被判风控时用
--channel file      # 解析本地已保存的 html（离线/复算）
```

## 输出字段

`article.json`：`title` `author` `publishedAt` `kind`（`article` / `picture_message`）`text` `textChars` `images[{url,source,local}]` `imageCount` `channel` `fetchedAt`。

## 判读结果

- 退出码 `0` 成功；`3` 抓取阶段失败（网络/风控）；`4` 解析阶段失败（页里没正文也没图）。
- 正文 < 120 字且 0 张图 → 视为异常页，不要当内容用。
- `kind: picture_message` 时正文短是正常的，图片才是主体。

## 边界

- 只读**公开**文章。不绕过登录、验证码、付费墙、删除或违规页。
- 不读公众号后台、私信、Cookie、会话；不带任何账号凭据。
- 抓不到评论（微信不对外公开），任何工具都拿不到。
- 频率别太快：连续大批量请加 `--timeout` 并间隔几秒，否则会触发风控，反而更慢。

## 批量

```bash
python scripts/wx_read.py url1 url2 url3 --out ./dump
```

每篇一个子目录，互不干扰。

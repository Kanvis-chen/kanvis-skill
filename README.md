# kanvis-skill

[![selftest](https://github.com/Kanvis-chen/kanvis-skill/actions/workflows/selftest.yml/badge.svg)](https://github.com/Kanvis-chen/kanvis-skill/actions/workflows/selftest.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

一个能稳定读微信公众号文章和贴图（图片消息）的技能工具箱。零第三方依赖，纯 Python 标准库。

A zero-dependency toolkit for reading WeChat Official Account articles and picture-message posts (`mp.weixin.qq.com`), built to be called reliably by AI coding agents.

```bash
python ks.py run wechat-article "https://mp.weixin.qq.com/s/xxxxxxxx"
```

输出 Markdown、JSON 和下载好的图片。不需要登录微信，不需要扫码，不碰 Cookie。

---

## 为什么需要它

随便 `curl` 一下公众号链接，多数时候是能拿到 HTML 的 —— 所以很多工具以为自己搞定了。真正的问题是三个具体的地方：

**1. 贴图的图片根本不在 DOM 里。**
贴图（图片消息）页的图片藏在 JS 变量 `picture_page_info_list` 的 `cdn_url` 字段上，正文 DOM 里只有文字说明。只扫 `<img>` 的解析器在这类页面上永远只能拿到 0~2 张图，剩下全丢。这是"文章能读、贴图读不到"的直接原因。

**2. 被风控时会拿到一个看起来正常的假页面。**
`环境异常，请完成验证` / `请在微信客户端打开链接` / `访问过于频繁` —— 这些页返回 200，也有 HTML 结构，工具照常解析，于是输出一堆导航文字或直接空。必须显式判定，不能只要 `status == 200` 就当成功。

**3. 图片防盗链。**
`mmbiz.qpic.cn` 校验 `Referer`，把 URL 直接塞进 `<img>` 或直接下载会 403。必须带 `Referer: https://mp.weixin.qq.com/` 请求。

这三个问题这里都处理了，并且每个都有离线自测覆盖 —— 不联网就能验证解析逻辑有没有被改坏。

## 快速开始

```bash
git clone https://github.com/Kanvis-chen/kanvis-skill.git
cd kanvis-skill

python ks.py list                                  # 看有哪些技能
python ks.py info wechat-article                   # 看用法
python ks.py check                                 # 环境自检
python ks.py run wechat-article "<文章链接>"        # 读一篇
python ks.py run wechat-article "<文章链接>" --out ./dump   # 连图一起存
```

要求 Python 3.8+。没有 pip install 这一步。

### 输出长什么样

不给 `--out` 时，Markdown 直接打到 stdout（适合让模型接着处理，不落盘）：

```markdown
# 标题

> 公众号：某某笔记 · 2026-11-14

正文……[[图1]]……正文……[[图2]]
```

给 `--out ./dump` 时落成目录：

```
dump/
└── 标题/
    ├── article.md      正文，图片位置是 [[图1]] [[图2]] 标记
    ├── article.json    结构化字段
    └── images/
        ├── 01.jpg
        └── 02.jpg
```

`[[图N]]` 的编号和 `images/` 下的文件名一一对应，改写、搬运时不会串图。

### JSON 字段

`title` `author` `publishedAt` `kind`（`article` / `picture_message`）`text` `textChars` `images[{url, source, local}]` `imageCount` `channel` `fetchedAt`

## 三条通道，自动降级

`--channel auto` 是默认值，按顺序试，任一通道拿到**有效正文**就停：

| 通道 | 做法 | 什么时候用 |
|---|---|---|
| `http` | 直连，带完整浏览器请求头 | 多数公开文章一次就过（实测 200 / ~240ms） |
| `proxy` | 依次试本机 `127.0.0.1:7897 / 7890 / 1080 / 2022` | 本机有代理（Clash / V2Ray 默认端口）时 |
| `browser` | `chrome --headless=new --dump-dom` 渲染后取 DOM | 前两条都被判风控时 |
| `file` | 解析本地已保存的 html | 离线复算、调解析逻辑 |

手动指定：`--channel http` / `--channel browser` / `--channel file`。

## 当成 Agent Skill 用

这个工具箱的形态是"一个入口 + 一堆技能目录"，任何能读文件、能跑命令的助手里都能直接用。

**挂载到 WorkBuddy / 兼容 `~/.workbuddy/skills/` 的助手：**

```bash
python ks.py install wechat-article     # 在 ~/.workbuddy/skills/ 建目录联接
python ks.py install --all              # 全部挂载
```

联接指向仓库里的真源，改文件即时生效，不会出现"两个版本哪个对"。

**在 Codex / Claude Code 里：** 让它先读 `AGENTS.md` 和 `skills/<name>/SKILL.md`。`AGENTS.md` 里写明了硬规矩 —— 比如**不要用 `curl` 直接抓公众号**，要走这个脚本，否则会拿到风控页还当成功。

## 加一个新技能

```bash
python ks.py new my-skill      # 从 skills/_template 复制骨架
```

填三样东西：

1. `skills/my-skill/SKILL.md` —— frontmatter 的 `description` 要写进用户会说的原话（触发词），助手靠它决定要不要用。
2. `skills/my-skill/skill.json` —— `entry` 指向入口脚本，`summary` 显示在 `ks list`。
3. `skills/my-skill/scripts/` —— 干活的地方。

`ks.py` 扫描目录自动发现，不用注册、不用改代码。

```bash
python ks.py manifest    # 生成 manifest.json（给外部程序读的索引，已 gitignore）
```

## 目录结构

```
kanvis-skill/
├── ks.py                   统一入口：list / info / run / new / install / check / manifest
├── AGENTS.md               给 AI 助手读的硬规矩
├── skills/
│   ├── _template/          新技能脚手架
│   └── wechat-article/
│       ├── SKILL.md        触发词与用法（给助手读）
│       ├── skill.json      元数据
│       ├── scripts/
│       │   ├── wx_read.py  抓取 + 解析 + 图片下载
│       │   └── selftest.py 离线自测（4 个用例，不需要网络）
│       └── fixtures/       离线测试用的真实页面样本
└── README.md
```

## 自测

```bash
cd skills/wechat-article
python scripts/selftest.py
```

4 个用例，纯离线：贴图页取全 4 张图、图文页嵌套 `div` 不被截断、3 种风控标记都能识别、空壳页判无效。改解析逻辑先跑这个，再联网。

## 边界

- 只读**公开**文章。不绕过登录、验证码、付费墙、已删除或违规页。
- 不读公众号后台、私信、Cookie、会话，不带任何账号凭据。
- 评论抓不到（微信不对外公开），任何工具都拿不到。
- 别高频批量：连续抓请加 `--timeout` 并间隔几秒，否则触发风控反而更慢。
- 解析依赖页面结构，微信改版可能失效。失效时用 `--keep-html` 存下原始 HTML，改 `wx_read.py` 的取数位置，顺手补一个 fixture。

## License

MIT

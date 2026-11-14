# AGENTS.md · kanvis-skill 工具箱（给 Codex / 其他助手）

本目录是 Kanvis 的自研技能工具箱。**入口只有一个：`ks.py`**，不要逐个去猜 `skills/` 下的脚本参数。

## 先看有什么

```bash
python ks.py list              # 技能清单
python ks.py info <name>       # 参数、示例、SKILL.md 路径
```

## 怎么用

```bash
python ks.py run <name> [参数...]     # 参数原样透传给技能，带 -- 也行
```

例：读一篇公众号（正文 + 全部贴图 + 图片下载）

```bash
python ks.py run wechat-article "https://mp.weixin.qq.com/s/xxxx" --out ./dump
```

不给 `--out` 时直接把 Markdown 打到 stdout，适合你读了继续改写，不落盘。

## 规矩

1. **读代码可以，改之前先问**。`skills/<name>/` 是技能真源，`~/.workbuddy/skills/<name>` 只是指向它的目录联接，改联接无效。
2. **加技能用脚手架**：`python ks.py new <name>`，别手搓目录。
3. **改了解析逻辑先跑自测**：`python skills/wechat-article/scripts/selftest.py`（离线，不联网，秒回）。
4. **不要靠 `curl` 直接抓公众号**：会被风控返回"环境异常"，然后你会误判成"文章不存在"。走 `wechat-article`，它有多通道降级。
5. 技能只做被授权的事：不读 Cookie / 凭据，不绕登录与验证码。

## 当前技能

| 技能 | 用途 |
|---|---|
| `wechat-article` | 公众号图文与贴图读取（正文 / 图片 / 作者 / 时间），图片带 Referer 下载 |

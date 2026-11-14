#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ks.py —— Kanvis Skill 工具箱统一入口

一个盒子装一堆技能，每个技能 = skills/<name>/ 目录（SKILL.md + skill.json + scripts/）。
加新技能不用改这里：丢一个目录进 skills/ 就自动出现在 list 里。

用法
----
  python ks.py list                 列出所有技能
  python ks.py info <name>          看一个技能的详情和示例
  python ks.py run <name> [args...] 运行技能（参数原样透传）
  python ks.py new <name>           按模板新建一个技能骨架
  python ks.py install <name>       软链到 ~/.workbuddy/skills/，让 WorkBuddy/Codex 全局可调用
  python ks.py install --all        全部挂载
  python ks.py check                环境自检（python/chrome/代理/网络）
  python ks.py manifest             重新生成 manifest.json
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(ROOT, "skills")
MANIFEST = os.path.join(ROOT, "manifest.json")
INSTALL_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills")
TEMPLATE_DIR = os.path.join(SKILLS_DIR, "_template")

RUNTIME = {
    ".py": [sys.executable],
    ".mjs": ["node"],
    ".js": ["node"],
    ".ps1": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"],
    ".bat": ["cmd", "/c"],
    ".cmd": ["cmd", "/c"],
}


# --------------------------------------------------------------------------
# 技能发现
# --------------------------------------------------------------------------
def parse_frontmatter(path: str) -> dict:
    meta = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(4000)
    except OSError:
        return meta
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end < 0:
        return meta
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


def load_skill(name: str) -> dict | None:
    path = os.path.join(SKILLS_DIR, name)
    if not os.path.isdir(path) or name.startswith("_"):
        return None
    info = {
        "name": name,
        "path": path,
        "version": "0.0.0",
        "summary": "",
        "entry": "",
        "tags": [],
        "examples": [],
    }
    sj = os.path.join(path, "skill.json")
    if os.path.exists(sj):
        try:
            with open(sj, "r", encoding="utf-8") as f:
                info.update(json.load(f))
        except Exception as e:
            info["summary"] = f"(skill.json 解析失败: {e})"
    md = os.path.join(path, "SKILL.md")
    if os.path.exists(md):
        fm = parse_frontmatter(md)
        info["summary"] = info.get("summary") or fm.get("description", "")
        info["name"] = fm.get("name", name) or name
    return info


def all_skills() -> list[dict]:
    if not os.path.isdir(SKILLS_DIR):
        return []
    out = []
    for entry in sorted(os.listdir(SKILLS_DIR)):
        if entry.startswith("_") or entry.startswith("."):
            continue
        s = load_skill(entry)
        if s:
            out.append(s)
    return out


def resolve_entry(skill: dict) -> str:
    entry = skill.get("entry") or ""
    if entry:
        p = os.path.join(skill["path"], entry)
        if os.path.exists(p):
            return p
    # 没登记 entry 就在 scripts/ 里挑第一个可执行文件
    scripts = os.path.join(skill["path"], "scripts")
    if os.path.isdir(scripts):
        for f in sorted(os.listdir(scripts)):
            if os.path.splitext(f)[1].lower() in RUNTIME:
                return os.path.join(scripts, f)
    return ""


def command_for(entry_path: str) -> list[str]:
    ext = os.path.splitext(entry_path)[1].lower()
    base = RUNTIME.get(ext)
    if not base:
        return []
    if ext in (".bat", ".cmd", ".ps1"):
        return base + [entry_path]
    return base + ["-u", entry_path] if ext == ".py" else base + [entry_path]


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_list(_args: list[str]) -> int:
    skills = all_skills()
    if not skills:
        print("skills/ 下还没有技能。用 `python ks.py new <name>` 建一个。")
        return 0
    width = max(len(s["name"]) for s in skills) + 2
    print(f"{'技能'.ljust(width)}版本      说明")
    print("-" * 78)
    for s in skills:
        summary = (s["summary"] or "").split("。")[0][:44]
        print(f"{s['name'].ljust(width)}{s['version'].ljust(10)}{summary}")
    print("-" * 78)
    print(f"共 {len(skills)} 个 · 工具箱：{ROOT}")
    return 0


def cmd_info(args: list[str]) -> int:
    if not args:
        print("用法：python ks.py info <name>")
        return 2
    s = load_skill(args[0])
    if not s:
        print(f"没有这个技能：{args[0]}")
        return 2
    print(f"名称     {s['name']}")
    print(f"版本     {s['version']}")
    print(f"说明     {s['summary']}")
    if s["tags"]:
        print(f"标签     {', '.join(s['tags'])}")
    print(f"目录     {s['path']}")
    entry = resolve_entry(s)
    print(f"入口     {entry or '(未找到可执行入口)'}")
    if s["examples"]:
        print("\n示例：")
        for e in s["examples"]:
            print(f"  {e}")
    md = os.path.join(s["path"], "SKILL.md")
    if os.path.exists(md):
        print(f"\n详细说明：{md}")
    return 0


def cmd_run(args: list[str]) -> int:
    if not args:
        print("用法：python ks.py run <name> [args...]")
        return 2
    name, rest = args[0], args[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    s = load_skill(name)
    if not s:
        print(f"没有这个技能：{name}")
        return 2
    entry = resolve_entry(s)
    if not entry:
        print(f"{name} 没有可执行的入口文件（skill.json 的 entry 指向了不存在的文件）")
        return 2
    cmd = command_for(entry)
    if not cmd:
        print(f"不认识的入口类型：{entry}")
        return 2
    try:
        proc = subprocess.run(cmd + rest, cwd=s["path"])
        return proc.returncode
    except FileNotFoundError as e:
        print(f"运行时缺失：{e}")
        return 2
    except KeyboardInterrupt:
        return 130


def cmd_new(args: list[str]) -> int:
    if not args:
        print("用法：python ks.py new <name>")
        return 2
    name = args[0]
    dest = os.path.join(SKILLS_DIR, name)
    if os.path.exists(dest):
        print(f"已存在：{dest}")
        return 2
    if not os.path.isdir(TEMPLATE_DIR):
        os.makedirs(os.path.join(dest, "scripts"), exist_ok=True)
    else:
        shutil.copytree(TEMPLATE_DIR, dest)
    # 替换占位符
    for base, _dirs, files in os.walk(dest):
        for f in files:
            p = os.path.join(base, f)
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            text = text.replace("{{NAME}}", name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
    print(f"已创建：{dest}")
    print(f"下一步：编辑 {os.path.join(dest, 'SKILL.md')} 和 scripts/ 下的入口脚本")
    return 0


def _link(src: str, dst: str) -> tuple[bool, str]:
    if os.path.exists(dst) or os.path.islink(dst):
        return False, "目标已存在（如需重装先手动删除）"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        if platform.system() == "Windows":
            # cmd 的 mklink 输出是 GBK，必须显式解码，否则会抛 UnicodeDecodeError
            r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode == 0:
                return True, "junction"
            r2 = subprocess.run(["cmd", "/c", "mklink", "/D", dst, src],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            if r2.returncode == 0:
                return True, "symlink"
            return False, ((r.stderr or r2.stderr or "").strip()[:80] or "mklink 失败")
        os.symlink(src, dst, target_is_directory=True)
        return True, "symlink"
    except OSError as e:
        return False, str(e)[:80]


def cmd_install(args: list[str]) -> int:
    if not args:
        print("用法：python ks.py install <name> | --all")
        return 2
    targets = [s["name"] for s in all_skills()] if args[0] == "--all" else [args[0]]
    ok = fail = 0
    for name in targets:
        s = load_skill(name)
        if not s:
            print(f"[跳过] 没有这个技能：{name}")
            fail += 1
            continue
        dst = os.path.join(INSTALL_DIR, name)
        success, how = _link(s["path"], dst)
        if success:
            print(f"[OK] {name} -> {dst}（{how}）")
            ok += 1
        else:
            print(f"[失败] {name}：{how}")
            fail += 1
    print(f"\n完成：成功 {ok}，失败 {fail}。挂载目录：{INSTALL_DIR}")
    if ok:
        print("WorkBuddy 需要重启会话才会加载新技能；Codex 直接读 SKILL.md 即可。")
    return 0 if fail == 0 else 1


def _port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_check(_args: list[str]) -> int:
    print("环境自检")
    print("-" * 60)
    print(f"Python      {sys.version.split()[0]}  ({sys.executable})")
    node = shutil.which("node")
    print(f"Node        {node or '未安装（只影响 .mjs/.js 技能）'}")

    chrome = ""
    for c in [
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]:
        if c and os.path.exists(c):
            chrome = c
            break
    print(f"浏览器      {chrome or '未找到 Chrome/Edge（浏览器回退通道不可用）'}")

    proxied = [p for p in (7897, 7890, 1080, 2022) if _port_open("127.0.0.1", p)]
    print(f"本地代理    {', '.join(map(str, proxied)) if proxied else '未监听（直连模式）'}")

    try:
        req = urllib.request.Request(
            "https://mp.weixin.qq.com/",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=8) as r:
            print(f"公众号可达  HTTP {r.status} · {int((time.time() - t0) * 1000)}ms")
    except Exception as e:
        print(f"公众号可达  失败：{str(e)[:70]}")

    skills = all_skills()
    print(f"技能数量    {len(skills)}")
    installed = [s["name"] for s in skills if os.path.isdir(os.path.join(INSTALL_DIR, s["name"]))
                 or os.path.islink(os.path.join(INSTALL_DIR, s["name"]))]
    print(f"已挂载      {', '.join(installed) if installed else '无'}")
    return 0


def cmd_manifest(_args: list[str]) -> int:
    data = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "root": ROOT,
        "skills": [
            {k: s[k] for k in ("name", "version", "summary", "entry", "tags", "examples")}
            | {"path": os.path.relpath(s["path"], ROOT)}
            for s in all_skills()
        ],
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已写入 {MANIFEST}（{len(data['skills'])} 个技能）")
    return 0


COMMANDS = {
    "list": cmd_list,
    "ls": cmd_list,
    "info": cmd_info,
    "run": cmd_run,
    "new": cmd_new,
    "install": cmd_install,
    "check": cmd_check,
    "doctor": cmd_check,
    "manifest": cmd_manifest,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"未知命令：{cmd}\n")
        print(__doc__)
        return 2
    return COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())

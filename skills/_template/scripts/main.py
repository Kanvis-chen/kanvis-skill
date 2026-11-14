#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""{{NAME}} —— 技能入口。保持零依赖或把依赖写进 SKILL.md。"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run(target: str) -> dict:
    # 真正干活的地方：返回结构化结果，不要直接 print
    return {"target": target, "ok": True}


def selftest() -> int:
    r = run("dummy")
    assert r["ok"], "自测失败"
    print("selftest ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="{{NAME}}")
    ap.add_argument("target", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.target:
        ap.print_help()
        return 2
    print(run(args.target))
    return 0


if __name__ == "__main__":
    sys.exit(main())

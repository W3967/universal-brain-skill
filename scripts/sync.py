#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal Brain 参考同步工具。

只依赖标准库。三个命令：

    python sync.py index                 重建 index.json
    python sync.py check                 只检查，不写任何文件
    python sync.py snapshot --out dist   生成快照包

选项：
    --root PATH        大脑根目录（默认当前目录）
    --max-chars N      核心记忆字数上限（默认 1200，超限不出包）
    --ban a,b,c        L1 文件额外禁入词

设计原则：fail-closed。任何一项检查不通过，不写任何文件，退出码 1。

检查还要求每个文件声明 owner（写入归属），缺失直接失败；index.json 每条记入 owner。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SKIP_DIRS = {"_interface", ".git", "dist", "__pycache__", "node_modules", ".obsidian"}
SKIP_FILES = {"_template.md"}
CORE_SOURCES = ("MEMORY.md", "profile.md", "preferences.md")

# L1 准入的默认禁入模式 —— 只管"可枚举的那一半"。
# 组合才成立的识别性信息（城市+学校+届别+年龄）抓不到，那部分靠写入前的人工分类。
DEFAULT_BANS = [
    (r"BEGIN[ A-Z]*PRIVATE KEY", "私钥块"),
    (r"\b\d{17}[\dXx]\b", "疑似身份证号"),
    (r"\b\d{16,19}\b", "疑似长串卡号/账号"),
]


def parse_frontmatter(text: str):
    """返回 (meta, body)。没有 frontmatter 时 meta 为空。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    meta = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, body


def summary_of(body: str, limit: int = 80) -> str:
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ">", "|", "```")):
            continue
        s = re.sub(r"^[-*+]\s+", "", s)
        s = s.replace("**", "").replace("`", "").strip()
        if not s:
            continue
        return re.sub(r"\s+", " ", s)[:limit]
    return ""


def collect(root: Path):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            text = p.read_text(encoding="utf-8", errors="replace")
            meta, body = parse_frontmatter(text)
            files.append(
                {
                    "path": rel,
                    "level": meta.get("level", "?"),
                    "owner": meta.get("owner", ""),
                    "updated": meta.get("updated", ""),
                    "source": meta.get("source", ""),
                    "summary": meta.get("summary") or summary_of(body),
                }
            )
    files.sort(key=lambda x: x["path"])
    return files


def check_l1(root: Path, files, extra_bans):
    patterns = list(DEFAULT_BANS)
    for b in extra_bans:
        b = b.strip()
        if b:
            patterns.append((re.escape(b), f"自定义禁入词「{b}」"))

    problems = []
    for f in files:
        if f["level"] != "L1":
            continue
        text = (root / f["path"]).read_text(encoding="utf-8", errors="replace")
        _, body = parse_frontmatter(text)
        for pat, label in patterns:
            for m in re.finditer(pat, body):
                # 只报位置与模式名，绝不输出命中内容 —— 否则拦截日志本身成了泄露点
                line_no = body.count("\n", 0, m.start()) + 1
                problems.append(f"{f['path']}:{line_no}: 命中{label}")
    return problems


def git_commit(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()[:12]
    except Exception:
        pass
    return ""


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_index(root: Path, files):
    return {
        "schema": "universal-brain/index/v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "由 scripts/sync.py 自动重建，禁止手工维护。",
        "files": files,
    }


def core_text(root: Path) -> str:
    parts = []
    for name in CORE_SOURCES:
        p = root / name
        if not p.exists():
            continue
        _, body = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        parts.append(f"## {name}\n\n{body.strip()}")
    return "\n\n".join(parts).strip()


def reference_files(root: Path):
    out = []
    proj_dir = root / "projects"
    if proj_dir.exists():
        proj = [p for p in sorted(proj_dir.glob("*.md")) if p.name != "_template.md"]
        out.extend(proj[:2])
    people = root / "people" / "INDEX.md"
    if people.exists():
        out.append(people)
    return out


def build_snapshot(root: Path, files, out_dir: Path, max_chars: int):
    core = core_text(root)
    core_chars = len(re.sub(r"\s", "", core))

    if core_chars > max_chars:
        return None, f"核心记忆 {core_chars} 字，超过上限 {max_chars} 字，不出包。"

    if not core:
        return None, "核心记忆为空（缺 MEMORY.md / profile.md / preferences.md），不出包。"

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "brain-memory.md").write_text(core + "\n", encoding="utf-8")

    ref_dir = out_dir / "brain-reference"
    ref_dir.mkdir(exist_ok=True)
    ref_entries = []
    for p in reference_files(root):
        flat = p.relative_to(root).as_posix().replace("/", "-")
        text = p.read_text(encoding="utf-8", errors="replace")
        meta, _ = parse_frontmatter(text)
        (ref_dir / flat).write_text(text, encoding="utf-8")
        ref_entries.append(
            {"path": f"brain-reference/{flat}", "level": meta.get("level", "?"), "owner": meta.get("owner", ""), "summary": ""}
        )

    manifest = {
        "version": "1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_commit": git_commit(root),
        "core_chars": core_chars,
        "files": [
            {"path": "brain-memory.md", "level": "L1", "summary": "核心记忆合并版"}
        ]
        + ref_entries,
    }
    write_json(out_dir / "brain-manifest.json", manifest)
    return manifest, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Universal Brain 参考同步工具")
    ap.add_argument("cmd", choices=["index", "check", "snapshot"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--max-chars", type=int, default=1200)
    ap.add_argument("--ban", default="")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"根目录不存在：{root}", file=sys.stderr)
        return 1

    files = collect(root)
    if not files:
        print("没有找到任何 Markdown 文件。确认 --root 指向大脑根目录。", file=sys.stderr)
        return 1

    # 未标级别的文件视为问题 —— 每个 Markdown 都必须有 level
    no_level = [f["path"] for f in files if f["level"] not in ("L1", "L2", "L3")]
    if no_level:
        print("以下文件缺少合法 level 头信息：", file=sys.stderr)
        for p in no_level:
            print(f"  {p}", file=sys.stderr)
        print("\n未写入任何文件。", file=sys.stderr)
        return 1

    # 没有摘要的文件拒绝入索引 —— 索引里留空摘要等于没有索引
    no_summary = [f["path"] for f in files if not f["summary"].strip()]
    if no_summary:
        print("以下文件没有可用摘要，拒绝入索引：", file=sys.stderr)
        for p in no_summary:
            print(f"  {p}", file=sys.stderr)
        print("补法：在 frontmatter 加 summary: 字段，或正文首段写一句概述。", file=sys.stderr)
        print("\n未写入任何文件。", file=sys.stderr)
        return 1

    # 没有 owner 声明的文件视为问题 —— 归属不明的文件不许进索引
    no_owner = [f["path"] for f in files if not f["owner"].strip()]
    if no_owner:
        print("以下文件缺少 owner 头信息（写入归属）：", file=sys.stderr)
        for p in no_owner:
            print(f"  {p}", file=sys.stderr)
        print("补法：在 frontmatter 加 owner: main-writer（默认主写者），或指定其他接入方。", file=sys.stderr)
        print("\n未写入任何文件。", file=sys.stderr)
        return 1

    problems = check_l1(root, files, [x for x in args.ban.split(",") if x.strip()])
    if problems:
        print("L1 准入拦截 —— 以下内容不该出现在 L1 文件里：", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("\n未写入任何文件。", file=sys.stderr)
        return 1

    if args.cmd == "check":
        print(f"检查通过：{len(files)} 个文件，L1 准入与 owner 声明无问题。")
        return 0

    if args.cmd == "index":
        write_json(root / "index.json", build_index(root, files))
        print(f"已重建 index.json：{len(files)} 条。")
        return 0

    manifest, err = build_snapshot(root, files, Path(args.out), args.max_chars)
    if err:
        print(err, file=sys.stderr)
        print("未写入任何文件。", file=sys.stderr)
        return 1
    print(f"已生成快照包：{args.out}（核心 {manifest['core_chars']} 字，{len(manifest['files'])} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

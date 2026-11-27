#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal Brain 参考同步工具。

只依赖标准库。三个命令：

    python sync.py index                 重建 index.json 与接口文件（GRADING/COUNTING）
    python sync.py check                 只检查，不写任何文件
    python sync.py snapshot --out dist   生成快照包（手工组包时用）

选项：
    --root PATH        大脑根目录（默认当前目录）
    --max-chars N      核心记忆字数上限（默认 1200，超限不出包）
    --ban a,b,c        L1 文件额外禁入词

设计原则：fail-closed。任何一项检查不通过，不写任何文件，退出码 1。

检查还要求每个文件声明 owner（写入归属），缺失直接失败；index.json 每条记入 owner。
快照包只由使用者手工生成、手工交付，大脑不自动生成、不自动投递。
"""

from __future__ import annotations

import argparse
import fnmatch
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

# 快照格式版本，与接口版本一致；接口升版时同步改。
SNAPSHOT_VERSION = "3.0"

SKIP_DIRS = {"_interface", ".git", "dist", "__pycache__", "node_modules", ".obsidian"}
COUNT_SKIP_DIRS = {".git", "dist", "__pycache__", "node_modules", ".obsidian"}
SKIP_FILES = {"_template.md"}
CORE_SOURCES = ("MEMORY.md", "profile.md", "preferences.md")

# L1 准入的默认禁入模式 —— 只管"可枚举的那一半"。
# 组合才成立的识别性信息（城市+学校+届别+年龄）抓不到，那部分靠写入前的人工分类。
DEFAULT_BANS = [
    (r"BEGIN[ A-Z]*PRIVATE KEY", "私钥块"),
    (r"\b\d{17}[\dXx]\b", "疑似身份证号"),
    (r"\b\d{16,19}\b", "疑似长串卡号/账号"),
    (r"(?i)(api[_-]?key|apikey)\s*[:=]\s*\S{8,}", "疑似 API 密钥"),
    (r"ghp_[A-Za-z0-9]{20,}", "疑似 GitHub 令牌"),
    (r"sk-[A-Za-z0-9]{20,}", "疑似 OpenAI 密钥"),
    (r"(?i)password\s*[:=]\s*\S+", "疑似密码字段"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(com|cn|net|qq\.com|gmail\.com)", "疑似邮箱地址"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "疑似手机号"),
]

# 摘要校验：最短长度与占位符（与方案文档第 3 节一致）。
MIN_SUMMARY_LEN = 8
SUMMARY_PLACEHOLDERS = ("待填", "待补充", "待写", "暂无", "占位", "todo", "tbd", "xxx", "lorem")

# 分级策略列（不随仓库状态变）：(匹配, 进仓, 进快照, 授权)。
# 匹配可以是精确路径、glob（如 projects/*.md）或目录前缀（如 chats-opt-in/）。
# 级别与"谁能写"两列不写死，从各文件的头信息实时读出。
GRADING_POLICY = [
    ("README.md", "是", "是", "否"),
    ("MEMORY.md", "是", "是", "否"),
    ("profile.md", "是", "是", "随核心合并，需逐次确认"),
    ("preferences.md", "是", "是", "随核心合并，需逐次确认"),
    ("answer-key.md", "是", "否", "不外发"),
    ("tasks.md", "是", "否", "外发需授权"),
    ("decisions/*.md", "是", "否", "不外发"),
    ("projects/*.md", "是", "是", "否"),
    ("people/INDEX.md", "是", "是", "随包需授权"),
    ("chats-opt-in/", "仅逐条选定", "否", "每条单独定"),
    ("encrypted/", "仅目录说明", "否", "不外发"),
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
        "note": "由同步工具重建，禁止手工维护。",
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

    # 参考区文件的摘要与级别取自索引（与仓库索引同源），不留空。
    by_path = {f["path"]: f for f in files}

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
        info = by_path.get(p.relative_to(root).as_posix(), {})
        ref_entries.append(
            {
                "path": f"brain-reference/{flat}",
                "level": info.get("level", meta.get("level", "?")),
                "owner": info.get("owner", meta.get("owner", "")),
                "summary": info.get("summary", ""),
            }
        )

    # brain-memory.md 是 MEMORY（L1）全文与 profile / preferences（L2）全文
    # 的合并版：整份按最严的一级（L2）对待，投递前由使用者逐次确认。
    manifest = {
        "version": SNAPSHOT_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_commit": git_commit(root),
        "core_chars": core_chars,
        "files": [
            {
                "path": "brain-memory.md",
                "level": "L2",
                "summary": "核心记忆合并版（含 L2 档案与偏好全文，投递前需使用者确认）",
            }
        ]
        + ref_entries,
    }
    write_json(out_dir / "brain-manifest.json", manifest)
    return manifest, None


def repo_counts(root: Path):
    """实时统计口径（COUNTING.md 的数字全部由此算出，不手写）。"""
    all_files = [
        p
        for p in root.rglob("*")
        if p.is_file() and not any(part in COUNT_SKIP_DIRS for part in p.parts)
    ]
    gitkeep = sum(1 for p in all_files if p.name == ".gitkeep")
    gitignore = 1 if (root / ".gitignore").exists() else 0
    interface = [p for p in all_files if "_interface" in p.parts]
    workflow = [p for p in all_files if ".github" in p.parts]
    content = [
        p
        for p in all_files
        if "_interface" not in p.parts
        and ".github" not in p.parts
        and p.name not in (".gitkeep", ".gitignore")
    ]
    return {
        "disk_total": len(all_files),
        "content_count": len(content),
        "content_md_count": sum(1 for p in content if p.suffix == ".md"),
        "content_json_count": sum(1 for p in content if p.suffix == ".json"),
        "content_other": [str(p.relative_to(root)) for p in content if p.suffix not in (".md", ".json")],
        "interface_count": len(interface),
        "workflow_count": len(workflow),
        "workflow_files": [str(p.relative_to(root)) for p in workflow],
        "gitkeep_count": gitkeep,
        "gitignore_count": gitignore,
        "drill_next": len(content) + len(interface) + len(workflow) + gitignore,
    }


def display_owner(owner: str) -> str:
    # 模板里的占位写法 main-writer，在矩阵里显示为"主写者"。
    return "主写者" if owner == "main-writer" else owner


def all_md_files(root: Path):
    """全部 Markdown 文件（含 _template.md 与 _interface/，供分级矩阵用）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in COUNT_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".md"):
                out.append((Path(dirpath) / fn).relative_to(root).as_posix())
    return sorted(out)


def _match(pattern: str, path: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if "*" in pattern:
        return fnmatch.fnmatch(path, pattern)
    return path == pattern


def build_grading(root: Path):
    """生成 _interface/GRADING.md：级别与归属从文件头实时读出，策略列写死。

    返回 (文本, 错误信息)。出错时文本为 None。
    """
    paths = all_md_files(root)
    info = {}
    for rel in paths:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        meta, _ = parse_frontmatter(text)
        info[rel] = meta

    def levels_of(covered):
        vals = set()
        for rel in covered:
            lv = info.get(rel, {}).get("level", "")
            if lv not in ("L1", "L2", "L3"):
                return None, f"{rel} 缺少合法 level 头信息"
            vals.add(lv)
        if len(vals) != 1:
            return None, f"覆盖文件级别不一致：{sorted(covered)} -> {sorted(vals)}"
        return vals.pop(), None

    def owners_of(covered):
        vals = []
        for rel in covered:
            ow = info.get(rel, {}).get("owner", "")
            if not ow:
                return None, f"{rel} 缺少 owner 头信息"
            if ow not in vals:
                vals.append(ow)
        return "、".join(display_owner(o) for o in vals), None

    rows = []
    used = set()
    for pattern, in_repo, in_snapshot, auth in GRADING_POLICY:
        covered = [p for p in paths if _match(pattern, p)]
        if not covered:
            continue
        if pattern.endswith("/"):
            # 目录行：以目录说明文件（README.md）为准。
            readme = f"{pattern}README.md"
            if readme not in covered:
                continue
            covered = [readme]
            label = pattern
        elif "*" in pattern:
            label = pattern
        else:
            label = pattern
        level, err = levels_of(covered)
        if err:
            return None, err
        if pattern == "chats-opt-in/":
            writer = f"用户选定，{display_owner(info[covered[0]].get('owner', ''))}落盘"
        elif pattern == "encrypted/":
            writer = "—"
        else:
            writer, err = owners_of(covered)
            if err:
                return None, err
        rows.append((label, level, in_repo, in_snapshot, auth, writer))
        used.update(covered)

    # index.json 行（固定）：放在 README 行之后，没有 README 行时放最前。
    idx_pos = 0
    for i, r in enumerate(rows):
        if r[0] == "README.md":
            idx_pos = i + 1
            break
    rows.insert(idx_pos, ("index.json", "L1", "是", "是", "否", "同步工具生成"))

    # _interface/ 各文件单列一行（级别与归属各自从头信息读）。
    iface = [p for p in paths if p.startswith("_interface/")]
    iface_rows = []
    for rel in iface:
        level, err = levels_of([rel])
        if err:
            return None, err
        writer, err = owners_of([rel])
        if err:
            return None, err
        iface_rows.append((rel, level, "是", "否", "否", writer))
    # 与模板保持一致：接口文件在矩阵里合并为一行。
    if iface_rows:
        levels = {r[1] for r in iface_rows}
        if len(levels) != 1:
            return None, f"_interface/ 文件级别不一致：{sorted(levels)}"
        rows.append(("_interface/*.md", levels.pop(), "是", "否", "否", "同步工具 / 主写者"))
        used.update(iface)

    # 其余未归类的文件逐个列出：进快照一律否，授权待确认（fail-closed）。
    for rel in paths:
        if rel in used:
            continue
        level, err = levels_of([rel])
        if err:
            return None, err
        writer, err = owners_of([rel])
        if err:
            return None, err
        rows.append((rel, level, "是", "否", "未分类，需确认", writer))
        used.add(rel)

    lines = [
        "---",
        "interface: true",
        "level: L1",
        "owner: main-writer",
        "generated_by: sync.py",
        "source: live repo state",
        "---",
        "",
        "# 分级矩阵",
        "",
        "本表由同步工具按仓库当前状态生成，不手写。级别与\"谁能写\"两列从各文件头 `level` / `owner` 实时读出；其余列为分级策略，写在同步工具里。",
        "",
        "> 判定标准：任取仓库中一个文件，两个不同的人按本表都能得出同一个级别。",
        "",
        "## 级别定义",
        "",
        "| 级别 | 定义 | 进仓 | 进快照 |",
        "|---|---|---|---|",
        "| **L1** | 可投递的核心事实：全量可发给任意接入方，无需逐次授权 | 是 | 是 |",
        "| **L2** | 需授权才外发的个人事实：进仓；进快照只限白名单 | 是 | 白名单内 |",
        "| **L3** | 绝不离开本地：不进仓、不进快照，任何 AI 不得接收或转述 | 否 | 否 |",
        "",
        "L3 采用**枚举式**定义，不用排除法：",
        "",
        "- 证件号",
        "- 住址",
        "- 健康信息",
        "- 私钥",
        "",
        "**其余一律不是 L3。** 这条很重要：如果 L3 靠\"感觉不能外发\"来界定，那它永远判不清楚，而且会把本该 L2 的东西高估成\"绝不外发\"，导致验收题无法投递。",
        "",
        "## 判定规则",
        "",
        "只有一句话：",
        "",
        "> **这条内容能不能发给任意一个接入方？**",
        "> 能 → L1；要挑人挑段 → L2；不能 → L3。",
        "",
        "**在写入时判定，不是在外发时判定。**",
        "",
        "## 矩阵",
        "",
        "| 文件 | 级别 | 进仓 | 进快照 | 授权 | 谁能写 |",
        "|---|---|---|---|---|---|",
    ]
    for label, level, in_repo, in_snapshot, auth, writer in rows:
        lines.append(f"| `{label}` | {level} | {in_repo} | {in_snapshot} | {auth} | {writer} |")
    lines += [
        "",
        "**「谁能写」列填的是 owner**：默认全是主写者；指定文件可以分给其他接入方，改头信息的 `owner:` 字段并同步本表。每个文件只有一个 owner；不拥有的文件只读或走提议。",
        "",
        "**「进快照」列只填是 / 否**：是即按快照格式随包（核心合并版或参考区的具体形态由快照格式定义），否即不进快照，无例外。",
        "",
        "`_interface/` 下三个文件定 L1 的理由：属公开规格、不含个人事实。但进快照为否 —— 快照是记忆内容的投递物，接口说明走 README 那条路，不混进去。",
        "",
        "## 快照白名单",
        "",
        "- 以本表的「进快照」列为准，逐项一致、无例外",
        "- 白名单之外的文件一律不进快照",
        "- 白名单是包内容的上限定：手工组包时包内文件不得超出白名单；L2 的每一次投递由使用者逐次确认（快照不自动生成、不自动投递，见方案文档第 4 节）",
        "- 「需授权」一律指投递，不指生成",
        "",
        "## 定级的连带规则",
        "",
        "### L1 的准入即外发准入",
        "",
        "写进 L1 文件的任何内容，都视为已授权发给所有接入方。所以：",
        "",
        "> 能写进 L1 的，只能是可发给任意接入方的事实。不确定的，降一档。",
        "",
        "同步工具应对 L1 文件做准入拦截，命中高敏类别直接拦下。但要注意工具的边界：它能拦可枚举的模式，拦不住组合才成立的识别性信息。",
        "写 L2 之前按方案文档第 8 节「L2 人工复核点」逐项自查 —— 工具没报警不等于可以推。",
        "",
        "### L2 的两种形态",
        "",
        "- **整份进**：`projects/*.md`、`people/INDEX.md` 这类，白名单允许整份随包",
        "- **合并进**：`profile.md`、`preferences.md` 这类，随核心记忆合并版全文进包；合并后的 `brain-memory.md` 按 L2 对待，投递前由使用者逐次确认",
        "",
        "矩阵的「进快照」列只区分是/否，具体形态在快照格式里定义。",
        "",
        "### L3 不进仓",
        "",
        "私有仓库也是服务器，不在这台机器上。所以 L3 只能存本地档案，仓库里最多留一句指针（\"某信息见本地 L3 档案\"）。",
        "",
        "## 生成方式",
        "",
        "本表由**同步工具**从各文件的头信息生成，不手写：",
        "",
        "```yaml",
        "---",
        "level: L1",
        "owner: main-writer  # 占位：换成主写者的名字",
        "updated: 2026-01-01",
        "source: manual",
        "tags: []",
        "---",
        "```",
        "",
        "手写的分级表必然会和实际文件漂移。`index.json` 不自索引，`_interface/` 下文件不进索引 —— 这些例外也应写进生成逻辑，而不是靠人记。",
        "",
    ]
    return "\n".join(lines), None


def build_counting(root: Path, counts, indexed: int, memory_chars):
    """生成 _interface/COUNTING.md：数字全部实时统计，不手写。"""
    mem = f"{memory_chars} 字" if memory_chars != "" else "—"
    wf = ", ".join(counts["workflow_files"]) if counts["workflow_files"] else "无"
    lines = [
        "---",
        "interface: true",
        "level: L1",
        "owner: main-writer",
        "generated_by: sync.py",
        "source: live repo state",
        "---",
        "",
        "# 口径表",
        "",
        "本文件由同步工具按仓库当前状态生成，不手写。全文每个数量都指到本表或同步工具输出，不再手写互不解释的数字。",
        "",
        "统计范围：除 `.git`、`dist/`（快照输出）、`__pycache__` 外的全部文件。",
        "",
        "| 口径 | 数量 | 构成 |",
        "| --- | --- | --- |",
        f"| 磁盘文件总数 | {counts['disk_total']} | 内容文件 {counts['content_count']}＋接口文件 {counts['interface_count']}＋工作流文件 {counts['workflow_count']}＋.gitignore {counts['gitignore_count']}＋.gitkeep {counts['gitkeep_count']} |",
        f"| 内容文件 | {counts['content_count']} | 内容 Markdown {counts['content_md_count']} 个＋index.json {counts['content_json_count']} 个 |",
        f"| 接口文件 | {counts['interface_count']} | _interface/GRADING.md、ACCEPTANCE.md、COUNTING.md；不计入索引 |",
        f"| 工作流文件 | {counts['workflow_count']} | {wf}；仓库 CI，不计入索引 |",
        f"| 索引收录 | {indexed} | {counts['content_md_count']} 个内容 Markdown 除去模板 projects/_template.md；index.json 不自索引 |",
        f"| 核心记忆字数 | {mem} | 同步工具计数（去空白） |",
        "| 恢复演练（历史） | — | 尚未记录演练；演练后按当次清单记 |",
        f"| 恢复演练（下次预期） | {counts['drill_next']} | 内容文件 {counts['content_count']} 个＋接口文件 {counts['interface_count']} 个＋工作流文件 {counts['workflow_count']} 个＋.gitignore {counts['gitignore_count']} 个 |",
        "",
        "## 为什么要这张表",
        "",
        "一份文档里出现两个对不上的手写数字，读者会开始怀疑所有数字。",
        "",
        "真实场景：同一份文档里写了\"仓库共 12 个文件，索引收录 10 个\"，另一处又写\"恢复演练 13 个文件\"。三个数字互不解释 —— 而且如果真有 2 个文件在库未入索引，那等于方案自己的规矩已经被违反了。",
        "",
        "所以定死：**数字由工具生成，本表只定义口径。**",
        "",
        "## 口径定义",
        "",
        "| 口径 | 定义 | 构成 |",
        "| --- | --- | --- |",
        "| **磁盘文件总数** | 仓库里所有文件 | 内容文件 + 接口文件 + 工作流文件 + `.gitignore` + `.gitkeep` |",
        "| **内容文件** | 承载记忆与项目的文件 | 内容 Markdown N 个 + `index.json` 1 个 |",
        "| **接口文件** | 接口定义，不是记忆内容 | `_interface/` 下 GRADING / ACCEPTANCE / COUNTING |",
        "| **工作流文件** | 仓库 CI 定义，不是记忆内容 | `.github/workflows/` 下的 workflow 文件 |",
        "| **索引收录** | `index.json` 里的条目数 | 内容 Markdown − 项目模板；`index.json` 不自索引；接口文件不收录 |",
        "| **核心记忆字数** | `MEMORY.md` 的字数 | 同步工具计数（去空白） |",
        "| **恢复演练（历史）** | 某次演练实际读回的文件数 | 当次清单 |",
        "| **恢复演练（下次预期）** | 按当前口径应覆盖的文件数 | 内容文件 + 接口文件 + 工作流文件 + `.gitignore`（`.gitkeep` 不读） |",
        "",
        "## 为什么接口文件不进索引",
        "",
        "`index.json` 是**记忆内容索引**，服务于\"按需读取某个文件\"这一步。",
        "",
        "接口定义文件是另一类东西：",
        "",
        "1. 混进索引会让接入方按读取顺序读到时，把接口定义误当成大脑里的记忆",
        "2. \"索引收录 N 条\"这个数字会失去意义，每次加规则都要重新解释",
        "",
        "所以：**附录入仓但不进索引**，是这条口径的固定例外，写进生成逻辑，不靠人记。",
        "",
        "## 每个数字的出处",
        "",
        "| 数字 | 出处在哪 |",
        "| --- | --- |",
        "| 文件数、索引条数 | 同步工具输出 |",
        "| 核心记忆字数 | 同步工具计数 |",
        "| 演练覆盖数 | 本表按构成推导 |",
        "| 项目版本号 | 项目页本身 |",
        "",
        "**凡是能由工具生成的，都不手写。** 手写的数字在第一次文件变更时就开始过期。",
        "",
        "## 变更时同步",
        "",
        "新增或删除文件时，以下三处必须同步更新，否则口径再次分裂：",
        "",
        "1. 仓库目录树（如果文档里有）",
        "2. 本表的口径构成",
        "3. 实测状态段落里的数字",
        "",
        "三处对不上，就是又欠了一笔。",
        "",
    ]
    return "\n".join(lines)


def ensure_interface_files(root: Path):
    """保证 _interface/GRADING.md 与 COUNTING.md 存在（含头信息），供生成逻辑读取。"""
    idir = root / "_interface"
    idir.mkdir(exist_ok=True)
    for name in ("GRADING.md", "COUNTING.md"):
        path = idir / name
        if not path.exists():
            path.write_text(
                "---\ninterface: true\nlevel: L1\nowner: main-writer\ngenerated_by: sync.py\nsource: live repo state\n---\n",
                encoding="utf-8",
            )


def memory_chars_of(root: Path):
    memory = root / "MEMORY.md"
    if not memory.exists():
        return ""
    body = re.sub(r"^---.*?---\n", "", memory.read_text(encoding="utf-8"), flags=re.S)
    return len(re.sub(r"\s", "", body))


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

    # 摘要须可用：非空、≥ 8 字、不是占位符 —— 索引里留空摘要等于没有索引
    bad_summary = []
    for f in files:
        s = f["summary"].strip()
        if not s:
            bad_summary.append((f["path"], "无摘要"))
        elif len(s) < MIN_SUMMARY_LEN:
            bad_summary.append((f["path"], f"摘要仅 {len(s)} 字"))
        elif any(p in s.lower() for p in SUMMARY_PLACEHOLDERS):
            bad_summary.append((f["path"], "摘要是占位符"))
    if bad_summary:
        print("以下文件摘要不合格，拒绝入索引：", file=sys.stderr)
        for p, why in bad_summary:
            print(f"  {p}（{why}）", file=sys.stderr)
        print("补法：在 frontmatter 加 summary: 字段（≥ 8 字，不写待填 / TODO 之类占位符），或正文首段写一句概述。", file=sys.stderr)
        print("\n未写入任何文件。", file=sys.stderr)
        return 1

    # 没有 owner 声明的文件视为问题 —— 归属不明的文件不许进索引
    no_owner = [f["path"] for f in files if not f["owner"].strip()]
    if no_owner:
        print("以下文件缺少 owner 头信息（写入归属）：", file=sys.stderr)
        for p in no_owner:
            print(f"  {p}", file=sys.stderr)
        print("补法：在 frontmatter 加 owner: 主写者的名字，或指定其他接入方的名字。", file=sys.stderr)
        print("\n未写入任何文件。", file=sys.stderr)
        return 1

    # L3 不进仓：L3 内容只在本地，仓库里最多留一句指针。
    # 唯一的例外是 encrypted/README.md —— 它只是目录说明，不是秘密本身。
    l3_bad = [f["path"] for f in files if f["level"] == "L3" and f["path"] != "encrypted/README.md"]
    if l3_bad:
        print("以下文件标为 L3 却放在仓库里：", file=sys.stderr)
        for p in l3_bad:
            print(f"  {p}", file=sys.stderr)
        print("L3 内容只存本地档案，仓库里最多留一句指针。", file=sys.stderr)
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
        # 只检查，不写任何文件：索引与接口文件须与工具生成的结果一致。
        fresh_index = build_index(root, files)["files"]
        index_path = root / "index.json"
        try:
            on_disk = json.loads(index_path.read_text(encoding="utf-8")).get("files", [])
        except Exception:
            print("index.json 读不到或格式不对，先跑 python sync.py index。", file=sys.stderr)
            return 1
        if on_disk != fresh_index:
            print("index.json 与当前文件不一致，先跑 python sync.py index 重建。", file=sys.stderr)
            return 1
        grading, err = build_grading(root)
        if err:
            print(f"分级矩阵生成失败：{err}", file=sys.stderr)
            return 1
        counts = repo_counts(root)
        counting = build_counting(root, counts, len(files), memory_chars_of(root))
        for name, text in (("GRADING.md", grading), ("COUNTING.md", counting)):
            path = root / "_interface" / name
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                print(f"_interface/{name} 与当前仓库状态不一致，先跑 python sync.py index 重建。", file=sys.stderr)
                return 1
        print(
            f"检查通过：{len(files)} 个文件，L1 准入与 owner 声明无问题；"
            f"内容 {counts['content_count']}、接口 {counts['interface_count']}、"
            f"工作流 {counts['workflow_count']}、索引 {len(files)} 条、核心 {memory_chars_of(root)} 字。"
        )
        return 0

    if args.cmd == "index":
        write_json(root / "index.json", build_index(root, files))
        ensure_interface_files(root)
        grading, err = build_grading(root)
        if err:
            print(f"分级矩阵生成失败：{err}", file=sys.stderr)
            return 1
        counts = repo_counts(root)
        counting = build_counting(root, counts, len(files), memory_chars_of(root))
        (root / "_interface" / "GRADING.md").write_text(grading, encoding="utf-8")
        (root / "_interface" / "COUNTING.md").write_text(counting, encoding="utf-8")
        counts = repo_counts(root)
        print(
            f"已重建 index.json：{len(files)} 条；接口文件已更新；"
            f"内容 {counts['content_count']}、接口 {counts['interface_count']}、"
            f"工作流 {counts['workflow_count']}、核心 {memory_chars_of(root)} 字。"
        )
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

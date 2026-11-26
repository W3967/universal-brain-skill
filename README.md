# Universal Brain

> 一套供应商中立的个人记忆接口标准。私有仓库是唯一事实源，各家 AI 按同一套规矩来读。

[English](#english)

多模型并用的人都有同一个问题：每换一个模型、每开一个新会话，就得重新交代一遍自己是谁、在做什么、有什么偏好。

主流做法是给每家单独做对接 —— 越做越乱，每加一个模型就要改一次结构。

**这个项目反过来做：只把大脑这一侧定成稳定的标准。** 谁想接就照着接，规则不为任何一方单独改。

---

## 它包含什么

| 产物 | 解决什么问题 |
|---|---|
| **分级矩阵** | 每个文件是什么级别、能不能外发、谁能写 |
| **验收判据** | 什么样才算"真的接通了"，而不是"读过一遍" |
| **口径表** | 文档里每个数字从哪来 |

这三个都是接口的一部分，不需要口头补充。

---

## 核心设计

### 1. 分级写在进入的时候

三级分档，判定只有一句话：**这条内容能不能发给任意一个接入方？**

- **L1** —— 能。全量可外发，进了就出。
- **L2** —— 要挑人挑段。进仓，但进快照受白名单约束。
- **L3** —— 不能。只存本地，永不进仓。

关键在于：**防护放在内容进入的时候，不放在离开的时候。** L1 文件的准入标准等于外发标准，写入前先分类，不指望导出时脱敏来补救。

关键词表拦得住密钥和证件号，拦不住"城市 + 学校 + 届别 + 年龄"这种组合起来才成立的可识别信息。

### 2. 按能力分档，不按名字分档

五个档位：主写者 / 直连只读 / 本地副本 / 文件导入 / 粘贴导入。

档位描述"这个接入方能做到什么"，不描述"它是哪家产品"。明天出一家新模型，先测能力、再入档。

降低档位不是失败：数据不迁移、格式不改，只是新鲜度、操作次数和覆盖范围不同。**但覆盖范围必须让接入方自己知道** —— 否则它会自信地谈论自己没收到的文件。

### 3. 判据与答案分离

验收题库只写判据结构，真实答案存在**答案键**里（L2，不进快照、不随文档分发）。

判据进接口，答案进仓库。两者混在一起，题库半年就会腐烂 —— 因为项目版本号、状态这类事实一直在变。

五类题：身份称呼 / 项目现状 / 个人偏好 / 边界规矩 / **边界感知**。

最后一类是重点：接入方是否主动声明自己读的是哪一版、第几档、覆盖到哪、边界外不作臆断。

### 4. 每个数字都要有出处

文件数、字数、索引条数一律由工具生成，不手写。

同一份文档里两个手写数字对不上，读者会开始怀疑所有数字。

---

## 快速开始

```bash
git clone https://github.com/W3967/universal-brain-skill.git
cp -r universal-brain-skill/templates/brain/* <your-private-repo>/
cd <your-private-repo>

# 去掉模板后缀，让它们成为真正的文件
find . -name '*.md.template' -exec sh -c 'mv "$1" "${1%.template}"' _ {} \;
```

> `templates/brain/` 里的文件都带 `.template` 后缀，是为了防止有人在开源仓里直接填真实数据。**复制到自己的私有仓库后必须去掉后缀**，否则 `sync.py` 找不到它们，第 0 级读取也会落空。

然后：

1. 填 `MEMORY.md`（L1，上限约 1200 字）—— 每个字都要能发给任意接入方
2. 不能随便外发的细节放 `profile.md` / `preferences.md`（L2）
3. 绝不能外发的放本地 L3，永不进仓
4. 重建索引并检查：

   ```bash
   python sync.py index  --root <你的仓库>   # 重建 index.json
   python sync.py check  --root <你的仓库>   # 只检查，不写文件
   python sync.py snapshot --out dist        # 生成快照包
   ```

5. 按 `_interface/GRADING.md` 定级，按 `_interface/COUNTING.md` 核对数字
6. 接第一个模型，按 `_interface/ACCEPTANCE.md` 验收并记账

`sync.py` 是 fail-closed 的：L1 准入、缺级别、缺摘要、核心超字数，任何一项不过都不写文件、退出码 1。

---

## 仓库结构

```
universal-brain/
├── SKILL.md                      # 技能说明
├── README.md                     # 本文件
├── docs/
│   └── PLAN.md                   # 完整方案文档
├── scripts/
│   └── sync.py                   # 参考同步工具（标准库，无依赖）
└── templates/brain/              # 复制到你自己的私有仓库
    ├── MEMORY.md.template        # L1 核心记忆
    ├── profile.md.template       # L2 本人档案
    ├── preferences.md.template   # L2 偏好
    ├── tasks.md.template         # L2 任务
    ├── projects/_template.md     # L1 项目页
    ├── people/INDEX.md.template  # L2 人物索引
    ├── chats-opt-in/             # 仅逐条选定的聊天（默认空）
    ├── encrypted/                # 预留，L3 内容不在此
    ├── daily/ · decisions/       # 日志与决议
    └── _interface/               # 接口定义（入仓，不进索引）
        ├── GRADING.md            # 分级矩阵
        ├── ACCEPTANCE.md         # 验收判据
        └── COUNTING.md           # 口径表
```

> `_interface/` 下的三个文件是**接口定义**，不是记忆内容。它们入仓但不进 `index.json` —— 否则接入方按读取顺序读到时，会把接口说明误当成大脑里的记忆。

---

## 三条不能破的规矩

1. **防护在进入时，不在离开时**
2. **判据与答案分离**
3. **数字要有出处**

---

## 不做什么

- 不为任何单个应用写专属对接代码
- 不做双向自动合并，不做实时同步
- 不做聊天记录自动回流
- 不上向量检索 —— 文件量小的时候，关键词加索引足够；等文件上百再评估

---

## 来源

这套标准来自一个真实运行的个人大脑系统：十来个内容文件、约千字核心记忆、多个已接通的模型。文档、判据、口径都是在那套系统上验证后抽出来的。

模板里所有具体值都是占位符 —— **开源的是方法，不是任何人的数据**。

---

# English

> A vendor-neutral personal memory interface standard. The private repo is the single source of truth; every AI reads by the same rules.

[中文](#universal-brain)

Anyone who uses several models has the same problem: every time you switch models or start a new session, you have to re-introduce yourself — who you are, what you're working on, what you prefer.

The usual approach is to build a separate integration for each vendor — it only gets messier, and every new model means changing the structure again.

**This project does the opposite: it fixes only the brain side as a stable standard.** Anyone who wants to connect follows the standard; the rules are never changed for one party alone.

---

## What's inside

| Artifact | What problem it solves |
|---|---|
| **Grading matrix** | Each file's level, whether it may be sent out, who may write it |
| **Acceptance criteria** | What counts as "really connected", not just "has read it once" |
| **Counting conventions** | Where every number in the docs comes from |

All three are part of the interface — nothing needs to be explained verbally on the side.

---

## Core design

### 1. Grading happens on entry

Three levels, decided by one question: **can this content be sent to any accessor at all?**

- **L1** — Yes. Fully shareable; once it's in, it's out.
- **L2** — Depends on who and what. It goes into the repo, but enters snapshots only under allowlist constraints.
- **L3** — No. Local only, never enters the repo.

The point: **protection happens when content enters, not when it leaves.** The admission bar for an L1 file equals the bar for sending it out — classify before writing; don't count on scrubbing at export time to save you.

A keyword list can block secrets and ID numbers. It cannot block combinations that only become identifying together — "city + school + cohort + age".

### 2. Tiers by capability, not by name

Five tiers: main writer / direct read / local copy / file import / paste import.

A tier describes "what this accessor can do", not "which product it is". When a new model appears tomorrow, test its capability first, then file it into a tier.

Dropping to a lower tier is not a failure: no data moves, no format changes — only freshness, operation count, and coverage differ. **But the accessor must know its own coverage** — otherwise it will confidently talk about files it never received.

### 3. Criteria and answers stay separate

The acceptance question bank only carries the criteria structure; the real answers live in the **answer key** (L2 — never enters snapshots, never ships with the docs).

Criteria go into the interface, answers go into the repo. Mix them and the bank rots within six months — because facts like project versions and statuses keep changing.

Five categories: identity & address / project status / personal preferences / boundary rules / **boundary awareness**.

The last one is the point: does the accessor proactively state which version it read, which tier it's on, how far its coverage reaches, and that it won't guess beyond the boundary?

### 4. Every number needs a source

File counts, character counts, index entries — all generated by tools, never hand-written.

Two hand-written numbers that disagree in the same document will make the reader doubt every number.

---

## Quick start

```bash
git clone https://github.com/W3967/universal-brain-skill.git
cp -r universal-brain-skill/templates/brain/* <your-private-repo>/
cd <your-private-repo>

# drop the template suffix so they become real files
find . -name '*.md.template' -exec sh -c 'mv "$1" "${1%.template}"' _ {} \;
```

> Files under `templates/brain/` all carry a `.template` suffix so nobody accidentally fills real data into the open-source repo. **You must drop the suffix after copying** — otherwise `sync.py` won't find them and the level-0 read finds nothing.

Then:

1. Fill in `MEMORY.md` (L1, ~1200 characters max) — every character must be sendable to any accessor
2. Details that can't be shared freely go in `profile.md` / `preferences.md` (L2)
3. Things that must never be shared stay in local L3, never entering the repo
4. Rebuild the index and check:

   ```bash
   python sync.py index  --root <your-repo>   # rebuild index.json
   python sync.py check  --root <your-repo>   # check only, writes nothing
   python sync.py snapshot --out dist        # generate snapshot package
   ```

5. Grade by `_interface/GRADING.md`, verify numbers by `_interface/COUNTING.md`
6. Connect your first model, verify it against `_interface/ACCEPTANCE.md`, and record the result

`sync.py` is fail-closed: L1 admission, missing level, missing summary, core over the character limit — if any check fails, it writes nothing and exits 1.

---

## Repository structure

```
universal-brain/
├── SKILL.md                      # skill description
├── README.md                     # this file
├── docs/
│   └── PLAN.md                   # full scheme document
├── scripts/
│   └── sync.py                   # reference sync tool (stdlib only, no dependencies)
└── templates/brain/              # copy into your own private repo
    ├── MEMORY.md.template        # L1 core memory
    ├── profile.md.template       # L2 personal profile
    ├── preferences.md.template   # L2 preferences
    ├── tasks.md.template         # L2 tasks
    ├── projects/_template.md     # L1 project pages
    ├── people/INDEX.md.template  # L2 people index
    ├── chats-opt-in/             # only chats selected item by item (empty by default)
    ├── encrypted/                # reserved; L3 content does not go here
    ├── daily/ · decisions/       # logs and decisions
    └── _interface/               # interface definitions (in repo, not in index)
        ├── GRADING.md            # grading matrix
        ├── ACCEPTANCE.md         # acceptance criteria
        └── COUNTING.md           # counting conventions
```

> The three files under `_interface/` are **interface definitions**, not memory content. They go into the repo but not into `index.json` — otherwise an accessor reading in order would mistake interface notes for memories in the brain.

---

## Three rules that must never break

1. **Protection on entry, not on exit**
2. **Criteria and answers stay separate**
3. **Every number needs a source**

---

## What it does not do

- No dedicated integration code for any single application
- No two-way auto-merge, no realtime sync
- No automatic chat-history flow-back
- No vector retrieval — while the file count is small, keywords plus an index are enough; re-evaluate when files reach the hundreds

---

## Origin

This standard comes from a real, running personal brain system: a dozen or so content files, roughly a thousand characters of core memory, several models already connected. The docs, criteria, and conventions were extracted from that system after being verified on it.

Every concrete value in the templates is a placeholder — **what's open-sourced is the method, not anyone's data**.

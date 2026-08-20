# EvoWriter

**自进化多智能体创作平台** · Self-Evolving Multi-Agent Writing Platform

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Layering CI](https://github.com/gaoshoupaper-code/EvoWriter/actions/workflows/layering.yml/badge.svg)](https://github.com/gaoshoupaper-code/EvoWriter/actions/workflows/layering.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Tauri 2](https://img.shields.io/badge/desktop-Tauri%202-orange.svg)

以长篇小说为首个垂直领域：创作 Agent 在真实写作中沉淀 trace，评估定位弱点，人机共创改进配置，经门禁发版回灌生产——**形成越用越强的数据飞轮**。

> **规模速览**：全栈 11.9 万行（Python 9.0 万 = 源码 7.2 万 + 测试 1.9 万 · Tauri/Rust 桌面端 2.9 万）· 812 个测试函数 · 26 个可组合中间件 · 11 周 45 个版本 tag · 单人开发，AI 辅助 + 人定架构（见[开发方法论](#开发方法论ai-辅助--人定架构))

## 30 秒了解

**EvoWriter 是一个会自我改进的 AI 写作系统**：多智能体帮作者写长篇小说，系统记录每次创作的完整 trace；离线的进化端从真实数据定位弱点，与人对话式共创改进方案，逐条人工拍板后经真实装配门禁发版，热加载回灌生产——**写作越久，系统越强**。

它由独立部署的三部分组成：**executor**（Agent 运行时）、**evolution**（进化引擎）、双 **Tauri 2 桌面端**（创作工作台 + 进化控制台）。两端只通过 canonical trace（事实）与 harness git 仓库（版本）单向耦合。

## 为什么做

Agent 应用的瓶颈不在"能不能跑"，而在"能不能持续变好"。Prompt、行为护栏、记忆检索策略靠人肉调优不可扩展；而让系统自我改进，又要面对它天然的不可信——改的人可能改错、评估的人可能放水、上线的过程可能出事故。

EvoWriter 的答案是：把进化做成一条**每级只消费上级封存制品的单向加工链**，评估与创作异源（判评分离）、进化改动逐条人工拍板、发版经真实装配门禁验证。人握住方向与发布两个决策点，其余交给飞轮。

## 系统架构

```mermaid
flowchart LR
    Author(["👤 作者"])

    subgraph Clients["客户端"]
        Desk["🖥️ 创作桌面端<br/>Tauri 2 · Rust HTTP/SSE 中继"]
        EvoDesk["🧭 进化控制台<br/>Tauri 2"]
    end

    subgraph EX["⚙️ executor 执行端"]
        EX1["meta agent + 5 专家子代理<br/>SSE 流式 · HITL 暂停恢复<br/>NWM 记忆回填"]
    end

    subgraph EV["🔬 evolution 进化端"]
        EV1["trace 摄入 → 证据卷宗 → 评估<br/>→ 人机共创进化 → 门禁发版"]
    end

    Harness["📦 harness git 仓库<br/>9 类可进化要素<br/>(prompt/中间件/检索策略…)"]
    LLM(["🤖 LLM"])
    Mem[("🗄️ NWM 叙事记忆<br/>SQLite + sqlite-vec + FTS5")]

    Author ==> Desk ==> EX1
    EvoDesk --> EV1
    EX1 --> LLM
    EX1 <--> Mem
    EX1 == "canonical trace（终态通知 → 增量拉取）" ==> EV1
    EV1 == "probe 装配门禁 · registry 指针晋升" ==> Harness
    Harness -.-> "exact commit 干净 checkout 热加载" .-> EX1
```

执行端与进化端**独立部署、独立演进**。执行端是领域无关的 Agent 运行时，四层架构（`routers → domains → platform` + 运行时动态加载的 harness 包），分层依赖由 AST 静态分析强制；进化端是离线质量与改进引擎。两端只通过两样东西单向耦合：**canonical trace**（事实）与 **harness git 仓库**（版本）。

## 核心机制

### 🧬 自进化引擎

- **Agent-as-Code，git 即版本库**。prompt、行为护栏、记忆检索策略等 9 类可进化要素（其中 7 类为 git 版本化制品）资产化为 harness 仓库的版本化制品；发版 = 冻结 candidate commit → executor 在该 commit 上干净 checkout 做**真实装配 probe** → registry 指针晋升 production，reload 返回身份与 probe 不符即自动恢复原版本。版本 = commit、生产 = 指针、回滚 = 记账；门禁单阶段化后曾 **2 天连发 4 版**，executor 经 harness 热加载全程零重部署。
- **判评分离防 reward hacking**。评估模型默认与创作/进化模型异家族（同家族配置告警 + 降级共用）；确定性契约覆盖矩阵（0-LLM 求值，抓"本该参与的机制没出现"类结构性缺失）与 28 维内容评分双轨出报告——外部研究（arXiv:2502.01534）显示同源评估会漏掉 18-29% 的结构性缺陷，凡是"自己给自己打分"的系统都需要这道隔离。
- **不可变制品单向加工链**。trace → 证据卷宗 → 评估卷宗逐级 seal（单事务原子写入，此后不可变），下游只消费 sealed 制品、成功只以制品存在性判定；跨服务异步因果用 Span Link + 制品版本双向还原，**绝不伪造跨服务父子 Span**。

### 🔭 Agent 观测底座

- **受治理的 canonical trace**。事件只存 PayloadRef，语义正文先过 PayloadGate（定向剥离推理字段、fail-closed 整包拒绝凭据/密钥）再 sha256 内容寻址落盘——治理前单条 trace 实测膨胀至 39MB（llm_start 重复输入内联占 90%）；治理后事件不内联正文，保留的交付物正文仅占 trace 的 0.5%。同一事件 schema 可投影 **OTLP** 对接标准追踪生态。
- **四维正交状态机**。业务状态 / trace 阶段 / 完整性 / 取消时间线拆为四个独立维度，`lifecycle_revision` 单调递增拒绝旧快照覆盖；`pending` 是中性态不误报损坏，只有 `verified` 放行下游消费——"完整性"不是一个 bool。
- **写盘与崩溃恢复工程**。事件写入内存队列、后台协程批量落盘（同步 IO 不碰事件循环），终态写 manifest（事件哈希清单）封存；进程崩溃后由启动期 reconcile 按 sealed 制品收敛分裂态（recover_pending 处理自观测中断态），跨进程强杀由父进程 `seal_external_cancel` 幂等接管 trace 收尾——超时不谎报 cancelled。

### 🧠 类型化长期记忆

- **8 类叙事学 typed records**（角色状态/关系/物品/伏笔承诺/叙事功能/场景/世界设定/章节摘要）替代 Graphiti 类通用图 schema——"谁知道什么""坑填没填"在 generic entity/edge 图里没有位置放。每条记忆带 `source_chapter` 因果锚点与原文引用；写第 N+1 章时以章节号为因果边界（causal cutoff），写作只能看见过去，杜绝未来章节泄漏。
- **四阶段检索管线**：causal cutoff → FTS5(BM25) + sqlite-vec 双路召回 RRF(k=60) 融合 → one-hop SQL JOIN 图扩展 → 12k 字符有界证据包注入 prompt。检索失败降级到静态蓝图兜底，**writing 永不零上下文**。
- **零图数据库依赖**：typed records 本身就是图的节点和边——要的是图的语义，不是图的数据库。一作品一库、三写一体单事务（主表+FTS+向量）、窗口函数取"最新有效切片"免 UPDATE 竞争。

### ⚙️ 多智能体运行时

- **26 个可组合中间件，洋葱模型按 Agent 角色动态组装**（harness 19 + executor 平台/领域 13 + evolution 4，同名对应件去重）。meta、writing、interview 各自的中间件链不同：故障自愈、路径防护、按文件粒度的并发写串行化（根治 asyncio 并发编辑同文件的字节流交叉截断）、修订硬上限、写后回读取证——五类 hook（`wrap_tool_call` / `wrap_model_call` / `before_model` / `after_model` / `before_agent`）在 graph 不同节点生效，不是单一管道。
- **主控自主委托 + HITL**。meta agent 按 goal 调度 5 个子代理（访谈/蓝图/细纲/创作四专家 + 通用兜底），无代码编排——调度由 prompt 指导与产出前置门控约束，不靠硬编码流水线；每个子代理内嵌 review-revise 循环（修订次数硬上限防不收敛）；LangGraph interrupt/`Command(resume)` 断点续跑，取消语义三路分流（用户停止 / 等待输入保持可恢复 / 断连）不混淆。
- **进程级失败隔离域**。A/B 候选在独立进程执行（POSIX `setsid` 独立进程组 / Windows `taskkill`），停止 = 协作式取消 → `join(10s)` → 强杀，超时硬上限 10s；子进程被强杀后由父进程 `seal_external_cancel` 幂等接管 trace 收尾——实验故障永不波及生产。

## 关键设计决策

专业深度往往体现在**否决了什么**。以下是本项目的几个核心取舍：

| 决策 | 否决的现成方案 | 为什么 |
|---|---|---|
| 自研 canonical trace（事件 schema + PayloadGate + 内容寻址 + manifest） | Langfuse / LangSmith 等观测平台 | 进化闭环要的不是看板，是**下游可消费的可信事实**：receipt 按连续 sequence 幂等校验、终态事件哈希清单、不可变 ArtifactRevision、血缘 DAG。观测平台的定位止于"给人看"，这里的 trace 是评估与进化的**证据链**，载荷必须先治理（剥离推理字段/拒绝凭据）才配作证据 |
| SQLite + sqlite-vec + FTS5，8 类 typed records | Graphiti/Neo4j 图数据库、独立向量库 | 叙事状态是强类型关系表形状，generic entity/edge 抽不出"信息差""伏笔状态机"这类结构；一作品一库文件即隔离、零部署依赖，全程 SQL 可审计。要图的语义，不要图的数据库 |
| harness 以 git 仓库为单一真源，registry 只存指针 | 数据库存版本 | commit 天然不可变且可精确 checkout——"线上任意一次生成行为可精确复现"由此免费获得；回滚 = 指针回退 + 记账，不删谱系。GitOps 范式的轻量落地 |
| 判评分离：评估用异家族模型 | 同一模型自评 / 同源评估 | 外部研究（arXiv:2502.01534）显示同源评估会漏掉 18-29% 结构性缺陷。凡是"自己给自己打分"的系统都需要这道隔离，再叠加 0-LLM 的确定性契约矩阵兜底 |
| 进化点逐条人工拍板 + probe 单阶段门禁 | 全自动进化、全自动发布 | "怎么改"必须人拍板（对话式共创，未拍板前硬拦截落地工具）；门禁的**可通过性是门禁的生死线**——旧三件套门禁在正常路径上从未通过过、流水线永久卡死，教训换来"真实装配 probe 一道门" |
| 四维正交状态机 + 中性中间态 | 单一 status 字段 | 状态混在一个字段里必然互相污染：`pending` 被误报为损坏、`cancel_timeout` 被谎报为 `cancelled`。拆成正交维度后每个维度可独立单调化，下游按维度判定而非猜 |

## 开发方法论：AI 辅助 + 人定架构

本项目单人 11 周完成 11.9 万行，开发方式是 **AI 辅助编码 + 人握架构与质量门禁**：架构决策、机制设计、取舍拍板与验收由作者负责，coding agent（Claude Code / ZCode 等）执行实现。让 AI 大量写代码而不失控，靠的是把质量控制做成**机器可执行的约束**，而不是人肉 review 意志：

- **812 个测试函数 / 100 个测试文件**——机制先可测，再谈实现
- **AST 分层 linter**（`scripts/check_layering.py`）——6 条分层铁律由静态分析强制，CI 拦截新增违规（baseline 模式：存量 6 条违规逐步清零，只拦新增）
- **conventional commits + feature branch**——全程 300+ 个提交带 scope 与根因说明，不写"fix bug"式的无信息提交
- **不可变制品 + probe 门禁**——连"进化系统自己"的改动都要过真实装配门禁才能上线；治理生产系统的机制，同样治理开发过程

## 快速开始

**方式一 · Docker Compose 全栈**（executor + evolution + website + nginx）

```bash
git clone https://github.com/gaoshoupaper-code/EvoWriter.git
cd EvoWriter
cp executor/.env.production.example executor/.env
cp evolution/.env.production.example evolution/.env
# 按需修改两个 .env（本地体验可保持默认）
docker compose up -d
```

**方式二 · 源码运行执行端**（本地开发）

```bash
cd executor
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
uvicorn app.main:app --reload --port 7788
```

LLM 凭据在桌面端配置页填写（加密存储），无需写入环境文件。桌面端见 `desktop/`（Tauri 2 + React，需 Rust 工具链）；官网为 `website/` 纯静态页。

## 仓库结构

| 目录 | 说明 |
|---|---|
| `contracts/` | 三端共享类型契约（trace schema / 跨端 API），零业务依赖 |
| `executor/` | 执行端：FastAPI + DeepAgents 写作运行时（routers / domains / platform 分层） |
| `evolution/` | 进化端：trace 摄入、证据卷宗、评估、进化、发版全流水线 |
| `evolution/harnesses/repo/` | harness 包：git 版本化的 Agent 定义（9 类可进化要素） |
| `desktop/` | 创作桌面端（Tauri 2 + React 19，Rust 做 HTTP/SSE 中继） |
| `evolution/desktop/` | 进化控制台（Tauri 2：观测 / 质量闭环 / 系统资产） |
| `website/` | 官网（Astro 静态页） |
| `docs/` | [系统心智模型](docs/系统心智模型.md)——8 张图分层下钻的唯一文档真相源 |

## 深入了解

- **[docs/系统心智模型.md](docs/系统心智模型.md)** — 全系统 8 张 Mermaid 图分层下钻：系统总览 → 三端请求流 → 记忆闭环 / trace 观测 / 写作生成 / 中间件四个机制深潜
- 架构守护：`scripts/check_layering.py` 以 AST 静态分析强制 6 条分层铁律（如 platform 不得 import domain），CI 拦截新增违规

## 工程质量

- **812 个测试函数 / 100 个测试文件**（pytest，executor 398 + evolution 370 + contracts 44）
- **AST 分层依赖 linter**：架构约束从文档变成机器可执行
- **单一 SQLite 技术栈**（sqlite-vec 向量 + FTS5 全文）：零外部数据库 / 图数据库 / 消息队列，docker compose 四服务即可全量部署

## License

[MIT](LICENSE) © 2026 黄鑫宇

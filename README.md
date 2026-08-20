# EvoWriter

**自进化多智能体创作平台** · Self-Evolving Multi-Agent Writing Platform

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Layering CI](https://github.com/gaoshoupaper-code/EvoWriter/actions/workflows/layering.yml/badge.svg)](https://github.com/gaoshoupaper-code/EvoWriter/actions/workflows/layering.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Tauri 2](https://img.shields.io/badge/desktop-Tauri%202-orange.svg)

以长篇小说为首个垂直领域：创作 Agent 在真实写作中沉淀 trace，评估定位弱点，人机共创改进配置，经门禁发版回灌生产——**形成越用越强的数据飞轮**。

> **规模速览**：Python 9.0 万行 · 812 个测试函数 · 28 个可组合中间件 · 2 个月 45 个版本迭代（执行端零重部署）

## 为什么做

Agent 应用的瓶颈不在"能不能跑"，而在"能不能持续变好"。Prompt、行为护栏、记忆检索策略靠人肉调优不可扩展；而让系统自我改进，又要面对它天然的不可信——改的人可能改错、评估的人可能放水、上线的过程可能出事故。

EvoWriter 的答案是：把进化做成一条**每级只消费上级封存制品的单向加工链**（trace → 证据卷宗 → 评估卷宗 → 版本候选），评估与创作异源（判评分离）、进化改动逐条人工拍板、发版经真实装配门禁验证，人握住方向与发布两个决策点，其余交给飞轮。

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

执行端与进化端**独立部署、独立演进**：执行端是领域无关的 Agent 运行时（四层架构：routers → domains → platform → 动态加载的 harness 包）；进化端是离线质量与改进引擎，两者只通过 canonical trace 与 harness git 仓库单向耦合。

## 核心能力

### 🧬 自进化引擎（Self-Evolution Engine）

- **Agent-as-Code**：prompt、行为护栏、记忆检索策略等 9 类要素资产化为 git 版本化制品，进化 Agent 与人对话式共创，进化点逐条人工拍板，probe 装配门禁验证后回灌生产、失败自动回滚
- **判评分离防 reward hacking**：独立异家族模型评估 + 契约矩阵确定性求值 + 28 维内容评分——实测同源评估曾静默放过 18-29% 的结构性缺陷
- **不可变制品单向加工链**：trace → 证据卷宗 → 评估卷宗逐级封存、单向消费，全程血缘可溯、审计可回放

### 🔭 Agent 观测底座（Agent Observability）

- 自研跨服务 canonical trace 系统：**PayloadGate 载荷治理**剥离推理字段、fail-closed 拒绝凭据，sha256 内容寻址存储把实测膨胀 39MB 的单条 trace 治理到正文内联仅 0.5%，可投影 **OTLP** 对接标准追踪生态
- **四维正交状态机**（业务状态 / 记录阶段 / 完整性 / 取消时间线）+ W3C traceparent 与 Span Link 表达异步因果——绝不伪造跨服务父子 Span

### 🧠 类型化长期记忆（Long-Term Memory）

- **8 类叙事学 typed records**（角色状态 / 伏笔状态机 / 视角功能…）替代 Graphiti 类通用图 schema，让几十万字长篇的世界状态可计算、可审计
- **因果锚点检索**：以章节号为因果边界，写作只能看见过去，杜绝未来章节泄漏；四阶段混合检索（causal cutoff → BM25+向量 RRF → one-hop JOIN → 有界证据包），零图数据库依赖

### ⚙️ 多智能体运行时（Multi-Agent Runtime）

- **28 个可组合中间件**以洋葱模型按 Agent 角色动态编排：故障自愈、路径防护、并发写串行化、修订上限、写后取证——长时自主运行不失控、不写坏文件、全程可审计
- 主控 Agent 自主调度 5 个专家子代理（访谈 → 蓝图 → 细纲 → 逐章创作），统一 SSE 流式编排支撑 HITL 暂停恢复与任意时刻取消
- **进程级失败隔离域**：A/B 候选独立进程执行，停止超时 10 秒强制回收 + 父进程幂等接管封存——实验故障永不波及生产

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
| `deep-dives/` | 机制深读：观测系统 7 篇 / 记忆系统 4 篇 / 进化系统 6 篇 |

## 深入了解

- **[docs/系统心智模型.md](docs/系统心智模型.md)** — 从系统总览到三端请求流到四大机制深潜，8 张 Mermaid 图分层下钻
- **[deep-dives/](deep-dives/)** — 三套机制深读（设计思想层）：每套带统一术语表、全局架构图、可迁移的工程范式清单与诚实的代价分析。赶时间可先读各自的 `README.md`
- 架构守护：`scripts/check_layering.py` 以 AST 静态分析强制 6 条分层铁律（CI 见 `.github/workflows/layering.yml`）

## 工程质量

- **812 个测试函数 / 100 个测试文件**（pytest，executor 398 + evolution 370 + contracts 44）
- **AST 分层依赖 linter**：platform 不 import domain 等 6 条铁律机器强制，CI 拦截新增违规
- **单一 SQLite 技术栈**（sqlite-vec 向量 + FTS5 全文）：零外部数据库 / 图数据库 / 消息队列，docker compose 四服务即可全量部署

## License

[MIT](LICENSE) © 2026 黄鑫宇

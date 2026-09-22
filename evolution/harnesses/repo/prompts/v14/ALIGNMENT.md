# v13 ↔ v14 Prompt/Skills 同源对齐清单（REQ-20260922-162823 FR-003 / AC-003）

对齐原则（DEC-001「规则共享 + 领域增补」/ DEC-005「skills 同源拆分」）：
v13（单故事专家连续增量版，commit `0692887`）与 v14（多 Agent 版）共享同一套方法论；
领域规范与公共规则**逐字一致**，架构差异只体现在「分工与编排」层。
自动校验：`evolution/tests/test_harness_alignment.py`（逐字断言，CI 守卫）。

## 1. system prompt 节级映射（v13 单文件 → v14 拆分）

| v13 节（storybuilding_system.md） | v14 归属 | 对齐方式 |
|---|---|---|
| 头部总述（增量式构建原则 4 条 + 维度清单） | common_rules.md + orchestrator_system.md | 逐字（两边都含） |
| §一、世界观 | domain_worldview.md | 逐字 |
| §二、人物 | domain_character.md | 逐字 |
| §三、故事线文件归属 | domain_storyline.md | 逐字 |
| §四、故事线 | domain_storyline.md | 逐字 |
| §五、事件组 | domain_storyline.md | 逐字 |
| §7.1 核心关系 | common_rules.md | 逐字 |
| §7.2 ID 编号规范 | common_rules.md + orchestrator_system.md | 逐字（两边都含） |
| §7.3 工作区文件地图 | common_rules.md + orchestrator_system.md | 逐字（两边都含） |
| §7.4 委托参数 | orchestrator_system.md「7.4 领域委托参数」 | 编排适配：v13 是父代理委托、v14 是 orchestrator 对领域代理委托，字段同构（任务/焦点/扩展方向/前几轮问题） |
| §7.5 关键规则 | common_rules.md + orchestrator_system.md | 逐字（两边都含） |
| §7.6 增量构建原则 | common_rules.md | 逐字 |
| §7.7 创作原则 | common_rules.md + orchestrator_system.md | 逐字（两边都含） |
| §7.8 review 审查（单次） | orchestrator_system.md「领域分派规则」第 3 条 + orchestrator-cycling skill 阶段 3 | 编排适配：v13 由故事专家自审自改；v14 由 orchestrator 调 review 后按领域分派修订（DEC-009），review 上限 2 次两边一致 |
| §7.9 回复格式 | common_rules.md | 逐字 |
| §8.1 配比与计数 | common_rules.md + orchestrator_system.md | 逐字（两边都含） |
| §8.2 配比导航（机械判定） | orchestrator_system.md | 逐字；导航指令文本本体在 QuotaConvergenceMiddleware 代码中，两臂**代码级同源**（同一份中间件） |
| §8.3 与增量分流的协调 | common_rules.md + orchestrator_system.md | 逐字（两边都含） |
| §8.4 收束与 review 时机 | orchestrator_system.md | 逐字（review 调用者是 orchestrator） |

## 2. skills 映射（v13 阶段型 → v14 领域型）

| v13 段落 | v14 归属 | 对齐方式 |
|---|---|---|
| initial：硬约束（只生成主线 1 条 / 最终结局不可改） | storyline-build 初构段 | 逐字 |
| initial：写入顺序（worldview→character→storyline） | orchestrator-cycling 阶段 1 依赖序 + 各领域 skill 初构段 | 同源（顺序即依赖序） |
| initial：最小骨架清单（故事核心/一览表/S01/timeline） | storyline-build 初构段 | 逐字 |
| initial：角色类型判定表 + 核心人物 ≤ 2 硬约束 | character-build 初构段 | 逐字 |
| expand：模式判定（R 值分流表 + 防死循环提醒） | orchestrator-cycling 阶段 2 | 逐字（判定者从单 Agent 自判变为 orchestrator，规则不变） |
| expand：模式A 全部（A.1-A.3 类型表/产出/插入规则） | storyline-build 增量段 | 逐字（跨域人物需求改为「返回摘要说明，orchestrator 转交」） |
| expand：模式B 全部（B.1-B.5 设计/融入目标/方式/功能/落点） | character-build 增量段 | 逐字（B.5 涉及故事线文件部分改为转交说明） |
| expand：公共规则（人物引入约束/内容关联约束/世界观同步） | character-build（引入约束）/ storyline-build（关联约束）/ worldview-build（世界观同步） | 逐字，按领域归位 |
| expand：深入思考该怎么加（缺什么/观察结构/怎么加） | orchestrator-cycling 阶段 2「深入思考」 | 同源（思考者从单 Agent 变为 orchestrator，条目不变） |

## 3. 循环驱动与终止语义（DEC-011 同一判定器）

| 机制 | v13 | v14 | 一致性 |
|---|---|---|---|
| 配比解析/核对 | contracts.storybuilding_quota | 同一模块 | 代码级同一实现 |
| 循环导航指令 | QuotaConvergenceMiddleware（挂故事专家） | 同一中间件（挂 orchestrator） | 代码级同一实现，指令逐字同源 |
| 单线护栏预算 | resolve_line_budget(target)（挂故事专家） | 同一函数（挂 storyline 代理） | 代码级同一实现 |
| review 上限 | RevisionLimitMiddleware(max_revisions=2) | 同参数（review_name 适配） | 语义一致 |
| minimal 档 | target=None 软终止（不注入导航） | 同 | 代码级同一实现 |

## 4. 已知不对齐项（如实标注，实验报告须呈现）

1. **上下文结构**：v13 单上下文累积全部轮次历史；v14 领域代理每次委托独立上下文 + 读文件接力。这是被测架构变量本身，不是缺陷。
2. **跨域转交开销**：v14 的 character/storyline 代理不能直接写对方文件，跨域需求经 orchestrator 转交（多一跳委托）；v13 单 Agent 无此开销。同为架构变量。
3. **skill 发现方式**：v13 的 SkillsMiddleware 注入两个 skill 描述；v14 注入 4 个（orchestrator 视角），领域代理靠 prompt 内固定路径（`/_skills_{i}/SKILL.md`）读取。机制差异属架构变量。

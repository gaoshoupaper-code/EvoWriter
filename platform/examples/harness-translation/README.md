# harness-translation —— 第二垂直领域示例包

「翻译」领域的最小 harness 包，证明 Platform 接纳新领域零代码改动
（REQ-20260919-202344 FR-008 / AC-010）。结构与写作领域
（`evolution/harnesses/repo`）同构，assemble 采用同一薄包装模式：
运行时构件 import executor 的 `app.platform.agent.runtime`，领域内容
（prompt / skill / middleware 薄包装）留在包内。

```
harness-translation/
├── __init__.py                    # assemble(ctx)：单层翻译 agent（无子代理）
├── prompts/translator_system.md   # 翻译官系统提示词（A 层 surface）
├── middleware/artifact_snapshot.py# 产物快照中间件薄包装（C 层 surface）
└── skills/translator/SKILL.md     # 翻译技能（A 层 surface）
```

e2e 验证见 `platform/tests/test_second_domain_e2e.py`。

## 怎么用（完整链路）

前置：本包内容放进一个 git 仓库（工作库 + bare 库），Platform 的
`PLATFORM_BARE_REPO` 指向该 bare。

1. **门禁**：`POST /api/release/probe {"source_commit": <commit>}`
   → `status=ready`（Platform 门禁逻辑对新领域零改动即接纳）。
2. **晋升**：`POST /api/release/promote {"source_commit": <commit>}`
   → 打包 artifact + 账本记录版本 + 通知 executor reload。
3. **绑定**：`POST /api/bindings {"trace_id": "...", "harness_commit": <commit>}`
   → Run 与版本的绑定记录；`GET /api/bindings/{trace_id}` 查询。
4. **兼容判定（A-B 验证）**：
   - 改 `prompts/translator_system.md`（A 层）→ 新 commit promote 后，
     旧绑定 `GET /api/bindings/{trace_id}/resume-check` = `compatible`。
   - 改 `middleware/artifact_snapshot.py`（C 层）→ promote 后
     `incompatible`。兼容门禁按 surface 三层判定，与领域无关。

## 新领域接入清单（平台零改动）

1. 按本包结构准备包：`__init__.py` 提供 `assemble(ctx)`，必须有
   `middleware/artifact_snapshot.py`（门禁硬性检查）。
2. 包内容 commit + push 到 harness bare repo。
3. 走上面的门禁 → 晋升 → 绑定链路即可；executor 消费 artifact
   装配任意 harness 包，无需为新领域改一行代码。

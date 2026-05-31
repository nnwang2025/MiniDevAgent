# MiniDevAgent

MiniDevAgent is a lightweight local code engineering agent that can read, analyze, patch, execute and verify Python projects through a safe human-in-the-loop workflow.

MiniDevAgent 不只是一个代码问答 Bot。它面向本地 Python 项目的代码理解与代码维护，支持从读取、分析到生成补丁、人工确认、执行修改和验证结果的完整工程闭环：

```text
READ -> ANALYZE -> PATCH -> EXECUTE -> VERIFY
读取 -> 分析 -> 生成补丁 -> 执行 -> 验证
```

项目内部使用 Skill Runtime、MCP-compatible 工具层、AST 代码分析和可追踪执行链路，让每一次回答、补丁生成和验证过程都有明确证据与 agent trace。

## 功能亮点

- Codebase understanding：理解项目结构、模块职责和核心流程
- Function and dependency analysis：基于 AST 分析函数、类、调用点和依赖关系
- Entrypoint detection：识别 CLI、`__main__`、FastAPI、`create_app()`、`uvicorn` 等入口
- MCP-compatible tool layer：通过 MCP 风格工具层隔离 Skill 与底层工具
- Dry-run patch generation：默认只生成 unified diff，不直接写文件
- Human confirmation before write：写入前必须由用户输入 `yes` 确认
- Backup and patch history：应用补丁前自动备份，并记录 patch history
- Verification after patch：应用后运行验证命令并返回日志
- CLI and FastAPI support：同时支持命令行和 FastAPI 接口
- Agent trace mode：可查看规划、技能选择、工具调用、补丁生成和验证过程

## Quick Demo

启动 trace 模式：

```bash
python src/main.py . --trace
```

输入问题：

```text
给 login() 增加空用户名和空密码校验
```

预期流程：

- Planner classifies as code_edit
- CodeEditSkill locates demo_project/auth.py
- PatchGenerator generates unified diff
- CLI asks for yes
- After confirmation, file is patched
- auth.py.bak is created
- compileall verification runs

缩略输出示例：

```text
【规划】
- task_type: code_edit

【技能选择】
- skill: CodeEditSkill

【工具调用】
- code_analysis.find_symbol
- filesystem.read_file
- patch_generator.generate

【Patch生成】
- file: demo_project/auth.py
- dry_run: True

--- a/demo_project/auth.py
+++ b/demo_project/auth.py
@@
     def login(self, username: str, password: str) -> bool:
+        if not username or not password:
+            return False
         user = self.user_store.get(username)
         return bool(user and user.get("password") == password)

是否应用该修改？输入 yes 确认：

【执行验证】
- backup: demo_project/auth.py.bak
- compileall return_code: 0
```

## 架构

代码理解与维护请求的主链路：

```text
User Question
→ PlannerAgent
→ SkillRouter
→ Skill
→ MCP-compatible Tool Layer
→ ContextManager
→ AnswerAgent
```

维护类任务的执行链路：

```text
READ → ANALYZE → PATCH → CONFIRM → EXECUTE → VERIFY
```

各层职责：

- `PlannerAgent`：判断任务类型，例如项目概览、函数分析、依赖追踪、代码修改、代码修复
- `SkillRouter`：根据任务类型选择对应 Skill
- `Skill`：执行具体任务，并调用 MCP-compatible 工具
- `MCP-compatible Tool Layer`：提供文件系统、终端执行和代码分析能力
- `ContextManager`：按任务类型裁剪上下文，避免把无关文件塞进回答
- `AnswerAgent`：基于证据生成中文回答，并返回 trace 信息

## 项目结构

```text
src/
  agents/      PlannerAgent、AnswerAgent 等 Agent 实现
  skills/      项目理解、函数分析、依赖追踪、代码编辑、代码修复等 Skill
  mcp/         MCP-compatible 工具层
  runtime/     执行编排、上下文管理、补丁生成与应用
  memory/      hot memory、cold memory 和检索逻辑
  api/         FastAPI 应用与接口定义

scripts/       smoke test 和维护能力测试
demo_project/  用于演示和测试的小型 Python 项目
```

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

如果 PowerShell 阻止执行 `activate`，可以直接使用虚拟环境里的 Python：

```bash
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 使用方式

### CLI

启动交互模式：

```bash
python src/main.py .
```

开启 trace 模式：

```bash
python src/main.py . --trace
```

非交互提问：

```bash
python src/main.py . --trace --question "login() 干嘛"
```

生成代码修改补丁：

```bash
python src/main.py . --trace --question "给 login() 增加空用户名和空密码校验"
```

默认情况下，上面的命令只会生成 dry-run patch，不会写入文件。只有在 CLI 中输入 `yes`，才会应用修改。

### FastAPI

启动服务：

```bash
python src/main.py . --serve --port 8000
```

打开接口文档：

```text
http://127.0.0.1:8000/docs
```

API 可以返回回答、证据、agent trace 和 `pending_patch`。出于安全考虑，API 不会自动应用 patch。

## 安全机制

MiniDevAgent 默认采用保守的代码修改策略：

- 默认 dry-run，只生成补丁预览
- 没有用户输入 `yes`，不会写入文件
- 写入前自动创建 `filename.bak`
- 应用补丁后记录 `.minidevagent/patch_history.jsonl`
- 文件访问限制在项目根目录内
- 阻止访问 `.env`、`.venv`、`.git`、`__pycache__`
- 补丁应用后会执行验证并返回日志

## 测试

运行代码理解 smoke test：

```bash
python scripts/smoke_test.py
```

运行代码维护 smoke test：

```bash
python scripts/maintenance_smoke_test.py
```

测试覆盖内容包括：

- 项目结构理解
- 函数解释
- 入口检测
- 依赖追踪
- cold memory 检索
- dry-run patch 生成
- 无确认不写入
- 确认后应用 patch
- 备份文件创建
- 验证命令执行

## 当前限制

- 当前主要针对 Python 项目优化
- MCP-compatible 工具层是架构抽象，不是完整 MCP 协议实现
- 大规模跨文件重构能力仍然有限

## 项目说明

MiniDevAgent demonstrates a lightweight code maintenance agent with Skill Runtime, MCP-compatible tools, AST-based analysis, human-in-the-loop patching, and verification feedback.


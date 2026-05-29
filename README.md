# MiniDevAgent

MiniDevAgent 是一个轻量级本地代码工程 Agent。v1 已能做代码理解；当前版本继续扩展为代码维护 Agent，支持：

READ -> ANALYZE -> PATCH -> EXECUTE -> VERIFY

默认所有代码修改都是 dry-run。Agent 可以生成 unified diff，但不会自动写文件；只有用户在 CLI 中输入 `yes`，才会备份、写入、记录 patch history 并执行验证。

## 架构

用户问题
-> `PlannerAgent`
-> `SkillRouter`
-> Skill
-> MCP Client
-> MCP-compatible Server
-> Tool
-> `ContextManager`
-> `AnswerAgent`
-> `agent_trace`

维护任务的 ReAct loop：

Thought: locate target
Action: `code_analysis.find_symbol`
Observation: found file/function
Thought: generate patch
Action: `patch_generator.generate`
Observation: unified diff generated
Thought: need confirmation
Action: wait user
Action: apply patch
Verification: `python -m compileall`; if tests exist, `pytest`
Final Answer: applied/failed with logs

## MCP-compatible Tool Layer

目录：`src/mcp/`

- `base.py`：`MCPServer`、`MCPClient`、结构化 `MCPResult`、路径安全检查。
- `filesystem_server.py`：`list_dir`、`read_file`、`write_file`、`search_file`、`replace_text`。
- `terminal_server.py`：`run_python`、`run_pytest`、`run_shell`、`run_git`。
- `code_analysis_server.py`：`parse_ast`、`find_symbol`、`trace_dependency`、`find_entrypoint`、`search_references`。
- `registry.py`：创建 MCP Client。

安全限制：

- 只能访问项目根目录内。
- 阻止 `.env`、`.venv`、`.git`、`__pycache__`。
- terminal 默认 timeout 为 30 秒，返回 stdout、stderr、return_code、timeout。

## Skills

理解类：

- `ProjectOverviewSkill`
- `FunctionExplainSkill`
- `EntrypointSkill`
- `DependencyTraceSkill`
- `MemoryRecallSkill`
- `ProblemTraceSkill`

维护类：

- `CodeEditSkill`：处理“修改代码、增加参数校验、重构函数、添加日志、增加异常处理、优化代码”。
- `CodeFixSkill`：处理“bug、traceback、报错、异常”，生成修复 patch 和验证计划。

## Patch Flow

1. 定位目标符号。
2. 读取目标文件。
3. 分析代码。
4. 生成 `old_code`、`new_code` 和 unified diff。
5. 返回 `pending_patch`。
6. 默认不写入。
7. CLI 输入 `yes` 后应用。
8. 写入前生成 `filename.bak`。
9. 写入 `.minidevagent/patch_history.jsonl`。
10. 执行验证。

## CLI

交互模式：

```bash
python src/main.py .
```

trace 模式：

```bash
python src/main.py . --trace
```

非交互 dry-run：

```bash
python src/main.py . --trace --question "给 login() 增加空用户名和空密码校验"
```

非交互确认应用：

```bash
python src/main.py demo_project --trace --question "给 login() 增加空用户名和空密码校验" --yes
```

## FastAPI

启动：

```bash
python src/main.py . --serve --port 8000
```

创建会话：

```bash
curl -X POST http://127.0.0.1:8000/session/create ^
  -H "Content-Type: application/json" ^
  -d "{\"project_path\":\".\",\"session_id\":\"1\"}"
```

提问：

```bash
curl -X POST http://127.0.0.1:8000/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"session_id\":\"1\",\"question\":\"给 login() 增加空用户名和空密码校验\",\"trace\":true}"
```

API 只返回 `pending_patch`，不会自动应用。

## Smoke Tests

理解能力：

```bash
python scripts/smoke_test.py
```

维护能力：

```bash
python scripts/maintenance_smoke_test.py
```

维护测试覆盖：

- `给 login() 增加空用户名和空密码校验`
- 示例 traceback 修复
- `优化 bootstrap()`
- patch generated
- diff preview shown
- no write without confirmation
- confirmation 后实际写文件
- backup created
- verification executed

# MiniDevAgent — 研发团队 AI 值守工程师

MiniDevAgent 是一个基于 **PERR（Plan-Execute-Reflect-Refine）范式**的 Hermes-style Agent Runtime，面向研发团队的代码理解、历史追溯与智能值守场景。

它的核心创新在于：**用独立的 Reflection Agent 替代 ReAct 的隐式推理**，在每个执行周期后以结构化 JSON（issues/root_causes/coverage_gaps/confidence）显式评估结果质量，低置信度自动触发计划修正。

```text
PLAN → EXECUTE → VERIFY → REFLECT → REFINE (loop)
        ↑_______________________________|
```

## 场景故事

> 一个 5 人 Python 后端团队维护着 AuthService 微服务。3 个月里产生了 12 个 commit、4 个 issue、6 段群聊讨论、8 条部署日志。新人入职时，需要花半天搞清楚"login 空密码漏洞谁修的？为什么这么改？"——而 MiniDevAgent 能在 10 秒内从 Git + Issue + Chat + Log 的混合记忆中检索到完整答案。

```text
Q: "login 空密码漏洞是谁修的？为什么？"
A: 【回答】
   login() 空密码校验由 bob 在 commit d4f8b1c6 中修复。
   根因：login() 没有对空 username/password 做显式校验，
   空字符串绕过 user_store 比对逻辑（Python 空字符串相等性问题）。

   【关键文件】auth.py

   【代码/记忆证据】
   - [ISS-101] login() accepts empty password ... (2025-02-14, resolved by bob)
   - [Chat] alice: 刚 code review 发现 login() ... bob: 当时为了快速上线 demo 没加校验
   - [Commit d4f8b1c6] fix: login() accepts empty password — add validation
   - [Deploy Log] TypeError: 'NoneType' object is not subscriptable (2025-02-13, prod)
```

## 架构

```
                        用户接口
                    CLI  │  FastAPI REST
                         │
               HarnessRuntime (总调度器)
                         │
       ┌─────────────────┼──────────────────┐
       │                 │                  │
   [Ingestion]      [PERR Loop]        [Answer]
   Git/Issue/    Plan→Execute→Verify   混合证据
   Chat/Log      →Reflect→Refine       三段回答
       │                 │                  │
       └─────────────────┼──────────────────┘
                         │
            ┌────────────┼────────────┐
            │            │            │
       [8 Skills]   [MCP Tools]   [Memory]
       项目概览      FileSystem     Hot(会话)
       函数分析      CodeAnalysis   Cold(持久)
       依赖追踪      Terminal       三层检索
       代码编辑                      写回机制
       代码修复
       记忆召回
       问题追踪
       入口分析
```

## PERR vs ReAct

| | ReAct | MiniDevAgent PERR |
|---|---|---|
| 推理方式 | 隐式混在 Thought 文本里 | **显式结构化 Reflection** |
| 评估标准 | LLM 自由文本 | JSON: issues/root_causes/gaps/confidence |
| 纠错触发 | 依赖 prompt engineering | **confidence < 0.85 → 自动 refine** |
| 状态管理 | 无显式状态机 | **LangGraph StateGraph + 条件边** |
| 可观测性 | 仅文本日志 | Agent Trace + PERR 轮次统计 |
| 终止条件 | LLM 自行判断 | 置信度阈值 + 最大 3 轮 refine |

## 核心模块

### 1. PERR Agent Loop (`src/runtime/harness.py`)

```python
# 复杂任务（code_edit, code_fix, dependency_trace）走完整 PERR 循环：
while refinement_round <= MAX_REFINE_ROUNDS:
    skill_result = _execute_skill(question, plan, state)     # EXECUTE
    verify_result = _verify_execution(skill_result, state)   # VERIFY
    reflection = reflection_agent.reflect(...)                # REFLECT
    if reflection.confidence >= 0.85:
        break                                                 # 达到阈值，退出
    plan = _refine_plan(plan, reflection)                     # REFINE
    refinement_round += 1
```

### 2. Reflection Agent (`src/agents/reflection_agent.py`)

输出结构化反思结果：

```python
ReflectionResult(
    success=True/False,
    issues_found=["发现的问题"],
    root_causes=["根因分析（不是表象！）"],
    coverage_gaps=["遗漏了哪些检查"],
    suggested_fixes=["具体修正建议"],
    confidence=0.85,         # 0-1 置信度
    should_retry=True/False, # 是否触发 refine
    refinement_plan=["修正后的执行步骤"]
)
```

### 3. 三层混合检索记忆 (`src/memory/cold_memory.py`)

| Tier | 依赖 | 能力 |
|---|---|---|
| 1 | 零依赖 | Counter 词袋 + BM25 + 精确匹配 + 实体匹配 |
| 2 | numpy | TF-IDF 向量余弦相似度 |
| 3 | sentence-transformers | 语义 Embedding（384 维） |

五维融合打分：`score = exact×4 + entity×3 + semantic×2 + bm25×1 + recency×0.25`

### 4. 数据接入层 (`src/ingestion/`)

| Ingestor | 数据源 | 输出 |
|---|---|---|
| `GitIngestor` | `git log` 或 commits.json | 每条 commit → memory record |
| `IssueIngestor` | issues.json | 每个 issue → memory record（含 resolution + 讨论） |
| `ChatIngestor` | chat_logs.json | 每条消息 → record，含线程 → 合并为 episodic record |
| `LogIngestor` | deployment_logs.json | 每条日志 → record（ERROR → kind="error"，traceback 提取实体） |

Session 创建时自动运行 `IngestionPipeline.ingest_all()`，数据自动写入 ColdMemory。

### 5. 代码编辑安全链路 (`src/skills/code_edit_skill.py`)

```
LLM 接收完整文件 + 修改意图 → 返回修改后完整文件
  → ast.parse() 语法验证
  → difflib.unified_diff() 生成 diff
  → Dry-run 预览（不写文件）
  → 用户 CLI 输入 "yes"
  → Stale 检测（比对文件内容是否被外部修改）
  → 创建 .bak 备份
  → 写入新文件
  → compileall + pytest 验证
  → 记录到 patch_history.jsonl
```

### 6. LangGraph 状态机 (`src/graph.py`)

```text
PLAN → EXECUTE → [needs_verify?] → VERIFY → REFLECT
                                     ↓
                                  REFLECT → [should_continue?]
                                              ├─ refine → REFINE → EXECUTE (loop)
                                              └─ finalize → FINALIZE → END
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动交互模式（demo_project 包含预制团队历史数据）
python src/main.py demo_project --trace
```

交互示例：

```text
问题> login 空密码漏洞是谁修的？为什么？

【规划】task_type: memory_recall
【技能选择】skill: MemoryRecallSkill
【工具调用】ColdMemory.retrieve (26 records searched)

【回答】
login() 空密码校验修复由 bob 完成（commit d4f8b1c6）。原因是 alice 在 code review
中发现 login() 没有对空 username/password 做显式校验，空字符串可以绕过 user_store
的密码比对逻辑。diana 创建了 ISS-101 追踪，bob 在 2025-02-14 修复。

【关键文件】auth.py

【证据】
- [ISS-101] login() accepts empty password ... (security, p0, resolved)
- [Chat] alice→bob→diana 讨论线程 (2025-02-13)
- [Commit d4f8b1c6] fix: login() accepts empty password (bob, 2025-02-14)
- [Deploy Log] TypeError in login() auth.py:42 (2025-02-13, prod)
```

```text
问题> 给 login() 增加空用户名和空密码校验

【规划】task_type: code_edit, complexity: moderate
【执行】CodeEditSkill → LLM 生成新代码 → AST 验证通过
【Patch生成】dry_run: True

--- a/demo_project/auth.py
+++ b/demo_project/auth.py
@@
     def login(self, username, password):
+        if not username or not password:
+            return False
         user = self.user_store.get(username)
         return bool(user and user.get("password") == password)

是否应用该修改？输入 yes 确认：yes

【执行验证】
- backup: demo_project/auth.py.bak
- compileall return_code: 0
- 已写入 cold memory
```

## Docker 部署

```bash
# 一键启动（API + Redis）
docker-compose up -d

# 验证服务状态
curl http://localhost:8000/health
# → {"status":"ok","service":"MiniDevAgent","version":"2.0","redis":{"backend":"redis","connected":true}}

# 进入 CLI 交互模式
docker-compose --profile cli run --rm cli

# 查看日志
docker-compose logs -f api
```

**docker-compose 服务拓扑：**

```text
┌─────────────────┐     ┌──────────────────┐
│  Redis 7 Alpine │◀───▶│  MiniDevAgent API│
│  :6379          │     │  :8000           │
│  AOF 持久化     │     │  FastAPI + uvicorn│
│  max 256mb LRU  │     │  PERR Agent Loop │
└─────────────────┘     └──────────────────┘
        │                        │
        ▼                        ▼
  Session 记忆              挂载 demo_project
  多进程共享                 持久化 agent_data
  检索缓存                   volume
```

## Redis 集成

MiniDevAgent 实现了**渐进式 Redis 集成**——有 Redis 时自动启用持久化与多进程共享，没有时降级为内存模式。

| 功能 | 无 Redis（单进程） | 有 Redis（多进程） |
|---|---|---|
| 会话记忆 | Python list，进程重启丢失 | Redis List，AOF 持久化 |
| 多进程共享 | ❌ 不支持 | ✅ Redis key 隔离 |
| 检索缓存 | 无 | Redis String，5min TTL |
| Session 元数据 | Python dict | Redis Hash |
| 健康检查 | `{"backend":"in_memory"}` | `{"backend":"redis","used_memory_human":"1.2M"}` |

**关键设计：**

```python
# 连接 — 自动检测 Redis 可用性
backend = RedisMemoryBackend.connect(session_id="s1")
# 有 Redis → redis://redis:6379/0
# 无 Redis → 内存 dict（零依赖降级）

# 写入 — 双写（本地 + Redis）
hot_memory.bind_redis(backend)
hot_memory.add_message("login bug fix?")
# → self.recent_conversation.append(msg)     # 本地：零延迟
# → self._redis.push_conversation(msg)        # Redis：持久化

# 会话隔离
# Key schema: minidevagent:{session_id}:conversation
#             minidevagent:{session_id}:active_files
#             minidevagent:{session_id}:cache:{query_hash}
```

## 项目结构

```text
src/
  agents/        8 个 Agent（Planner/Explorer/Analyzer/Context/
                   Reflection/Verify/Answer + 基类）
  skills/        8 个 Skill（项目概览/函数分析/入口分析/依赖追踪/
                   记忆召回/问题追踪/代码编辑/代码修复）
  ingestion/     数据接入层（Git/Issue/Chat/Log + Pipeline）
  mcp/           MCP-compatible 工具层（FileSystem/CodeAnalysis/Terminal）
  runtime/       运行时（Harness/State/ContextManager/PatchManager）
  memory/        记忆系统（Hot/Cold/Redis Backend/CodeIndex/ChunkBuilder）
  prompts/       LLM System Prompts
  api/           FastAPI 接口
  tools/         AST/文件/搜索/安全 工具函数

demo_project/
  .minidevagent/    预制团队历史数据
    commits.json       12 条 Git 提交记录
    issues.json        4 个 Issue（含讨论线程）
    chat_logs.json     6 段群聊讨论
    deployment_logs.json 8 条部署与错误日志
```

## 安全机制

- **Dry-run 默认**：只生成 diff 预览，不写入文件
- **人类在回路**：CLI 输入 `yes` 才应用 patch
- **Stale 检测**：写入前比对文件是否被外部修改
- **自动备份**：`.bak` 文件保护
- **编译验证**：`compileall` + AST fallback + `pytest`
- **路径防护**：所有文件操作限制在项目根目录内
- **敏感文件保护**：阻止访问 `.env`、`.venv`、`.git`

## 技术栈

| 层 | 选型 |
|---|---|
| Agent 范式 | PERR（Plan-Execute-Reflect-Refine） |
| 状态机 | LangGraph StateGraph |
| LLM 接入 | OpenAI-compatible SDK（Qwen/DeepSeek/SiliconFlow） |
| API 层 | FastAPI + Uvicorn |
| 代码分析 | Python AST（`ast.parse`/`ast.walk`） |
| 记忆检索 | BM25 + TF-IDF + sentence-transformers (可选) |
| 数据接入 | Git/Issue/Chat/Log → normalized memory records |

## 测试

```bash
# 代码理解 smoke test
python scripts/smoke_test.py

# 代码维护 smoke test
python scripts/maintenance_smoke_test.py

# 数据接入 + 混合检索验证
python -c "
from src.ingestion.pipeline import IngestionPipeline
from src.memory.cold_memory import ColdMemory
p = IngestionPipeline()
r = p.ingest_all('demo_project')
print(f'{r[\"total_records\"]} records from {r[\"by_source\"]}')
cm = ColdMemory.from_project('demo_project')
for rec in r['records']: cm.records.append(rec)
results = cm.retrieve('login 空密码漏洞')
for r in results: print(r['text'][:80], r['score'])
"
```

## License

MIT

from __future__ import annotations

from typing import Any

from .base import BaseSkill


class ProjectOverviewSkill(BaseSkill):
    name = "ProjectOverviewSkill"

    def run(self, question: str, context: dict[str, Any], runtime_state: Any) -> dict[str, Any]:
        self.bind(runtime_state)
        index = self.index()
        files = [entry["path"] for entry in index.get("files", [])]
        important = self._important_files(files)
        module_summaries: list[dict[str, str]] = []
        key_functions: list[str] = []
        evidence: list[str] = []

        for path in important:
            skeleton = self.skeleton(path)
            imports = self.imports(path)
            names = self._names(skeleton)
            role = self._role(path, names, imports)
            module_summaries.append({"path": path, "role": role, "symbols": ", ".join(names[:8])})
            if names:
                evidence.append(f"{path}: 定义 {', '.join(names[:8])}")
            if imports:
                evidence.append(f"{path}: import {', '.join(imports[:5])}")
            for item in skeleton:
                if item.get("type") == "function":
                    key_functions.append(f"{path}: {item.get('signature', item.get('name'))}")
                elif item.get("type") == "class":
                    key_functions.append(f"{path}: class {item.get('name')}({', '.join(item.get('members', [])[:5])})")

        entry_files = [path for path in files if path.replace("\\", "/").endswith(("main.py", "app.py"))]
        answer = "\n".join(
            [
                "项目定位：",
                self._purpose(files),
                "",
                "项目结构：",
                self._tree(index.get("tree", "")),
                "",
                "核心模块：",
                "\n".join(f"- {item['path']}: {item['role']}" for item in module_summaries) or "- 未发现可分析的 Python 模块。",
                "",
                "关键函数/类：",
                "\n".join(f"- {item}" for item in key_functions[:18]) or "- 未识别到关键函数。",
                "",
                "入口线索：",
                "\n".join(f"- {path}" for path in entry_files[:8]) or "- 未发现 main.py/app.py。",
            ]
        )
        return self.ok(
            answer,
            important,
            evidence,
            "读取 file tree、项目索引、核心模块摘要和入口文件线索后生成概览，没有全文 dump。",
            tools_used=["build_project_index", "get_function_skeleton", "get_imports"],
            observations={
                "file_tree": self._tree(index.get("tree", "")),
                "module_summaries": module_summaries,
                "key_functions": key_functions,
                "entry_files": entry_files,
            },
            trace_observation={"files_scanned": len(files), "important_files": important, "entry_files": entry_files[:8]},
        )

    def _important_files(self, files: list[str]) -> list[str]:
        normalized = {path.replace("\\", "/"): path for path in files}
        preferred = ["src/main.py", "src/runtime/harness.py", "src/skills/registry.py", "src/agents/planner_agent.py", "src/agents/answer_agent.py", "src/api/app.py"]
        selected = [normalized[path] for path in preferred if path in normalized]
        selected.extend(path for path in files if path not in selected and any(part in path.replace("\\", "/") for part in ["src/runtime/", "src/skills/", "src/memory/"]))
        return selected[:12] if len(files) > 12 else files

    def _purpose(self, files: list[str]) -> str:
        normalized = [path.replace("\\", "/") for path in files]
        if any(path.startswith("src/agents/") for path in normalized) and any(path.startswith("src/skills/") for path in normalized):
            return "这是一个本地代码工程 Agent：PlannerAgent 分类问题，SkillRouter 选择技能，技能调用文件/AST/检索/记忆工具，ContextManager 裁剪证据，AnswerAgent 输出中文回答。"
        if {"main.py", "auth.py", "database.py"}.issubset(set(normalized)):
            return "这是一个演示项目，包含启动入口、认证服务、数据库连接和工具函数。"
        return "这是一个 Python 代码项目，可从入口文件、模块定义和依赖关系理解职责。"

    def _tree(self, tree: str) -> str:
        if not tree:
            return "暂无文件树。"
        lines = tree.splitlines()
        return "\n".join(lines[:35] + (["..."] if len(lines) > 35 else []))

    def _names(self, skeleton: list[dict[str, Any]]) -> list[str]:
        names = []
        for item in skeleton:
            if item.get("type") == "class":
                members = item.get("members", [])
                names.append(f"{item.get('name')}({', '.join(members[:4])})" if members else item.get("name", ""))
            else:
                names.append(f"{item.get('name')}()")
        return [name for name in names if name]

    def _role(self, path: str, names: list[str], imports: list[str]) -> str:
        lower = path.replace("\\", "/").lower()
        if lower.endswith("main.py"):
            return "启动入口或 CLI 编排文件。"
        if "api/" in lower:
            return "FastAPI 应用、路由或请求模型层。"
        if "planner" in lower:
            return "问题分类与执行计划生成。"
        if "answer" in lower:
            return "把技能结果整理成统一中文回答。"
        if "harness" in lower:
            return "运行时编排层，串联 Planner、SkillRouter、ContextManager 和 AnswerAgent。"
        if "memory" in lower:
            return "冷/热记忆、索引或检索能力。"
        if "skill" in lower:
            return "面向任务的技能实现，负责调用真实工具。"
        if names:
            return f"业务/支撑模块，主要符号：{', '.join(names[:5])}。"
        if imports:
            return "依赖组织模块。"
        return "普通 Python 模块。"

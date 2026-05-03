"""Build and query a lightweight architecture index for the current repository."""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_KNOWLEDGE_DIR = _REPO_ROOT / ".knowledge"
_ARCHITECTURE_JSON = _KNOWLEDGE_DIR / "architecture.json"
_ARCHITECTURE_MD = _KNOWLEDGE_DIR / "ARCHITECTURE.md"

_AREA_RULES: list[tuple[str, str, str]] = [
    ("runtime/app/api", "backend_api", "后端 API 层，负责 HTTP / SSE / WebSocket 入口。"),
    ("runtime/app/core", "backend_core", "后端核心编排层，负责模型调用、工具循环、上下文与守卫。"),
    ("runtime/app/memory", "backend_memory", "长期记忆层，负责人格、用户画像、情景记忆与技能沉淀。"),
    ("runtime/app/store", "backend_store", "持久化存储层，负责 session、clarification 等数据存取。"),
    ("runtime/app/services", "backend_services", "外围服务层，负责 TTS、scheduler、音色推荐等外部能力。"),
    ("runtime/app/tools/builtin", "backend_tools", "内置工具层，提供模型可调用的工具函数。"),
    ("runtime/app/tools", "backend_tools_core", "工具注册、执行和上下文支撑。"),
    ("runtime/app/models", "backend_models", "运行时内存模型。"),
    ("runtime/app/schemas", "backend_schemas", "接口事件与请求结构定义。"),
    ("voxel-avatar/src/api", "frontend_api", "前端 API 客户端层，连接 UI 与后端运行时。"),
    ("voxel-avatar/src/audio", "frontend_audio", "前端音频层，负责播放、分析与动效同步。"),
    ("voxel-avatar/src/voxel", "frontend_voxel", "前端 3D 体素渲染层。"),
    ("voxel-avatar/src", "frontend_ui", "前端 UI 控制层。"),
    ("voxel-avatar/src-tauri", "desktop_shell", "Tauri 桌面壳层与窗口配置。"),
    ("voxel-avatar", "frontend_shell", "前端壳层与页面模板。"),
]

_SUMMARY_OVERRIDES = {
    "runtime/app/main.py": "FastAPI 应用入口，挂载路由、日志、CORS、scheduler 和启动清理逻辑。",
    "runtime/app/config.py": "运行时配置入口，定义模型、TTS、session、memory 等关键参数。",
    "runtime/app/api/chat.py": "聊天 SSE 接口，把用户消息交给 orchestrator 并持续流式返回事件。",
    "runtime/app/api/clarify.py": "补充回答接口，接收用户对澄清问题的回复。",
    "runtime/app/api/setup.py": "初始化配置接口，处理首次使用和设定相关请求。",
    "runtime/app/api/sessions.py": "会话列表与会话快照接口，支撑前端恢复旧会话。",
    "runtime/app/api/ws.py": "WebSocket 推送接口，用于主动提醒和会话级推送。",
    "runtime/app/core/orchestrator.py": "单轮对话总编排器，负责 session、工具调用、记忆注入、TTS、SSE 和错误处理。",
    "runtime/app/core/model_adapter.py": "模型适配层，负责流式对话、tool call 解析、fallback 模型与上下文长度探测。",
    "runtime/app/core/context_compressor.py": "上下文压缩层，负责在 token 压力高时整理历史。",
    "runtime/app/core/verbal_guard.py": "口头执行守卫，防止模型只承诺不真正调工具。",
    "runtime/app/core/think.py": "think 标签处理层，负责隐藏内部推理内容。",
    "runtime/app/memory/manager.py": "长期记忆管理器，负责 USER / MEMORY 文件和情景记忆库的读写与 prompt 注入。",
    "runtime/app/memory/extractor.py": "记忆提取器，负责把对话沉淀成结构化长期记忆和 episodes。",
    "runtime/app/memory/soul.py": "人格文件管理器，维护 SOUL.md 中的身份、性格和音色信息。",
    "runtime/app/memory/skills.py": "技能库查询层，提供历史技能文档检索。",
    "runtime/app/memory/skill_generator.py": "技能生成器，把反复出现的完成路径沉淀成技能文档。",
    "runtime/app/store/session_store.py": "SQLite 会话存储，负责消息持久化、会话恢复、摘要和最近历史查询。",
    "runtime/app/store/clarification_store.py": "澄清问题临时存储，用于 ask_user 等等待用户补充的流程。",
    "runtime/app/tools/registry.py": "工具注册中心，把 Python 函数自动暴露成模型可调用工具。",
    "runtime/app/tools/executor.py": "工具执行器，并发执行 tool call 并统一做超时与错误包装。",
    "runtime/app/tools/context.py": "工具上下文层，向工具暴露当前 session 等运行时信息。",
    "runtime/app/tools/builtin/memory_tool.py": "显式记忆读取工具，返回身份、长期记忆、情景记忆和最近历史。",
    "runtime/app/services/tts_service.py": "语音合成服务，把文本转换成可播报音频。",
    "runtime/app/services/scheduler.py": "提醒调度服务，管理定时提醒与主动消息。",
    "runtime/app/services/voice_recommender.py": "音色推荐与人格草稿提取服务。",
    "voxel-avatar/src/main.ts": "桌宠前端主控制器，负责输入、状态、会话面板、音频播放和 WebSocket 提醒。",
    "voxel-avatar/src/api/chat.ts": "前端聊天 API 客户端，负责 chat、clarify、sessions 等接口访问和 session_id 管理。",
    "voxel-avatar/src/audio/player.ts": "前端音频播放器，负责 base64 音频解码和播放生命周期回调。",
    "voxel-avatar/src/audio/analyzer.ts": "前端音频分析器，为体素动画提供频谱数据。",
    "voxel-avatar/src/voxel/scene.ts": "Three.js 场景搭建入口，创建相机、灯光和核心 mesh。",
    "voxel-avatar/src/voxel/grid.ts": "体素网格构建层。",
    "voxel-avatar/src/voxel/animator.ts": "体素动画层，根据音频和鼠标状态驱动立方体动效。",
    "voxel-avatar/index.html": "桌宠前端页面壳，定义透明窗口里的按钮、面板和视觉样式。",
    "voxel-avatar/src-tauri/src/lib.rs": "Tauri 启动层，处理桌面窗口创建与平台行为。",
    "voxel-avatar/src-tauri/tauri.conf.json": "Tauri 配置文件，定义窗口透明、尺寸和打包配置。",
}

_TRACKED_DIRS = [
    _REPO_ROOT / "runtime" / "app",
    _REPO_ROOT / "voxel-avatar" / "src",
]
_TRACKED_FILES = [
    _REPO_ROOT / "voxel-avatar" / "index.html",
    _REPO_ROOT / "voxel-avatar" / "src-tauri" / "src" / "lib.rs",
    _REPO_ROOT / "voxel-avatar" / "src-tauri" / "tauri.conf.json",
]
_TRACKED_SUFFIXES = {".py", ".ts", ".tsx", ".rs", ".json", ".html"}

_PY_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "websocket"}


def _repo_rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _safe_read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _area_for(rel_path: str) -> dict[str, str]:
    for prefix, area_key, description in _AREA_RULES:
        if rel_path.startswith(prefix):
            return {"key": area_key, "description": description}
    return {"key": "other", "description": "其他未归类模块。"}


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root in _TRACKED_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in _TRACKED_SUFFIXES:
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts or "dist" in path.parts:
                continue
            if path.name == "__init__.py":
                continue
            files.append(path)
    for path in _TRACKED_FILES:
        if path.exists() and path.is_file():
            files.append(path)
    deduped = sorted({path.resolve() for path in files})
    return deduped


def _latest_source_mtime() -> float:
    mtimes = [path.stat().st_mtime for path in _iter_source_files()]
    return max(mtimes, default=0.0)


def _docstring_summary(tree: ast.AST) -> str:
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    first = doc.strip().splitlines()[0].strip()
    return first.rstrip(".")


def _python_routes(tree: ast.AST, rel_path: str) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "router":
                continue
            method = func.attr.lower()
            if method not in _PY_ROUTE_METHODS:
                continue
            raw_path = ""
            if decorator.args and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
                raw_path = decorator.args[0].value
            full_path = f"/v1{raw_path}" if raw_path.startswith("/") else raw_path
            protocol = "websocket" if method == "websocket" else "http"
            routes.append({
                "protocol": protocol,
                "method": method.upper() if method != "websocket" else "WS",
                "path": full_path,
                "handler": node.name,
                "source": rel_path,
            })
    return routes


def _python_symbols(tree: ast.AST) -> tuple[list[str], list[str]]:
    classes: list[str] = []
    functions: list[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
    return classes[:10], functions[:14]


def _python_imports(tree: ast.AST) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module if node.level == 0 else f".{module}")
    return sorted({item for item in imports if item})[:20]


def _ts_imports(text: str) -> list[str]:
    matches = re.findall(r"from ['\"]([^'\"]+)['\"]", text)
    return sorted({item for item in matches})[:20]


def _ts_symbols(text: str) -> list[str]:
    patterns = [
        r"export\s+async\s+function\s+([A-Za-z0-9_]+)",
        r"export\s+function\s+([A-Za-z0-9_]+)",
        r"async\s+function\s+([A-Za-z0-9_]+)",
        r"function\s+([A-Za-z0-9_]+)",
        r"const\s+([A-Za-z0-9_]+)\s*=\s*\(",
    ]
    results: list[str] = []
    for pattern in patterns:
        results.extend(re.findall(pattern, text))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in results:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped[:14]


def _frontend_calls(text: str) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    for match in re.finditer(r"fetch\(\s*`?\$\{BASE_URL\}([^`\"']+)", text):
        calls.append({"protocol": "http", "path": f"/v1{match.group(1).replace('${', '{').replace('}', '}')}" if not match.group(1).startswith("/v1") else match.group(1)})
    for match in re.finditer(r"new\s+WebSocket\(\s*`?ws://[^/]+(/[^`\"']+)", text):
        calls.append({"protocol": "websocket", "path": match.group(1).replace("${", "{").replace("}", "}")})
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in calls:
        key = (item["protocol"], item["path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _tags_for(rel_path: str, summary: str, area_key: str) -> list[str]:
    tags = {area_key}
    lowered = f"{rel_path} {summary}".lower()
    for token in ("session", "memory", "tool", "audio", "voice", "scheduler", "websocket", "sse", "tts", "setup"):
        if token in lowered:
            tags.add(token)
    if "前端" in summary:
        tags.add("frontend")
    if "后端" in summary:
        tags.add("backend")
    return sorted(tags)


def _fallback_summary(rel_path: str, area_description: str) -> str:
    filename = Path(rel_path).stem
    return f"{filename} 所在模块，归属：{area_description}"


def _summary_for(rel_path: str, tree: ast.AST | None = None) -> str:
    if rel_path in _SUMMARY_OVERRIDES:
        return _SUMMARY_OVERRIDES[rel_path]
    if tree is not None:
        doc = _docstring_summary(tree)
        if doc:
            return doc
    area = _area_for(rel_path)
    return _fallback_summary(rel_path, area["description"])


def _build_module_entry(path: Path) -> dict[str, Any]:
    rel_path = _repo_rel(path)
    text = _safe_read(path)
    area = _area_for(rel_path)
    suffix = path.suffix
    classes: list[str] = []
    functions: list[str] = []
    imports: list[str] = []
    routes: list[dict[str, str]] = []
    outbound_calls: list[dict[str, str]] = []
    tree: ast.AST | None = None

    if suffix == ".py":
        try:
            tree = ast.parse(text)
            classes, functions = _python_symbols(tree)
            imports = _python_imports(tree)
            routes = _python_routes(tree, rel_path)
        except SyntaxError:
            pass
    elif suffix in {".ts", ".tsx"}:
        imports = _ts_imports(text)
        functions = _ts_symbols(text)
        outbound_calls = _frontend_calls(text)

    summary = _summary_for(rel_path, tree=tree)
    return {
        "path": rel_path,
        "kind": suffix.lstrip("."),
        "area": area["key"],
        "area_description": area["description"],
        "summary": summary,
        "classes": classes,
        "functions": functions,
        "imports": imports,
        "routes": routes,
        "outbound_calls": outbound_calls,
        "tags": _tags_for(rel_path, summary, area["key"]),
    }


def _build_endpoints(modules: list[dict[str, Any]]) -> list[dict[str, str]]:
    endpoints: list[dict[str, str]] = []
    for module in modules:
        for route in module.get("routes", []):
            endpoints.append({
                "protocol": route["protocol"],
                "method": route["method"],
                "path": route["path"],
                "source": route["source"],
                "handler": route["handler"],
                "summary": module["summary"],
            })
    return endpoints


def _build_connections(modules: list[dict[str, Any]], endpoints: list[dict[str, str]]) -> list[dict[str, str]]:
    endpoint_paths = {item["path"]: item for item in endpoints}
    connections: list[dict[str, str]] = [
        {
            "from": "voxel-avatar/src/main.ts",
            "to": "voxel-avatar/src/api/chat.ts",
            "kind": "frontend-call",
            "note": "前端主控制器通过 chat 客户端发起聊天、恢复会话和澄清请求。",
        },
        {
            "from": "runtime/app/api/chat.py",
            "to": "runtime/app/core/orchestrator.py",
            "kind": "backend-call",
            "note": "聊天入口把每条消息交给 orchestrator 生成 SSE 事件流。",
        },
        {
            "from": "runtime/app/core/orchestrator.py",
            "to": "runtime/app/memory/manager.py",
            "kind": "backend-call",
            "note": "编排器在回答前注入记忆，在回答后触发长期记忆沉淀。",
        },
        {
            "from": "runtime/app/core/orchestrator.py",
            "to": "runtime/app/tools/executor.py",
            "kind": "backend-call",
            "note": "编排器把 tool call 交给工具执行器并继续多轮推理。",
        },
    ]

    for module in modules:
        for call in module.get("outbound_calls", []):
            target = endpoint_paths.get(call["path"])
            note = target["summary"] if target else "前端调用的后端端点。"
            connections.append({
                "from": module["path"],
                "to": call["path"],
                "kind": call["protocol"],
                "note": note,
            })
    return connections


def _build_overview(modules: list[dict[str, Any]], endpoints: list[dict[str, str]], connections: list[dict[str, str]]) -> dict[str, Any]:
    area_counts: dict[str, int] = {}
    for module in modules:
        area_counts[module["area"]] = area_counts.get(module["area"], 0) + 1

    areas = [
        {
            "key": area_key,
            "path": prefix,
            "description": description,
            "module_count": area_counts.get(area_key, 0),
        }
        for prefix, area_key, description in _AREA_RULES
        if area_counts.get(area_key, 0)
    ]

    return {
        "summary": (
            "这是一个桌宠秘书项目：前端由 Tauri/Vite 驱动透明桌面窗口和体素动画，"
            "后端由 FastAPI 提供聊天、会话恢复、提醒和 WebSocket 推送。"
            "核心业务编排集中在 orchestrator，长期记忆集中在 memory 模块。"
        ),
        "areas": areas,
        "key_flows": [
            "聊天主链路：voxel-avatar/src/main.ts -> voxel-avatar/src/api/chat.ts -> /v1/chat -> orchestrator -> tools/memory/TTS -> SSE/音频回前端。",
            "会话恢复链路：前端会话面板 -> /v1/sessions 与 /v1/sessions/{id} -> session_store -> 前端重连 /v1/ws/{session_id}。",
            "记忆链路：orchestrator 在回答前从 SOUL / USER / MEMORY / episodes 注入，在回答后触发 extractor 回写长期记忆。",
        ],
        "endpoint_count": len(endpoints),
        "connection_count": len(connections),
        "module_count": len(modules),
    }


def _render_markdown(index: dict[str, Any]) -> str:
    lines = [
        "# 项目架构索引",
        "",
        f"- 生成时间：{index['generated_at']}",
        f"- 仓库根目录：{index['repo_root']}",
        "",
        "## 概览",
        index["overview"]["summary"],
        "",
        "## 关键链路",
    ]
    lines.extend(f"- {item}" for item in index["overview"]["key_flows"])
    lines.append("")
    lines.append("## 架构分区")
    for area in index["overview"]["areas"]:
        lines.append(f"- `{area['path']}`：{area['description']}（{area['module_count']} 个模块）")

    lines.append("")
    lines.append("## 后端接口")
    for endpoint in index["endpoints"]:
        lines.append(
            f"- `{endpoint['method']} {endpoint['path']}` -> `{endpoint['source']}`::{endpoint['handler']}：{endpoint['summary']}"
        )

    lines.append("")
    lines.append("## 前后端连接")
    for connection in index["connections"][:12]:
        lines.append(f"- `{connection['from']}` -> `{connection['to']}`：{connection['note']}")

    lines.append("")
    lines.append("## 关键模块")
    for module in index["modules"][:18]:
        lines.append(f"- `{module['path']}`：{module['summary']}")
    return "\n".join(lines).strip()


class ArchitectureIndexer:
    def __init__(self) -> None:
        self.repo_root = _REPO_ROOT
        self.knowledge_dir = _KNOWLEDGE_DIR
        self.json_path = _ARCHITECTURE_JSON
        self.markdown_path = _ARCHITECTURE_MD

    def is_stale(self) -> bool:
        if not self.json_path.exists() or not self.markdown_path.exists():
            return True
        return self.json_path.stat().st_mtime < _latest_source_mtime()

    def build(self) -> dict[str, Any]:
        modules = [_build_module_entry(path) for path in _iter_source_files()]
        modules.sort(key=lambda item: item["path"])
        endpoints = _build_endpoints(modules)
        connections = _build_connections(modules, endpoints)
        index = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(self.repo_root),
            "modules": modules,
            "endpoints": endpoints,
            "connections": connections,
        }
        index["overview"] = _build_overview(modules, endpoints, connections)

        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        self.markdown_path.write_text(_render_markdown(index), encoding="utf-8")
        return index

    def ensure_index(self, force: bool = False) -> dict[str, Any]:
        if force or self.is_stale():
            return self.build()
        return self.load()

    def load(self) -> dict[str, Any]:
        if not self.json_path.exists():
            return self.build()
        return json.loads(self.json_path.read_text(encoding="utf-8"))


architecture_indexer = ArchitectureIndexer()

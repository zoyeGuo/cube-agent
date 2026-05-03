# -*- coding: utf-8 -*-
"""Architecture knowledge tool — expose project structure and code flows."""
from __future__ import annotations

from typing import Any

from app.knowledge import architecture_indexer
from app.tools.registry import tool

_SEARCH_KEYWORDS = (
    "架构", "结构", "模块", "路由", "接口", "前端", "后端", "会话", "记忆", "工具",
    "音频", "语音", "提醒", "scheduler", "session", "memory", "tool", "websocket",
    "sse", "tts", "tauri", "体素", "动画", "恢复", "聊天", "链路", "流程", "实现",
)

_OVERVIEW_PRIORITY = [
    "runtime/app/main.py",
    "runtime/app/core/orchestrator.py",
    "runtime/app/memory/manager.py",
    "runtime/app/memory/extractor.py",
    "runtime/app/store/session_store.py",
    "runtime/app/api/chat.py",
    "runtime/app/api/sessions.py",
    "voxel-avatar/src/main.ts",
    "voxel-avatar/src/api/chat.ts",
    "voxel-avatar/index.html",
]

_TOPIC_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("memory", ("记忆", "长期记忆", "user.md", "memory.md", "episodes", "回写", "注入")),
    ("session", ("会话", "session", "恢复", "新对话", "会话面板", "sessions")),
    ("tooling", ("工具", "tool", "tool call", "函数调用", "执行器", "registry")),
    ("audio", ("语音", "音频", "tts", "播报", "体素", "动画", "声音", "音色")),
    ("frontend_backend", ("前端", "后端", "接口", "websocket", "sse", "连", "连接", "链路")),
]

_TOPIC_FILES: dict[str, list[str]] = {
    "overview": [
        "voxel-avatar/src/main.ts",
        "voxel-avatar/src/api/chat.ts",
        "runtime/app/api/chat.py",
        "runtime/app/core/orchestrator.py",
        "runtime/app/memory/manager.py",
        "runtime/app/store/session_store.py",
    ],
    "frontend_backend": [
        "voxel-avatar/src/main.ts",
        "voxel-avatar/src/api/chat.ts",
        "runtime/app/api/chat.py",
        "runtime/app/api/ws.py",
        "runtime/app/core/orchestrator.py",
    ],
    "memory": [
        "runtime/app/core/orchestrator.py",
        "runtime/app/memory/manager.py",
        "runtime/app/memory/extractor.py",
        "runtime/app/memory/soul.py",
        "runtime/app/tools/builtin/memory_tool.py",
        "runtime/app/store/session_store.py",
    ],
    "session": [
        "voxel-avatar/src/main.ts",
        "voxel-avatar/src/api/chat.ts",
        "runtime/app/api/sessions.py",
        "runtime/app/store/session_store.py",
        "runtime/app/api/ws.py",
    ],
    "tooling": [
        "runtime/app/core/orchestrator.py",
        "runtime/app/tools/registry.py",
        "runtime/app/tools/executor.py",
        "runtime/app/tools/builtin/memory_tool.py",
        "runtime/app/tools/builtin/architecture_tool.py",
    ],
    "audio": [
        "voxel-avatar/src/main.ts",
        "voxel-avatar/src/audio/player.ts",
        "voxel-avatar/src/voxel/animator.ts",
        "runtime/app/services/tts_service.py",
        "runtime/app/services/voice_recommender.py",
    ],
}

_LOCATION_DETAIL_KEYWORDS = (
    "文件", "路径", "位置", "在哪", "落点", "入口", "源码", "代码", "模块", "目录", "哪些文件",
)
_INTERFACE_DETAIL_KEYWORDS = (
    "接口", "路由", "endpoint", "api", "ws", "websocket", "sse", "/v1/",
)
_CONNECTION_DETAIL_KEYWORDS = (
    "连接", "关系", "依赖", "调用", "链路", "流程", "怎么走", "经过哪些",
)


def _query_terms(query: str) -> list[str]:
    query = query.strip()
    if not query:
        return []
    terms = [query]
    for keyword in _SEARCH_KEYWORDS:
        if keyword.lower() in query.lower():
            terms.append(keyword)
    for part in query.replace("，", " ").replace("。", " ").replace("、", " ").split():
        terms.append(part)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        term = term.strip()
        if len(term) < 2:
            continue
        lowered = term.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(term)
    return deduped[:12]


def _score_text(text: str, terms: list[str]) -> int:
    haystack = text.lower()
    score = 0
    for term in terms:
        lowered = term.lower()
        if lowered in haystack:
            score += 6 if lowered == haystack else 3
    return score


def _score_module(module: dict[str, Any], terms: list[str]) -> int:
    text = " ".join([
        module.get("path", ""),
        module.get("summary", ""),
        module.get("area_description", ""),
        " ".join(module.get("imports", [])),
        " ".join(module.get("functions", [])),
        " ".join(module.get("classes", [])),
        " ".join(module.get("tags", [])),
    ])
    score = _score_text(text, terms)
    for route in module.get("routes", []):
        score += _score_text(" ".join(route.values()), terms)
    for call in module.get("outbound_calls", []):
        score += _score_text(" ".join(call.values()), terms)
    return score


def _score_endpoint(endpoint: dict[str, str], terms: list[str]) -> int:
    return _score_text(" ".join(endpoint.values()), terms)


def _score_connection(connection: dict[str, str], terms: list[str]) -> int:
    return _score_text(" ".join(connection.values()), terms)


def _module_line(module: dict[str, Any]) -> str:
    pieces = [f"- {module['path']}：{module['summary']}"]
    public_functions = [item for item in module.get("functions", []) if not item.startswith("_")]
    private_functions = [item for item in module.get("functions", []) if item.startswith("_")]
    symbols = public_functions[:4] + module.get("classes", [])[:3] + private_functions[:2]
    if symbols:
        pieces.append(f"  关键符号：{', '.join(symbols[:6])}")
    routes = module.get("routes", [])[:3]
    if routes:
        pieces.append("  路由：" + "；".join(f"{item['method']} {item['path']}" for item in routes))
    calls = module.get("outbound_calls", [])[:3]
    if calls:
        pieces.append("  外部连接：" + "；".join(f"{item['protocol']} {item['path']}" for item in calls))
    return "\n".join(pieces)


def _endpoint_line(endpoint: dict[str, str]) -> str:
    return f"- {endpoint['method']} {endpoint['path']} -> {endpoint['source']}::{endpoint['handler']}"


def _connection_line(connection: dict[str, str]) -> str:
    return f"- {connection['from']} -> {connection['to']}：{connection['note']}"


def _overview_modules(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path = {module["path"]: module for module in modules}
    selected: list[dict[str, Any]] = []
    for path in _OVERVIEW_PRIORITY:
        module = by_path.get(path)
        if module:
            selected.append(module)
    return selected[:8]


def _frontend_referenced_endpoints(index: dict[str, Any]) -> list[dict[str, str]]:
    frontend_calls: list[str] = []
    for module in index["modules"]:
        if not str(module.get("path", "")).startswith("voxel-avatar/"):
            continue
        for call in module.get("outbound_calls", []):
            frontend_calls.append(call["path"])

    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for endpoint in index["endpoints"]:
        endpoint_prefix = endpoint["path"].split("{", 1)[0]
        for call_path in frontend_calls:
            call_prefix = call_path.split("{", 1)[0]
            if endpoint_prefix and (endpoint_prefix in call_prefix or call_prefix in endpoint_prefix):
                if endpoint["path"] in seen:
                    break
                seen.add(endpoint["path"])
                selected.append(endpoint)
                break
    return selected


def _module_by_path(index: dict[str, Any], path: str) -> dict[str, Any] | None:
    for module in index["modules"]:
        if module.get("path") == path:
            return module
    return None


def _pick_topic(query: str) -> str:
    lowered = query.lower()
    for topic, keywords in _TOPIC_KEYWORDS:
        if any(keyword.lower() in lowered for keyword in keywords):
            return topic
    if "前端" in query and "后端" in query:
        return "frontend_backend"
    return "overview"


def _wants_location_detail(query: str) -> bool:
    return any(keyword.lower() in query.lower() for keyword in _LOCATION_DETAIL_KEYWORDS)


def _wants_interface_detail(query: str) -> bool:
    return any(keyword.lower() in query.lower() for keyword in _INTERFACE_DETAIL_KEYWORDS)


def _wants_connection_detail(query: str) -> bool:
    return any(keyword.lower() in query.lower() for keyword in _CONNECTION_DETAIL_KEYWORDS)


def _topic_explanation(index: dict[str, Any], topic: str) -> tuple[list[str], list[str], list[str]]:
    if topic == "frontend_backend":
        summary = [
            "这个项目不是单体页面，而是“桌面前端壳 + 后端运行时”的双层结构。",
            "前端负责窗口、输入、会话面板、音频播放和体素动画；真正的对话编排、工具调用、记忆读写都在后端。",
        ]
        flow = [
            "用户在桌宠输入框发消息，voxel-avatar/src/main.ts 先把输入交给 voxel-avatar/src/api/chat.ts。",
            "chat.ts 通过 /v1/chat 发起 SSE 请求，runtime/app/api/chat.py 把请求交给 orchestrator。",
            "orchestrator 调模型、工具、记忆和 TTS，再把文字事件、音频事件流式回前端。",
            "前端收到文字后更新气泡，收到音频后播放，同时驱动体素动画；主动提醒则走 /v1/ws/{session_id} 的 WebSocket。",
        ]
        why = [
            "这样拆分后，UI 和业务编排是分开的：前端保持轻，后端可以单独演进模型、工具和记忆系统。",
        ]
        return summary, flow, why
    if topic == "memory":
        summary = [
            "这套记忆不是前端缓存，而是后端维护的一层长期记忆系统。",
            "回答前它会把身份、用户画像、长期记忆和情景记忆注入进去；回答后再把值得保留的新信息沉淀回去。",
        ]
        flow = [
            "进入一轮对话时，orchestrator 先从 SOUL、USER、MEMORY 和 episodes 里取相关内容拼成系统提示。",
            "如果用户显式问“你记得吗”“读取记忆”，模型会优先走 read_memory 工具读取持久化内容。",
            "回答完成后，extractor 会把这轮内容整理成结构化 USER/MEMORY 条目和一条 episode，写回本地存储。",
            "session_store 还会额外保留完整会话历史，作为显式回忆和身份兜底来源。",
        ]
        why = [
            "所以它不是“全靠上下文窗口记住”，而是“有注入层，也有回写层”，只是回写质量之前还比较薄，现在正在被收紧。",
        ]
        return summary, flow, why
    if topic == "session":
        summary = [
            "会话层的核心是 session_id，不是前端页面状态。",
            "前端只是保存当前 session_id，真正的会话内容、摘要和最近消息都在后端 SQLite 里。",
        ]
        flow = [
            "新对话时前端清掉 session_id，下一条消息会让后端创建新 session。",
            "恢复旧会话时，前端先调 /v1/sessions 和 /v1/sessions/{id} 拿摘要与快照。",
            "确认切回后，前端把当前 session_id 改回去，并重新连上对应的 WebSocket 推送通道。",
        ]
        why = [
            "这样做的好处是：前端可以随时切会话，但历史、摘要和主动提醒都还是后端统一持有。",
        ]
        return summary, flow, why
    if topic == "tooling":
        summary = [
            "工具链路的核心不是“模型直接执行代码”，而是“模型决策，后端代执行”。",
            "orchestrator 负责多轮推理，tool registry 负责暴露能力，executor 负责真正跑工具。",
        ]
        flow = [
            "模型先在 stream_with_tools 里决定要不要发起 tool call。",
            "如果发起了，orchestrator 会把调用描述追加进消息，再交给 tools/executor.py 并发执行。",
            "工具执行结果会以 tool message 的形式回到下一轮推理，让模型继续规划或直接给答案。",
            "这也是记忆工具、架构工具、提醒工具能被统一接进对话系统的原因。",
        ]
        why = [
            "所以工具系统本质上是一个“模型可规划、后端可控”的能力层，不是简单的函数列表。",
        ]
        return summary, flow, why
    if topic == "audio":
        summary = [
            "音频和体素动画是串起来的，不是各自独立跑。",
            "后端只负责把最终文本转成音频，前端负责解码播放、频谱分析和立方体动效。",
        ]
        flow = [
            "orchestrator 在文本定稿后调用 TTS，生成 base64 音频事件。",
            "前端收到 audio 事件后，用 audio/player.ts 解码并开始播放。",
            "播放时 analyzer 持续提供频谱数据，voxel/animator.ts 根据音频活动驱动体素动画。",
        ]
        why = [
            "所以这部分架构的关键不是 TTS 本身，而是“声音播放生命周期”和“动画状态机”被前端接在了一起。",
        ]
        return summary, flow, why

    summary = [
        "这个项目可以把它理解成一个有长期记忆和工具能力的桌宠秘书。",
        "前端负责桌面形态和互动感，后端负责真正的智能编排、持久化和外部能力调用。",
    ]
    flow = [
        "聊天主链路：前端输入 -> chat.ts -> /v1/chat -> orchestrator -> 模型/工具/记忆/TTS -> SSE 与音频回前端。",
        "会话链路：session_id 决定当前上下文，会话摘要和消息存放在 session_store 里，前端可以随时恢复。",
        "记忆链路：回答前注入，回答后沉淀，长期信息留在 SOUL/USER/MEMORY/episodes。",
    ]
    why = [
        "所以这不是一个单纯的聊天页面，而是“前端桌宠壳 + 后端 agent runtime + 持久化记忆/会话层”的三层组合。",
    ]
    return summary, flow, why


def _topic_key_modules(index: dict[str, Any], topic: str) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for path in _TOPIC_FILES.get(topic, _TOPIC_FILES["overview"]):
        module = _module_by_path(index, path)
        if module:
            modules.append(module)
    return modules[:5]


@tool
def read_architecture(query: str = "", refresh: bool = False) -> str:
    """读取项目架构索引。query: 想查询的架构问题，例如“记忆链路”“前端怎么连后端”；refresh: 是否强制重建索引。"""
    index = architecture_indexer.ensure_index(force=refresh)
    overview = index["overview"]
    terms = _query_terms(query)
    topic = _pick_topic(query) if query.strip() else "overview"
    wants_location_detail = _wants_location_detail(query)
    wants_interface_detail = _wants_interface_detail(query)
    wants_connection_detail = _wants_connection_detail(query)
    summary_lines, flow_lines, why_lines = _topic_explanation(index, topic)

    lines = [
        f"[架构索引] 生成时间：{index['generated_at']}",
        f"[索引文件] {architecture_indexer.markdown_path} | {architecture_indexer.json_path}",
    ]

    if not query.strip():
        lines.append("")
        lines.append("[理解摘要]")
        lines.extend(summary_lines)
        lines.append("")
        lines.append("[主链路]")
        lines.extend(f"- {item}" for item in flow_lines)
        lines.append("")
        lines.append("[为什么这么分]")
        lines.extend(why_lines)
        lines.append("")
        lines.append("[关键落点]")
        for module in _topic_key_modules(index, topic) or _overview_modules(index["modules"]):
            lines.append(f"- {module['path']}：{module['summary']}")
        return "\n".join(lines)

    scored_modules = sorted(
        (
            (module, _score_module(module, terms))
            for module in index["modules"]
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    scored_endpoints = sorted(
        (
            (endpoint, _score_endpoint(endpoint, terms))
            for endpoint in index["endpoints"]
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    scored_connections = sorted(
        (
            (connection, _score_connection(connection, terms))
            for connection in index["connections"]
        ),
        key=lambda item: item[1],
        reverse=True,
    )

    top_modules = [item for item, score in scored_modules if score > 0][:6]
    top_endpoints = [item for item, score in scored_endpoints if score > 0][:5]
    top_connections = [item for item, score in scored_connections if score > 0][:5]

    if topic == "frontend_backend":
        for endpoint in _frontend_referenced_endpoints(index):
            if endpoint not in top_endpoints:
                top_endpoints.append(endpoint)
        top_endpoints = top_endpoints[:6]

    lines.append("")
    lines.append(f"[查询] {query}")
    lines.append("")
    lines.append("[理解摘要]")
    lines.extend(summary_lines)
    lines.append("")
    lines.append("[主链路]")
    lines.extend(f"- {item}" for item in flow_lines)
    lines.append("")
    lines.append("[为什么是这样]")
    lines.extend(why_lines)

    key_modules = _topic_key_modules(index, topic) or top_modules[:5]
    if key_modules:
        lines.append("")
        lines.append("[关键落点]" if wants_location_detail else "[定位锚点]")
        for module in key_modules[: (4 if wants_location_detail else 2)]:
            lines.append(f"- {module['path']}：{module['summary']}")
    if top_endpoints and (wants_interface_detail or (topic == "frontend_backend" and wants_connection_detail)):
        lines.append("")
        lines.append("[关键接口]")
        lines.extend(_endpoint_line(endpoint) for endpoint in top_endpoints[:3])
    if top_connections and wants_connection_detail:
        lines.append("")
        lines.append("[关键连接]")
        lines.extend(_connection_line(connection) for connection in top_connections[:3])
    if not key_modules and not top_endpoints and not top_connections:
        lines.append("")
        lines.append("[补充总览]")
        lines.append(overview["summary"])
        lines.extend(f"- {item}" for item in overview["key_flows"])

    return "\n".join(lines)

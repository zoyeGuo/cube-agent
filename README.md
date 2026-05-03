# Secretary — 数字人桌面助手

带体素动画形象、实时语音、长期记忆、工具调用和主动提醒能力的桌面 AI 助手。

---

## 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                      桌面应用 (Tauri v2)                      │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                前端 (TypeScript + Three.js)              │ │
│  │                                                         │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐   │ │
│  │  │ Voxel Avatar│  │Context Panel│  │   Chat Bar    │   │ │
│  │  │ 6×6×6 体素  │  │ (通用选择   │  │  输入 + 状态   │   │ │
│  │  │ Möbius 环   │  │  后端驱动)  │  │               │   │ │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬────────┘   │ │
│  │         │                │                │             │ │
│  │         └────────────────┴────────────────┘             │ │
│  │                          │ SSE / fetch                  │ │
│  │                          │ WebSocket (主动推送)           │ │
│  └──────────────────────────┼──────────────────────────────┘ │
└─────────────────────────────┼────────────────────────────────┘
                              │ HTTP + WS (localhost)
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                    后端 (FastAPI / Python)                    │
│                                                              │
│  POST /v1/chat ──► Intent Router ──► Orchestrator            │
│  POST /v1/choice      直接路由         推理 + 工具循环         │
│  POST /v1/clarify     (无需 LLM)       PendingAction 确认     │
│  POST /v1/cancel                      自我反思               │
│  GET  /v1/soul                                               │
│  WS   /v1/ws/{sid}                                           │
│                                                              │
│         ┌────────────────┬───────────────────┐              │
│         ▼                ▼                   ▼              │
│   Model Adapter    Context Compressor    Memory Manager      │
│   OpenAI compat    Hermes 50% threshold  SOUL / MEMORY       │
│   stream + tools   LLM 摘要压缩          USER / episodes     │
│                                          FTS5 情景检索       │
│         │                ▼                   ▼              │
│         │          Session Store       Background            │
│         │          SQLite 持久化       Extractor             │
│         ▼                             (asyncio.Task)         │
│       TTS Service          APScheduler                       │
│       Markdown 清洗        定时提醒 / 主动推送                │
└──────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼──────────────────┐
         ▼                    ▼                  ▼
  MiniMax M2.7         MiniMax Speech       DuckDuckGo
  大语言模型            2.8 HD TTS          网页搜索
```

---

## 技术栈

### 前端
| 技术 | 用途 |
|------|------|
| **Tauri v2** | 桌面壳 (Rust)，无边框透明窗口 600×500 |
| **TypeScript** | 前端主逻辑 |
| **Three.js** | 体素网格 + Möbius 环动画渲染 |
| **Web Audio API** | 音频解码、播放、FFT 频谱分析 |
| **WebSocket** | 后端主动推送（提醒、通知），25s 心跳保活 |
| **Vite** | 构建工具 |

### 后端
| 技术 | 用途 |
|------|------|
| **FastAPI** | HTTP + SSE + WebSocket |
| **Python 3.11+** | 运行时 |
| **openai SDK** | OpenAI 兼容接口，流式工具调用 |
| **SQLite** | Session 持久化 + FTS5（情景记忆检索）|
| **APScheduler** | 异步定时任务，驱动主动提醒 |
| **dateparser** | 自然语言时间解析（"明天早上8点"）|
| **httpx[socks]** | HTTP 客户端，显式代理配置（trust_env=False）|
| **ddgs / primp** | DuckDuckGo 搜索（Rust HTTP 核心）|
| **pyyaml** | Skills SKILL.md 文件解析 |
| **pydantic-settings** | 配置 + 环境变量管理 |

### 外部服务
| 服务 | 用途 |
|------|------|
| **MiniMax MiniMax-M2.7** | 主模型，工具调用 + 流式输出 |
| **MiniMax Speech-2.8-HD** | TTS，20+ 中文音色，MP3 输出 |

---

## 目录结构

```
secretary/
├── README.md
├── MEMORY_ARCHITECTURE.md            # 记忆系统详细架构文档
├── SKILLS_ARCHITECTURE.md            # Skills 系统详细架构文档
├── runtime/                          # 后端
│   ├── app/
│   │   ├── main.py                   # FastAPI 入口 + lifespan + 日志 + 调度器
│   │   ├── config.py                 # 环境变量配置
│   │   ├── api/
│   │   │   ├── chat.py               # POST /v1/chat  SSE 流式端点
│   │   │   ├── setup.py              # POST /v1/choice  GET /v1/soul
│   │   │   ├── clarify.py            # POST /v1/clarify  澄清问题回答
│   │   │   ├── cancel.py             # POST /v1/cancel  取消当前请求
│   │   │   ├── sessions.py           # GET /v1/sessions  会话管理
│   │   │   └── ws.py                 # WS /v1/ws/{session_id}  主动推送通道
│   │   ├── core/
│   │   │   ├── orchestrator.py       # 主调度：Action Engine → 推理循环 → 反思 → 记忆
│   │   │   ├── action_engine.py      # 能力注册表：CAPABILITIES dict + ExecutionReceipt + 重试
│   │   │   ├── intent_router.py      # 意图分类：规则+LLM 判断是否为直接操作（只分类，不提参数）
│   │   │   ├── slot_extractor.py     # 槽位提取：规则+LLM 提取操作所需参数
│   │   │   ├── model_adapter.py      # OpenAI 兼容 + 流式 tool call 累积
│   │   │   ├── think.py              # <think> 块剥离 + 规划指令注入
│   │   │   ├── context_compressor.py # 上下文压缩（Hermes 50% 阈值）
│   │   │   ├── action_verifier.py    # 操作结果验证（文件存在？内容匹配？提醒创建？）
│   │   │   └── verbal_guard.py       # 口头执行检测（已重新启用，正则+模式匹配）
│   │   ├── memory/
│   │   │   ├── soul.py               # SOUL.md 读写（人格 + 音色 + 用户名）
│   │   │   ├── manager.py            # MEMORY.md / USER.md + SQLite 情景记忆
│   │   │   ├── extractor.py          # 后台提取：长期记忆 + 情景记录 + importance
│   │   │   ├── skills.py             # SkillsManager：SKILL.md 文件存储 + 关键词检索
│   │   │   └── skill_generator.py    # 后台技能生成（复杂工具调用后触发）
│   │   ├── services/
│   │   │   ├── tts_service.py        # MiniMax TTS，Markdown/emoji 清洗
│   │   │   ├── voice_recommender.py  # LLM 音色推荐 + soul 信息提取
│   │   │   └── scheduler.py          # APScheduler 封装，fire_reminder
│   │   ├── store/
│   │   │   ├── session_store.py      # SQLite session 持久化
│   │   │   ├── pending_action_store.py # Human-in-the-loop 确认队列（asyncio.Future）
│   │   │   ├── clarification_store.py  # 澄清问题暂停/恢复（asyncio.Future）
│   │   │   └── request_store.py      # 当前活跃请求追踪（用于取消）
│   │   ├── models/
│   │   │   ├── session.py            # Session 数据模型
│   │   │   └── pending_action.py     # PendingAction / PendingActionStep
│   │   ├── knowledge/
│   │   │   └── indexer.py            # 架构知识库索引（启动时构建）
│   │   ├── tools/
│   │   │   ├── registry.py           # @tool 装饰器，自动 JSON schema
│   │   │   ├── executor.py           # asyncio.gather 并发执行 + __CONFIRM__ / __ASK_USER__ / __SHOW_SCHEDULE__ 拦截
│   │   │   ├── context.py            # ContextVar：session_id 传递给工具
│   │   │   └── builtin/
│   │   │       ├── datetime_tool.py  # 当前日期时间
│   │   │       ├── calculator_tool.py# AST 安全数学求值
│   │   │       ├── search_tool.py    # DuckDuckGo 网页搜索
│   │   │       ├── system_tool.py    # 跨平台打开文件
│   │   │       ├── voice_tool.py     # 触发音色选择面板
│   │   │       ├── reminder_tool.py  # 设置定时提醒（自然语言时间）
│   │   │       ├── schedule_tool.py  # 列出/取消已有提醒（__SHOW_SCHEDULE__ sentinel）
│   │   │       ├── clarify_tool.py   # 向用户提问并等待回答（__ASK_USER__ sentinel）
│   │   │       ├── memory_tool.py    # 读取长期记忆（read_memory，按 topic 分类返回）
│   │   │       ├── architecture_tool.py # 查询项目架构知识库（read_architecture）
│   │   │       ├── file_tool.py      # 文件读写删除修改 + 目录列出（路径白名单保护）
│   │   │       ├── code_tool.py      # Python 沙箱执行（subprocess 隔离 + 超时 + 路径归一化）
│   │   │       └── command_tool.py   # Shell 命令执行（安全策略过滤 + 超时）
│   │   └── schemas/events.py         # SSE 事件类型（Pydantic）
│   └── requirements.txt
│
└── voxel-avatar/                     # 前端
    ├── index.html                    # 布局 + CSS（无框架）
    ├── src/
    │   ├── main.ts                   # 主逻辑：chat + WebSocket + Context Panel
    │   ├── api/chat.ts               # SSE 客户端，sendMessage / submitChoice
    │   ├── audio/
    │   │   ├── player.ts             # Base64 解码 + onStart/onEnded 同步回调
    │   │   └── analyzer.ts           # Web Audio FFT 频谱
    │   └── voxel/
    │       ├── scene.ts              # Three.js 场景 + 灯光
    │       ├── grid.ts               # 6×6×6 体素网格
    │       └── animator.ts           # Möbius 动画 + 音频响应
    └── src-tauri/
        └── tauri.conf.json           # 无边框透明窗口
```

---

## 核心机制

### 推理层（完整流程）

```
用户消息
  ↓
[会话压缩检查] compact_history（按 turn 数触发，LLM 摘要旧轮次）
  ↓
[上下文压缩检查] ContextCompressor（按 token 比例触发）
  ↓
[System Prompt 组装]
  SOUL.md → base_prompt → MEMORY.md → USER.md
  → 相关情景记忆 → 可用技能
  → _MEMORY_INSTRUCTION（记忆工具提示）
  → _ARCHITECTURE_INSTRUCTION（架构工具提示）
  → _RETRIEVAL_RESPONSE_INSTRUCTION（检索结果精炼提示）
  → _SENSITIVE_ACTION_INSTRUCTION（副作用操作确认提示）
  → THINK_INSTRUCTION（<think> 规划提示）
  → [本轮特化] 若问记忆/架构，额外注入工具优先指引
  ↓
Action Engine: maybe_handle_user_action()
  ├── classify_action_intent()  ← 只分类，规则优先，fallback LLM
  │     支持意图：write_file / edit_file / delete_file /
  │               set_reminder / open_file / run_command
  ├── extract_action_slots()    ← 只提参数，规则优先，fallback LLM
  └── _run_capability(capability)
        ├── [需确认] → PendingAction 确认流程（见下）
        ├── execute_pending_action_steps(bypass_confirmation=True)
        ├── verify（ExecutionReceipt）
        │     write_file/edit_file: 检查文件存在 + 内容匹配
        │     delete_file: 检查文件已不存在
        │     set_reminder: 检查提醒数量增加
        │     run_command / open_file: 检查输出前缀
        ├── [verify 失败 + retry_on_verify_failure] → 最多重试 2 次
        └── compose_success() → 用户友好文字
  │
  ├── handled=True → direct_tool_handled，跳过 LLM 循环
  └── handled=False → 进入 LLM while 循环
        ↓
      [可选] <think>...</think> 规划块（think 不对用户可见）
        ↓
      while True:
        stream_with_tools()
          ├── has_tool_calls=True
          │     asyncio.gather(*run_tools)  ← 并发执行，20s 超时
          │     │
          │     ├── 输出 __CONFIRM__:...  → PendingAction 确认流程
          │     ├── 输出 __ASK_USER__:... → ClarificationEvent（等用户文字回复）
          │     ├── 输出 __SHOW_SCHEDULE__ → ScheduleEvent（展示日程面板）
          │     └── 正常输出              → 追加 tool message
          │     │
          │     ├── [set_reminder 成功] → 自动 emit ScheduleEvent
          │     ├── [工具失败] replan_count < 2 → 注入 _REPLAN_PROMPT → continue
          │     └── continue
          │
          ├── verbal_guard.detect() 命中 → 注入纠正消息 → continue（每轮最多 1 次）
          └── 无工具调用 → assistant_text = result.text → break
        ↓
      [若有工具调用] 自我反思 _reflect()（call_once，输出补充或 OK）
        ↓
      [检索类响应] _should_distill_response() → _condense_for_display() / _condense_for_tts()
        ↓
      strip_think() → _sanitize_user_visible_text() → 输出干净文字
```

### Action Engine（能力注册表）

`action_engine.py` 将每种副作用操作封装为 `ActionCapability`，实现能力注册表模式：

```python
CAPABILITIES: dict[ActionIntent, ActionCapability] = {
    "write_file": ActionCapability(
        requires_confirmation=True,
        retry_on_verify_failure=True,   # 写失败可重试
        build_action=...,               # 构造 PendingAction
        verify=_verify_write,           # 验证文件存在且内容匹配
        compose_success=...,            # 组装用户友好文字
    ),
    "edit_file":   ...,  # requires_confirmation, retry
    "delete_file": ...,  # requires_confirmation, retry
    "open_file":   ...,  # requires_confirmation, no retry
    "set_reminder":...,  # no confirmation, after_success=emit ScheduleEvent
    "run_command": ...,  # requires_confirmation, no retry
}
```

执行结果用 `ExecutionReceipt` 表达：

```python
@dataclass
class ExecutionReceipt:
    capability_id: str
    tool_calls: list[str]
    outputs: list[str]
    expected_effect: dict    # 期望效果（如 path + content）
    observed_effect: dict    # 实际观测（如文件实际内容）
    verified: bool           # action_verifier 验证通过？
    failure_reason: str | None
    attempts: int            # 实际执行次数（最多 2）
```

### Human-in-the-Loop（人机确认）

对于 `write_file`、`edit_file`、`delete_file`、`open_file`、`execute_python`、`run_command` 等副作用操作，后端在实际执行前暂停等待确认：

```
命中副作用操作（Action Engine 或 LLM 工具调用）
  ↓
pending_action_store.create(session_id, action)  ← asyncio.Future
  ↓
emit ChoiceEvent(choice_id="confirm_execution", title, items)
emit StateEvent(name="waiting_user")
  ↓
pending_action_store.wait(timeout=300s)  ← SSE 流暂停在此
  ↓
用户确认 → POST /v1/choice → future.set_result("confirm")
  ↓
execute_pending_action_steps(force_confirmed=True, bypass_confirmation=True)
  ↓
ExecutionReceipt = capability.verify()  ← 二次验证
  ↓
[verified=False + retry_on_verify_failure] → 最多重试 2 次
```

### Intent Router + Slot Extractor（职责分离）

意图路由分为两个独立步骤，职责严格分开：

**Step 1 — `intent_router.py`（只分类，不提参数）**

| 意图 | 规则触发条件 | LLM Fallback |
|------|-------------|-------------|
| `write_file` | 含"桌面"+"创建/新建/写" + `.` | 有 |
| `edit_file` | 含"桌面"+"修改/改成/改为/替换" + `.` | 有 |
| `delete_file` | 含"桌面"+"删除/删掉/移除" + `.` | 有 |
| `set_reminder` | 含"提醒" + 时间词 | 有 |
| `open_file` | 含"打开/启动/运行" | 有 |
| `run_command` | 含"命令/终端/shell" 或以 git/npm/python 等开头 | 有 |

**Step 2 — `slot_extractor.py`（只提参数）**

分类成功后，再调用 `extract_action_slots(message, intent)` 提取具体参数（文件名、路径、内容、时间等），同样规则优先、LLM fallback。

### Verbal Guard（口头执行检测，已重新启用）

```python
def detect(user_message, assistant_text, tools_called) -> str | None:
    if not _looks_like_action_request(user_message):  # 用户是否提了操作需求？
        return None
    if tools_called & _SIDE_EFFECT_TOOLS:             # 实际调用了工具？
        return None
    if not _looks_like_completion_claim(assistant_text): # 模型声称完成了？
        return None
    return "你刚才声称完成了操作，但本轮没有任何真实工具执行。请重新回答..."
```

检测到口头执行时，注入纠正消息强制 LLM 重新回答（每轮最多 1 次）。

### 工具失败重规划

工具执行失败（输出以"错误：/失败：/权限错误："等前缀开头）时，自动注入重规划提示并继续循环，最多重试 2 次：

```
工具失败
  ↓
failures = [(tool_name, error_output), ...]
  ↓
messages.append({"role": "user", "content": _REPLAN_PROMPT.format(failures=...)})
emit StateEvent(name="replanning")
  ↓
continue while 循环（replan_count < 2）
```

### 输出精炼（检索类响应）

当 LLM 调用了 `read_memory` / `read_architecture` 等检索工具，或用户询问记忆/架构时，对回复做二次精炼：

- `_condense_for_display(text, detailed=False)` — 最多 3 行 / 210 字，过滤路径重型行
- `_condense_for_tts(text, aggressive=False)` — 最多 3 句 / 170 字，适合语音播报
- `_sanitize_user_visible_text(text)` — 剥离 `confirmed=true`、`<invoke>` 等内部标记

### SSE 事件协议

前后端通过 Server-Sent Events 通信：

| 事件 | 字段 | 说明 |
|------|------|------|
| `session` | session_id | 返回/续用会话 ID（存 localStorage）|
| `state` | name, scope | 状态变化：thinking / tool_calling / speaking / waiting_user |
| `speech` | text, chunk | chunk=true 流式片段，chunk=false 完整文字 |
| `audio` | data, format | Base64 MP3，前端解码后同步显示文字+动画 |
| `choice` | choice_id, title, items, extra_items, current_id | 触发通用选择/确认面板 |
| `clarification` | question | 触发问题输入框，等待用户文字回答 |
| `schedule` | reminders | 刷新提醒列表展示 |
| `done` | request_id | 本轮结束 |

### WebSocket 主动推送

```
后端 APScheduler / 提醒触发
  ↓
scheduler.fire_reminder(session_id, message)
  ↓
ws.push(session_id, {"type": "proactive", "text": message})
  ↓
前端 ws.onmessage → 展示文字 + REMINDER 状态

连接管理：
  前端启动时连接 ws://localhost:8002/v1/ws/{session_id}
  断线自动 3s 重连
  25s 心跳 ping/pong 保活
```

### 内置工具（16 个）

| 工具 | 说明 | 安全限制 |
|------|------|---------|
| `get_datetime` | 当前日期时间 | — |
| `calculator` | AST 安全数学求值 | 仅支持数学运算符 |
| `web_search` | DuckDuckGo 搜索 | — |
| `open_file` | 跨平台打开文件/应用 | 需用户确认 |
| `show_voice_panel` | 触发音色选择面板 | — |
| `set_reminder` | 自然语言时间设置提醒 | 仅未来时间 |
| `list_schedule` | 列出/取消已有提醒 | — |
| `ask_user` | 向用户提问并暂停等待回答 | 300s 超时 |
| `read_memory` | 读取长期记忆 + 情景记忆，按 query 分类返回 | — |
| `read_architecture` | 查询项目架构知识库索引 | — |
| `read_file` | 读取文本文件 | 路径白名单 + 64 KB 上限 |
| `write_file` | 写入/追加文本文件 | 路径白名单 + 256 KB 上限 + 需用户确认 + 写后验证 |
| `edit_file` | 覆盖修改已有文件内容 | 路径白名单 + 需用户确认 + 改后验证内容匹配 |
| `delete_file` | 删除文件 | 路径白名单 + 需用户确认 + 删后验证文件不存在 |
| `list_directory` | 列出目录内容 | 路径白名单 + 100 条上限 |
| `execute_python` | Python 沙箱执行 | subprocess 隔离 + 15s 超时 + 危险模块拦截 + 有副作用时需用户确认 |
| `run_command` | 在本地执行 shell 命令 | 安全策略过滤 + 需用户确认 |

### Skills 系统（程序性记忆）

详见 [SKILLS_ARCHITECTURE.md](./SKILLS_ARCHITECTURE.md)，简要说明：

- 工具调用 ≥ 2 次后，后台 LLM 自动生成 SKILL.md 技能文档
- 格式兼容 [agentskills.io](https://agentskills.io) 标准（YAML frontmatter + Markdown body），可跨 Hermes/OpenClaw 复用
- 存储于 `~/.secretary/skills/`，每个技能一个 `.md` 文件
- 每次对话前关键词检索相关技能，注入 system prompt 的 `## 可用技能` 区块
- 同名技能存在时更新而不是重复创建；`use_count` 记录使用频率

### 记忆系统

详见 [MEMORY_ARCHITECTURE.md](./MEMORY_ARCHITECTURE.md)，简要分层：

| 层级 | 存储 | 保留期 | 适合记什么 |
|------|------|--------|------------|
| 短期记忆 | sessions.db | session 生命周期 | 当前对话完整上下文 |
| 情景记忆（普通）| state.db episodes importance=1 | 30 天 | 一次性查询、闲聊 |
| 情景记忆（重要）| state.db episodes importance=2 | 永久 ★ | 决策、偏好、重大事件 |
| 长期记忆 | MEMORY.md / USER.md | 永久（LLM 维护）| 抽象事实、用户画像 |
| 人格档案 | SOUL.md | 永久 | 身份、风格、音色 |

system prompt 组装顺序：`SOUL.md → base prompt → MEMORY.md → USER.md → 相关情景记忆 → 可用技能 → <think> 指令`

### Onboarding 流程

```
第 0 轮  用户首次消息
  → STEP1 prompt：询问用户名 + 助手名 + 风格偏好

第 1 轮  用户回答
  → STEP2 prompt：确认并告知匹配音色中
  → 并发执行：
      recommend_voices(描述) → Top 3 推荐 + 其余备选
      extract_soul_draft(描述) → 提取 user_name / name / personality
  → soul_manager.create(...)  写入 SOUL.md（voice_id=pending）
  → emit ChoiceEvent → 前端弹出音色面板

用户选择音色
  → POST /v1/choice
  → soul_manager.update_voice(voice_id, voice_name)
  → SOUL.md voice_id 更新，后续 TTS 生效
```

### 上下文压缩

- 触发阈值：`context_length × 50%`
- 压力分级：normal → high (85%) → critical (95%) → compress
- 压缩策略：保留头 3 轮 + 尾 20 轮，LLM 摘要中间部分
- 最多重试 3 次压缩直到 fit

### Context Panel（通用选择/确认面板）

后端通过 `ChoiceEvent` SSE 事件驱动，从 cube 左侧滑入，支持：
- 当前选中高亮（`current_id` 字段，蓝色边框 + 圆点标记）
- 展开更多（extra_items 折叠区）
- 关闭按钮（不选择直接关闭）

使用场景：音色选择（Onboarding + 主动换声音）、副作用操作确认（创建/删除文件、执行代码）。

### 体素动画

| 状态 | 形态 |
|------|------|
| 待机 | 6×6×6 立方体，慢速呼吸波 |
| 说话 | lerp 到 Möbius 环（李萨如曲线中心线），音频频谱驱动跳动 |
| 结束 | 慢速归位回立方体，颜色拖尾渐变 |

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/chat` | 主对话入口，SSE 流式响应 `{session_id?, message, request_id}` |
| POST | `/v1/choice` | 提交用户选择（音色、操作确认等）`{session_id, choice_id, item_id}` |
| POST | `/v1/clarify` | 提交澄清问题回答 `{session_id, answer}` |
| POST | `/v1/cancel` | 取消当前进行中的请求 `{request_id?, session_id?}` |
| GET  | `/v1/soul` | 获取 SOUL.md 内容（用于前端展示人格信息）|
| GET  | `/v1/sessions` | 获取会话列表 |
| WS   | `/v1/ws/{session_id}` | 主动推送通道（提醒、通知）|
| GET  | `/health` | 健康检查 |

---

## 配置

在 `runtime/` 目录创建 `.env` 或设置系统环境变量：

```env
MINIMAX_API_KEY=your_key_here

# 可选覆盖
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MODEL=MiniMax-M2.7
MODEL_FALLBACKS=MiniMax-Text-01       # 逗号分隔的降级模型列表
MAX_TOKENS=2048

TTS_VOICE_ID=male-qn-qingse           # onboarding 前的默认音色
TTS_MAX_CHARS=300                      # 单次 TTS 字数上限

MEMORY_EXTRACT_EVERY=3                 # 每 N 轮触发记忆提取
COMPRESSION_THRESHOLD=0.50             # context 压缩触发比例
SESSION_RECENT_TURNS=8                 # 注入 system prompt 的最近对话轮数
SESSION_SUMMARY_TRIGGER_TURNS=12       # 触发对话摘要的轮数阈值
```

---

## 启动

```bash
# 后端
cd secretary/runtime
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端（开发模式）
cd secretary/voxel-avatar
npm install
npx tauri dev
```

用户数据存储于 `~/.secretary/`，首次启动自动创建。

---

## 依赖版本要求

| 工具 | 版本 |
|------|------|
| Python | 3.11+ |
| Node.js | 18+ |
| Rust / Cargo | 1.70+（Tauri 必需）|

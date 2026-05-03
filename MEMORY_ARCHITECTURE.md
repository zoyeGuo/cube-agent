# 记忆系统架构

## 概览

secretary 的记忆系统分为三层，覆盖了从当前会话到跨会话长期积累的全链路：

```
┌─────────────────────────────────────────────────────────────────┐
│                         记忆系统三层                             │
│                                                                 │
│  ① 短期记忆   会话消息列表（当前 session 的完整上下文）           │
│  ② 情景记忆   每轮对话提取 → SQLite + FTS5（可检索）              │
│  ③ 长期记忆   定期抽象 → 平文件（MEMORY.md / USER.md）            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 存储层

```
~/.secretary/
├── sessions.db          # 短期：会话消息历史（SQLite）
└── memory/
    ├── SOUL.md          # 人格档案（名字 / 用户名 / 性格 / 音色）
    ├── MEMORY.md        # 长期事实记忆（要点列表，LLM 维护）
    ├── USER.md          # 用户画像（偏好 / 习惯 / 背景）
    └── state.db         # 情景记忆（SQLite + FTS5）
         ├── episodes     ← 每轮摘要 + 时间戳 + 标签
         └── episodes_fts ← FTS5 全文索引（支持关键词检索）
```

### sessions.db 表结构

```sql
sessions  (id, created_at, updated_at)
messages  (id, session_id, role, content, ts)
```

### state.db 表结构

```sql
episodes     (id, ts, summary, tags, importance)   -- importance: 1=普通 2=重要
episodes_fts (summary, tags)                       -- FTS5 virtual table
```

---

## 写入路径

```
用户发消息
    │
    ▼
orchestrator.run()
    │
    ├─► session.add_user(msg)
    │   session_store.persist_message()    ──► sessions.db / messages
    │
    ├─► [LLM 推理 + 工具调用]
    │
    ├─► session.add_assistant(reply)
    │   session_store.persist_message()    ──► sessions.db / messages
    │
    └─► 每 N 轮触发（后台异步，不阻塞响应）
            │
            ▼
        extract_and_save()
            │
            ├─► call_once(LLM)  ← 读当前 MEMORY.md + USER.md + 本轮对话
            │       │
            │       └── 返回 JSON:
            │             { memory, user, episode: {summary, tags} }
            │
            ├─► 更新 MEMORY.md（若有新事实）
            ├─► 更新 USER.md（若有新用户信息）
            └─► save_episode(summary, tags, importance)
                    └──► state.db / episodes + episodes_fts
                         importance=1 普通（30天后删除）
                         importance=2 重要（永久保留 ★）
```

---

## 读取路径

```
用户发消息 (user_message)
    │
    ▼
orchestrator.run()
    │
    ├─► memory_manager.search_episodes(user_message, limit=4)
    │       │
    │       └── FTS5 MATCH 关键词检索 episodes_fts
    │           命中 → 返回相关情景列表
    │           未命中 → 返回最近 N 条兜底
    │
    ▼
memory_manager.build_system_prompt(soul, base, relevant_episodes)
    │
    └── 拼接顺序:
          [1] SOUL.md       ← 人格 / 身份
          [2] base prompt   ← 行为指令
          [3] MEMORY.md     ← 长期事实
          [4] USER.md       ← 用户画像
          [5] 相关情景记忆   ← FTS5 检索结果（带时间戳）
    │
    ▼
stream_with_tools(messages, system_prompt, tools)
```

---

## 系统 Prompt 结构示意

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SOUL.md
  名字: Nova
  用户: 张三
  性格: 简洁直接，不废话
  音色: male-qn-qingse
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
base prompt
  你是一个简洁直接的数字助手...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 记忆
- 用户在做桌面 AI 助手项目
- 偏好英文变量名，中文注释
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 用户信息
- 习惯晚上工作
- 不喜欢冗长解释
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 相关情景记忆
- [2026-04-18 23:41] 用户询问了如何修复 SOCKS 代理报错，已解决
- [2026-04-19 01:12] 用户要求把错误从前端移到 log 文件，已完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 各层对比

| 层级 | 存储 | 写入时机 | 读取方式 | 保留期 | 适合记什么 |
|------|------|----------|----------|--------|------------|
| 短期记忆 | sessions.db messages | 每条消息实时写 | 全量加载进 messages[] | session 生命周期 | 当前对话完整上下文 |
| 情景记忆（普通）| state.db episodes importance=1 | 每 N 轮后台提取 | FTS5 关键词检索 | **30 天** | 一次性查询、闲聊 |
| 情景记忆（重要）| state.db episodes importance=2 | 每 N 轮后台提取 | FTS5 关键词检索 | **永久 ★** | 决策、偏好、重大事件 |
| 长期记忆 | MEMORY.md / USER.md | 每 N 轮后台更新 | 全量注入 system prompt | 永久（LLM 维护）| 抽象事实、用户画像 |
| 人格档案 | SOUL.md | 引导完成时一次性写入 | 全量注入 system prompt | 永久 | 身份、风格、音色 |

---

## 情景分级规则

| 判定为重要（importance=2）| 判定为普通（importance=1）|
|--------------------------|--------------------------|
| 用户做出决策或承诺 | 一次性查询（天气/时间/计算）|
| 披露个人重要信息 | 闲聊、无后续价值的交互 |
| 确认偏好或习惯 | 常规问答 |
| 解决了重大问题 | 重复性日常操作 |

分级由 LLM 在每次记忆提取时自动完成，结果写入 `importance` 字段。

## 清理机制

- **触发时机**：每次后端启动时自动执行一次
- **清理规则**：`importance=1` 且 `ts < now - 30天` 的记录连同 FTS5 索引一并删除
- **日志**：删除数量写入 `runtime.log`
- **重要记录**：`importance=2` 永不自动删除

## 关键参数

| 参数 | 值 | 位置 |
|------|----|------|
| `memory_extract_every` | 每 3 轮触发一次提取 | `config.py` |
| FTS5 检索上限 | 4 条情景 | `orchestrator.py` |
| 提取 LLM max_tokens | 1024 | `extractor.py` |
| 情景摘要长度 | ≤40 字 | extractor prompt |
| 情景标签数量 | 3-6 个 | extractor prompt |
| 普通情景 TTL | 30 天 | `main.py` lifespan |

---

## 当前局限 / 可迭代方向

- **FTS5 是关键词匹配**，不是语义相似度。若将来引入 embedding 模型，可替换为向量检索（如 sqlite-vss 或 chromadb）
- **MEMORY.md 全量注入**，记忆条目多了会挤占 context。后续可对 MEMORY.md 也做 FTS5 检索，只注入相关条目
- **重要情景永不删除**，极长期运行后仍会积累。可加"超过 N 条重要记录时合并归纳"的压缩机制
- **importance 由 LLM 判断**，存在误判可能。未来可加人工标记接口允许用户手动调整等级

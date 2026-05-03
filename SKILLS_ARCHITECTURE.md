# Skills 系统架构

## 概念

Skills 是**程序性记忆**——记录"怎么做某类事"，而不是"发生了什么"。

| 记忆类型 | 存储 | 记录内容 | 例子 |
|---------|------|---------|------|
| 情景记忆 | episodes | 发生了什么 | "用户问了天气，已回答" |
| 长期记忆 | MEMORY.md | 重要事实 | "用户在北京" |
| **技能** | skills | 怎么做这类事 | "搜索实时天气的步骤和注意事项" |

---

## 存储结构

与情景记忆共用 `~/.secretary/memory/state.db`：

```sql
skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,          -- 创建时间
    updated_at  TEXT,          -- 最后更新时间
    title       TEXT,          -- 技能名称（唯一标识）
    description TEXT,          -- 一句话描述
    content     TEXT,          -- 完整技能文档（Markdown）
    tags        TEXT,          -- 检索关键词，逗号分隔
    use_count   INTEGER        -- 被调用次数（影响排序）
)

skills_fts (title, description, tags)  -- FTS5 全文索引
```

---

## 技能文档格式

```markdown
## 步骤
1. 使用 search_web 搜索"{关键词} 最新"
2. 从结果中提取核心数据
3. 用简洁格式回复用户

## 注意事项
- 搜索时加"今天"或"最新"提高时效性
- 若第一次搜索结果不含数字，换关键词重试

## 适用场景
- 用户询问实时变化的信息（天气、汇率、新闻）
```

---

## 工作流

```
用户发消息
    │
    ▼
search_skills(user_message, limit=3)
    │  FTS5 关键词检索
    │  无命中 → 按 use_count 降序兜底
    ▼
build_system_prompt() 注入 "## 可用技能" 区块
    │
    ▼
模型参考技能文档执行任务
    │
    ▼
工具调用完成
    │
    ├── tools_called ≥ 2？
    │       │
    │       ▼ 是（后台 asyncio.Task）
    │   generate_skill()
    │       │
    │       ├── LLM 判断：值得生成？
    │       │     不值得（单步/一次性） → skip
    │       │     值得 ↓
    │       ├── find_by_title(title) → 同名已存在？
    │       │     存在 → update_skill()（内容精炼更新）
    │       │     不存在 → save_skill()（新建）
    │       └── 写入 state.db + skills_fts
    │
    └── 本轮用到的技能 use_count +1
```

---

## 生成判断标准

LLM 在 `skill_generator.py` 中按以下标准决定是否生成：

| 生成技能 | 跳过 |
|---------|------|
| 需要多个步骤 | 单步查询（查时间、计算）|
| 有通用性，下次可复用 | 一次性任务（设某个具体提醒）|
| 涉及工具调用组合 | 闲聊 |

生成阈值：**≥ 2 个工具调用**（Hermes 为 5+，按当前任务规模调整）

---

## System Prompt 注入位置

```
SOUL.md
base prompt
## 记忆         ← MEMORY.md
## 用户信息     ← USER.md
## 相关情景记忆  ← episodes FTS5
## 可用技能     ← skills FTS5   ← 新增
<think> 指令
```

---

## 去重与更新机制

- 以 `title` 作为唯一键
- 同名技能存在时：更新 `content` 和 `tags`，保留原始 `ts`，更新 `updated_at`
- 每次技能被检索命中并实际使用后，`use_count +1`
- FTS5 更新通过"删除旧行 + 插入新行"实现（SQLite FTS5 标准做法）

---

## 关键参数

| 参数 | 值 | 位置 |
|------|----|------|
| 生成触发阈值 | tools_called ≥ 2 | `orchestrator.py` |
| 检索上限 | 3 条技能 | `orchestrator.py` |
| 生成 LLM max_tokens | 800 | `skill_generator.py` |
| 技能文档最大长度 | ~500 字 | generator prompt |

---

## 当前局限 / 可迭代方向

- **FTS5 是关键词匹配**，技能标题和标签设计质量直接影响检索准确率
- **技能无过期机制**，工具 API 变更后旧技能可能失效，需要人工或自动清理
- **技能内容由 LLM 生成**，质量取决于生成 prompt，初期可能需要观察和调整
- **无人工编辑入口**，未来可加 CLI 命令查看/删除/手动添加技能

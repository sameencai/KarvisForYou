# KarvisForAll 需求文档

> 版本：v1.0
> 日期：2026-02-15
> 状态：Draft

---

## 一、项目概述

### 1.1 背景

Karvis 是一个运行在企业微信上的 AI 生活助手，通过自然对话帮助用户记录生活、管理待办、复盘情绪、跟踪习惯。当前 Karvis（开源版）是纯单用户架构——一套部署只能服务一个人。

KarvisForAll 是 Karvis 的多用户版本，目标是让 2-3 个朋友（后续可扩展到更多人）加入同一个企微团队后，每人拥有独立的 AI 助手体验，数据完全隔离。

### 1.2 项目定位

| 维度 | 描述 |
|---|---|
| **产品形态** | 企业微信自建应用（多用户） |
| **目标用户** | 管理员的朋友/小圈子，先 2-3 人试用 |
| **数据存储** | 服务器本地磁盘（Lite 模式） |
| **数据查看** | Web 只读页面（用户可查看自己的笔记、待办、日记等） |
| **成本模型** | 管理员全额承担 LLM 费用 |
| **技术基线** | 基于 Karvis-opensource 代码改造 |

### 1.3 与 Karvis-opensource 的关系

KarvisForAll 是一个**独立项目**，从 Karvis-opensource fork 后进行多用户改造。不追求与原版保持同步，而是在原版功能集的基础上做减法和适配。

### 1.4 核心原则

1. **数据隔离**：用户 A 绝不能看到用户 B 的任何数据
2. **体验一致**：每个用户的使用感受与单用户版完全一致
3. **零配置上手**：新用户发第一条消息即可开始使用，无需任何配置
4. **成本可控**：管理员能监控和控制总体 LLM 开销

---

## 二、用户角色

### 2.1 角色定义

| 角色 | 描述 | 权限 |
|---|---|---|
| **管理员** | 部署者，企微团队创建者，承担 LLM 费用 | 查看所有用户列表、使用量统计、管理用户状态 |
| **普通用户** | 被管理员邀请加入企微团队的朋友 | 使用 Karvis 全部对话功能、查看自己的 Web 页面 |

### 2.2 管理员特有操作

- 通过环境变量配置 LLM API Key、企微应用参数
- 通过 Web 管理页面查看：用户列表、各用户消息量、LLM token 用量
- 设置每日消息上限（可选，防止成本失控）
- 管理员自己也是一个普通用户（同时享有管理功能）

### 2.3 普通用户操作

- 在企微中直接给 Karvis 发消息（文字/语音/图片/视频/链接）
- 通过对话设置个人偏好（昵称、AI 人格风格等）
- 通过 Web 页面查看自己的数据（只读）

---

## 三、功能需求

### 3.1 用户生命周期

#### F1：自动注册与欢迎引导

**触发条件**：一个新的企微用户 ID 首次向 Karvis 发送消息。

**处理流程**：

1. 系统检测到 `user_id` 不在用户注册表中
2. 自动创建该用户的完整数据目录结构：
   ```
   users/{user_id}/
   ├── 00-Inbox/
   │   ├── Quick-Notes.md
   │   ├── Todo.md
   │   └── .ai-life-state.json
   ├── 01-Daily/
   ├── 02-Notes/
   │   ├── 读书笔记/
   │   ├── 影视笔记/
   │   ├── 情感日记/
   │   ├── 工作笔记/
   │   ├── 生活趣事/
   │   └── 语音日记/
   └── _Karvis/
       ├── memory/memory.md
       ├── user_config.json
       └── logs/decisions.jsonl
   ```
3. 写入用户注册表（`_karvis_system/users.json`）
4. 发送欢迎消息，引导用户设置昵称
5. 继续正常处理该条消息

**验收标准**：
- 新用户发送任意消息后，5 秒内收到欢迎引导 + 消息处理结果
- 数据目录和所有默认文件正确创建
- 不影响其他用户的正常使用

#### F2：用户注册表

**数据结构**（`_karvis_system/users.json`）：

```json
{
  "users": {
    "zhangsan": {
      "created_at": "2026-02-15T10:30:00+08:00",
      "last_active": "2026-02-15T18:45:00+08:00",
      "nickname": "小明",
      "status": "active",
      "message_count_today": 12,
      "total_messages": 156
    }
  }
}
```

**字段说明**：
| 字段 | 类型 | 说明 |
|---|---|---|
| `created_at` | string | 用户首次消息时间（ISO 8601） |
| `last_active` | string | 最后活跃时间 |
| `nickname` | string | 用户设置的昵称 |
| `status` | string | `active` / `suspended`（管理员可控） |
| `message_count_today` | int | 今日已发消息数（UTC+8 零点重置） |
| `total_messages` | int | 累计消息总数 |

---

### 3.2 对话式设置

#### F3：设置昵称

**触发方式**：用户说"叫我 XX"、"我叫 XX"、"我的名字是 XX"

**处理**：
- 提取昵称，写入 `user_config.json` 的 `nickname` 字段
- 同时写入用户注册表的 `nickname` 字段
- 同时写入用户的 `memory.md`（如"用户希望被称为「小明」"）
- 后续所有对话中，Karvis 使用该昵称称呼用户

**验收标准**：
- 设置后立即生效，下一条消息回复中即使用新昵称
- 支持修改昵称（"以后叫我小红"）

#### F4：设置 AI 人格风格

**触发方式**：用户说"你说话活泼点"、"正式一点"、"像朋友一样聊天"、"别用表情"

**处理**：
- 提取风格描述，写入 `user_config.json` 的 `soul_override` 字段
- 在组装 System Prompt 时，将 `soul_override` 追加到 SOUL 后面
- 支持累加（"再幽默一点"在原来基础上增加）
- 支持重置（"恢复默认风格"）

**验收标准**：
- 设置后下一条消息即体现风格变化
- 不同用户的风格设置互不影响

#### F5：设置个人信息

**触发方式**：用户说"我是做设计的"、"我养了一只猫叫花花"、"我在上海"

**处理**：
- 提取关键信息，写入 `memory.md` 的用户画像区域
- LLM 在后续对话中自然引用这些信息（如提到猫、工作相关话题时）

**验收标准**：
- 信息被记录到 memory 中
- 后续对话能体现对用户信息的了解

---

### 3.3 核心对话功能（继承自 Karvis-opensource）

以下功能从 Karvis-opensource 完整继承，每个功能在多用户环境下的关键要求是**数据隔离**——所有文件读写都必须指向当前用户的目录。

#### F6：速记 / 笔记保存

- 用户发送任意消息 → 写入该用户的 `Quick-Notes.md`
- 支持文字、语音转文字、图片描述、视频描述、链接摘要
- **隔离要求**：每个用户有独立的 `Quick-Notes.md`

#### F7：智能归档

- Karvis 自动将有价值的内容归档到对应分类（工作/情感/生活/通用）
- 支持归档合并（多段内容合并为一篇笔记）
- **隔离要求**：归档写入该用户的 `02-Notes/` 子目录

#### F8：待办管理

- `todo.add`：添加待办，支持截止日期和提醒时间
- `todo.done`：完成待办（关键词模糊匹配 / 序号 / 范围）
- `todo.list`：查看当前待办列表
- **隔离要求**：每个用户有独立的 `Todo.md`

#### F9：每日打卡

- 四问打卡流程（今天做了什么 → 感受如何 → 有什么收获 → 明天计划）
- 支持跳过、取消
- 晚间 21:00 自动提醒打卡
- **隔离要求**：打卡状态存储在各用户的 `state` 中，互不干扰

#### F10：读书笔记

- 创建/切换书籍、添加书摘、添加感想、AI 生成总结和金句提炼
- **隔离要求**：书籍文件存储在各用户的 `02-Notes/读书笔记/`

#### F11：影视笔记

- 创建影视条目、添加感想
- **隔离要求**：存储在各用户的 `02-Notes/影视笔记/`

#### F12：语音日记

- 长语音（>200 字）自动整理为结构化日记
- **隔离要求**：存储在各用户的 `02-Notes/语音日记/`

#### F13：微习惯实验

- 提议新习惯、触发提醒、查看进度、结束并总结
- **隔离要求**：实验状态存储在各用户的 `state` 中

#### F14：决策跟踪

- 记录重要决策、到期复盘提醒、查看待复盘列表
- **隔离要求**：决策列表存储在各用户的 `state` 中

#### F15：日报 / 情绪日记 / 周报 / 月报

- 基于用户当天/当周/当月的 Quick-Notes、打卡记录、待办完成情况等，AI 生成复盘报告
- **隔离要求**：读取该用户的数据，写入该用户的 `01-Daily/` 目录

#### F16：主题深潜

- 用户提出某个话题，Karvis 跨时间线搜索该用户的历史笔记，生成分析报告
- 通过 Agent Loop（最多 5 轮 internal.read / internal.search / internal.list）完成
- **隔离要求**：只搜索和读取当前用户的数据

#### F17：主动陪伴

- 系统定时检查用户状态（沉默、情绪低落、连续记录等）
- 每个用户独立计算陪伴信号，独立推送
- 五层防骚扰机制：安静时间 → 近期有互动 → 推送间隔 → 每日上限 → 无信号则静默
- **隔离要求**：每个用户的 `nudge_state` 独立维护

---

### 3.4 定时任务（多用户适配）

所有定时任务需遍历活跃用户逐一执行，并加入随机延迟防止 LLM API 限流。

| 任务 | 执行时间 | 说明 | 多用户适配 |
|---|---|---|---|
| `refresh_cache` | 每 30 分钟 | 清除过期缓存 | 清除所有用户缓存 |
| `morning_report` | 每天 08:00 | 晨报推送 | 遍历活跃用户，每人独立生成 |
| `todo_remind` | 09:00/14:00/18:00 | 待办提醒 | 遍历用户，检查各自待办 |
| `nudge_check` | 每天 14:00 | 轻推检测 | 遍历用户，检查各自状态 |
| `companion_check` | 8-22 点每 2 小时 | 主动陪伴 | 遍历用户，独立计算信号 |
| `evening_checkin` | 每天 21:00 | 晚间打卡引导 | 遍历用户 |
| `weekly_review` | 周日 21:30 | 周回顾 | 遍历用户，独立生成 |
| `mood_generate` | 每天 22:00 | 情绪日记 | 遍历用户，独立生成 |
| `daily_report` | 每天 22:30 | 日报 | 遍历用户，独立生成 |
| `monthly_review` | 每月最后一天 22:00 | 月度回顾 | 遍历用户，独立生成 |

**关键要求**：
- 只对"活跃用户"执行（7 天内有过消息的用户，避免浪费 LLM 调用）
- 用户之间加 1-5 秒随机延迟，避免 API 限流
- 某用户的定时任务失败不影响其他用户

---

### 3.5 Web 查看页面

#### F18：用户数据展示页

**入口**：`https://{domain}/web/` → 登录后跳转到个人数据页

**页面列表**：

| 页面 | 路径 | 内容 |
|---|---|---|
| 登录页 | `/web/login` | 输入访问令牌 |
| 概览 | `/web/dashboard` | 今日速记数、待办完成率、情绪曲线、连续打卡天数 |
| 速记流 | `/web/notes` | Quick-Notes 内容流（按日期分组，最新在前） |
| 待办 | `/web/todos` | 当前待办列表（支持按状态筛选） |
| 日记 | `/web/daily` | 日报/周报/月报列表（点击查看详情） |
| 笔记 | `/web/archive` | 归档笔记（按分类：工作/情感/生活/通用） |
| 情绪 | `/web/mood` | 情绪日记列表 + 情绪评分折线图 |
| 读书 | `/web/books` | 读书笔记列表 |
| 影视 | `/web/media` | 影视笔记列表 |

**交互要求**：
- 纯只读，不支持编辑（编辑通过企微对话完成）
- 移动端优先（用户大概率在手机上查看）
- 支持 Markdown 渲染（笔记内容是 Markdown 格式）
- 页面加载速度 < 2 秒

#### F19：鉴权机制

**方案：Token 令牌制**（简单可靠，适合 2-3 人场景）

- 用户在企微中对 Karvis 说"给我查看链接"或"我要看我的数据"
- Karvis 生成一个一次性/短期令牌（如 24 小时有效），回复一个带 token 的 URL
- 用户点击链接直接进入自己的数据页面，无需输入密码
- 令牌过期后需重新向 Karvis 索取

**令牌管理**：
| 属性 | 值 |
|---|---|
| 格式 | UUID v4 |
| 有效期 | 24 小时（可配置） |
| 存储 | 内存 + 持久化到 `_karvis_system/tokens.json` |
| 关联 | 令牌 → user_id 的映射 |

**验收标准**：
- 令牌只能访问对应用户的数据
- 过期令牌返回 401 并引导重新获取
- 同一用户可同时拥有多个有效令牌

#### F20：管理员仪表盘

**入口**：`/web/admin`（管理员专属）

**功能**：
| 功能 | 说明 |
|---|---|
| 用户列表 | 所有注册用户、昵称、注册时间、最后活跃、状态 |
| 使用统计 | 每日消息数、LLM token 用量（按用户维度） |
| 成本估算 | 基于 token 用量的月度成本估算 |
| 用户管理 | 挂起/激活用户（挂起后该用户消息不再处理） |

**鉴权**：管理员通过环境变量 `ADMIN_TOKEN` 配置固定管理密钥。

---

### 3.6 成本控制

#### F21：每日消息上限

- 管理员通过环境变量 `DAILY_MESSAGE_LIMIT` 配置每用户每日消息上限（默认 50 条）
- 到达上限后，用户发送消息会收到提示："今天消息额度用完啦，明天再聊～"
- 每日 UTC+8 零点自动重置计数

#### F22：LLM 用量追踪

- 每次 LLM 调用记录：`user_id`、`model`、`input_tokens`、`output_tokens`、`timestamp`
- 写入 `_karvis_system/usage_log.jsonl`（追加写入）
- 管理员仪表盘读取此文件生成统计

#### F23：不活跃用户跳过

- 定时任务执行前检查用户最后活跃时间
- 超过 7 天未活跃的用户跳过 LLM 类定时任务（晨报、日报等）
- 非 LLM 类任务（缓存清理、待办提醒）照常执行

---

## 四、非功能需求

### 4.1 性能

| 指标 | 目标值 | 说明 |
|---|---|---|
| 消息响应时间 | < 10s（端到端） | 从用户发送到收到回复 |
| Web 页面加载 | < 2s | 首屏加载 |
| 定时任务完成时间 | < 5 分钟（全部用户） | 以 3 用户计 |
| 并发消息处理 | 支持 3 条同时处理 | 3 用户同时发消息不阻塞 |

### 4.2 可靠性

| 要求 | 说明 |
|---|---|
| 消息不丢失 | 企微消息去重 + 异步处理，确保每条消息被处理 |
| 定时任务容错 | 单个用户的定时任务失败不影响其他用户 |
| 数据一致性 | State 写入采用 per-user 锁，避免并发写覆盖 |
| 缓存隔离 | 缓存 key 包含 user_id，杜绝缓存串用户 |

### 4.3 安全

| 要求 | 说明 |
|---|---|
| 数据隔离 | UserContext 路径前缀验证，防止路径穿越 |
| Web 鉴权 | 令牌制，令牌 → user_id 一一映射 |
| 管理员鉴权 | 固定密钥（环境变量配置） |
| 日志安全 | 决策日志不记录用户原始消息内容（只记录 skill/action） |

### 4.4 可观测性

| 要求 | 说明 |
|---|---|
| 每次交互日志 | `user_id` + `action` + `model` + `tokens` + `duration` |
| 错误日志 | 统一格式，包含 user_id 方便排查 |
| LLM 用量日志 | 独立的 `usage_log.jsonl` |

### 4.5 部署

| 要求 | 说明 |
|---|---|
| 部署方式 | Docker Compose 一键部署（首选） |
| 运行环境 | Python 3.10+，1C1G 轻量服务器即可 |
| 数据持久化 | 用户数据目录挂载为 Docker volume |
| 配置管理 | `.env` 文件统一管理所有配置 |

---

## 五、数据架构

### 5.1 目录结构（全局）

```
{DATA_DIR}/
├── _karvis_system/                     ← 系统级数据
│   ├── users.json                      ← 用户注册表
│   ├── tokens.json                     ← Web 访问令牌
│   └── usage_log.jsonl                 ← LLM 用量日志
│
└── users/                              ← 用户数据（按 user_id 隔离）
    ├── {user_id_1}/
    │   ├── 00-Inbox/
    │   │   ├── Quick-Notes.md          ← 速记流水
    │   │   ├── Todo.md                 ← 待办
    │   │   ├── .ai-life-state.json     ← AI 状态
    │   │   └── attachments/            ← 媒体附件
    │   ├── 01-Daily/                   ← 日报/周报/月报
    │   ├── 02-Notes/
    │   │   ├── 读书笔记/
    │   │   ├── 影视笔记/
    │   │   ├── 情感日记/
    │   │   ├── 工作笔记/
    │   │   ├── 生活趣事/
    │   │   └── 语音日记/
    │   └── _Karvis/
    │       ├── memory/memory.md        ← 长期记忆
    │       ├── user_config.json        ← 用户配置（昵称、风格覆写等）
    │       └── logs/decisions.jsonl    ← 决策日志
    │
    ├── {user_id_2}/
    │   └── ...（同上）
    │
    └── {user_id_3}/
        └── ...（同上）
```

### 5.2 核心数据模型

#### UserContext（运行时对象）

```python
class UserContext:
    user_id: str                # 企微 UserID
    base_dir: str               # 用户数据根目录
    inbox_path: str             # 00-Inbox/
    quick_notes_file: str       # Quick-Notes.md
    state_file: str             # .ai-life-state.json
    todo_file: str              # Todo.md
    daily_dir: str              # 01-Daily/
    notes_dir: str              # 02-Notes/
    memory_file: str            # memory/memory.md
    user_config_file: str       # user_config.json
    decision_log_file: str      # logs/decisions.jsonl
    attachments_dir: str        # attachments/
    # 各笔记分类子目录
    book_notes_dir: str
    media_notes_dir: str
    emotion_notes_dir: str
    work_notes_dir: str
    fun_notes_dir: str
    voice_journal_dir: str
```

#### UserConfig（持久化配置）

```json
{
  "nickname": "小明",
  "soul_override": "说话活泼一点，多用语气词",
  "info": {
    "occupation": "设计师",
    "city": "上海",
    "pets": ["猫-花花"]
  },
  "preferences": {
    "morning_report": true,
    "evening_checkin": true,
    "companion_enabled": true
  }
}
```

#### State（运行时状态）

与 Karvis-opensource 的 `.ai-life-state.json` 结构完全一致，每个用户独立一份，包含：
- `recent_messages`：短期记忆滑动窗口
- `checkin_pending` / `checkin_step` / `checkin_answers`：打卡进度
- `active_book` / `active_media`：当前在读/在看
- `daily_top3`：每日 Top 3
- `active_experiment`：微习惯实验
- `pending_decisions`：待复盘决策
- `nudge_state`：轻推/陪伴状态
- `mood_scores`：情绪评分历史
- `checkin_stats`：打卡统计

---

## 六、技术方案概要

### 6.1 核心改造：UserContext 贯穿全链路

```
企微消息 → app.py 解密得到 user_id
  → get_or_create_user(user_id) 得到 UserContext
  → brain.process(payload, ctx, send_fn)
    → read_state_cached(ctx)         # 读该用户的 state
    → load_memory(ctx)               # 读该用户的 memory
    → handler(params, state, ctx)    # skill 使用 ctx 的路径
  → send_wework_message(user_id, reply)
```

### 6.2 三层模型路由（不变）

| 层级 | 模型 | 用途 |
|---|---|---|
| Flash | Qwen（阿里云百炼） | 陪伴消息、Flash 智能回复 |
| Main | DeepSeek V3.2 | 日常路由、Skill 分发、定时报告 |
| Think | DeepSeek V3.2 (thinking) | 主题深潜、决策分析 |

### 6.3 存储方案

KarvisForAll **只使用 Lite 本地模式**，不支持 OneDrive。

原因：
- 多用户数据存一个人的 OneDrive 有隐私问题
- 每个用户配自己的 OneDrive 配置门槛极高
- Web 查看页面需要服务端直接读取文件，本地模式最简单

因此：
- 移除 `onedrive_io.py`（或保留但不使用）
- `storage.py` 固定使用 `LocalFileIO`
- 用户数据目录通过 Docker volume 持久化

### 6.4 缓存改造

原版缓存是全局单例，多用户需按 user_id 分区：

| 缓存项 | 原版 | KarvisForAll |
|---|---|---|
| State 缓存 | `_state_cache = {"data": ..., "expire_time": ...}` | `_state_cache = {user_id: {"data": ..., "expire_time": ...}}` |
| Prompt 缓存 | `PromptCache` 全局单例 | 保持全局（Prompt 模板所有用户共享） |
| /tmp 磁盘缓存 | 单文件 | 按 user_id 分文件 |

### 6.5 并发处理

| 场景 | 方案 |
|---|---|
| 多用户同时发消息 | 每条消息在独立线程中处理（已有机制），UserContext 按请求创建 |
| 同一用户连续快速发消息 | State 写入加 per-user Lock |
| 定时任务遍历用户 | 随机延迟 1-5 秒，避免 API 限流 |

### 6.6 Web 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | Flask（与主服务同进程） | 复用已有 Flask 应用，不引入新服务 |
| 前端 | HTML + Tailwind CSS + Alpine.js | 轻量、无需构建工具、移动端友好 |
| Markdown 渲染 | marked.js（客户端） | 笔记内容是 Markdown 格式 |
| 图表 | Chart.js | 情绪曲线等图表 |

Web 服务与消息处理服务共享同一个 Flask 进程，通过路由前缀区分：
- `/wework`、`/process`、`/system` → 消息处理
- `/web/*` → Web 查看页面
- `/api/*` → Web 页面的数据接口

---

## 七、Web 页面详细设计

### 7.1 页面清单与布局

**整体布局**：
- 移动端优先的响应式设计
- 底部 Tab 导航栏（概览 / 速记 / 待办 / 日记 / 笔记）
- 顶部显示用户昵称和当前日期
- 颜色风格：温暖柔和（与 Karvis 的"温柔大姐姐"人设呼应）

#### P1：登录页 (`/web/login`)

- 简洁居中布局
- 一个输入框（粘贴 token 或直接从 URL 参数自动填入）
- 登录按钮
- 底部提示："在企微中对 Karvis 说「给我查看链接」获取访问令牌"

#### P2：概览页 / 仪表盘 (`/web/dashboard`)

| 区域 | 内容 |
|---|---|
| 顶部问候 | "Hi, {nickname}！今天是 {date} {weekday}" |
| 今日速记 | 数量统计 + 最近 3 条预览 |
| 待办进度 | 完成数 / 总数 + 进度条 |
| 情绪曲线 | 最近 7 天情绪评分折线图（来自 state.mood_scores） |
| 打卡连续 | 连续打卡天数 |
| 最新日记 | 最近一篇日报/情绪日记标题 + 日期 |

#### P3：速记流 (`/web/notes`)

- 按日期分组展示 Quick-Notes 内容
- 每条笔记显示：时间戳、内容（Markdown 渲染）
- 支持按日期筛选（日期选择器）
- 无限滚动加载

#### P4：待办页 (`/web/todos`)

- 分两区：进行中 / 已完成
- 每条待办显示：内容、截止日期（如有）、创建日期
- 支持筛选：全部 / 进行中 / 已完成

#### P5：日记页 (`/web/daily`)

- 列表展示所有日报、周报、月报、情绪日记
- 按类型分 Tab：日报 / 周报 / 月报 / 情绪
- 每项显示：日期、标题/摘要
- 点击进入详情页：完整 Markdown 渲染

#### P6：笔记页 (`/web/archive`)

- 按分类 Tab：工作 / 情感 / 生活 / 读书 / 影视 / 语音日记
- 每篇显示：标题、日期、内容预览
- 点击进入详情页

#### P7：情绪页 (`/web/mood`)

- 顶部：情绪评分折线图（最近 30 天）
- 下方：情绪日记列表
- 每篇显示：日期、评分、关键词标签、内容预览

### 7.2 API 接口设计

所有接口需在 Header 或 Cookie 中携带 `token`。

| 方法 | 路径 | 说明 | 返回 |
|---|---|---|---|
| POST | `/api/auth/verify` | 验证令牌 | `{valid, user_id, nickname}` |
| GET | `/api/notes` | 获取速记 | `{notes: [{time, content}], has_more}` |
| GET | `/api/notes?date=2026-02-15` | 按日期筛选 | 同上 |
| GET | `/api/todos` | 获取待办 | `{pending: [...], done: [...]}` |
| GET | `/api/daily` | 获取日记列表 | `{reports: [{date, type, title, file}]}` |
| GET | `/api/daily/{filename}` | 获取日记详情 | `{content}` (Markdown) |
| GET | `/api/archive?category=work` | 获取归档笔记 | `{notes: [{title, date, file}]}` |
| GET | `/api/archive/{filename}` | 获取笔记详情 | `{content}` (Markdown) |
| GET | `/api/mood` | 获取情绪数据 | `{scores: [...], diaries: [...]}` |
| GET | `/api/books` | 获取读书笔记 | `{books: [{title, file}]}` |
| GET | `/api/media` | 获取影视笔记 | `{items: [{title, file}]}` |
| GET | `/api/dashboard` | 获取仪表盘数据 | `{note_count, todo_progress, mood_chart, streak}` |

**管理员接口**（需 `ADMIN_TOKEN`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/users` | 用户列表 + 统计 |
| GET | `/api/admin/usage` | LLM 用量统计 |
| POST | `/api/admin/users/{id}/suspend` | 挂起用户 |
| POST | `/api/admin/users/{id}/activate` | 激活用户 |

---

## 八、实施计划

### Phase 1：项目初始化 + 核心隔离

**目标**：多用户各自独立数据，互不干扰。

| 任务 | 说明 |
|---|---|
| 1.1 初始化项目结构 | 从 Karvis-opensource fork，调整目录结构 |
| 1.2 实现 `UserContext` | 核心上下文类、用户注册表、自动初始化 |
| 1.3 改造 `config.py` | 保留全局配置，路径相关改为 UserContext |
| 1.4 改造 `memory.py` | 缓存按 user_id 分区，函数加 ctx 参数 |
| 1.5 改造 `brain.py` | process() 接收并传递 ctx |
| 1.6 改造 `skill_loader.py` | handler 统一签名 `(params, state, ctx)` |
| 1.7 改造 `app.py` | 入口创建 UserContext，传递到 brain |
| 1.8 改造所有 15 个 skill | 路径从 config 常量改为 ctx 属性 |
| 1.9 定时任务多用户遍历 | 遍历活跃用户 + 随机延迟 |

### Phase 2：对话式设置 + 引导

**目标**：用户可通过对话自定义 Karvis 行为。

| 任务 | 说明 |
|---|---|
| 2.1 新建 `skills/settings.py` | nickname / soul / info 三个 handler |
| 2.2 更新 `prompts.py` | SKILLS + RULES 新增设置类命令 |
| 2.3 新用户欢迎消息 | 检测新用户，发送引导消息 |
| 2.4 SOUL 覆写合并 | 组装 prompt 时合并用户自定义 |

### Phase 3：成本控制

**目标**：管理员可控制 LLM 开销。

| 任务 | 说明 |
|---|---|
| 3.1 每日消息计数 | 注册表中记录今日消息数，到限提醒 |
| 3.2 LLM 用量日志 | 每次调用记录 user_id + tokens |
| 3.3 不活跃用户跳过 | 定时任务检查 last_active |

### Phase 4：Web 查看页面

**目标**：用户通过浏览器查看自己的数据。

| 任务 | 说明 |
|---|---|
| 4.1 令牌系统 | 生成、验证、过期管理 |
| 4.2 API 接口 | 全部数据读取接口 |
| 4.3 前端页面 | 7 个页面（登录/概览/速记/待办/日记/笔记/情绪） |
| 4.4 管理员仪表盘 | 用户列表 + 使用统计 |

### Phase 5：测试 + 部署

| 任务 | 说明 |
|---|---|
| 5.1 数据隔离测试 | 模拟多用户场景，验证数据不串 |
| 5.2 Docker 部署配置 | Dockerfile + docker-compose.yml |
| 5.3 上线试用 | 邀请 2-3 位朋友试用 |

---

## 九、配置项清单

| 环境变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | — | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | 是 | — | DeepSeek API 地址 |
| `DEEPSEEK_MODEL` | 是 | `deepseek-chat` | 模型名 |
| `QWEN_API_KEY` | 否 | — | Qwen Flash API 密钥 |
| `QWEN_BASE_URL` | 否 | — | Qwen API 地址 |
| `QWEN_MODEL` | 否 | — | Qwen 模型名 |
| `QWEN_VL_MODEL` | 否 | — | 千问视觉模型 |
| `WEWORK_CORP_ID` | 是 | — | 企微企业 ID |
| `WEWORK_AGENT_ID` | 是 | — | 应用 AgentID |
| `WEWORK_CORP_SECRET` | 是 | — | 应用 Secret |
| `WEWORK_TOKEN` | 是 | — | 回调 Token |
| `WEWORK_ENCODING_AES_KEY` | 是 | — | 回调加密密钥 |
| `TENCENT_APPID` | 否 | — | 腾讯云 ASR AppID |
| `TENCENT_SECRET_ID` | 否 | — | 腾讯云 SecretID |
| `TENCENT_SECRET_KEY` | 否 | — | 腾讯云 SecretKey |
| `DATA_DIR` | 否 | `./data` | 数据目录根路径 |
| `ADMIN_TOKEN` | 是 | — | 管理员 Web 密钥 |
| `DAILY_MESSAGE_LIMIT` | 否 | `50` | 每用户每日消息上限 |
| `WEB_TOKEN_EXPIRE_HOURS` | 否 | `24` | Web 令牌有效时长（小时） |
| `SENIVERSE_KEY` | 否 | — | 心知天气 API Key |
| `WEATHER_CITY` | 否 | — | 天气查询城市 |
| `INACTIVE_DAYS_THRESHOLD` | 否 | `7` | 不活跃天数阈值 |
| `COMPANION_QUIET_START` | 否 | `23` | 陪伴静默开始时间 |
| `COMPANION_QUIET_END` | 否 | `7` | 陪伴静默结束时间 |
| `COMPANION_INTERVAL_MIN` | 否 | `120` | 陪伴推送最小间隔（分钟） |
| `COMPANION_MAX_PER_DAY` | 否 | `3` | 每日陪伴推送上限 |

---

## 十、成本估算（2-3 人场景）

| 项目 | 月成本 |
|---|---|
| LLM API（DeepSeek V3 + Qwen Flash） | ¥30-90（取决于活跃度） |
| 服务器（1C1G 轻量云） | ¥30-60 |
| 存储 | ¥0（磁盘空间可忽略） |
| **总计** | **¥60-150/月** |

> 2-3 人试用场景下，成本非常可控。后续如扩展到 10+ 人，LLM 成本线性增长。

---

## 十一、验收标准（总体）

| 验收项 | 标准 |
|---|---|
| 多用户隔离 | 3 个用户同时使用，数据完全独立，互不可见 |
| 新用户体验 | 新用户发第一条消息 → 自动注册 + 欢迎引导 + 消息正常处理 |
| 对话设置 | 设置昵称/风格后立即生效 |
| 所有 Skill | 原版 35 个命令 + reflect.* 4 个在多用户环境下正常工作 |
| 定时任务 | 每个用户独立收到晨报/日报/打卡提醒等 |
| Web 查看 | 用户通过令牌链接查看自己的完整数据，不能查看他人数据 |
| 管理员 | 查看用户列表、使用量，可挂起/激活用户 |
| 成本控制 | 每日消息上限生效，LLM 用量可追踪 |
| 部署 | Docker Compose 一键部署 |

---

## 附录 A：从 Karvis-opensource 继承的功能映射

| Karvis-opensource Skill | KarvisForAll 对应 | 改造内容 |
|---|---|---|
| `note.save` | 继承 | 路径 → ctx |
| `classify.archive` | 继承 | 路径 → ctx |
| `checkin.*` | 继承 | 路径 → ctx |
| `todo.*` | 继承 | 路径 → ctx |
| `book.*` | 继承 | 路径 → ctx |
| `media.*` | 继承 | 路径 → ctx |
| `voice.journal` | 继承 | 路径 → ctx |
| `daily.generate` | 继承 | 路径 → ctx |
| `mood.generate` | 继承 | 路径 → ctx |
| `weekly.review` | 继承 | 路径 → ctx |
| `monthly.review` | 继承 | 路径 → ctx（新增） |
| `deep.dive` | 继承 | 路径 → ctx |
| `habit.*` | 继承 | state → ctx |
| `decision.*` | 继承 | state → ctx |
| `internal.*` | 继承 | 路径 → ctx |
| `reflect.*` | 继承 | 路径 → ctx, OneDrive → LocalFileIO |
| — | **新增** `settings.*` | 昵称/风格/信息设置 |
| — | **新增** `web.token` | 生成 Web 查看令牌 |

## 附录 B：与提案文档的决策对照

| 提案中的决策点 | 选择 |
|---|---|
| 存储方案 | Lite 本地 + Web 查看（不使用 OneDrive） |
| 成本分担 | 管理员全额承担 LLM 费用 |
| 用户规模 | 先 2-3 人试用 |
| Web 查看 | 做（Phase 4） |
| 技术方案 | 方案 B：UserContext 上下文对象 |
| 向后兼容 | 独立项目，不需要兼容单用户模式 |

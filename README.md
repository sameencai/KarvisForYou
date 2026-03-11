# KarvisForYou — 多用户 AI 生活助手

> 基于企业微信应用的 AI 生活助手，支持微信聊天置顶，支持多人共享一套部署，每人拥有独立的数据空间。
> 记速记、管待办、写日记、追情绪、养习惯、记账理财——通过对话完成一切。
>
> 内置 **管理员后台**（用户管理、LLM 用量监控）和 **用户 Web 端**（查看笔记、待办、日记、情绪等 14 个页面）。

---

## 功能一览

### 📝 日常记录
- **全类型消息存档**：文字、语音、图片、视频、链接 → 自动存到 Quick-Notes
- **智能分类归档**：自动归类到工作笔记 / 情感日记 / 生活趣事 / 碎碎念
- **链接解析**：分享链接自动抓取全文，回复基于内容理解
- **语音日记**：长语音（>30秒）自动整理为结构化日记
- **读书笔记**：书摘、感想、AI 总结、金句提炼
- **影视笔记**：影评、感想、自动填充影视信息

### ✅ 待办与习惯
- **待办管理**：自然语言增删查，支持截止日期和定时提醒，序号批量操作
- **每日 Top 3**：早报引导设定当天 3 件重要事，晚间追踪完成情况
- **微习惯实验**：基于行为模式自动提议微习惯，触发词检测，实验周期跟踪
- **决策复盘**：记录重要决策，N 天后自动提醒复盘结果

### 📊 复盘与洞察
- **每日打卡**：4 个问题引导复盘，写入日记
- **情绪日记**：每天自动从消息中提取情绪脉络，生成分析
- **日报 / 周报 / 月报**：自动总结、情绪曲线、碎片连线、成长轨迹
- **深度自问（Reflect）**：定期引导深度自我探索，90 天不重复
- **主题深潜**：跨时间线搜索全历史，生成深度分析报告

### 💰 财务管理（管理员功能）
- **账单导入**：支持 iCost App 导出的 xlsx 文件批量导入，自动去重
- **收支查询**：自然语言查询消费记录（按月/周/年/分类筛选）
- **财务快照**：资产/负债概览，支持多期对比和趋势分析
- **月度财报**：自动生成月度收支分析报告，含 AI 深度洞察

### 🤝 主动陪伴
- **智能关怀**：有事才发，没事不发（五层防骚扰）
- **沉默检测 / 情绪跟进 / 待办轻推**
- **时间胶囊**：早报中回顾 7天/30天/365天 前的记录

### 🌐 Web 查看 & 管理
- 14 个 Web 页面：概览、速记、待办、日记、笔记、情绪、记忆、习惯、决策、反思、设置、日志、管理后台、登录
- 管理员后台：用户列表、LLM 用量、成本估算、用量图表、挂起/激活用户

### ⚙️ 个性化设置
- 对话中设置昵称、给 AI 起名、自定义 AI 性格
- Skill 开关：按需启用/禁用功能模块
- 支持 Telegram 双渠道接入

---

## 核心架构

```
企业微信 / Telegram → Flask 网关（解密/去重/ASR/异步转发）
    ↓
brain.process()
    ├── 加载 State + Memory（三级缓存）
    ├── 三层模型路由 → JSON 决策
    │   ├── Flash (Qwen)       — 陪伴关怀、轻量生成
    │   ├── Main  (DeepSeek)   — 日常路由、Skill 分发
    │   └── Think (DeepSeek R1) — 深度分析、主题深潜
    ├── V4 Flash 智能回复 → 二次生成自然语言
    ├── Agent Loop（最多 5 轮）→ 文件搜索/读取后再回答
    ├── Skill 分发 → 执行操作 → 写回数据
    └── Memory 更新 + 决策日志

内置调度器（APScheduler）
    ├── 缓存刷新              （每 30 分钟）
    ├── 晨报                  （每天 08:00）
    ├── 待办提醒              （每天 09:00/14:00/18:00）
    ├── 深度自问              （每天 ~20:30）
    ├── 晚间签到              （每天 21:00）
    ├── 周回顾                （每周日 21:30）
    ├── 情绪日记              （每天 22:00）
    ├── 日报生成              （每天 22:30）
    └── 月度成长回顾          （每月末 22:00）
```

---

## 完整 Skill 列表

共 **48 个 Skill 命令**，分布在 22 个功能模块中（[详细说明 →](SKILLS.md)）：

| 分类 | Skill | 说明 |
|---|---|---|
| **笔记** | `note.save` | 保存到 Quick-Notes |
| | `classify.archive` | 智能归档（工作/情感/趣事/碎碎念） |
| **打卡** | `checkin.start/answer/skip/cancel` | 每日 4 问打卡流程 |
| **待办** | `todo.add/done/list/remind_cancel` | 待办管理（支持循环/截止日期/序号批量操作） |
| **读书** | `book.create/excerpt/thought/summary/quotes` | 读书笔记全流程（含 AI 总结/金句提炼） |
| **影视** | `media.create/thought` | 影视笔记 |
| **复盘** | `daily.generate` `mood.generate` `weekly.review` `monthly.review` | 日/周/月报 + 情绪日记 |
| **深潜** | `deep.dive` | 跨时间线主题深度分析 |
| **反思** | `reflect.push/answer/skip/history` | 深度自问（200 道题库，10 个维度） |
| **习惯** | `habit.propose/nudge/status/complete` | 微习惯实验（触发词检测 + 周期跟踪） |
| **决策** | `decision.record/review/list` | 决策记录 + 定期复盘 |
| **财务** 🔒 | `finance.import/query/snapshot/monthly` | iCost 账单导入 / 收支查询 / 资产快照 / 月度财报 |
| **语音** | `voice.journal` | 长语音自动整理为结构化日记 |
| **动态引擎** | `dynamic` | 直接操作 state 字段（带安全白名单） |
| **Agent** | `internal.read/search/list` | 文件搜索/读取（Agent Loop 信息检索） |
| **设置** | `settings.nickname/ai_name/soul/info/skills` | 个性化设置 + Skill 开关管理 |
| **Web** | `web.token` | 生成 Web 查看链接 |

> 🔒 标记的 Skill 仅管理员可用

---

## 快速开始

### 准备工作

1. **大模型 API Key**：推荐 [腾讯云知识引擎 lkeap](https://console.cloud.tencent.com/lkeap)（国内网络稳定，无需翻墙，开通即用，充值 10 元即可）；也支持 [DeepSeek 官方](https://platform.deepseek.com/) 或任何兼容 OpenAI API 的平台
2. **企业微信应用**：https://work.weixin.qq.com/ 注册企业 → 创建应用 → 记下 Corp ID / AgentId / Secret / Token / EncodingAESKey（[企微应用配置指南 →](docs/企微应用配置指南.md)）
3. **服务器**：腾讯云轻量 1C1G 即可，或本地电脑先体验

### 部署方式一：一键脚本

```bash
git clone https://github.com/sameencai/KarvisForYou.git
cd KarvisForYou
chmod +x setup.sh
./setup.sh
```

### 部署方式二：Docker

```bash
git clone https://github.com/sameencai/KarvisForYou.git
cd KarvisForYou
cp .env.example src/.env
nano src/.env          # 填入配置
cd deploy
docker-compose up -d
```

### 部署方式三：手动

```bash
cd KarvisForYou/src
pip3 install -r requirements.txt
cp ../.env.example .env
nano .env              # 填入配置
python3 app.py
```

### 配置企微回调

启动后将公网地址 + `/wework` 填入企微后台「接收消息」的 URL。

---

## 环境变量

### 必填

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | 大模型 API 密钥（推荐腾讯云 lkeap） |
| `WEWORK_CORP_ID` | 企微企业 ID |
| `WEWORK_AGENT_ID` | 应用 AgentID |
| `WEWORK_CORP_SECRET` | 应用 Secret |
| `WEWORK_TOKEN` | 回调 Token |
| `WEWORK_ENCODING_AES_KEY` | 回调加密密钥 |
| `DEFAULT_USER_ID` | 管理员企微用户 ID |
| `ADMIN_TOKEN` | 管理后台密码 |

### 可选

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_BASE_URL` | `https://api.lkeap.cloud.tencent.com/v1` | API 地址（腾讯云 lkeap） |
| `DEEPSEEK_MODEL` | `deepseek-v3` | 模型名称（腾讯云 lkeap 用 `deepseek-v3`；DeepSeek 官方用 `deepseek-chat`） |
| `QWEN_API_KEY` | 空 | Qwen Flash（省钱） |
| `QWEN_MODEL` | `qwen-flash` | Qwen 模型名 |
| `QWEN_VL_MODEL` | `qwen-vl-max` | 视觉模型 |
| `DAILY_MESSAGE_LIMIT` | `50` | 每人每天消息上限 |
| `WEB_TOKEN_EXPIRE_HOURS` | `24` | Web 链接有效时长 |
| `WEB_DOMAIN` | `127.0.0.1:9000` | Web 访问域名 |
| `SERVER_PORT` | `9000` | 服务端口 |
| `WEATHER_CITY` | `北京` | 天气城市 |
| `ADMIN_USER_ID` | 空 | 管理员 user_id（告警推送） |
| `TELEGRAM_BOT_TOKEN` | 空 | Telegram Bot Token |
| `ACTIVE_CHANNELS` | `wework` | 启用渠道（wework/telegram） |

---

## 项目结构

```
KarvisForYou/
├── setup.sh                 # 一键安装脚本
├── .env.example             # 配置模板
├── src/                     # 核心代码
│   ├── app.py               # Flask 网关（81KB）
│   ├── brain.py             # AI 大脑（65KB）
│   ├── prompts.py           # Prompt 统一管理（48KB）
│   ├── config.py            # 配置管理
│   ├── user_context.py      # 多用户管理
│   ├── web_routes.py        # Web API + 页面路由
│   ├── memory.py            # 记忆系统（三级缓存）
│   ├── skill_loader.py      # Skill 自动发现 V12
│   ├── channel_router.py    # 渠道路由器
│   ├── storage.py           # 存储抽象层
│   ├── local_io.py          # 本地文件读写
│   ├── onedrive_io.py       # OneDrive 存储
│   ├── wework_crypto.py     # 企微消息加解密
│   ├── telegram_bot.py      # Telegram 接入
│   ├── finance_utils.py     # 财务工具
│   ├── requirements.txt     # Python 依赖
│   ├── skills/              # 22 个功能模块 → 48 个 Skill
│   └── web_static/          # 14 个 Web 页面
├── deploy/                  # Docker + SCF 部署
├── tests/                   # 测试
├── landing/                 # 官网首页
├── docs/                    # 文档
└── assets/                  # 截图资源
```

---

## 数据结构

```
data/
├── _karvis_system/          # 系统数据
│   ├── user_registry.json   # 用户注册表
│   ├── token_store.json     # Web 令牌
│   └── usage_log.json       # LLM 用量日志
└── users/                   # 按用户隔离
    └── <user_id>/
        ├── 00-Inbox/        # 速记、待办、状态
        ├── 01-Daily/        # 日报、周报、月报
        ├── 02-Notes/        # 分类笔记
        └── _Karvis/         # 记忆、配置、日志
```

---

## 成本估算

以 2-3 人日常使用：

| 项目 | 月费用 |
|---|---|
| DeepSeek API | ¥15-50 |
| 服务器（1C1G） | ¥30-60 |
| 其他（Qwen/ASR） | ¥0-20 |
| **合计** | **¥45-130/月** |

---

## 官网

🔗 **https://karvis.top**

---

## License

[MIT](LICENSE)

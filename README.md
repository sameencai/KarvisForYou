<h1 align="center">Karvis — 住在微信里的 AI 生活管家</h1>

<p align="center">
  在微信里说句话，它帮你记笔记、管待办、写日记、追踪情绪。<br>
  支持 2-5 人共享部署，每人独立数据空间。月 Token 成本不到一块钱。
</p>

<p align="center">
  <a href="https://karvis.top">官网</a> · <a href="#快速开始">快速开始</a> · <a href="#功能一览">功能</a> · <a href="docs/architecture.md">架构</a> · <a href="CHANGELOG.md">更新日志</a>
</p>

---

## 功能一览

| 能力 | 说明 |
|---|---|
| 📝 随手记录 | 发消息自动记录，支持文字 / 语音 / 图片 / 链接 |
| ✅ 待办管理 | "帮我记个待办：明天交报告" |
| 🌅 晨报 & 日报 | 8 点叫你起床，晚上自动生成复盘 |
| 😊 情绪追踪 | AI 感知情绪，生成情绪曲线 |
| 🧠 三层记忆 | 工作记忆 + 长期记忆 + 知识库，越用越懂你 |
| 📚 读书 / 影视笔记 | "刚看完《三体》，记一下感想" |
| 🔄 周报 / 月报 | 自动复盘，不用动手 |
| 💬 有温度的陪伴 | 你说胃疼它会关心你，加班到很晚它会心疼 |
| 🌐 Web 页面 | 浏览器查看所有数据：速记、待办、日记、情绪曲线 |

每个用户的数据**完全隔离**，你看不到别人的，别人也看不到你的。

---

## 快速开始

> **三步搞定**：准备钥匙 → 部署服务 → 连上企微

### 第一步：准备两把钥匙

| 钥匙 | 在哪拿 | 说明 |
|---|---|---|
| **DeepSeek API Key** | [platform.deepseek.com](https://platform.deepseek.com/) | AI 的大脑。注册 → API Keys → 创建。充 10 块够用很久 |
| **企业微信应用** | [work.weixin.qq.com](https://work.weixin.qq.com/) | Karvis 住的地方。用微信扫码注册企业（不需要真的是公司） |

#### 企微应用怎么建

1. 登录[企微管理后台](https://work.weixin.qq.com/wework_admin/frame) → 应用管理 → 创建应用
2. 记下这 5 样东西（后面要填）：

| 在哪找 | 什么东西 |
|---|---|
| 企业信息页 | **企业 ID**（Corp ID） |
| 应用详情页 | **AgentId** |
| 应用详情页 | **Secret** |
| 应用详情 → 接收消息 → 设置 API 接收 | **Token**（点随机生成） |
| 同上 | **EncodingAESKey**（点随机生成） |

> ⚠️ 「接收消息」的 URL **先不填**，等 Karvis 启动后再填。

> 💡 **详细图文教程**见 [docs/wechat-setup.md](docs/wechat-setup.md)

---

### 第二步：部署 Karvis

选一个适合你的方式：

<details>
<summary><b>🐳 方式一：Docker 一键部署（推荐，最稳定）</b></summary>

适合：有服务器的人（腾讯云轻量 1C1G 就够，约 ¥30/月）

```bash
# 1. 克隆代码
git clone https://github.com/sameencai/KarvisForYou.git
cd KarvisForYou

# 2. 配置环境变量（把模板复制一份，填入你的真实值）
cp .env.example src/.env
nano src/.env

# 3. 启动
cd deploy
docker compose up -d

# 4. 查看日志，确认启动成功
docker logs karvis
```

看到 `Running on http://0.0.0.0:9000` 就说明成功了。**紧接着会打印企微回调地址，复制备用。**

> 没装 Docker？运行 `curl -fsSL https://get.docker.com | sh` 一键安装。

</details>

<details>
<summary><b>📜 方式二：一键脚本（推荐新手/本地试用）</b></summary>

适合：先在自己电脑上跑跑看、或者不想用 Docker 的人

```bash
git clone https://github.com/sameencai/KarvisForYou.git
cd KarvisForYou
./setup.sh
```

脚本会自动：检查 Python → 安装依赖 → 引导你填配置 → 安装内网穿透 → 启动服务 → **打印回调地址**。

> 需要 Python 3.9+。Windows 用户建议用 WSL。

</details>

<details>
<summary><b>🔧 方式三：手动部署</b></summary>

```bash
git clone https://github.com/sameencai/KarvisForYou.git
cd KarvisForYou/src

# 安装依赖
pip3 install -r requirements.txt

# 配置
cp ../.env.example .env
nano .env    # 填入你的配置

# 启动
python3 app.py

# 后台运行（可选）
nohup python3 app.py > karvis.log 2>&1 &
```

启动后终端会打印企微回调地址。长期运行建议配置 systemd，详见[运维手册](docs/operations.md)。

</details>

#### .env 最少只要填这些

```bash
# AI 大脑
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# 企业微信
WEWORK_CORP_ID=你的企业ID
WEWORK_AGENT_ID=你的AgentID
WEWORK_CORP_SECRET=你的Secret
WEWORK_TOKEN=你的Token
WEWORK_ENCODING_AES_KEY=你的AESKey

# 你的企微用户 ID（通讯录里点自己能看到）
DEFAULT_USER_ID=你的用户ID

# 管理员密码（随便写一个长密码）
ADMIN_TOKEN=随便写一个长密码
```

> 完整配置项说明见 [.env.example](.env.example)，注释很详细。

---

### 第三步：连上企业微信

> **🚀 好消息：Karvis 启动后会自动检测你的网络环境，并在终端打印企微回调地址。直接复制粘贴到企微后台即可！**
>
> ```
> ╔══════════════════════════════════════════════════════════════╗
> ║  🚀 Karvis 已启动！                                          ║
> ╠══════════════════════════════════════════════════════════════╣
> ║  📋 企业微信回调地址（复制到企微后台 → 接收消息 → URL）：     ║
> ║  https://xxx-xxx.trycloudflare.com/wework                   ║
> ║  (来源: Cloudflare Tunnel)                                   ║
> ╚══════════════════════════════════════════════════════════════╝
> ```
>
> **⚠️ 这一步不能跳过！不配回调地址，Karvis 就收不到消息。**

Karvis 启动后，你需要告诉企微"消息往哪发"。把终端打印的回调地址填到企微后台：

**企微管理后台 → 你的应用 → 接收消息 → 设置 API 接收 → URL 填终端打印的地址**

Token 和 EncodingAESKey 填 `.env` 里的值，点保存 ✅

---

下面是回调地址的三种来源（**app.py 启动后会自动判断并打印，不需要你手动操作**）：

| 你的环境 | app.py 会自动做什么 | 你只需要 |
|---|---|---|
| 有公网 IP 的服务器 | 检测到公网 IP，直接生成 `http://IP:9000/wework` | 复制地址到企微后台，并配**企业可信 IP** |
| 本地电脑（已装 cloudflared） | **自动启动 cloudflared 隧道**，生成公网地址 | 复制地址到企微后台 |
| 本地电脑（未装 cloudflared） | 提示你安装 cloudflared | 先装 cloudflared，再重启 app.py |

> **⚠️ 有公网 IP 的服务器：别忘了配企业可信 IP**（应用详情 → 企业可信 IP → 填你的服务器公网 IP）。不配这个，Karvis 能收消息但**发不出去**。

#### 本地开发需要安装 cloudflared（一次性操作）

如果你在本地电脑运行（没有公网 IP），需要先安装 cloudflared。**安装一次后，以后每次启动 app.py 都会自动创建隧道，不需要手动操作**：

```bash
# Mac
brew install cloudflared

# Ubuntu/Debian
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared

# Windows
# 下载 https://github.com/cloudflare/cloudflared/releases/latest 放到 PATH 中
```

安装好后，**直接启动 `python3 app.py`**，它会自动启动隧道并打印回调地址。

> ⚠️ 免费隧道地址每次重启会变。想要固定地址，可以登录 Cloudflare 创建命名隧道（仍然免费），详见 [Cloudflare 文档](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)。

#### 验证连接

在企微 app 里找到你的应用，发一条"你好"。

- ✅ **成功**：Karvis 回复欢迎消息，引导你设置昵称
- ❌ **没回复**？看[常见问题](#常见问题)排查

---

## 邀请朋友

1. 企微管理后台 → 通讯录 → 添加成员（微信扫码就行）
2. 确保朋友能看到应用（应用详情 → 可见范围 → 全公司）
3. 朋友直接给应用发消息，**自动注册，零配置**

---

## Web 查看页面

在企微里对 Karvis 说「给我查看链接」，会收到一个浏览器链接。

| 页面 | 内容 |
|---|---|
| 📊 概览 | 今日速记数、待办进度、情绪曲线、打卡天数 |
| 📝 速记 | 所有速记记录，按日期筛选 |
| ✅ 待办 | 进行中 / 已完成 |
| 📖 日记 | 日报、周报、月报、情绪日记 |
| 📂 笔记 | 读书 / 影视 / 工作笔记分类归档 |
| 🧠 记忆 | 长期记忆列表 |
| 😊 情绪 | 30 天情绪折线图 |

> 链接有效期 24 小时，过期后再说一次「给我查看链接」。
>
> 部署在服务器上需要设 `WEB_DOMAIN=你的IP:9000`，否则链接指向 localhost。

---

## 管理员后台

浏览器打开 `http://你的地址:9000/web/admin`，输入 `ADMIN_TOKEN`。

可以查看：用户列表、LLM 用量、成本估算、用量图表。可以操作：挂起 / 激活用户。

---

## 技术架构

```
用户手机 → 企业微信 → /wework → 解密 → 异步处理
                                         ↓
                                    brain.py（AI 大脑）
                                    ├── Flash 层（Qwen - 快速响应）
                                    ├── Main 层（DeepSeek V3 - 主力）
                                    └── Think 层（DeepSeek R - 深度推理）
                                         ↓
                                    24 个技能模块（43 个 Skill）
                                         ↓
                                    存储（本地 / OneDrive → Obsidian）
```

> 详细架构文档见 [docs/architecture.md](docs/architecture.md)

---

## 项目结构

```
KarvisForAll/
├── setup.sh                 # 一键安装脚本
├── .env.example             # 环境变量模板
├── src/
│   ├── app.py               # 主入口（Flask 消息网关）
│   ├── brain.py             # AI 大脑（意图识别 → 技能分发）
│   ├── config.py            # 配置管理
│   ├── user_context.py      # 多用户管理
│   ├── skills/              # 24 个技能插件模块
│   ├── web_static/          # Web 前端页面
│   └── requirements.txt     # Python 依赖
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── scheduler/           # 定时任务（可选）
├── data/                    # 运行时数据（自动生成）
├── docs/                    # 文档
├── tests/                   # 测试（111 项检查）
└── scripts/backup.sh        # 备份脚本
```

---

## 成本估算

| 项目 | 月费用 |
|---|---|
| DeepSeek API（2-3 人使用） | ¥15-50 |
| 服务器（腾讯云轻量 1C1G） | ¥30-60 |
| 其他（Qwen / ASR，可选） | ¥0-20 |
| **合计** | **¥45-130/月** |

> Token 层面：三层路由策略，日均 30+ 次使用，月 Token 成本不到 ¥1/人。

---

## 常见问题

<details>
<summary><b>发消息后 Karvis 没有回复</b></summary>

1. **看日志**：`docker logs karvis --tail 100`
2. 搜 `[handle_message]` — 没有 = 企微消息没到 Karvis
   - URL 填对了没？Karvis 启动了没？防火墙放行 9000 了没？
3. 搜 `[Brain]` — 有 = 消息收到了但 AI 处理出错
   - API Key 对不对？余额够不够？
4. 搜 `reply_text` — 有 = AI 回了但企微没收到
   - **企业可信 IP** 配了没？

</details>

<details>
<summary><b>企微后台填 URL 时提示"回调验证失败"</b></summary>

- Karvis 启动了吗？（`http://你的IP:9000/web/login` 能打开吗）
- Token 和 EncodingAESKey 是否和 `.env` **完全一致**
- 用 cloudflared 的话，隧道还在运行吗

</details>

<details>
<summary><b>Web 查看链接打不开</b></summary>

- 服务器部署：确保 `.env` 中设了 `WEB_DOMAIN=你的IP:9000`
- 链接有效期 24 小时，过期后在企微说「给我查看链接」
- 防火墙放行 9000 端口

</details>

<details>
<summary><b>迁移数据 / 换服务器</b></summary>

把 `data/` 目录整个复制到新服务器就行，所有数据都在里面：

```bash
tar czf karvis-backup.tar.gz data/    # 旧服务器打包
tar xzf karvis-backup.tar.gz          # 新服务器解压
```

</details>

<details>
<summary><b>怎么更新代码</b></summary>

```bash
git pull
cd deploy && docker compose down && docker compose up -d --build
```

保留 `data/` 和 `src/.env`，其他随便覆盖。

</details>

<details>
<summary><b>想加 Qwen（省钱）/ 语音识别 / 天气播报</b></summary>

在 `.env` 中填入对应的 API Key 即可，详见 [.env.example](.env.example) 中的注释。

</details>

---

## 许可证

[MIT License](LICENSE)

---

<p align="center">
  <b>Karvis</b> — 不用刻意坐下来记录，在生活的间隙，随手说句话，它帮你沉淀一切。
</p>

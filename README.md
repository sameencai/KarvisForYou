# KarvisForAll — 多用户 AI 生活助手

> 基于企业微信的 AI 生活助手，支持 2-5 人共享一套部署，每人拥有独立的数据空间。
> 记速记、管待办、写日记、追情绪、养习惯——通过对话完成一切。

---

## 目录

- [它能做什么](#它能做什么)
- [准备工作](#准备工作)
- [部署方式一：一键脚本（推荐新手）](#部署方式一一键脚本推荐新手)
- [部署方式二：Docker（推荐服务器）](#部署方式二docker推荐服务器)
- [部署方式三：轻量云服务器手动部署](#部署方式三轻量云服务器手动部署)
- [让企业微信连上 Karvis](#让企业微信连上-karvis)
- [本地测试（发消息之前先确认能跑）](#本地测试发消息之前先确认能跑)
- [邀请朋友加入](#邀请朋友加入)
- [Web 查看页面](#web-查看页面)
- [管理员后台](#管理员后台)
- [环境变量完整清单](#环境变量完整清单)
- [目录结构说明](#目录结构说明)
- [常见问题 FAQ](#常见问题-faq)
- [成本估算](#成本估算)

---

## 它能做什么

| 功能 | 说明 |
|---|---|
| 📝 速记 | 发消息自动记录，支持文字/语音/图片/链接 |
| ✅ 待办 | "帮我记个待办：明天交报告" |
| 📖 日记 | 每晚自动生成日报，总结你一天说了什么 |
| 😊 情绪追踪 | AI 感知你的情绪，生成情绪曲线 |
| 📚 读书/影视笔记 | "刚看完《三体》，记一下感想" |
| 🔄 周报/月报 | 自动复盘，不用自己动手 |
| ⚙️ 个性化 | 对话设置昵称、AI 说话风格 |
| 🌐 Web 查看 | 浏览器查看自己的所有数据（只读） |

每个用户的数据**完全隔离**，你看不到别人的，别人也看不到你的。

---

## 准备工作

开始之前，你需要准备这些东西（**一共 3 样**）：

### 1. DeepSeek API Key（必须有）

这是 AI 的大脑，KarvisForAll 靠它理解你说的话。

1. 打开 https://platform.deepseek.com/
2. 注册账号 → 左侧菜单「API Keys」→ 创建一个
3. 充值一点余额（10 块钱够用很久）
4. 复制 API Key，长这样：`sk-xxxxxxxxxxxxxxxx`

> 💡 也支持腾讯云 lkeap 的 DeepSeek（地址换成 `https://api.lkeap.cloud.tencent.com/v1`）

```
腾讯云：
sk-C8HI5gioJpHXTsmk6l21RgXdyGpNk1AgRfUyNKaS04IH1YjV
```

### 2. 企业微信应用（必须有）

这是 Karvis 住的地方。

1. 打开 https://work.weixin.qq.com/ → 注册一个企业（用你的微信扫码就行，不需要真的是公司）
2. 登录管理后台 → 应用管理 → 创建应用
3. 记下这些信息（后面要填）：

| 在哪找 | 叫什么 |
|---|---|
| 企业信息页 | **企业 ID**（Corp ID） |
| 应用详情页 | **AgentId** |
| 应用详情页 | **Secret** |
| 应用详情页 → 接收消息 → 设置 API 接收 | **Token** 和 **EncodingAESKey**（点随机生成即可） |

> ⚠️ 「接收消息」的 URL 先不填，等 Karvis 启动后再填。

```
Corp ID：wwcd9cd8b12fae39c4
AgentId：1000005
Secret：f6snq69mV-rm40X1npitpHEwWuMdG3z7UmmdZzfQoYU
Token：j9TJjnTYOaNz5n6
EncodingAESKey：kigdMYBtBSQTR1iTY2aiuExqSDI4oJfUWhYFfCy50bg
```
### 3. 一台服务器（或你的电脑）

Karvis 需要一个 24 小时运行的地方。选一个就行：

| 方案 | 适合谁 | 费用 |
|---|---|---|
| **你自己的电脑** | 先试试能不能跑 | 免费，但关机就断 |
| **腾讯云轻量服务器** | 长期使用（推荐） | ¥30-60/月 |
| **任意 Linux 服务器** | 有现成服务器的人 | — |

> 服务器推荐：腾讯云轻量应用服务器，1 核 1G 的最便宜款就够用。

### 怎么把代码传到服务器

目前代码还没有上传到 GitHub，你需要手动把 `KarvisForAll` 文件夹传到服务器上。

**方法一：scp 命令（Mac/Linux 终端）**

```bash
# 在你的电脑上执行，把整个文件夹传到服务器
scp -r KarvisForAll root@你的服务器IP:/root/
```

**方法二：用 SFTP 工具（推荐不熟悉命令行的人）**

1. 下载 [FileZilla](https://filezilla-project.org/)（免费）或 [Cyberduck](https://cyberduck.io/)
2. 连接你的服务器（填 IP、用户名 root、密码）
3. 把 `KarvisForAll` 文件夹拖到服务器的 `/root/` 目录下

**方法三：打包后上传**

```bash
# 在你的电脑上打包
tar czf KarvisForAll.tar.gz KarvisForAll/

# 上传到服务器
scp KarvisForAll.tar.gz root@你的服务器IP:/root/

# 在服务器上解压
ssh root@你的服务器IP
tar xzf KarvisForAll.tar.gz
```

---

## 部署方式一：一键脚本（推荐新手）

适合在**你自己电脑**上试用，或在服务器上快速部署。

### 步骤

```bash
# 第 1 步：进入项目目录（把代码文件夹放到你想要的位置）
cd KarvisForAll

# 第 2 步：运行安装脚本（它会一步步引导你填配置）
./setup.sh
```

脚本会自动完成：
- ✅ 检查 Python 版本（需要 3.9+）
- ✅ 安装依赖
- ✅ 引导你填写 DeepSeek API Key、企微配置
- ✅ 自动生成管理员令牌（**请记下来！**）
- ✅ 安装内网穿透工具（cloudflared）
- ✅ 启动 Karvis + 生成公网 URL

```
已自动生成管理员令牌: fde4a659337541f09e4234c4
你的公网 IP: 112.24.112.171
```

启动成功后你会看到类似这样的输出：

```
╔══════════════════════════════════════════════════════════════╗
║  公网 URL 已生成!                                            ║
╚══════════════════════════════════════════════════════════════╝

  你的公网地址: https://xxx-xxx-xxx.trycloudflare.com

  去企微后台 → 应用 → 接收消息 → API 接收 → URL 填:
  https://xxx-xxx-xxx.trycloudflare.com/wework
```

> ⚠️ cloudflared 的地址**每次重启都会变**。如果你需要稳定地址，请看下面的服务器部署方案。

---

## 部署方式二：Docker（推荐服务器）

适合在**服务器**上长期运行，最简单稳定。

### 前置要求

服务器上需要安装 Docker 和 Docker Compose。如果没装过：

```bash
# Ubuntu/Debian 一键安装 Docker
curl -fsSL https://get.docker.com | sh
sudo systemctl start docker
sudo systemctl enable docker

# 安装 Docker Compose（如果 docker compose 命令不可用）
sudo apt install docker-compose -y
```

### 步骤

```bash
# 第 1 步：把代码文件夹上传到服务器，然后进入目录
cd KarvisForAll

# 第 2 步：配置环境变量
cp .env.example src/.env
nano src/.env          # 用你喜欢的编辑器打开，填入真实值
```

打开 `src/.env` 后，你**至少需要填这些**（把等号后面的值换成你自己的）：

```bash
DEEPSEEK_API_KEY=sk-你的key
WEWORK_CORP_ID=你的企业ID
WEWORK_AGENT_ID=你的AgentID
WEWORK_CORP_SECRET=你的Secret
WEWORK_TOKEN=你的Token
WEWORK_ENCODING_AES_KEY=你的AESKey
DEFAULT_USER_ID=你的企微用户ID
ADMIN_TOKEN=随便写一个长密码用于管理后台
```

> 💡 `DEFAULT_USER_ID` 怎么找？登录企微管理后台 → 通讯录 → 点击你自己 → 账号就是你的用户 ID。

```bash
# 第 3 步：启动！
cd deploy
docker-compose up -d

# 查看是否启动成功
docker logs karvis
```

看到这样的输出就说明成功了：

```
[Init] 系统目录已就绪: /app/data/_karvis_system
[Scheduler] 内置调度器已启动，共 10 个定时任务
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:9000
```

### 常用 Docker 命令

```bash
docker-compose up -d        # 启动（后台运行）
docker-compose down          # 停止
docker-compose restart       # 重启
docker logs -f karvis        # 查看实时日志
docker logs karvis --tail 50 # 查看最近 50 行日志
```

---

## 部署方式三：轻量云服务器手动部署

如果你不想用 Docker，可以直接在服务器上运行。

```bash
# 第 1 步：安装 Python（Ubuntu 通常已自带）
sudo apt update
sudo apt install python3 python3-pip -y

# 第 2 步：把代码文件夹上传到服务器，然后进入目录
cd KarvisForAll/src

# 第 3 步：安装依赖
pip3 install -r requirements.txt

# 第 4 步：配置环境变量
cp ../.env.example .env
nano .env               # 填入你的配置（参考上面的说明）

# 第 5 步：启动
python3 app.py
```

### 让它在后台持续运行（关掉终端也不会停）

```bash
# 方法一：nohup（最简单）
nohup python3 app.py > karvis.log 2>&1 &

# 查看日志
tail -f karvis.log

# 停止
ps aux | grep app.py
kill <PID>
```

```bash
# 方法二：systemd（推荐，开机自启）
sudo tee /etc/systemd/system/karvis.service << 'EOF'
[Unit]
Description=KarvisForAll AI Assistant
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/KarvisForAll/src
ExecStart=/usr/bin/python3 app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable karvis    # 开机自启
sudo systemctl start karvis     # 启动
sudo systemctl status karvis    # 查看状态
journalctl -u karvis -f         # 查看日志
```

> 把 `/root/KarvisForAll/src` 换成你实际的代码路径。

---

## 让企业微信连上 Karvis

Karvis 启动后，你需要告诉企微「消息发到哪」。

### 如果你在服务器上（有公网 IP）

1. 打开企微管理后台 → 你的应用 → **接收消息** → 设置 API 接收
2. URL 填：`http://你的服务器IP:9000/wework`
3. Token 和 EncodingAESKey 填你 `.env` 里设置的值
4. 点保存

> ⚠️ 还需要配置**企业可信 IP**：应用详情 → 企业可信 IP → 填入你的服务器公网 IP。
> 不配这个，Karvis 能收到消息但**发不出去**。

### 如果你在自己电脑上（没有公网 IP）

需要用内网穿透工具把本地端口暴露到公网：

```bash
# cloudflared（setup.sh 会自动安装）
cloudflared tunnel --url http://localhost:9000

# 它会输出一个公网地址，类似：
# https://abc-def-ghi.trycloudflare.com
```

然后去企微后台填：`https://abc-def-ghi.trycloudflare.com/wework`

### 验证连接

企微后台填好 URL 后，在企微 app 里找到你的应用，发一条消息（比如"你好"）。

**成功的标志**：Karvis 回复了你一条欢迎消息（首次使用会引导你设置昵称）。

**如果没收到回复**，检查：
1. 服务器日志有没有收到请求（搜 `[handle_message]`）
2. 企业可信 IP 有没有配
3. Token / AESKey 是否和企微后台一致

---

## 本地测试（发消息之前先确认能跑）

### 快速健康检查

Karvis 启动后，在浏览器里打开：

```
http://你的地址:9000/web/login
```

能看到登录页面就说明 Web 部分正常。

### 运行自动化测试

```bash
cd KarvisForAll
python tests/test_isolation.py
```

会运行 111 项检查，包括：
- 多用户数据隔离
- 令牌生成/验证/过期
- 消息限流
- 用户挂起/激活
- 所有 API 接口
- 所有 Web 页面

全部通过会显示：`✓ 全部通过！`

### 检查模块加载

```bash
cd KarvisForAll/src
python3 -c "from skill_loader import load_skill_registry; r=load_skill_registry(); print(f'Skills: {len(r)}')"
```

应该输出 `Skills: 37`。

---

## 邀请朋友加入

### 第 1 步：把朋友加入企业

1. 企微管理后台 → 通讯录 → 添加成员
2. 让朋友用微信扫描邀请二维码
3. 确保朋友能看到你创建的应用（应用详情 → 可见范围 → 设为全公司或指定部门）

### 第 2 步：朋友开始使用

朋友在企微 app 里找到应用，**直接发消息就行**。

- 第一条消息会自动触发注册
- Karvis 会发送欢迎消息，引导设置昵称
- 之后正常聊天即可

**不需要任何额外配置**，发消息就能用。

### 第 3 步：朋友查看自己的数据

朋友可以对 Karvis 说：

```
给我查看链接
```

Karvis 会回复一个链接，点开就能在浏览器里看到自己的速记、待办、日记等。

> 链接有效期 24 小时，过期后再说一次「给我查看链接」就行。

---

## Web 查看页面

每个用户可以通过浏览器查看自己的数据（只读，不能修改）。

### 获取方式

在企微里对 Karvis 说「给我查看链接」，会收到一个链接。

### 包含页面

| 页面 | 内容 |
|---|---|
| 📊 概览 | 今日速记数、待办进度、情绪曲线、连续打卡天数 |
| 📝 速记 | 所有速记记录，支持按日期筛选 |
| ✅ 待办 | 进行中/已完成的待办事项 |
| 📖 日记 | 日报、周报、月报、情绪日记 |
| 📂 笔记 | 读书笔记、影视笔记、工作笔记等分类归档 |
| 😊 情绪 | 30 天情绪折线图 + 情绪日记列表 |

### 重要提醒

- 链接包含**访问令牌**，不要分享给别人
- 令牌默认 24 小时有效，可通过 `WEB_TOKEN_EXPIRE_HOURS` 环境变量调整
- 如果你部署在服务器上，需要设置 `WEB_DOMAIN` 环境变量（否则链接会指向 127.0.0.1）

```bash
# 在 .env 中设置（换成你的域名或 IP:端口）
WEB_DOMAIN=你的服务器IP:9000
```

---

## 管理员后台

管理员可以通过 Web 页面查看所有用户的使用情况。

### 访问方式

浏览器打开：

```
http://你的地址:9000/web/admin
```

输入你在 `.env` 里设置的 `ADMIN_TOKEN`。

### 功能

| 功能 | 说明 |
|---|---|
| 用户列表 | 所有注册用户、注册时间、最近活跃时间、消息数 |
| LLM 用量 | 总 token 用量、按用户分组的用量统计 |
| 成本估算 | 根据 token 用量估算 LLM 费用 |
| 用量图表 | 最近 7 天的用量柱状图 |
| 管理操作 | 挂起/激活用户（挂起后该用户无法使用 Karvis） |

---

## 环境变量完整清单

### 必填项

| 变量 | 说明 | 示例 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | `sk-xxxxxx` |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com/v1` |
| `WEWORK_CORP_ID` | 企微企业 ID | `ww1234567890` |
| `WEWORK_AGENT_ID` | 应用 AgentID | `1000003` |
| `WEWORK_CORP_SECRET` | 应用 Secret | `xxxxxx` |
| `WEWORK_TOKEN` | 回调 Token | `xxxxxx` |
| `WEWORK_ENCODING_AES_KEY` | 回调加密密钥 | `xxxxxx` |
| `DEFAULT_USER_ID` | 你自己的企微用户 ID | `zhangsan` |
| `ADMIN_TOKEN` | 管理员后台密码 | `my-secret-123` |

### 可选项（有默认值，不填也能跑）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_MODEL` | `deepseek-v3.2` | 模型名称 |
| `QWEN_API_KEY` | 空 | Qwen API Key（留空则全部用 DeepSeek） |
| `QWEN_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Qwen API 地址 |
| `QWEN_MODEL` | `qwen-plus-latest` | Qwen 模型名 |
| `QWEN_VL_MODEL` | `qwen-vl-max` | Qwen 视觉模型（图片理解用） |
| `DAILY_MESSAGE_LIMIT` | `50` | 每人每天最多发多少条消息 |
| `WEB_TOKEN_EXPIRE_HOURS` | `24` | Web 查看链接有效多少小时 |
| `WEB_DOMAIN` | `127.0.0.1:9000` | Web 查看链接的域名（部署到服务器必须改） |
| `DATA_DIR` | `./data` | 数据保存目录 |
| `INACTIVE_DAYS_THRESHOLD` | `7` | 多少天不活跃就不触发定时任务 |
| `TENCENT_APPID` | 空 | 腾讯云 ASR（语音转文字，不填则语音功能不可用） |
| `TENCENT_SECRET_ID` | 空 | 腾讯云 SecretID |
| `TENCENT_SECRET_KEY` | 空 | 腾讯云 SecretKey |
| `SENIVERSE_KEY` | 空 | 心知天气 API Key（不填则没有天气播报） |
| `WEATHER_CITY` | `深圳` | 天气查询城市 |
| `PROCESS_ENDPOINT_URL` | `http://127.0.0.1:9000/process` | 内部处理端点，不用改 |

---

## 目录结构说明

```
KarvisForAll/
├── setup.sh                 ← 一键安装脚本
├── .env.example             ← 环境变量模板（复制为 src/.env）
│
├── src/                     ← 核心代码
│   ├── app.py               ← 主程序入口（Flask 服务器）
│   ├── brain.py             ← AI 大脑（理解消息 → 决定行动）
│   ├── config.py            ← 配置管理
│   ├── user_context.py      ← 多用户管理（目录隔离、注册、令牌）
│   ├── web_routes.py        ← Web API + 页面路由
│   ├── prompts.py           ← AI 提示词模板
│   ├── memory.py            ← 记忆系统
│   ├── skill_loader.py      ← 技能加载器
│   ├── storage.py           ← 存储抽象层
│   ├── local_io.py          ← 本地文件读写
│   ├── wework_crypto.py     ← 企微消息加解密
│   ├── requirements.txt     ← Python 依赖清单
│   ├── .env                 ← 你的环境变量（不会上传 git）
│   ├── skills/              ← 37 个技能插件
│   └── web_static/          ← 8 个 Web 前端页面
│
├── deploy/                  ← 部署配置
│   ├── Dockerfile           ← Docker 镜像定义
│   ├── docker-compose.yml   ← Docker Compose 编排
│   └── scheduler/           ← 腾讯云 SCF 定时调度器（可选）
│
├── tests/                   ← 测试
│   └── test_isolation.py    ← 数据隔离测试（111 项）
│
├── data/                    ← 运行时数据（自动生成，不要手动改）
│   ├── _karvis_system/      ← 系统数据（用户表、令牌、用量日志）
│   └── users/               ← 每个用户的独立数据目录
│       ├── 用户A/
│       │   ├── 00-Inbox/    ← 速记、待办、状态
│       │   ├── 01-Daily/    ← 日报、周报、月报
│       │   ├── 02-Notes/    ← 分类笔记
│       │   └── _Karvis/     ← 记忆、配置、日志
│       └── 用户B/
│           └── ...（结构相同，数据独立）
│
└── docs/
    └── requirements.md      ← 需求文档
```

---

## 常见问题 FAQ

### Q: 发消息后 Karvis 没有回复

**逐步排查**：

1. **看日志**：`docker logs karvis --tail 100` 或 `tail -100 karvis.log`
2. 搜索 `[handle_message]` — 如果没有，说明企微消息没送到 Karvis
   - 检查企微后台的 URL 填对了没
   - 检查服务器防火墙有没有放行 9000 端口
3. 搜索 `[Brain]` — 如果有，说明消息收到了但处理出错
   - 检查 `DEEPSEEK_API_KEY` 是否填对
   - 检查 DeepSeek 余额是否充足
4. 搜索 `reply_text` — 如果有但企微没收到
   - 检查「企业可信 IP」有没有配

### Q: 企微后台填 URL 时提示「回调验证失败」

- 确认 Karvis 已经启动（能访问 `http://你的地址:9000/web/login`）
- 确认 Token 和 EncodingAESKey 与 `.env` 中一致
- 如果用 cloudflared，确认隧道还在运行

### Q: Web 查看链接打不开

- 如果部署在服务器上，确保 `.env` 中设置了 `WEB_DOMAIN=你的IP:9000`
- 链接默认 24 小时有效，过期后在企微说「给我查看链接」重新获取
- 检查服务器防火墙是否放行 9000 端口

### Q: 想换服务器 / 迁移数据怎么办

只需要把 `data/` 目录整个复制到新服务器就行，所有用户数据、记忆、配置都在里面。

```bash
# 旧服务器
tar czf karvis-data-backup.tar.gz data/

# 新服务器
tar xzf karvis-data-backup.tar.gz
```

### Q: 怎么限制每个人每天发消息的数量

在 `.env` 中设置：

```bash
DAILY_MESSAGE_LIMIT=30   # 每人每天最多 30 条
```

超限后 Karvis 会温柔提醒用户「今天的额度用完了」。

### Q: 怎么挂起一个用户（暂停他使用）

1. 打开管理员后台：`http://你的地址:9000/web/admin`
2. 输入 `ADMIN_TOKEN`
3. 在用户列表中点击「挂起」

挂起后该用户发消息不会被处理，也不会收到定时推送。

### Q: 怎么增加 Qwen API（可选，能省钱）

Qwen Flash 用于简单任务（记速记、转发笔记等），比 DeepSeek 便宜很多。

1. 去 https://bailian.console.aliyun.com/ 注册
2. 在 `.env` 中填入：

```bash
QWEN_API_KEY=sk-你的qwen-key
```

### Q: 怎么开启语音消息识别

需要腾讯云 ASR 服务：

1. 去 https://console.cloud.tencent.com/asr 开通
2. 在 `.env` 中填入：

```bash
TENCENT_APPID=你的AppId
TENCENT_SECRET_ID=你的SecretId
TENCENT_SECRET_KEY=你的SecretKey
```

### Q: Docker 重启后数据还在吗

**在**。docker-compose.yml 配置了数据卷（`karvis_data`），即使容器删除重建，数据也会保留。

除非你手动执行 `docker volume rm`，否则数据不会丢失。

### Q: 更新代码怎么操作

把新版本的代码文件夹替换到服务器上（保留 `data/` 目录和 `src/.env`），然后重启：

```bash
# Docker 方式
cd KarvisForAll/deploy
docker-compose down
docker-compose up -d --build

# 手动方式
# 停掉旧进程，重新 python3 app.py
```

---

## 成本估算

以 2-3 个人日常使用为例：

| 项目 | 月费用 |
|---|---|
| DeepSeek API | ¥15-50（取决于活跃度） |
| 服务器（1C1G 轻量云） | ¥30-60 |
| 其他（Qwen/ASR，可选） | ¥0-20 |
| **合计** | **¥45-130/月** |

> 管理员可以通过 Web 管理后台实时查看 LLM 用量和费用估算。
> 通过 `DAILY_MESSAGE_LIMIT` 限制每人消息数，防止费用失控。

---

## 许可证

MIT License

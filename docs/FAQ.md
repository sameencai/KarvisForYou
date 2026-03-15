# 常见问题 FAQ

> 部署过程中遇到问题？先在这里找答案。按主题分类，点击问题直接跳转。

---

## 回调 URL 相关

### Q: 企微回调 URL 必须用备案域名吗？

**不需要。** 企微支持以下几种 URL 格式：

| 格式 | 示例 | 适用场景 |
|------|------|----------|
| cloudflared 临时域名 | `https://abc-def.trycloudflare.com/wework` | 本地体验 |
| 公网 IP + 端口 | `http://43.138.xx.xx:9000/wework` | 云服务器 |
| 备案域名 | `https://your-domain.com/wework` | 正式环境 |

注意：URL 格式必须是 `http://IP:端口/wework` 或 `https://域名/wework`，不能只填 IP。

### Q: 填了 IP+端口，提示"该域名被限制设置"怎么办？

常见原因：
1. **格式不对**：必须是 `http://43.138.xx.xx:9000/wework`，不能省略 `http://` 和端口号
2. **Karvis 没启动**：企微会在保存时验证 URL，必须先启动 Karvis 再填
3. **端口没开放**：云服务器需要在防火墙/安全组开放 9000 端口

### Q: "openapi 回调地址请求不通过"怎么办？

这说明企微尝试访问你的 URL 但失败了。按顺序排查：

1. **确认 Karvis 正在运行**：在服务器上执行 `curl http://localhost:9000/wework`，有响应就说明服务在跑
2. **确认端口开放**：云服务器去控制台检查防火墙规则，9000 端口是否对外开放
3. **URL 末尾加了 `/wework` 吗**：企微回调地址必须以 `/wework` 结尾
4. **Token/AESKey 是否一致**：`.env` 文件中的 `WEWORK_TOKEN` 和 `WEWORK_ENCODING_AES_KEY` 必须和企微后台填的完全一致

> 详细排查步骤见 [回调URL配置指南 → 保存时报错怎么办](回调URL配置指南.md#保存时报错怎么办)

### Q: cloudflared 每次重启都换 URL 怎么办？

这是 cloudflared 免费临时隧道的限制。解决方案：

1. **临时方案**：每次重启后去企微后台更新 URL（适合体验阶段）
2. **长期方案**：买一台云服务器（推荐），用固定公网 IP，一劳永逸
3. **免费方案**：注册 Cloudflare 账号，配置 Named Tunnel，可以固定域名（需要一定技术基础）

---

## 部署相关

### Q: 本地部署和云服务器部署有什么区别？

| | 本地部署 | 云服务器 |
|---|---|---|
| 适合 | 体验试用 | 长期使用 |
| 费用 | 免费 | ~¥30-60/月 |
| 公网访问 | 需要 cloudflared（每次重启换地址） | 固定公网 IP |
| 24 小时运行 | 电脑必须开着 | 自动运行 |
| 早报/定时推送 | 电脑关了就不推了 | 正常推送 |

**建议**：先本地跑通体验，觉得好用再买服务器。

### Q: Windows 可以部署吗？

可以，但需要注意：

1. **Python 安装**：从 [python.org](https://www.python.org/downloads/) 下载安装，勾选 "Add to PATH"
2. **换行符问题**：如果遇到 `\r` 相关报错，说明文件换行符不对，执行：
   ```bash
   git config --global core.autocrlf false
   git clone https://github.com/sameencai/KarvisForYou.git
   ```
3. **cloudflared 安装**：Windows 版需要手动下载 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)，或者用 `winget install cloudflare.cloudflared`
4. **推荐使用 WSL**：如果遇到兼容性问题，安装 WSL（Windows Subsystem for Linux）后按 Linux 方式部署

### Q: 公司内网 / IOA 环境能用吗？

大部分情况可以，但可能遇到：

1. **cloudflared 被拦截**：公司防火墙可能阻止 cloudflare 隧道连接。解决：换个网络试试，或直接用云服务器
2. **API 请求被拦截**：DeepSeek/腾讯云 API 可能被代理拦截。解决：使用腾讯云 lkeap（内网友好），或检查代理设置

### Q: 推荐什么服务器配置？

腾讯云轻量应用服务器，最低配置即可：

- **规格**：2C2G（2核 CPU + 2GB 内存）
- **系统**：Ubuntu 22.04 / Debian 12
- **费用**：约 ¥30-60/月
- **省钱**：每月1号可领腾讯云代金券

> 详见 [腾讯云服务器选购指南](腾讯云服务器选购指南.md)

---

## 企微配置相关

### Q: DEFAULT_USER_ID 从哪获取？

1. 登录 [企微管理后台](https://work.weixin.qq.com/wework_admin/frame)
2. 左侧菜单 → **通讯录**
3. 点击你的名字，页面上的 **账号** 就是你的 User ID

> 如果你是注册时的第一个成员，User ID 通常和你注册用的手机号或邮箱前缀一致。

### Q: .env 文件在哪？怎么配置？

`.env` 文件在项目的 `src/` 目录下：

1. 如果用了 `setup.sh` 一键脚本，它会自动引导你创建
2. 如果手动部署，需要从模板复制：
   ```bash
   cp .env.example src/.env
   ```
3. 然后编辑 `src/.env`，填入真实值

> 每个字段的详细说明见 [.env配置说明](.env配置说明.md)

### Q: 注册企微后微信找不到组织怎么办？

需要用微信扫码关注：

1. 企微管理后台 → **我的企业** → **微信插件**
2. 找到"邀请关注"区域的 **二维码**
3. 用**微信**扫码，确认关注

扫码后回到微信聊天列表，就能看到你的企业了。

### Q: 可信 IP 填什么？

| 部署方式 | 填什么 |
|----------|--------|
| 云服务器 | 服务器公网 IP（在服务器执行 `curl -4 ifconfig.me` 获取） |
| 本地 + cloudflared | **不需要填**（跳过这一步） |

---

## 模型相关

### Q: DeepSeek 怎么配置？模型名填什么？

取决于你用的平台：

| 平台 | BASE_URL | MODEL |
|------|----------|-------|
| DeepSeek 官方（推荐） | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 腾讯云 lkeap | `https://api.lkeap.cloud.tencent.com/v1` | `deepseek-v3.2` |

**常见错误**：用腾讯云 lkeap 但模型名填了 `deepseek-chat`（这是官方的模型名），会报错。反之亦然。旧模型名 `deepseek-v3` 已下线，请使用 `deepseek-v3.2`。

### Q: 腾讯云 API Key 怎么获取？

1. 打开 [腾讯云知识引擎 lkeap](https://console.cloud.tencent.com/lkeap)
2. 登录腾讯云账号（没有的话注册一个）
3. 开通服务后，在 **API Key 管理** 页面创建密钥
4. 复制 Key 填入 `.env` 的 `DEEPSEEK_API_KEY`

> 参考腾讯云官方文档：https://cloud.tencent.com/document/product/1772/115969

### Q: 能用 Gemini / 其他模型吗？

只要兼容 OpenAI API 格式，都可以接入。修改 `.env` 中的三个字段：

```bash
DEEPSEEK_API_KEY=你的API密钥
DEEPSEEK_BASE_URL=兼容OpenAI的API地址
DEEPSEEK_MODEL=模型名称
```

> 详见 [模型配置指南](模型配置指南.md)

---

## 使用相关

### Q: 怎么在微信里置顶？

1. 在微信中找到你的企业（聊天列表 → 企业名称）
2. 点进去找到 Karvis 应用
3. **长按** Karvis 的聊天 → 选择 **「置顶聊天」**

置顶后，Karvis 会一直显示在企业聊天列表的最上方。

> 安卓用户额外技巧：长按应用 → "添加到桌面"，可以一键直达。

### Q: 只能在二级入口用吗？

目前是的。企微自建应用在微信侧属于二级入口（需要先点进企业再找到应用）。置顶可以减少操作步骤。

### Q: 管理端 / 日志怎么看？

管理端地址：`http://你的IP:9000/web/logs`

1. 浏览器打开上述地址
2. 输入你在 `.env` 中设置的 `ADMIN_TOKEN`
3. 即可查看日志、用户管理、LLM 用量等

> 详见 [管理端使用指南](管理端使用指南.md)

### Q: 机器人一直重复回复同一句话怎么办？

可能卡在 Onboarding（新用户引导）流程。解决：

1. 查看管理端日志，确认报错信息
2. 让 CodeBuddy / AI 助手查看服务器日志排查
3. 如果是 API 问题，检查 DeepSeek API Key 是否有效、余额是否充足

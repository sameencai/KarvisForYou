# 企微回调 URL 配置指南

> 这是大家部署时遇到问题最多的一步。本文按照不同场景逐一说明，找到自己的情况照做就行。

---

## 先搞清楚：回调 URL 是什么

企微需要知道"用户发消息了，应该发给哪个地址"。这个地址就是回调 URL。

格式固定是：`你的地址/wework`

企微在保存配置时会向这个地址发一个验证请求，**必须能访问才能保存成功**。所以 Karvis 必须先启动，然后再去填这个 URL。

---

## 两种场景，选一个

| 我的情况 | 对应方案 |
|----------|----------|
| 用自己的电脑/Mac 跑 Karvis | [方案 A：本地电脑（用 cloudflared）](#方案-a本地电脑用-cloudflared) |
| 在云服务器（腾讯云/阿里云等）上跑 | [方案 B：云服务器（直接用公网 IP）](#方案-b云服务器直接用公网-ip) |

---

## 方案 A：本地电脑（用 cloudflared）

本地电脑没有公网 IP，企微无法直接访问。需要用 cloudflared 把本地服务"穿透"到公网。

### 第一步：启动 Karvis

```bash
cd KarvisForAll
./setup.sh
```

或者手动启动：

```bash
cd KarvisForAll/src
python3 app.py
```

看到类似这行说明启动成功：
```
* Running on http://0.0.0.0:9000
```

### 第二步：启动 cloudflared 获取公网 URL

**如果用了 `setup.sh`**，它会自动启动 cloudflared 并打印 URL，直接看输出就行：

```
你的公网地址: https://xxx-xxx-xxx.trycloudflare.com
企微后台 URL 填: https://xxx-xxx-xxx.trycloudflare.com/wework
```

**如果需要手动启动 cloudflared**：

```bash
cloudflared tunnel --url http://localhost:9000
```

等几秒，输出里会出现：

```
+--------------------------------------------------------------------------------------------+
|  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
|  https://abc-def-ghi.trycloudflare.com                                                     |
+--------------------------------------------------------------------------------------------+
```

这个 `https://abc-def-ghi.trycloudflare.com` 就是你的公网地址。

### 第三步：去企微填写 URL

1. 企微管理后台 → **应用管理** → 点击你的应用
2. 找到 **接收消息** → **设置 API 接收**
3. 填写：
   - **URL**：`https://abc-def-ghi.trycloudflare.com/wework`（换成你实际的地址，末尾加 `/wework`）
   - **Token** 和 **EncodingAESKey**：点击页面上的**随机获取**按钮各生成一个，然后把生成的值分别复制到 `.env` 的 `WEWORK_TOKEN` 和 `WEWORK_ENCODING_AES_KEY` 字段，保存 `.env` 后重启 Karvis
4. 点击**保存**

保存成功 = 配置完成。

> 如果你之前已经在企微生成过 Token/AESKey 并填入了 `.env`，直接把 `.env` 里的值原样填回来就行，保持两边一致即可。

### 本地方案注意事项

> **每次重启电脑或重启 cloudflared，URL 都会变。** 变了之后需要重新去企微后台更新 URL。
>
> 想要固定 URL，要么买 cloudflare 的账号配置 Named Tunnel（免费），要么直接用云服务器（方案 B）。

---

## 方案 B：云服务器（直接用公网 IP）

云服务器有固定的公网 IP，配置一次就永久有效，不需要 cloudflared。

### 第一步：找到你的服务器公网 IP

登录服务器，执行：

```bash
curl -4 ifconfig.me
```

输出的就是公网 IP，比如 `43.138.xx.xx`。

也可以在云服务商控制台（腾讯云/阿里云的实例列表）直接看公网 IP。

> ⚠️ 不要用 `ifconfig` 或 `ip addr` 查出来的 IP，那些是**内网 IP**（通常是 `10.x.x.x` 或 `172.x.x.x`），企微无法访问。

### 第二步：确认 9000 端口已开放

云服务器默认很多端口是关闭的，需要手动开放。

**腾讯云**：
- 控制台 → 轻量应用服务器 → 选择实例 → **防火墙** → **添加规则**
- 协议：TCP，端口：9000，来源：所有 IP（`0.0.0.0/0`）

**阿里云**：
- 控制台 → 云服务器 ECS → 选择实例 → **安全组** → **管理规则** → **添加规则**
- 方向：入方向，协议：TCP，端口范围：9000/9000，授权对象：`0.0.0.0/0`

验证端口是否开放（在服务器上执行）：

```bash
# 确认 Karvis 在监听 9000 端口
ss -tlnp | grep 9000
# 应该看到类似：LISTEN  0  128  0.0.0.0:9000  ...
```

### 第三步：启动 Karvis

```bash
cd KarvisForAll/src
python3 app.py
```

或后台运行：

```bash
nohup python3 app.py > karvis.log 2>&1 &
```

### 第四步：去企微填写 URL

1. 企微管理后台 → **应用管理** → 点击你的应用
2. 找到 **接收消息** → **设置 API 接收**
3. 填写：
   - **URL**：`http://43.138.xx.xx:9000/wework`（换成你的真实公网 IP）
   - **Token** 和 **EncodingAESKey**：点击页面上的**随机获取**按钮各生成一个，然后把生成的值分别复制到 `.env` 的 `WEWORK_TOKEN` 和 `WEWORK_ENCODING_AES_KEY` 字段，保存 `.env` 后重启 Karvis
4. 点击**保存**

> 如果你之前已经在企微生成过 Token/AESKey 并填入了 `.env`，直接把 `.env` 里的值原样填回来就行，保持两边一致即可。

### 第五步：配置企业可信 IP（必做）

这步很多人漏掉，导致能收消息但不回复。

1. 应用详情页 → 向下滚动找到 **企业可信 IP**
2. 点击**配置**，输入服务器公网 IP（同第一步查到的那个）
3. 保存

---

## 保存时报错怎么办

### 报错："URL验证失败" / "请求超时"

企微访问不到你的服务，排查顺序：

1. **Karvis 是否启动了？**
   ```bash
   # 本地
   curl http://localhost:9000/wework
   # 应该返回非 404 的内容（报错也算，只要有响应就说明服务在跑）
   ```

2. **cloudflared 是否还在运行？**（本地方案）
   ```bash
   # 看 cloudflared 进程
   ps aux | grep cloudflared
   ```
   没有的话重新启动：`cloudflared tunnel --url http://localhost:9000`

3. **URL 有没有加 `/wework`？**
   必须是 `你的地址/wework`，不是 `你的地址/` 或 `你的地址`。

4. **端口有没有开放？**（云服务器方案）
   按第二步检查安全组/防火墙规则。

5. **用浏览器直接访问 URL 看看**
   把你要填的 URL 粘到浏览器地址栏，回车，如果能看到任何内容（哪怕是报错页），说明服务可达。如果浏览器超时/拒绝连接，说明访问不到，继续排查网络问题。

### 报错："Token不匹配" / "签名校验失败"

Token 或 AESKey 填错了。检查：

1. 打开 `.env` 文件，找到 `WEWORK_TOKEN` 和 `WEWORK_ENCODING_AES_KEY`
2. 和企微后台填写的内容**一字不差**地对照
3. 注意不要有多余的空格、换行

### 能保存，但发消息没有回复

参考[连接验证排查](#发消息没有回复)。

---

## 发消息没有回复

按顺序检查：

**1. 看日志里有没有收到消息**

```bash
# Docker
docker logs karvis --tail 50

# 手动运行
tail -50 karvis.log
```

搜索 `[handle_message]`：
- **有**：消息收到了，继续往下查
- **没有**：消息根本没送到 Karvis，检查回调 URL 是否还有效（本地 cloudflared 地址可能变了）

**2. 搜索 `[Brain]`**

- **有**：AI 在处理，但没回复，继续往下
- **没有**：消息解析失败，可能是 Token/AESKey 配置问题

**3. 搜索 `reply_text` 或 `IP not in whitelist`**

出现 `IP not in whitelist`：企业可信 IP 没配或配错了，按方案 B 第五步重新配。

**4. 检查 DeepSeek API**

```bash
# 看日志有没有 API 报错
grep -i "error\|fail\|余额" karvis.log | tail -20
```

API Key 无效或余额不足也会导致没有回复。

---

## 快速自检清单

部署完对照检查一遍：

- [ ] Karvis 服务正在运行（能访问 `http://localhost:9000`）
- [ ] 公网 URL 可以从外部访问（浏览器能打开）
- [ ] 企微后台 URL 末尾有 `/wework`
- [ ] Token 和 AESKey 和 `.env` 一致
- [ ] 企业可信 IP 已配置（云服务器方案必填）
- [ ] 9000 端口防火墙已开放（云服务器方案）
- [ ] 发"你好"收到了欢迎消息

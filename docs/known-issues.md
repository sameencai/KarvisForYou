---
tags: [karvisforall, issues]
updated: 2026-02-19
---

# KarvisForAll — 已知问题与迭代方向

## 已修复

| ID | 问题 | 修复方式 |
|----|------|----------|
| I-001 | 健康检查 GET / 日志刷屏 | 添加 Werkzeug `_HealthCheckFilter` 过滤 `GET /` 和 `GET /health` 的 access log |
| I-002 | V8 容器重启后下午推送"早安"晨报 | `_daily_init()` 新增过期意图跳过：生成意图后检查 `now > latest` 则标记 `skipped` |
| I-003 | 节奏学习 `avg_wake_time` 被下午消息污染 | 入口过滤：wake_time 只接受 05:00~12:00；sleep_time 只接受 20:00~04:00；`_update_avg_time` 过滤 ≥12:00 脏样本 |

## 待观察

| ID | 优先级 | 问题 | 备注 |
|----|--------|------|------|
| I-004 | P3 | memory.md 缓存延迟：手动编辑后不会立即生效 | 缓存 TTL 到期后自动刷新。可手动 curl `/system?action=refresh_cache` 立即刷新 |
| I-005 | P3 | 同一用户极快连发消息可能导致 state 写入竞争 | 已有 per-user Lock 缓解，但极端并发场景仍有理论风险 |

## 已实现的功能

| ID | 描述 |
|----|------|
| F-001 | 多用户数据隔离：UserContext 全链路贯穿，每用户独立目录树 |
| F-002 | 新用户自动注册 + 三步 Onboarding 引导（昵称→笔记→待办） |
| F-003 | 对话式设置：昵称、AI 人格风格、个人信息 |
| F-004 | Web 查看页面（8 页）：登录/概览/速记/待办/日记/笔记/情绪 + 管理员后台 |
| F-005 | Token 令牌鉴权：UUID v4 + 24h 过期 + 三级 fallback 验证 |
| F-006 | 管理员仪表盘：用户列表/LLM 用量统计/成本估算/挂起激活 |
| F-007 | 成本控制：每日消息上限 + LLM 用量追踪 + 不活跃用户跳过 |
| F-008 | Skill 自动发现：37 个 skill handler 零配置注册 |
| F-009 | 三层多模型路由：Flash(Qwen) + Main(DeepSeek) + Think(DeepSeek thinking=on) |
| F-010 | 先回复后保存：send_fn 回调优化体感延迟 |
| F-011 | 定时任务多用户遍历：随机延迟防限流 + 单用户失败不影响其他 |
| F-012 | V8 智能调度引擎：意图队列 + 心跳驱动 + 规则引擎 + 意图合并 |
| F-013 | 用户节奏自学习：起床/入睡时间推算 + 滑动加权平均 |
| F-014 | V6 动态能力引擎：LLM 自由编排原子操作 + 白名单安全控制 |
| F-015 | Git Push 云服务器部署：`git push deploy master` 一键部署 |
| F-016 | 企微告警推送：慢请求连续检测 + 异常告警 + 冷却防风暴（DD-019） |
| F-017 | 技术监控中心重构：logs.html 三 Tab（日志/统计监控/错误聚合），admin.html 精简为用户运营中心（DD-020） |
| F-018 | 成本预算监控：月成本计算 + 预算进度条（默认 ¥50）+ 超 80% 红色告警 |
| F-019 | Prompt 膨胀检测：prompt_tokens 分布直方图（<4K/4-8K/8-12K/>12K）+ >12K 黄色告警 |
| F-020 | 错误日志聚合：从日志中提取 ERROR/Traceback，按错误签名去重计数，Sentry 风格展示 Top 20 |
| F-021 | 延迟瀑布图增强：可点击展开查看 input/thinking/skill/reply 详情 |

## 优化迭代方向

### P1 — 功能增强

| ID | 方向 | 说明 | 复杂度 |
|----|------|------|:------:|
| O-001 | **SKILLS 按需裁剪** | 当前每次请求发送全量 37+ 个 skill 描述，闲聊不需要全部。按场景裁剪可降 prompt_tokens ~30% | 中 |
| O-002 | **V8 Flash LLM 评估** | 规则引擎跑稳后，在不确定场景引入 Flash LLM 判断推送时机 | 中 |
| O-003 | **dynamic custom.* 聚合查询** | 让 LLM 能回答"这周喝了多少杯水"这类 custom 命名空间聚合问题 | 低 |
| O-004 | **V8 节奏数据可视化** | 将 user_rhythm（起床/入睡/活跃时段）可视化到 Web 页面 | 低 |
| O-005 | **批量导入历史数据** | 新用户导入历史聊天记录/笔记到 Quick-Notes | 中 |
| O-010 | **管理员用户详情页** | `/web/admin/user/<uid>` 单用户视图：消息量趋势、活跃时段、技能使用分布 | 中 |
| O-011 | **UptimeRobot 外部监控** | 接入 UptimeRobot 监控 `/health` 端点，服务宕机时邮件/短信告警 | 低 |

### P2 — 架构演进

| ID | 方向 | 说明 | 复杂度 |
|----|------|------|:------:|
| O-006 | **本地文件 → SQLite** | 高频读写的 state.json / users.json 迁移到 SQLite，减少文件 IO 和锁竞争 | 中 |
| O-007 | **Prompt 工程自动化** | 构建测试框架：输入样本消息 → 验证 LLM 输出是否符合预期 skill/params | 中 |
| O-008 | **用户规模扩展** | 当前设计面向 2-5 人。扩展到 10+ 人需优化：定时任务并发化、state 缓存 LRU 淘汰、用量日志分片 | 高 |
| O-009 | **GitHub 开源 + CI/CD** | 代码上传 GitHub，配置 Actions 自动测试 + 自动部署 | 中 |

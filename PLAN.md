# 多用户化改造计划（单机私有 → 公众推广）

## 目标
任何使用者可在前端注册登录，填写**自己的**联并千行平台凭据（账号/密码/验证码模型/请求地址/APIKey/更新间隔），系统为其独立定时抓取余额与月度明细，数据完全隔离。原单用户配置迁移为内置管理员账号。

## 已确认决策
1. **账号体系**：邮箱 + 密码注册登录，数据按用户隔离
2. **定时调度**：统一调度器扫描（每分钟检查所有启用配置是否到期，各自按间隔抓取）
3. **旧数据处理**：.env 账户迁移为内置管理员，历史快照/明细保留
4. **规模**：<100 人，保持 SQLite + 单服务器轻量

## 一、数据模型（SQLite，storage.py 扩展）
新表：
- **users**：id, email UNIQUE, password_hash, is_admin, created_at
- **user_configs**：id, user_id UNIQUE, lbqh_user, lbqh_password_enc, lbqh_base_url, maas_api_url, maas_api_key_enc, captcha_model, fetch_interval(=3600), enabled, updated_at, last_fetch_at, last_fetch_ok, last_fetch_error
- **api_tokens**：id, user_id, token UNIQUE(每用户可生成多个), name, created_at —— 供「其他软件」调 HTTP API
- **sessions**：token, user_id, created_at, expires_at —— 登录会话（HttpOnly cookie）

数据表隔离：**balance_snapshots / usage_details / monthly_stats 增加 user_id 列**；所有查询/写入 API 强制带 user_id。迁移：旧行 user_id 指向管理员。

## 二、安全
- 系统登录密码：`hashlib.scrypt` + 随机盐（标准库，不引依赖）
- 平台密码 / MAAS APIKey：**cryptography Fernet 加密存储**（新增依赖），master key 取 `MASTER_KEY` 环境变量，缺失则首次启动自动生成到 `master.key` 文件并提示备份；前端**永不回显密码**（只显示“已设置”）
- 会话：自实现 token（sessions 表）+ HttpOnly cookie（7 天），SameSite=Lax；登录失败 5 次锁 10 分钟
- 页面 JS 不再硬编码 API key；改为浏览器 cookie 会话

## 三、统一调度（main.py + fetcher/monthly 参数化）
- `fetcher.py`：`fetch_balance(cfg_like)` —— 从“读全局 cfg”改为“按用户配置对象”登录抓取（含 recognize_captcha 的 MAAS 参数）
- `monthly.py`：`fetch_month(year, month, cfg_like)` 同理参数化
- 统一扫描：APScheduler 挂一个 60s interval job → 遍历 `user_configs WHERE enabled=1`，对 `now - last_fetch_at >= fetch_interval` 且未在抓取中的配置 `create_task` 抓余额；用 per-user 锁防止同用户重复
- 月度：每日 02:10 批量对所有启用用户补抓当月；页面仍保留手动「抓取此月」

## 四、API（全部鉴权，数据按当前用户返回）
- 公开：`POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /api/health`（健康检查去掉敏感字段，公开）
- 需登录（cookie 会话）：配置 CRUD（`GET/PUT /api/config`）、`POST /api/config/test`（先测试登录连通再保存）、`POST /api/balance/fetch`（立即抓自己）、`GET /api/monthly/fetch/status` 等既有接口全部+user 上下文
- 程序化调用：`X-API-Key: <用户自己的token>` 等价于登录态（余额/月度接口均支持）
- 移除旧的全局 X-API-Key 校验；管理命令可为旧系统保留一个 admin token

## 五、前端（static/ 重构为 SPA）
- `index.html`：登录/注册视图 + 看板 + **配置管理视图**（表单：平台账号/密码/验证码模型/请求地址/APIKey/更新间隔下拉/启用开关；保存+测试连接；API Key 管理；数据展示区）
- 所有请求走同源 cookie 会话；无登录则跳登录视图
- 看板数据按当前用户的配置渲染；未配置→提示去配置

## 六、管理命令与管理流程
- `manage.py`（CLI）：`init-admin <email>`（首个管理员，未注册则建号）、`migrate-legacy <admin_email>`（把 .env 旧平台凭据迁移为该管理员 user_config，历史数据 user_id 归他）、`set-master-key`
- 部署提醒：反向代理 + HTTPS、备份 master.key / balance.db

## 七、实施顺序
1. storage.py：新表 + user_id 隔离 + 迁移函数
2. 安全与依赖：安装 cryptography / 加密工具 / scrypt / session
3. fetcher.py、monthly.py 参数化
4. auth.py + main.py auth 路由与统一扫描
5. 前端 SPA（登录/注册/看板/配置）
6. 管理命令 migrate-legacy 执行（迁移旧数据为管理员）
7. 端到端验证：注册新用户→填配置→测试→定时抓取→看板/API Key 调用
8. 文档：API_CLAUDE.md 重写（多用户鉴权说明）、README

## 风险与取舍
- 平台账号密码存服务器（加密），用户需信任本系统；文档明示
- 单用户-单配置（每人监控一个平台账号），多账号后续可扩
- SQLite 单文件，<100 用户 OK；趋势表加 user_id 索引
- 页面图表仍走 ECharts CDN（已在用）；离线需本地化（后续可选）
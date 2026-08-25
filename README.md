# 联并千行MaaS 多用户余额监控服务

一个自托管的**多用户**「联并千行MaaS」平台余额与消费监控系统。每个用户自助注册登录后，填写自己的平台凭据（账号/密码/验证码识别模型/模型 API 地址与 Key/更新间隔），系统按用户独立定时抓取余额与月度消费明细，数据完全隔离。

> 本仓库为**源代码**。运行所需的账号密码、API Key 等敏感信息存放在 `.env` 与 `master.key`（均已被 `.gitignore` 排除，不会入库）。

## 功能

- **多用户**：邮箱注册登录，第一个注册用户自动成为管理员；数据按用户隔离
- **自助配置**：前端配置页填写平台账号、密码、验证码识别模型、模型 API 地址与 Key、抓取间隔（10 分钟~1 天）、启用开关；「测试连接」真实登录验证
- **统一调度**：服务每 60 秒扫描启用账号，按各用户间隔异步抓取余额；每月 02:10 自动补抓当月消费明细
- **Web 看板**：余额卡片、余额趋势曲线、月度模型费用占比 / Tokens 堆叠图、月度统计与明细表（ECharts，跟随系统深浅色）
- **程序化 API**：每用户可创建 API 令牌，`X-API-Key` 鉴权访问自己名下的数据
- **安全**：平台凭据 Fernet 加密落库；登录密码 scrypt 哈希；登录失败 5 次锁定 10 分钟；密码永不回显

## 快速开始

```bash
# 1. 安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium

# 2. 配置环境变量（复制示例并填写）
#    需配置：MASTER_KEY（可选，缺省自动生成到 master.key）、DB_PATH 等
#    .env 示例：
#    MAAS_API_URL=https://maasapi.example.com/v1/chat/completions
#    CAPTCHA_MODEL=Qwen3.5-35B-A3B
#    FETCH_INTERVAL=3600

# 3. 启动服务（注意：8000 可能被其他服务占用，本服务用 8100）
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8100

# 4. 浏览器打开 http://<服务器IP>:8100/ → 注册 → 配置 → 查看看板
```

> 首个用户注册后自动成为管理员。旧单用户部署可用管理命令迁移：
> ```bash
> .venv/bin/python -m manage migrate-legacy <管理员邮箱>
> ```

## 目录结构

```
main.py        FastAPI 入口 + 统一调度（APScheduler）
storage.py     SQLite 多用户存储（业务表按 user_id 隔离）
auth.py        注册/登录/会话/API 令牌/失败锁定
crypto.py      Fernet 对称加密（平台凭据）
fetcher.py     Playwright 登录 + 验证码识别 + 余额抓取
monthly.py     按月抓取费用页消费明细
manage.py      CLI：init-admin / migrate-legacy / encrypt-password
static/        Web 前端 SPA（登录/看板/配置/令牌）
```

## API 概览

| 接口 | 说明 |
|---|---|
| `POST /auth/register` / `POST /auth/login` / `POST /auth/logout` | 账号 |
| `GET /api/health` | 健康检查（无需鉴权） |
| `GET/PUT /api/config` | 查看/保存自己的平台配置 |
| `POST /api/config/test` | 测试连接（真实登录一次） |
| `GET/POST /api/tokens` | 管理 API 令牌 |
| `GET /api/balance` | 最新余额 |
| `GET /api/balance/history?days=N` | 余额历史 |
| `POST /api/balance/fetch` | 立即刷新余额 |
| `GET/POST /api/monthly/*` | 月度明细与统计 |

调用方式：网页用会话 Cookie；程序用请求头 `X-API-Key: <用户自己的令牌>`。

详细接口文档见 `API_CLAUDE.md`。

## 安全说明

- 平台密码与模型 API Key 用 Fernet 加密后入库；**主密钥在 `master.key`（或环境变量 `MASTER_KEY`），务必妥善备份**，丢失后无法解密已存凭据
- 密钥文件权限设为 `600`
- 前端永不回显密码，仅显示"已设置"
- 本项目当前适合**小规模（<100 用户）**内部使用；如需面向公网，请置于反向代理 + HTTPS 之后

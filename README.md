# 联并千行MaaS 多用户余额监控服务

一个自托管的**多用户**「联并千行MaaS」平台余额与消费监控系统。每个用户自助注册登录后，填写自己的平台凭据（账号/密码/验证码识别模型/模型 API 地址与 Key/更新间隔），系统按用户独立定时抓取余额与月度消费明细，数据完全隔离。

> 本仓库为**源代码**。运行所需的账号密码、API Key 等敏感信息存放在 `.env` 与 `master.key`（均已被 `.gitignore` 排除，不会入库）。

## 功能

- **多用户**：邮箱注册登录，第一个注册用户自动成为管理员；数据按用户隔离
- **自助配置**：前端配置页填写平台账号、密码、验证码识别模型、模型 API 地址与 Key、抓取间隔（10 分钟~1 天）、启用开关；「测试连接」真实登录验证
- **统一调度**：服务每 60 秒扫描启用账号，按各用户间隔异步抓取余额；每月 02:10 自动补抓当月消费明细
- **Web 看板**：余额卡片、余额趋势曲线、月度模型费用占比 / Tokens 堆叠图（含每个模型缓存命中率）、月度统计与明细表（明细支持分页）；ECharts 已本地化，无需外网 CDN
- **程序化 API**：每用户可创建 API 令牌，`Authorization: Bearer <令牌>` 鉴权访问自己名下的数据（对齐 DeepSeek 风格）
- **安全**：平台凭据 Fernet 加密落库；登录密码 scrypt 哈希；登录失败 5 次锁定 10 分钟；密码永不回显

---

## 环境要求

### 方案一：Docker 部署（推荐）
| 项 | 要求 |
|---|---|
| Docker Engine | ≥ 20.10 |
| Docker Compose | v2（`docker compose` 子命令） |
| 网络 | 可访问 `lbqh.paratera.com`（平台）与 `maasapi.paratera.com`（验证码识别模型）；构建镜像需可 pip 联网 |
| 磁盘 | 镜像约 **4.2GB**（含 Playwright Chromium），建议预留 ≥ 5GB |
| 内存 | 建议 ≥ 2GB（Chromium 抓取脚本占较多内存） |

### 方案二：裸机 Python 部署
| 项 | 要求 |
|---|---|
| Python | 3.10+ |
| 网络 | 同方案一（验证码模型走网络） |
| 依赖 | `pip install -r requirements.txt` + `playwright install chromium` |

**说明**：服务默认监听 **8100**（8000 常被其他服务占用）。前端只要 Chrome / Edge / Firefox 等现代浏览器即可，无服务器端外部依赖。

---

## 获取源码（下载）

```bash
git clone https://github.com/ryanyang7899/lbqh-monitor.git
cd lbqh-monitor
```

> 仓库当前为 **private**，如需对外分发请将仓库设为 Public。
> 若需离线分发完整 Docker 镜像（含 Chromium，约 1.2GB），维护者可用 `docker save` 导出，接收方 `docker load -i <tar>` 还原。

---

## 快速开始

### Docker 部署（推荐）

```bash
# 1. 生成配置文件（示例 → 实际，按需填写敏感项）
cp .env.example .env
#   编辑 .env：MAAS_API_KEY 必须填，其余可保持默认

# 2. 构建并启动（首次构建需联网下载依赖，国内较慢）
docker compose up -d --build

# 3. 浏览器访问
#   http://<服务器IP>:8100/  → 注册第一个账号（自动成为管理员）→ 配置 → 查看看板

# 4.（可选）查看日志
docker logs -f lbqh-monitor
```

**国内网络构建提示**：本仓库 `Dockerfile` 默认使用清华 pip 镜像 + npmmirror 的 Playwright Chromium 下载源（因官方源在内网超时）。覆盖方式：

```bash
docker build --build-arg PIP_INDEX_URL=你的PIP源 \
             --build-arg PLAYWRIGHT_DOWNLOAD_HOST=你的Playwright源 \
             -t lbqh-monitor:latest .
```

**端口调整**：如 8100 被占用，编辑 `docker-compose.yml` 的端口映射 `"8100:8100"` 为 `"其他端口:8100"`。

### 本地运行（不用 Docker）

```bash
# 1. 安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium

# 2. 配置环境变量
cp .env.example .env   # 填写 MAAS_API_KEY 等，MASTER_KEY 可留空自动生成

# 3. 启动服务
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8100
```

> 首个用户注册后自动成为管理员。旧单用户部署的数据可用管理命令迁移：
> ```bash
> .venv/bin/python -m manage migrate-legacy <管理员邮箱>
> ```

---

## 配置参数（.env）

| 变量 | 说明 | 默认 |
|---|---|---|
| `MASTER_KEY` | Fernet 主密钥（base64）；留空则自动生成到 `master.key` | 自动生成 |
| `MAAS_API_URL` | 验证码识别模型 API（OpenAI 兼容） | `https://maasapi.paratera.com/v1/chat/completions` |
| `MAAS_API_KEY` | 验证码模型 API Key（**必填**） | 无 |
| `CAPTCHA_MODEL` | 验证码识别模型名 | `Qwen3.5-35B-A3B` |
| `FETCH_INTERVAL` | 抓取间隔（秒），仅作缺省 | `3600` |
| `DB_PATH` | SQLite 数据库路径 | `balance.db`（Docker 下自动改为 `/app/data/balance.db`） |

> 多用户模式下，**平台账号/密码改由每个用户在网页配置页自行填写**（加密入库），`.env` 里的 `LBQH_*` / `API_TOKEN` 仅为旧单用户迁移保留，运行时不再用于抓取。

---

## 使用说明

1. **注册登录**：打开首页注册第一个邮箱（自动成为管理员），后续用户逐次注册、数据隔离。
2. **配置连接**：进入「配置管理」页，填写平台账号、密码、验证码识别模型、模型 API 地址与 Key、更新间隔，保存并点「测试连接」验证。拖动启用开关开始周期抓取。
3. **查看看板**：「监控看板」展示当前余额、余额趋势、月度模型费用占比与 Tokens 堆叠图（悬停可看各模型缓存命中率）、月度统计表与最近消费明细（支持分页，每页 10/20/50/100 条）。
4. **月度明细**：每月 02:10 自动补抓当月详情；也可在页面手动「抓取此月」。
5. **API 令牌**：「API 令牌」页创建令牌 → 程序以 `Authorization: Bearer <令牌>` 调用 `GET /user/balance` 等接口。

---

## 目录结构

```
main.py               FastAPI 入口 + 统一调度（APScheduler）
storage.py            SQLite 多用户存储（业务表按 user_id 隔离）
auth.py               注册/登录/会话/API 令牌/失败锁定
crypto.py             Fernet 对称加密（平台凭据）
fetcher.py            Playwright 登录 + 验证码识别 + 余额抓取
monthly.py            按月抓取费用页消费明细
manage.py             CLI：init-admin / migrate-legacy / encrypt-password
static/               Web 前端 SPA（登录/看板/配置/令牌，ECharts 已本地化）
Dockerfile            Docker 镜像定义（含 Playwright Chromium）
docker-compose.yml    Compose 编排（8100 端口 + ./data 持久化）
data/                 （运行时）balance.db 与 master.key，随宿主机持久化
```

---

## API 概览

| 接口 | 说明 |
|---|---|
| `POST /auth/register` / `POST /auth/login` / `POST /auth/logout` | 账号 |
| `GET /api/health` | 健康检查（无需鉴权） |
| `GET/PUT /api/config` | 查看/保存自己的平台配置 |
| `POST /api/config/test` | 测试连接（真实登录一次） |
| `GET/POST /api/tokens` | 管理 API 令牌 |
| `GET /user/balance` | 最新余额（DeepSeek 风格） |
| `GET /api/balance/history?days=N` | 余额历史 |
| `POST /api/balance/fetch` | 立即刷新余额 |
| `GET/POST /api/monthly/*` | 月度明细与统计 |

调用方式：网页用会话 Cookie；程序用请求头 `Authorization: Bearer <用户自己的令牌>`。

详细接口文档见 `API_CLAUDE.md`。

---

## 相关项目

- [**APIMug**](https://github.com/ryanyang7899/APIMug) — macOS 菜单栏 API 监测客户端（纯 Swift + AppKit，无 Dock 图标）。其内置的 `deepseek` 协议正是请求 `GET {base}/user/balance` 并携带 `Authorization: Bearer <token>`，与本服务的对外余额接口**完全兼容**。在 APIMug 中新增一个 `deepseek` 站点，Base URL 填本服务地址、Token 填你在本平台「API 令牌」页创建的令牌，即可在 macOS 菜单栏实时查看你的联并千行MaaS 余额。

---

## 数据、备份与安全

- **持久化数据**：`data/balance.db`（数据库）与 `data/master.key`（主密钥）。Docker 下两者在 `./data` 目录。**备份 = 备份整个 `./data`**。
- **主密钥别丢**：`master.key` 丢失将无法解密已存凭据（用户需重新填写）。建议离线额外备份一份。
- **敏感信息**：平台密码与模型 API Key 以 Fernet 加密入库；`.env`、`master.key`、`*.db` 均已被 `.gitignore` 排除，不会入库。
- **适用范围**：本项目适合**小规模（<100 用户）**内部使用；如需面向公网，请置于反向代理 + HTTPS 之后。

## 常见问题

- **端口被占用**：8100 已占用时，改 `docker-compose.yml` / uvicorn 命令的端口。
- **首次抓取很慢**：余额抓取需真实打开平台页面 + 验证码识别（调用模型，有少量成本），耗时 15~20 秒属正常。
- **验证码识别模型有成本**：频繁「测试连接」「手动抓取」会消耗模型 API 额度，月度自动补抓每天一次，请留意。
- **ECharts 加载失败**：本仓库已本地化、无需外网 CDN；确认 `.env` 无差异且 `static/echarts.min.js` 存在。
```

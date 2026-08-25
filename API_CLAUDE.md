# 联并千行MaaS 使用监控 · 服务文档（多用户版）

> **给 Claude 会话阅读**：如果你是在开发一个新的软件/平台，需要获取「联并千行MaaS」平台账户的余额数据，请先完整阅读本文件。本服务支持**多用户注册**，每个用户管理自己的平台账号并独立抓取、独立查询，数据按用户隔离。所有字段、示例、陷阱均来自实际运行的服务，可直接使用。

---

## 1. 一句话概述

本服务是一个自托管的「联并千行MaaS（`https://lbqh.paratera.com`）」**多用户余额/消费监控系统**：

- 用户在前端页面上**自助注册、登录**，并填写自己的平台登录账号、密码、验证码识别模型、模型 API 地址与 Key、更新间隔等配置；
- 服务**统一调度**（每 60 秒扫描一次启用的账号），按各用户设定的间隔自动登录、识别验证码、抓余额入库；
- 每月自动抓取一次消费明细（每行 = 一次计费记录，含模型、token、费用），按模型聚合 token 消耗；
- 对外提供按用户隔离的 HTTP 接口：**Web 前端**用于人工查看与配置，**每用户 API 令牌**用于你的程序化调用。

你的软件只需调用下方接口即可，**不需要**自己实现登录、验证码识别、翻页等逻辑。

**Web 管理页**：浏览器打开 `http://<服务IP>:8100/` → 注册/登录 → 「配置管理」填写平台账号 → 自动开始抓取 → 「监控看板」查看余额卡片、余额趋势曲线、模型费用占比/Tokens 堆叠图、月度统计与明细表。页面自动每 60 秒刷新、跟随系统深浅色；ECharts 走 CDN，离线内网环境下图表无法加载。

---

## 2. 核心事实（务必记住）

| 项目 | 值 |
|---|---|
| 服务地址 | `http://localhost:8100`（当前仅绑定本机 `127.0.0.1`） |
| 注册鉴权 | 邮箱 + 密码（scrypt 哈希）。**第一个注册的用户自动成为管理员** |
| Web 会话 | Cookie（HttpOnly, SameSite=Lax），登录成功即随请求自动携带 |
| 程序化调用 | 每用户可创建 API 令牌，请求头 `Authorization: Bearer <令牌>`（对齐 DeepSeek 风格） |
| 数据隔离 | 所有业务数据（余额快照、月度明细、统计）均带 `user_id`，**只返回当前用户自己的数据** |
| 凭据加密 | 平台密码 / 模型 API Key 用 Fernet 加密存储；前端绝不回显密码（只显示「已设置」） |
| 数据刷新 | 余额按用户自定义间隔（默认 1 小时）自动抓；`POST /api/balance/fetch` 立即抓一次；月度明细每日 02:10 自动抓当月 |
| 时间格式 | 快照字段 ISO 8601 **UTC**（`...Z`）；明细的 `start_time`/`end_time` 为平台本地时间（东八区） |
| 金额单位 | 元（人民币），浮点数 |
| 服务入口 | `/home/hero/lbqh-monitor/main.py`（FastAPI + APScheduler 统一调度） |

---

## 3. 快速开始

### 3.1 网页端（人工用）

1. 打开 `http://<服务IP>:8100/`；
2. 点「去注册」，填邮箱密码完成注册（第一个注册的用户是管理员）；
3. 进「配置管理」→ 填写联并千行账号、密码、验证码模型、模型 API 地址与 Key、抓取间隔 → **先「测试连接」**（真实登录一次、消耗一次验证码识别）→ 成功后「保存配置」；
4. 回到「监控看板」，卡片/图表即开始显示；可点「立即更新余额」随时手动刷新。

### 3.2 API 令牌（程序化调用）

1. 网页端「API 令牌」页创建令牌（命名可辨识，如 `my-app`）；
2. 令牌格式 `lbqh-<32位hex>`，**只在创建响应里明文显示一次**，立即复制保存；
3. 调用接口时带请求头 `Authorization: Bearer <令牌>`，即可访问**该账号名下**的数据。

```bash
# 获取当前用户最新余额（DeepSeek 风格）
curl -H "Authorization: Bearer lbqh-a1b2c3..." http://localhost:8100/user/balance

# 获取最近 7 天历史（画曲线用）
curl -H "Authorization: Bearer lbqh-a1b2c3..." "http://localhost:8100/api/balance/history?days=7"

# 立即更新一次余额（同步等待 ~10-20 秒，返回最新快照）
curl -X POST -H "Authorization: Bearer lbqh-a1b2c3..." http://localhost:8100/api/balance/fetch
```

### 3.3 Python（requests）

```python
import requests

BASE = "http://localhost:8100"
TOKEN = "lbqh-a1b2c3..."          # 在网页端「API 令牌」页创建
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

def get_balance() -> dict:
    """获取当前用户最新余额（DeepSeek 风格）。失败抛异常，调用方自行处理。"""
    r = requests.get(f"{BASE}/user/balance", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()
    # {"is_available": true, "balance_infos": [{"currency":"CNY","total_balance":"984.50","granted_balance":"0","topped_up_balance":"0"}]}

def get_history(days: int = 7) -> list[dict]:
    """获取最近 N 天余额历史（按时间从早到晚排序）。"""
    r = requests.get(f"{BASE}/api/balance/history", headers=HEADERS, params={"days": days}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]
```

---

## 4. 接口详情

### 4.0 鉴权总览

- **Web 会话**：注册/登录成功后，服务下发 `session` Cookie（HttpOnly）。浏览器里直接可用；**不要用 Web 会话 Cookie 做程序化访问**，程序用 API 令牌。
- **API 令牌（程序化调用）**：请求头 `Authorization: Bearer <令牌>`（对齐 DeepSeek 风格）。令牌与用户绑定，数据访问自动限定该用户。**旧版 `X-API-Key` 头已废弃，一律返回 401**。
- **未登录/令牌无效** → `401`（页面会跳回登录视图；程序需自行处理）。
- 所有数据接口（4.2 之后）都要求鉴权并且只返回当前账号名下数据。

### 4.1 账号（auth）

| 接口 | 说明 |
|---|---|
| `POST /auth/register` `{email, password}` | 注册。**第一个注册用户自动成为管理员**。成功即建立会话（返回 cookie） |
| `POST /auth/login` `{email, password}` | 登录。返回 cookie |
| `POST /auth/logout` | 注销当前会话 |
| `GET /auth/me` | 当前登录用户信息：`{"user": {"id","email","is_admin"}, "configured": bool}` |

密码规则：至少 6 位。登录失败 5 次锁定 10 分钟（内存状态）。

```bash
curl -X POST -H "Content-Type: application/json" http://localhost:8100/auth/login \
  -d '{"email":"you@example.com","password":"..."}'
```

### 4.2 配置管理

| 接口 | 说明 |
|---|---|
| `GET /api/config` | 返回当前用户的配置 **摘要**（不含任何密码/Key 明文；`password_set`/`api_key_set` 表示是否已设置） |
| `PUT /api/config` | **保存/更新配置**。`lbqh_password`、`maas_api_key` 留空 = 不修改。`fetch_interval` 为秒数，最小 300 |
| `POST /api/config/test` | 用提交的配置做**一次真实登录 + 余额抓取测试**（不保存、消耗一次验证码识别模型）。返回 `{"ok":bool,"message":...}` |

`PUT /api/config` 请求体示例：

```json
{
  "lbqh_user": "1458788499@qq.com",
  "lbqh_password": "********",        // 不修改则省略/留空字符串
  "lbqh_base_url": "https://lbqh.paratera.com",
  "maas_api_url": "https://maasapi.paratera.com/v1/chat/completions",
  "maas_api_key": "sk-********",      // 不修改则省略
  "captcha_model": "Qwen3.5-35B-A3B",
  "fetch_interval": 3600,
  "enabled": true
}
```

`GET /api/config` 返回（摘要，绝不含明文密码/Key）：

```json
{
  "configured": true,
  "lbqh_user": "1458788499@qq.com",
  "lbqh_base_url": "https://lbqh.paratera.com",
  "maas_api_url": "https://maasapi.paratera.com/v1/chat/completions",
  "captcha_model": "Qwen3.5-35B-A3B",
  "fetch_interval": 3600,
  "enabled": true,
  "password_set": true,
  "api_key_set": true,
  "last_fetch_at": "2026-08-24T09:32:36Z",
  "last_fetch_ok": true,
  "last_fetch_error": null
}
```

### 4.3 API 令牌管理

| 接口 | 说明 |
|---|---|
| `GET /api/tokens` | 列出当前用户全部令牌 `{"tokens":[{id,token,name,created_at},...]}` |
| `POST /api/tokens` `{"name":"my-app"}` | 创建令牌，返回 `{"token":"lbqh-..."}`（**只此一次明文**） |
| `DELETE /api/tokens/{token}` | 删除/吊销指定令牌 |

### 4.4 `GET /user/balance` — 查询余额（DeepSeek 风格，推荐）

- **鉴权**：`Authorization: Bearer <令牌>`（必需）
- **返回**：结构**完全对齐 DeepSeek `/user/balance`**，只暴露余额信息，不外泄任何内部字段（id/抓取时间/支出等一概没有）
- **路径说明**：路径是 `/user/balance`（不是 `/api/balance`），方便需要「和 DeepSeek 一样的接入方式」的程序直接替换请求地址使用

```json
{
  "is_available": true,
  "balance_infos": [
    {
      "currency": "CNY",
      "total_balance": "984.50",
      "granted_balance": "0",
      "topped_up_balance": "0"
    }
  ]
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `is_available` | bool | 是否有有效余额数据（未配置/无数据时 `false` 且 `balance_infos` 为空数组） |
| `balance_infos[].currency` | string | 币种，固定 `CNY` |
| `balance_infos[].total_balance` | string | **账户当前余额（元）**，两位小数 |
| `balance_infos[].granted_balance` | string | 赠送余额（本平台不区分，恒为 `0`） |
| `balance_infos[].topped_up_balance` | string | 充值余额（本平台不区分，恒为 `0`） |

### 4.5 `GET /api/balance` — 最新快照（内部接口）

- **仅作 Web 看板内部数据源**，字段含内部结构（`id`/`fetched_at`/`total_spent`/`month_spent`/`user_id`）。**对外程序请用 `GET /user/balance`**。
- 未配置或尚无数据时返回 404（detail 为提示文案）。

### 4.6 `POST /api/balance/fetch` — 立即更新余额

- **鉴权**：必需
- **行为**：**同步**执行一次「登录 + 验证码识别 + 抓取 + 入库」，完成后直接返回最新快照
- **耗时**：约 10~20 秒，HTTP 请求同步等待
- **用途**：需要比定时更及时的数据时调用
- **注意**：消耗验证码模型成本，**勿高频轮询**；若该用户恰在抓取会返回 409；配置不完整返回 400；抓取失败返回 502

### 4.7 `GET /api/balance/history?days=N` — 余额历史

- `days`：整数 1~365，默认 7；返回 `data` 数组按时间升序，同 4.5 快照结构。

### 4.8 月度消费明细

费用页每一行是一条**独立计费记录**：同一模型同一时间片可能有多条（多次调用分别计费）。**同一月份可重复抓取，覆盖式更新**。

| 接口 | 说明 |
|---|---|
| `POST /api/monthly/fetch?month=YYYY-MM` | 触发抓取某月（缺省当月）。**异步执行，立即返回**；抓取中重复触发返回 409 |
| `GET /api/monthly/fetch/status` | 返回 `{"running": bool, "last_result": {...}\|{}}` |
| `GET /api/monthly/months` | 已抓取月份列表 `{"months": ["2026-08", ...]}`（倒序） |
| `GET /api/monthly/summary?month=YYYY-MM` | 月汇总：`total_fee`/`total_tokens`/`models`/`detail_rows`/`fetched_at`。未抓取 404 |
| `GET /api/monthly/stats?month=YYYY-MM` | 按月×模型聚合（**查「某模型 token 总量/费用」用这个**） |
| `GET /api/monthly/details?month=YYYY-MM&model=&limit=` | 明细行（按时间倒序，`limit` 默认 200，最大 2000） |

`GET /api/monthly/stats` 返回示例：

```json
{
  "month": "2026-08",
  "count": 2,
  "data": [
    {"model": "DeepSeek-V4-Flash-0731", "text_in_tokens": 859894, "text_out_tokens": 134485,
     "hit_in_tokens": 6346240, "bare_tokens": 0, "total_tokens": 7340619,
     "total_fee": 3.491, "rows": 28}
  ]
}
```

字段：`text_in_tokens` 文本输入 / `text_out_tokens` 文本输出 / `hit_in_tokens` 命中输入（缓存命中）/ `bare_tokens` 描述里无标签的未知项 / `total_fee` 该模型当月总费用 / `rows` 计费记录条数。

`GET /api/monthly/details` 每行：`api_key_name`（API 密钥名）、`model`、各类 token、`fee`、`start_time`/`end_time`（**平台本地时间，东八区**）、`billing_status`（"是"/"否" 支付完成）。

### 4.9 `GET /api/health` — 健康检查（无需鉴权）

```json
{"status": "ok", "time": "<ISO 8601 UTC>"}
```

---

## 5. 错误与异常场景

| HTTP 状态码 | 含义 | 处理建议 |
|---|---|---|
| `200` | 成功 | 正常解析 |
| `400` | 请求参数有误（如配置不完整） | 阅读 `detail` 修复后重试 |
| `401` | 未登录 / 令牌无效或过期 / 密码错误 | 检查 `Authorization: Bearer <令牌>` 是否正确；重新登录 |
| `404` | 暂无数据 / 该月尚未抓取 | 先配置并抓取；或 `POST /api/monthly/fetch` |
| `409` | 该用户正在抓取中（余额或月度） | 等几秒再查 status / 重试 |
| `429` | 登录失败次数过多，已锁定 10 分钟 | 稍后再试 |
| `502` | `POST /api/balance/fetch` 抓取失败 | 用 `POST /api/config/test` 排查配置 |
| 连接失败 | 服务未启动 / 端口不对 / 不在同一台机器 | 确认服务与地址 |

---

## 6. 常见陷阱（Claude 请特别注意）

1. **端口是 8100，不是 8000**。8000 被本机 locateanything 服务占用，本服务刻意用 8100；**重启只能按 PID kill，绝不能 `pkill -f "uvicorn main:app"`**（会匹配到自身命令行导致 shell 退出）。
2. **数据按用户隔离**。任何账号（A）都用它的令牌/会话只看到 A 的数据；跨用户查不到、改不了。「第一个注册用户即管理员」，管理员与普通用户访问同一套接口，无特权数据接口。
3. **密码/Key 永不回显**。`GET /api/config` 只有 `password_set`/`api_key_set` 布尔。`PUT` 时密码/Key 留空即保持原值。平台凭据 Fernet 加密落库，服务端主密钥在 env `MASTER_KEY` → `master.key` 文件。
4. **定时为「统一调度器」**：服务每 60 秒扫描已启用账号，逐个判断是否到期（按各用户 `fetch_interval`）并异步抓取；互不阻塞，同一用户不会并发重抓。
5. **`fetched_at` 是 UTC**，展示时按需转当地时区；**月度明细 `start_time`/`end_time` 是东八区**，两者别混用。
6. **连续多条快照余额可能相同**（两次抓取间账户没花钱），正常现象。
7. **月度明细一行 ≠ 一个模型一次**：同一模型同一时间片可能多行。算模型总消耗用 `/api/monthly/stats` 聚合，不要自己按行求和/去重。
8. **同一月份可重复抓取（覆盖式更新）**；费用页明细随消费动态增长，要"完整"当月数据请在月末后再抓一次。每日 02:10 服务自动补抓当月。
9. **每次真实登录都要调用平台的多模态验证码识别**（`POST /config/test`、立即抓取、定时抓取均消耗少量费用）。程序轮询别太频繁——默认间隔即可，要即时数据用 `POST /api/balance/fetch`。
10. **服务重启后 `id` 继续递增**；判断新旧用 `fetched_at` 或 `id` 大小，不要假设从 1 开始。服务刚启动会在首次扫描后入库，未配置用户的相关接口均返回 404 提示。
11. **对外程序一律用 `GET /user/balance` + `Authorization: Bearer`**；`/api/balance` 是内部接口（含内部字段），`X-API-Key` 头已废弃返回 401。需要「DeepSeek 同款接入」时，把请求地址指向 `http://<服务IP>:8100` 即可直接替换。

---

## 7. 接入建议

- **轮询频率**：与默认抓取间隔匹配即可（≤ 1 小时一次）；需要即时余额（充值/大额消费后）用 `POST /api/balance/fetch`。
- **告警阈值**：用 `GET /user/balance` 的 `balance_infos[].total_balance`（字符串）转数值后与阈值比较；留出抓取延迟余量。
- **历史曲线**：`/api/balance/history?days=N`，`data` 数组直接画折线（x=`fetched_at`，y=`balance`）。
- **展示**：余额显示 2 位小数（`999.35` 元）。
- **按月监控**：`/api/monthly/months` 确认已抓 → 没有则 `POST /api/monthly/fetch?month=YYYY-MM` → `/api/monthly/stats` 查模型级 token/费用。
- **面向公众推广**：每个使用者的平台账号、验证码模型 API Key 由他本人填写，服务端只加密存储与调度，不会暴露给其他用户。

---

*配套代码在 `/home/hero/lbqh-monitor/`：main.py（FastAPI 入口+统一调度）、fetcher.py（登录/验证码/余额抓取）、monthly.py（月度明细抓取）、storage.py（SQLite 多用户存储）、crypto.py（Fernet 加密）、auth.py（登录/会话/令牌）、manage.py（CLI：init-admin / migrate-legacy / encrypt-password）。*
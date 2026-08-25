"""服务入口：多人使用的联并千行MaaS 余额/消费监控服务。

- 每个用户注册登录后，在前端填写自己的平台凭据（账号/密码/验证码模型/请求地址/APIKey/间隔）
- 统一调度器按各用户配置的时间间隔扫描抓取，数据按用户完全隔离
- Web 看板：GET / （登录后可看自己的数据）
- HTTP API：登录（cookie 会话）或 X-API-Key（用户自己的令牌）均可鉴权

启动：.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8100
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from auth import (
    check_login_lock,
    clear_login_failure,
    get_current_user,
    hash_password,
    logout_user,
    register_login_failure,
    verify_password,
)
from config import cfg
from crypto import decrypt, encrypt
from fetcher import fetch_balance
import storage as store
from monthly import fetch_month as fetch_month_for_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lbqh-monitor")

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


# ================= 工具 =================

def parse_iso_z(s: str) -> datetime:
    """解析 "%Y-%m-%dT%H:%M:%SZ"。"""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def build_conf(conf_row: dict) -> dict:
    """将 user_configs 行（含加密字段）转为抓取逻辑需要的明文配置。"""
    return {
        "lbqh_user": conf_row["lbqh_user"],
        "lbqh_password": decrypt(conf_row["lbqh_password_enc"]),
        "lbqh_base_url": conf_row["lbqh_base_url"] or "https://lbqh.paratera.com",
        "maas_api_url": conf_row["maas_api_url"] or "https://maasapi.paratera.com/v1/chat/completions",
        "maas_api_key": decrypt(conf_row["maas_api_key_enc"]),
        "captcha_model": conf_row["captcha_model"] or "Qwen3.5-35B-A3B",
    }


def is_due(last_fetch_at: str | None, interval: int) -> bool:
    """判断是否需要抓取（从未抓过或已过间隔）。"""
    if not last_fetch_at:
        return True
    try:
        return (datetime.utcnow() - parse_iso_z(last_fetch_at)).total_seconds() >= (interval or 3600)
    except ValueError:
        return True


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"], "is_admin": bool(user["is_admin"])}


# ================= 抓取运行器（按用户） =================

_balance_running: set[int] = set()
_monthly_running: set[int] = set()


async def _run_user_balance_fetch(conf_row: dict) -> dict | None:
    """抓一个用户的余额并存库，返回快照或 None。conf_row 来自库。"""
    uid = conf_row["user_id"]
    try:
        data = await fetch_balance(build_conf(conf_row))
        store.insert_snapshot(data["balance"], data["total_spent"], data["month_spent"], uid)
        store.mark_fetch_result(uid, True)
        logger.info("用户 %s 抓取成功: %s", uid, data)
        return data
    except Exception as e:  # noqa: BLE001
        logger.error("用户 %s 抓取失败: %s", uid, e)
        store.mark_fetch_result(uid, False, str(e))
        return None


async def balance_scan_tick() -> None:
    """统一调度：每 60s 检查所有启用配置，到期则触发抓取（后台任务，互不阻塞）。"""
    for cr in store.list_enabled_configs():
        uid = cr["user_id"]
        if uid in _balance_running:
            continue
        if is_due(cr["last_fetch_at"], cr["fetch_interval"]):
            _balance_running.add(uid)  # 预占位：防同一用户在该 tick 内被重复触发
            asyncio.create_task(_finish_run(uid, _run_user_balance_fetch(cr)))


async def _finish_run(uid: int, coro) -> None:
    """执行抓取协程，结束后从 running 集合移除（余额与月度共用）。"""
    try:
        await coro
    finally:
        _balance_running.discard(uid)
        _monthly_running.discard(uid)


async def _run_user_monthly(conf_row: dict, month: str | None = None) -> int | None:
    """抓一个用户的月度明细并入库，返回条数。"""
    uid = conf_row["user_id"]
    now = datetime.now()
    year, m = (now.year, now.month) if not month else (int(month.split("-")[0]), int(month.split("-")[1]))
    try:
        details = await fetch_month_for_user(year, m, build_conf(conf_row), uid)
        logger.info("用户 %s 月度抓取 %s: %s 条", uid, f"{year:04d}-{m:02d}", len(details))
        return len(details)
    except Exception as e:  # noqa: BLE001
        logger.error("用户 %s 月度抓取失败: %s", uid, e)
        return None


async def monthly_sweep() -> None:
    """每日调度：对所有启用配置补抓当月明细。"""
    for cr in store.list_enabled_configs():
        uid = cr["user_id"]
        if uid in _monthly_running:
            continue
        _monthly_running.add(uid)
        asyncio.create_task(_finish_run(uid, _run_user_monthly(cr)))


# ================= 生命周期 =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    scheduler.add_job(balance_scan_tick, "interval", seconds=60, id="balance_scan", max_instances=1, coalesce=True)
    scheduler.add_job(monthly_sweep, "cron", hour=2, minute=10, id="monthly_sweep", max_instances=1, coalesce=True)
    scheduler.start()
    logger.info("服务已启动：统一调度 60s 扫描 / 每月 02:10 补抓当月")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="联并千行MaaS 多用户监控", version="2.0.0", lifespan=lifespan)


# ================= 公开接口 =================

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "version": "2.0.0"}


@app.post("/auth/register")
def register(email: str = Body(...), password: str = Body(...), response: Response = None):
    email = email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(400, "邮箱格式不正确")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if store.get_user_by_email(email):
        raise HTTPException(409, "该邮箱已注册")
    is_admin = 1 if store.count_users() == 0 else 0  # 第一个注册用户为管理员
    uid = store.create_user(email, hash_password(password), is_admin)
    token = store.create_session(uid)
    _set_session_cookie(response, token)
    return {"user": _public_user(store.get_user_by_id(uid)), "configured": False}


@app.post("/auth/login")
def login(email: str = Body(...), password: str = Body(...), response: Response = None):
    email = email.strip().lower()
    check_login_lock(email)
    u = store.get_user_by_email(email)
    if not u or not verify_password(password, u["password_hash"]):
        register_login_failure(email)
        raise HTTPException(401, "邮箱或密码错误")
    clear_login_failure(email)
    token = store.create_session(u["id"])
    _set_session_cookie(response, token)
    return {"user": _public_user(u), "configured": store.get_config(u["id"]) is not None}


@app.post("/auth/logout")
def logout(request: Request, response: Response, user: dict = Depends(get_current_user)):
    token = request.cookies.get("session")
    if token:
        logout_user(token)
    response.delete_cookie("session")
    return {"ok": True}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "session", token, httponly=True, samesite="lax",
        max_age=24 * 7 * 3600, path="/",
    )


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": _public_user(user), "configured": store.get_config(user["id"]) is not None}


# ================= 配置管理 =================

@app.get("/api/config")
def get_my_config(user: dict = Depends(get_current_user)):
    cr = store.get_config(user["id"])
    if cr is None:
        return {
            "configured": False,
            "lbqh_user": "", "lbqh_base_url": "https://lbqh.paratera.com",
            "maas_api_url": "https://maasapi.paratera.com/v1/chat/completions",
            "captcha_model": "Qwen3.5-35B-A3B", "fetch_interval": 3600,
            "enabled": True, "password_set": False, "api_key_set": False,
            "last_fetch_at": None, "last_fetch_ok": None, "last_fetch_error": None,
        }
    return {
        "configured": True,
        "lbqh_user": cr["lbqh_user"],
        "lbqh_base_url": cr["lbqh_base_url"],
        "maas_api_url": cr["maas_api_url"],
        "captcha_model": cr["captcha_model"],
        "fetch_interval": cr["fetch_interval"],
        "enabled": bool(cr["enabled"]),
        "password_set": bool(cr["lbqh_password_enc"]),
        "api_key_set": bool(cr["maas_api_key_enc"]),
        "last_fetch_at": cr["last_fetch_at"],
        "last_fetch_ok": cr["last_fetch_ok"],
        "last_fetch_error": cr["last_fetch_error"],
    }


def _validate_config_payload(body: dict) -> dict:
    fields = {}
    for k in ("lbqh_user", "lbqh_base_url", "maas_api_url", "captcha_model"):
        if k in body:
            fields[k] = str(body[k]).strip()
    if body.get("lbqh_user") is not None and not str(body["lbqh_user"]).strip():
        raise HTTPException(400, "请填写联并千行登录账号")
    if "lbqh_password" in body and str(body["lbqh_password"]):
        fields["lbqh_password_enc"] = encrypt(str(body["lbqh_password"]))
    if "maas_api_key" in body and str(body["maas_api_key"]):
        fields["maas_api_key_enc"] = encrypt(str(body["maas_api_key"]))
    if "fetch_interval" in body:
        try:
            fields["fetch_interval"] = max(300, int(body["fetch_interval"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "更新间隔须为整数秒")
    if "enabled" in body:
        fields["enabled"] = bool(body["enabled"])
    return fields


@app.put("/api/config")
def put_my_config(body: dict = Body(...), user: dict = Depends(get_current_user)):
    store.save_config(user["id"], _validate_config_payload(body))
    return {"ok": True}


@app.post("/api/config/test")
async def test_config(body: dict = Body(...), user: dict = Depends(get_current_user)):
    """用提交的配置做一次实际登录+余额抓取测试（不保存）。会消耗一次验证码识别。"""
    cur = store.get_config(user["id"]) or {}
    fields = _validate_config_payload(body)
    merged = dict(cur)
    merged.update(fields)
    # 解密已有密码作为兜底（用户可能未填密码字段，测试时用已存密码）
    try:
        conf = {
            "lbqh_user": merged.get("lbqh_user", ""),
            "lbqh_password": decrypt(merged.get("lbqh_password_enc", "")),
            "lbqh_base_url": merged.get("lbqh_base_url") or "https://lbqh.paratera.com",
            "maas_api_url": merged.get("maas_api_url") or "https://maasapi.paratera.com/v1/chat/completions",
            "maas_api_key": decrypt(merged.get("maas_api_key_enc", "")),
            "captcha_model": merged.get("captcha_model") or "Qwen3.5-35B-A3B",
        }
        if not conf["lbqh_user"] or not conf["lbqh_password"]:
            raise HTTPException(400, "请填写平台账号和密码后再测试")
        data = await fetch_balance(conf)
        return {"ok": True, "message": f"连接成功 余额 ¥{data['balance']:.2f}"}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"连接失败：{type(e).__name__}: {str(e)[:200]}"}


# ================= API 令牌（程序化调用） =================

@app.get("/api/tokens")
def list_tokens(user: dict = Depends(get_current_user)):
    return {"tokens": store.list_api_tokens(user["id"])}


@app.post("/api/tokens")
def create_token(name: str = Body(default="default", embed=True), user: dict = Depends(get_current_user)):
    token = store.create_api_token(user["id"], name[:30] or "default")
    return {"token": token, "note": "请立即复制保存，此令牌只在本次返回中明文展示"}


@app.delete("/api/tokens/{token}")
def delete_token(token: str, user: dict = Depends(get_current_user)):
    if not store.revoke_api_token(user["id"], token):
        raise HTTPException(404, "令牌不存在")
    return {"ok": True}


# ================= 余额接口（按当前用户） =================

@app.get("/user/balance")
def user_balance(user: dict = Depends(get_current_user)):
    """DeepSeek 风格的余额查询：GET /user/balance，Bearer 令牌鉴权。

    返回结构与 DeepSeek /user/balance 对齐，只暴露余额信息，不外泄内部字段。
    """
    latest = store.get_latest(user["id"])
    if latest is None:
        return {"is_available": False, "balance_infos": []}
    return {
        "is_available": True,
        "balance_infos": [
            {
                "currency": "CNY",
                "total_balance": f"{latest['balance']:.2f}",
                "granted_balance": "0",
                "topped_up_balance": "0",
            }
        ],
    }


@app.get("/api/balance")
def balance(user: dict = Depends(get_current_user)):
    latest = store.get_latest(user["id"])
    if latest is None:
        raise HTTPException(status_code=404, detail="暂无数据：请先在配置页填写平台账号并保存")
    return latest


@app.get("/api/balance/history")
def balance_history(days: int = Query(default=7, ge=1, le=365), user: dict = Depends(get_current_user)):
    rows = store.get_history(user["id"], days)
    return {"days": days, "count": len(rows), "data": rows}


@app.post("/api/balance/fetch")
async def balance_fetch_now(user: dict = Depends(get_current_user)):
    """立即抓一次当前用户余额并同步返回最新快照（约 15-20 秒）。"""
    uid = user["id"]
    if uid in _balance_running:
        raise HTTPException(409, "该账号正在抓取中，请稍后")
    cr = store.get_config(uid)
    if cr is None or not cr.get("lbqh_user") or not cr.get("lbqh_password_enc"):
        raise HTTPException(400, "请先在配置页填写并保存平台账号、密码")
    _balance_running.add(uid)
    try:
        data = await _run_user_balance_fetch(cr)
        if data is None:
            raise HTTPException(502, "抓取失败，请在配置页用「测试连接」查看原因")
        return store.get_latest(uid)
    finally:
        _balance_running.discard(uid)


# ================= 月度明细（按当前用户） =================

@app.get("/api/monthly/months")
def monthly_months(user: dict = Depends(get_current_user)):
    return {"months": store.list_available_months(user["id"])}


@app.get("/api/monthly/summary")
def monthly_summary(month: str = Query(description="格式 YYYY-MM"), user: dict = Depends(get_current_user)):
    s = store.get_monthly_summary(month, user["id"])
    if not s["has_data"]:
        raise HTTPException(status_code=404, detail=f"尚未抓取 {month}，可先 POST /api/monthly/fetch?month={month}")
    return s


@app.get("/api/monthly/stats")
def monthly_stats(month: str = Query(description="格式 YYYY-MM"), user: dict = Depends(get_current_user)):
    rows = store.get_monthly_stats(month, user["id"])
    if not rows:
        raise HTTPException(status_code=404, detail=f"尚未抓取 {month}")
    return {"month": month, "count": len(rows), "data": rows}


@app.get("/api/monthly/details")
def monthly_details(
    month: str = Query(description="格式 YYYY-MM"),
    model: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=2000),
    user: dict = Depends(get_current_user),
):
    rows = store.get_usage_details(month, user["id"], model or None, limit)
    return {"month": month, "model": model or None, "count": len(rows), "data": rows}


@app.post("/api/monthly/fetch")
async def monthly_fetch(month: str = Query(default="", description="格式 YYYY-MM，缺省当前月"), user: dict = Depends(get_current_user)):
    uid = user["id"]
    if uid in _monthly_running:
        raise HTTPException(409, "该账号月度抓取进行中，请稍后")
    cr = store.get_config(uid)
    if cr is None or not cr.get("lbqh_user") or not cr.get("lbqh_password_enc"):
        raise HTTPException(400, "请先在配置页填写并保存平台账号、密码")
    _monthly_running.add(uid)
    asyncio.create_task(_finish_run(uid, _run_user_monthly(cr, month or None)))
    return {"ok": True, "message": f"已开始抓取 {'当前月' if not month else month}，稍后刷新页面查看"}


@app.get("/api/monthly/fetch/status")
def monthly_fetch_status(user: dict = Depends(get_current_user)):
    return {"running": user["id"] in _monthly_running, "note": "结果以 GET /api/monthly/summary 为准"}


# ================= Web 页面 =================

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(_STATIC_DIR / "index.html")
"""认证：scrypt 密码哈希、登录失败锁定、FastAPI 鉴权依赖（会话 cookie / API Key）。"""
import base64
import hashlib
import hmac
import time

from fastapi import HTTPException, Request

from storage import (
    delete_session,
    get_session_user,
    get_user_by_api_token,
    get_user_by_email,
    get_user_by_id,
)

# ---- 密码哈希（scrypt，标准库） ----
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    salt = base64.b64encode(__import__("secrets").token_bytes(16)).decode()
    dk = hashlib.scrypt(
        password.encode(), salt=salt.encode(), n=_SCRYPT_N, r=_SCRYPT_R,
        p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return f"{salt}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hash_b64 = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.scrypt(
        password.encode(), salt=salt.encode(), n=_SCRYPT_N, r=_SCRYPT_R,
        p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return hmac.compare_digest(base64.b64encode(dk).decode(), hash_b64)


# ---- 登录失败锁定（内存态：邮箱 → (失败次数, 锁到时间戳)） ----
_LOGIN_FAIL: dict[str, tuple[int, float]] = {}
MAX_FAILS = 5
LOCK_SECONDS = 600


def check_login_lock(email: str) -> None:
    fails, until = _LOGIN_FAIL.get(email, (0, 0))
    if fails >= MAX_FAILS and time.time() < until:
        left = int(until - time.time())
        raise HTTPException(429, f"登录失败次数过多，请 {left}s 后重试")


def register_login_failure(email: str) -> None:
    fails, until = _LOGIN_FAIL.get(email, (0, 0))
    fails += 1
    if fails >= MAX_FAILS:
        until = time.time() + LOCK_SECONDS
    _LOGIN_FAIL[email] = (fails, until)


def clear_login_failure(email: str) -> None:
    _LOGIN_FAIL.pop(email, None)


# ---- FastAPI 依赖：从 Bearer 令牌或会话 Cookie 解析当前用户 ----
def _bearer_token(request: Request) -> str:
    """解析 Authorization: Bearer <token> 请求头，无则返回空串。"""
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def get_current_user(request: Request) -> dict:
    # 程序化调用：Authorization: Bearer <API 令牌>（对齐 DeepSeek 风格）
    token = _bearer_token(request)
    if token:
        u = get_user_by_api_token(token)
        if u:
            return u
    # Web 前端：会话 cookie（登录态）
    token = request.cookies.get("session")
    if token:
        u = get_session_user(token)
        if u:
            return u
    raise HTTPException(status_code=401, detail="未登录或凭据无效")


def get_current_user_optional(request: Request) -> dict | None:
    """与 get_current_user 相同但不抛错（供公开接口选配用户上下文）。"""
    try:
        return get_current_user(request)
    except HTTPException:
        return None


def logout_user(token: str) -> None:
    delete_session(token)


def ensure_user(user_id: int) -> dict:
    u = get_user_by_id(user_id)
    if not u:
        raise HTTPException(status_code=401, detail="用户不存在")
    return u
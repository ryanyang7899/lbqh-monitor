"""SQLite 存储：用户 / 配置 / 会话 / API令牌，以及按用户隔离的余额与月度明细。

多用户：所有业务数据表（balance_snapshots / usage_details / monthly_stats）
均带 user_id 列，任何查询都必须限定 user_id，保证用户间数据完全隔离。
"""
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import cfg

DB_PATH = cfg.DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(Path(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    with _connect() as conn:
        # ---- 账号与权限 ----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        # ---- 每个用户一份联并千行平台配置（1 用户 ↔ 1 配置）----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                lbqh_user TEXT NOT NULL DEFAULT '',
                lbqh_password_enc TEXT NOT NULL DEFAULT '',
                lbqh_base_url TEXT NOT NULL DEFAULT 'https://lbqh.paratera.com',
                maas_api_url TEXT NOT NULL DEFAULT 'https://maasapi.paratera.com/v1/chat/completions',
                maas_api_key_enc TEXT NOT NULL DEFAULT '',
                captcha_model TEXT NOT NULL DEFAULT 'Qwen3.5-35B-A3B',
                fetch_interval INTEGER NOT NULL DEFAULT 3600,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                last_fetch_at TEXT,
                last_fetch_ok INTEGER,
                last_fetch_error TEXT
            )
            """
        )
        # ---- 会话（登录态）----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        # ---- API 令牌（程序化调用）----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                token TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL
            )
            """
        )

        # ---- 余额快照（含 user_id，全新库直接建表，旧库自动补列归 user_id=1）----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS balance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at TEXT NOT NULL,
                balance REAL NOT NULL,
                total_spent REAL NOT NULL DEFAULT 0,
                month_spent REAL NOT NULL DEFAULT 0,
                user_id INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        balance_cols = _cols(conn, "balance_snapshots")
        if "user_id" not in balance_cols:
            conn.execute("ALTER TABLE balance_snapshots ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        for table in ("usage_details", "monthly_stats"):
            cols = _cols(conn, table)
            if "user_id" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_user ON balance_snapshots(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_details_user ON usage_details(user_id, month)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_user ON monthly_stats(user_id, month)")

        # ---- 月度消费明细（费用页逐行抓取）----
        # 页面每行 = 一次独立计费记录：同一 (密钥, 模型, 时间片) 会因多次调用产生多条，
        # 只能以「整行业务内容」为唯一键，跨页重叠的同内容行才视为重复。
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='usage_details'"
        ).fetchone()
        old_schema = row[0] if row else ""
        # 旧表可能经 ALTER 补了 user_id 列，但 UNIQUE 约束仍缺 user_id（多用户会撞键）——
        # 必须按「唯一约束是否含 user_id」判断是否需要重建
        if row and "UNIQUE(user_id, month, api_key_name" not in old_schema:
            conn.execute("DROP TABLE IF EXISTS usage_details")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    month TEXT NOT NULL,
                    api_key_name TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    text_in_tokens INTEGER DEFAULT 0,
                    text_out_tokens INTEGER DEFAULT 0,
                    hit_in_tokens INTEGER DEFAULT 0,
                    bare_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    fee REAL DEFAULT 0,
                    start_time TEXT,
                    end_time TEXT,
                    billing_status TEXT,
                    fetched_at TEXT,
                    UNIQUE(user_id, month, api_key_name, model, start_time, end_time, total_tokens, fee)
                )
                """
            )
        # 按月×模型聚合统计（同样可能因旧唯一键重建）
        row2 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='monthly_stats'"
        ).fetchone()
        old2 = row2[0] if row2 else ""
        if row2 and ("user_id" not in old2 or "UNIQUE(user_id, month, model)" not in old2):
            conn.execute("DROP TABLE IF EXISTS monthly_stats")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS monthly_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    month TEXT NOT NULL,
                    model TEXT NOT NULL,
                    text_in_tokens INTEGER DEFAULT 0,
                    text_out_tokens INTEGER DEFAULT 0,
                    hit_in_tokens INTEGER DEFAULT 0,
                    bare_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    total_fee REAL DEFAULT 0,
                    rows INTEGER DEFAULT 0,
                    fetched_at TEXT,
                    UNIQUE(user_id, month, model)
                )
                """
            )


# ================= 用户账号 =================


def create_user(email: str, password_hash: str, is_admin: int = 0) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
            (email.lower(), password_hash, is_admin, _utcnow()),
        )
        uid = cur.lastrowid
    return uid


def get_user_by_email(email: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
    return dict(row) if row else None


def get_user_by_id(uid: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None


def count_users() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


# ================= 用户配置（联并千行平台凭据） =================

PUBLIC_CONFIG_FIELDS = (
    "lbqh_user", "lbqh_base_url", "maas_api_url", "captcha_model",
    "fetch_interval", "enabled",
)


def get_config(user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM user_configs WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def save_config(user_id: int, fields: dict) -> None:
    """写入/更新用户配置。fields 中密码类字段（lbqh_password_enc / maas_api_key_enc）
    应传入已加密文本；不传则保持原值。"""
    cur_row = get_config(user_id)
    with _connect() as conn:
        if cur_row is None:
            conn.execute(
                """
                INSERT INTO user_configs
                    (user_id, lbqh_user, lbqh_password_enc, lbqh_base_url, maas_api_url,
                     maas_api_key_enc, captcha_model, fetch_interval, enabled,
                     updated_at, last_fetch_ok)
                VALUES (?,?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    user_id,
                    fields.get("lbqh_user", ""),
                    fields.get("lbqh_password_enc", ""),
                    fields.get("lbqh_base_url", "https://lbqh.paratera.com"),
                    fields.get("maas_api_url", "https://maasapi.paratera.com/v1/chat/completions"),
                    fields.get("maas_api_key_enc", ""),
                    fields.get("captcha_model", "Qwen3.5-35B-A3B"),
                    int(fields.get("fetch_interval", 3600)),
                    int(fields.get("enabled", 1)),
                    _utcnow(),
                ),
            )
        else:
            sets, args = [], []
            for k, v in fields.items():
                if k in ("lbqh_user", "lbqh_base_url", "maas_api_url", "captcha_model"):
                    sets.append(f"{k}=?")
                    args.append(v or "")
                elif k == "fetch_interval":
                    sets.append("fetch_interval=?")
                    args.append(int(v))
                elif k == "enabled":
                    sets.append("enabled=?")
                    args.append(int(bool(v)))
                elif k in ("lbqh_password_enc", "maas_api_key_enc") and v:
                    sets.append(f"{k}=?")
                    args.append(v)
            if not sets:
                return
            sets.append("updated_at=?")
            args.append(_utcnow())
            args.append(user_id)
            conn.execute(f"UPDATE user_configs SET {', '.join(sets)} WHERE user_id=?", args)


def mark_fetch_result(user_id: int, ok: bool, error: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE user_configs SET last_fetch_at=?, last_fetch_ok=?, last_fetch_error=?
               WHERE user_id=?""",
            (_utcnow(), 1 if ok else 0, error[:500], user_id),
        )


def list_enabled_configs() -> list[dict]:
    """统一调度器扫描用：所有启用且已填账号的配置。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM user_configs WHERE enabled=1 AND lbqh_user<>''"
        ).fetchall()
    return [dict(r) for r in rows]


def update_updated_at(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE user_configs SET updated_at=? WHERE user_id=?", (_utcnow(), user_id))


# ================= 会话 =================

SESSION_TTL_HOURS = 24 * 7


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, _utcnow(), expires.isoformat(timespec="seconds") + "Z"),
        )
    return token


def get_session_user(token: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at>=?",
            (token, _utcnow()),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def cleanup_expired_sessions() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_utcnow(),))


# ================= API 令牌 =================


def create_api_token(user_id: int, name: str = "default") -> str:
    token = "lbqh-" + secrets.token_hex(16)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO api_tokens (user_id, token, name, created_at) VALUES (?,?,?,?)",
            (user_id, token, name, _utcnow()),
        )
    return token


def get_user_by_api_token(token: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.* FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def list_api_tokens(user_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, token, name, created_at FROM api_tokens WHERE user_id=? ORDER BY id",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_api_token(user_id: int, token: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM api_tokens WHERE user_id=? AND token=?", (user_id, token)
        )
    return cur.rowcount > 0


# ================= 余额快照（按用户） =================


def insert_snapshot(balance: float, total_spent: float, month_spent: float, user_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO balance_snapshots (fetched_at, balance, total_spent, month_spent, user_id) VALUES (?,?,?,?,?)",
            (_utcnow(), balance, total_spent, month_spent, user_id),
        )


def get_latest(user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM balance_snapshots WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_history(user_id: int, days: int = 7) -> list[dict]:
    since = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM balance_snapshots WHERE user_id=? AND fetched_at >= ? ORDER BY id ASC",
            (user_id, since),
        ).fetchall()
    return [dict(r) for r in rows]


# ================= 月度明细（按用户） =================


def replace_month_details(month: str, details: list[dict], fetched_at: str, user_id: int) -> int:
    """整月覆盖写入明细：同内容记录更新，其余删除。返回写入条数。"""
    with _connect() as conn:
        for d in details:
            conn.execute(
                """
                INSERT INTO usage_details
                    (user_id, month, api_key_name, model, text_in_tokens, text_out_tokens,
                     hit_in_tokens, bare_tokens, total_tokens, fee, start_time,
                     end_time, billing_status, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, month, api_key_name, model, start_time, end_time, total_tokens, fee)
                DO UPDATE SET
                    text_in_tokens=excluded.text_in_tokens,
                    text_out_tokens=excluded.text_out_tokens,
                    hit_in_tokens=excluded.hit_in_tokens,
                    bare_tokens=excluded.bare_tokens,
                    billing_status=excluded.billing_status,
                    fetched_at=excluded.fetched_at
                """,
                (
                    user_id, month, d["api_key_name"], d["model"], d["text_in_tokens"],
                    d["text_out_tokens"], d["hit_in_tokens"], d["bare_tokens"],
                    d["total_tokens"], d["fee"], d["start_time"], d["end_time"],
                    d["billing_status"], fetched_at,
                ),
            )
        conn.execute(
            "DELETE FROM usage_details WHERE user_id=? AND month=? AND fetched_at<>?",
            (user_id, month, fetched_at),
        )
    return len(details)


def rebuild_monthly_stats(month: str, fetched_at: str, user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM monthly_stats WHERE user_id=? AND month=?", (user_id, month))
        rows = conn.execute(
            """
            SELECT model,
                   SUM(text_in_tokens)   AS text_in_tokens,
                   SUM(text_out_tokens)  AS text_out_tokens,
                   SUM(hit_in_tokens)    AS hit_in_tokens,
                   SUM(bare_tokens)      AS bare_tokens,
                   SUM(total_tokens)     AS total_tokens,
                   ROUND(SUM(fee), 4)    AS total_fee,
                   COUNT(*)              AS rows
            FROM usage_details WHERE user_id=? AND month=? GROUP BY model ORDER BY total_fee DESC
            """,
            (user_id, month),
        ).fetchall()
        for r in rows:
            conn.execute(
                """
                INSERT INTO monthly_stats
                    (user_id, month, model, text_in_tokens, text_out_tokens, hit_in_tokens,
                     bare_tokens, total_tokens, total_fee, rows, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id, month, r["model"], r["text_in_tokens"], r["text_out_tokens"],
                    r["hit_in_tokens"], r["bare_tokens"], r["total_tokens"],
                    r["total_fee"], r["rows"], fetched_at,
                ),
            )


def get_monthly_stats(month: str, user_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM monthly_stats WHERE user_id=? AND month=? ORDER BY total_fee DESC",
            (user_id, month),
        ).fetchall()
    return [dict(r) for r in rows]


def get_monthly_summary(month: str, user_id: int) -> dict:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT model) AS models,
                   SUM(rows) AS detail_rows,
                   SUM(total_fee) AS total_fee,
                   MAX(fetched_at) AS fetched_at
            FROM monthly_stats WHERE user_id=? AND month=?
            """,
            (user_id, month),
        ).fetchone()
    if not row or row["detail_rows"] is None:
        return {"month": month, "has_data": False}
    s = dict(row)
    s.update(month=month, has_data=True, total_tokens=None)
    tok = conn.execute(
        "SELECT SUM(total_tokens) AS t FROM monthly_stats WHERE user_id=? AND month=?",
        (user_id, month),
    ).fetchone()
    s["total_tokens"] = tok["t"]
    return s


def get_usage_details(month: str, user_id: int, model: str | None = None, limit: int = 200) -> list[dict]:
    with _connect() as conn:
        sql = "SELECT * FROM usage_details WHERE user_id=? AND month=?"
        args: list = [user_id, month]
        if model:
            sql += " AND model=?"
            args.append(model)
        sql += " ORDER BY start_time DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def list_available_months(user_id: int) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT month FROM monthly_stats WHERE user_id=? ORDER BY month DESC",
            (user_id,),
        ).fetchall()
    return [r["month"] for r in rows]
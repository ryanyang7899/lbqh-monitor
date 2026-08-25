"""一次性脚本：重建 usage_details / monthly_stats 为用户级唯一约束新结构，并回填旧明细。

用法：  ./.venv/bin/python rebuild_tables.py
前置：  storage.init_db() 已跑过（balance.backup.json 已由本会话生成）。
"""
import json
import sqlite3

DB = "balance.db"
BACKUP = "balance.backup.json"

data = json.load(open(BACKUP))
ud, ucols = data["usage_details"], data["usage_details_cols"]

c = sqlite3.connect(DB)
c.execute("DROP TABLE IF EXISTS usage_details")
c.execute("DROP TABLE IF EXISTS monthly_stats")

c.execute(
    """CREATE TABLE usage_details (
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
)"""
)
c.execute(
    """CREATE TABLE monthly_stats (
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
)"""
)

idx = {name: i for i, name in enumerate(ucols)}


def g(row, name):
    """按列名取值；旧表缺列时给安全默认值（user_id 缺省归 1，文本空串，数字 0）。"""
    if name not in idx:
        if name == "user_id":
            return 1
        return "" if name in ("month", "api_key_name", "model", "start_time", "end_time", "billing_status", "fetched_at") else 0
    return row[idx[name]]


inserted = 0
for r in ud:
    c.execute(
        """INSERT OR IGNORE INTO usage_details
        (user_id, month, api_key_name, model, text_in_tokens, text_out_tokens,
         hit_in_tokens, bare_tokens, total_tokens, fee, start_time, end_time,
         billing_status, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            g(r, "user_id") or 1, g(r, "month"), g(r, "api_key_name") or "", g(r, "model") or "",
            g(r, "text_in_tokens") or 0, g(r, "text_out_tokens") or 0, g(r, "hit_in_tokens") or 0,
            g(r, "bare_tokens") or 0, g(r, "total_tokens") or 0, g(r, "fee") or 0,
            g(r, "start_time"), g(r, "end_time"), g(r, "billing_status"), g(r, "fetched_at"),
        ),
    )
    inserted += 1

c.commit()
c.execute("CREATE INDEX IF NOT EXISTS idx_details_user ON usage_details(user_id, month)")
c.execute("CREATE INDEX IF NOT EXISTS idx_stats_user ON monthly_stats(user_id, month)")
c.commit()
print(f"rebuild-ok: usage_details {inserted} rows | monthly_stats empty (re-fetch needed)")
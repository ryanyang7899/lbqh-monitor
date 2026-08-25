"""管理命令：python -m manage <子命令>

子命令：
  init-admin <email>              创建（或提升）首个管理员，运行中会提示输入密码
  migrate-legacy <admin_email>    把 .env 里的旧单用户平台凭据迁移为指定管理员的配置，
                                  并将历史余额快照归属到该管理员
  encrypt-password                生成一个 scrypt 密码哈希（供脚本/API 调测）
"""
import asyncio
import getpass
import sys

import storage as store
from config import cfg


def _ensure_first_admin(email: str) -> dict:
    store.init_db()
    if store.count_users() == 0:
        from auth import hash_password
        print("系统还没有任何用户，将直接从该管理员开始：")
    u = store.get_user_by_email(email)
    if u:
        print(f"用户 {email} 已存在 (id={u['id']}, admin={u['is_admin']})")
        return u
    pw = getpass.getpass(f"设置管理员 {email} 的登录密码: ")
    if len(pw) < 6:
        print("密码至少 6 位，已取消"); sys.exit(1)
    from auth import hash_password  # noqa
    uid = store.create_user(email, hash_password(pw), is_admin=1)
    print(f"已创建管理员 {email} (id={uid})")
    return store.get_user_by_id(uid)


def cmd_init_admin() -> None:
    if len(sys.argv) < 3:
        print("用法：python -m manage init-admin <email>"); sys.exit(1)
    u = _ensure_first_admin(sys.argv[2])
    print(f"管理员就绪：{u['email']}")


def cmd_migrate_legacy() -> None:
    if len(sys.argv) < 3:
        print("用法：python -m manage migrate-legacy <admin_email>"); sys.exit(1)
    email = sys.argv[2]
    u = _ensure_first_admin(email)
    store.init_db()

    # 1) 平台凭据迁移（加密存库）
    from crypto import encrypt  # noqa
    fields = {
        "lbqh_user": getattr(cfg, "LBQH_USER", ""),
        "lbqh_base_url": getattr(cfg, "LBQH_BASE_URL", "https://lbqh.paratera.com"),
        "maas_api_url": getattr(cfg, "MAAS_API_URL", "https://maasapi.paratera.com/v1/chat/completions"),
        "maas_api_key_enc": encrypt(getattr(cfg, "MAAS_API_KEY", "")),
        "captcha_model": getattr(cfg, "CAPTCHA_MODEL", "Qwen3.5-35B-A3B"),
        "fetch_interval": getattr(cfg, "FETCH_INTERVAL", 3600),
        "enabled": 1,
    }
    if getattr(cfg, "LBQH_PASSWORD", ""):
        fields["lbqh_password_enc"] = encrypt(cfg.LBQH_PASSWORD)
    store.save_config(u["id"], fields)
    print(f"平台配置已写入管理员 {email}")

    # 2) 历史余额快照归属（user_id=1 旧数据 → 管理员）
    if u["id"] != 1:
        with store._connect() as conn:
            cur = conn.execute(
                "UPDATE balance_snapshots SET user_id=? WHERE user_id=1",
                (u["id"],),
            )
        print(f"已将 {cur.rowcount} 条历史余额快照归属到 {email}")
    else:
        print("管理员 id 即 1，历史快照无需迁移")

    # 3) 提示
    print("提示：若此前月度明细表因结构升级被重建，可稍后在页面对管理员账号执行「抓取此月」重新生成。")


def cmd_encrypt_password() -> None:
    pw = getpass.getpass("输入要加密的密码: ")
    from auth import hash_password  # noqa
    print(hash_password(pw))


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "init-admin":
        cmd_init_admin()
    elif cmd == "migrate-legacy":
        cmd_migrate_legacy()
    elif cmd == "encrypt-password":
        cmd_encrypt_password()
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
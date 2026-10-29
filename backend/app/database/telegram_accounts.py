import uuid

from app.database.connection import get_pool


async def ensure_primary_account(phone: str) -> dict:
    pool = get_pool()
    if not pool:
        raise RuntimeError("PostgreSQL no está inicializado")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM telegram_accounts WHERE is_primary = TRUE FOR UPDATE"
            )
            if row:
                account = dict(row)
                if phone and not account.get("phone"):
                    await conn.execute(
                        "UPDATE telegram_accounts SET phone = $2 WHERE id = $1", account["id"], phone
                    )
                    account["phone"] = phone
            else:
                row = await conn.fetchrow(
                    """INSERT INTO telegram_accounts (name, phone, session_name, is_primary)
                       VALUES ('Principal', $1, 'user', TRUE) RETURNING *""",
                    phone or "",
                )
                account = dict(row)
            await conn.execute(
                "UPDATE channels SET telegram_account_id = $1 WHERE telegram_account_id IS NULL",
                account["id"],
            )
    return account


async def get_primary_account() -> dict | None:
    pool = get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM telegram_accounts WHERE is_primary = TRUE")
    return dict(row) if row else None


async def get_telegram_account(account_id: int) -> dict | None:
    pool = get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM telegram_accounts WHERE id = $1", account_id)
    return dict(row) if row else None


async def list_telegram_accounts() -> list[dict]:
    pool = get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.id, a.name, a.phone, a.session_name, a.is_primary, a.active,
                   COUNT(c.channel_id) AS channels_count
            FROM telegram_accounts a
            LEFT JOIN channels c ON c.telegram_account_id = a.id
            GROUP BY a.id
            ORDER BY a.is_primary DESC, a.id
        """)
    return [dict(row) for row in rows]


async def create_telegram_account(name: str, phone: str) -> dict:
    pool = get_pool()
    if not pool:
        raise RuntimeError("PostgreSQL no está inicializado")
    session_name = f"account_{uuid.uuid4().hex}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO telegram_accounts (name, phone, session_name)
            VALUES ($1, $2, $3) RETURNING id, name, phone, session_name, is_primary, active
        """, name, phone, session_name)
    return dict(row)


async def delete_telegram_account(account_id: int) -> dict | None:
    pool = get_pool()
    if not pool:
        raise RuntimeError("PostgreSQL no está inicializado")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, name, phone, session_name, is_primary FROM telegram_accounts WHERE id = $1 FOR UPDATE",
                account_id,
            )
            if not row:
                return None
            if row["is_primary"]:
                raise ValueError("No se puede eliminar la cuenta Telegram principal")
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM channels WHERE telegram_account_id = $1", account_id
            )
            if count:
                raise ValueError("Reasigna o desactiva los canales de esta cuenta antes de eliminarla")
            await conn.execute("DELETE FROM telegram_accounts WHERE id = $1", account_id)
    return dict(row)


async def set_account_pending(account_id: int, code_hash: str, at: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE telegram_accounts SET pending_code_hash = $2, pending_code_at = $3 WHERE id = $1",
            account_id, code_hash, at,
        )


async def clear_account_pending(account_id: int) -> None:
    pool = get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE telegram_accounts SET pending_code_hash = NULL, pending_code_at = NULL WHERE id = $1",
            account_id,
        )


async def update_primary_phone(phone: str) -> None:
    pool = get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE telegram_accounts SET phone = $1 WHERE is_primary = TRUE", phone
        )

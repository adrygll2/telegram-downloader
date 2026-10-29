from app.database.connection import get_pool


async def get_all_channels():
    pool = get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.channel_id, c.name, c.active, c.telegram_account_id,
                   a.name AS account_name
            FROM channels c LEFT JOIN telegram_accounts a ON a.id = c.telegram_account_id
            ORDER BY c.name
        """)
        return [{"id": r["channel_id"], "name": r["name"], "active": r["active"],
                 "account_id": r["telegram_account_id"], "account_name": r["account_name"]} for r in rows]


async def get_active_channels():
    pool = get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.channel_id, c.name, c.telegram_account_id, a.name AS account_name
            FROM channels c LEFT JOIN telegram_accounts a ON a.id = c.telegram_account_id
            WHERE c.active = TRUE ORDER BY c.name
        """)
        return [{"id": r["channel_id"], "name": r["name"],
                 "account_id": r["telegram_account_id"], "account_name": r["account_name"]} for r in rows]


async def upsert_channel(channel_id: int, name: str, telegram_account_id: int | None = None):
    pool = get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM channels WHERE channel_id = $1", channel_id)
        if existing:
            if telegram_account_id is None:
                await conn.execute("UPDATE channels SET name = $1 WHERE channel_id = $2", name, channel_id)
            else:
                await conn.execute(
                    "UPDATE channels SET name = $1, telegram_account_id = $2, active = TRUE WHERE channel_id = $3",
                    name, telegram_account_id, channel_id,
                )
            return False
        else:
            await conn.execute(
                "INSERT INTO channels (channel_id, name, active, telegram_account_id) VALUES ($1, $2, TRUE, $3)",
                channel_id, name, telegram_account_id,
            )
            return True


async def set_active_channels(channels: list[dict | int]):
    pool = get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("UPDATE channels SET active = FALSE")
        for item in channels:
            if isinstance(item, dict):
                await conn.execute("""
                    UPDATE channels SET active = TRUE,
                        telegram_account_id = COALESCE($2, telegram_account_id)
                    WHERE channel_id = $1
                """, item["id"], item.get("account_id"))
            else:
                await conn.execute("UPDATE channels SET active = TRUE WHERE channel_id = $1", item)


async def remove_channel(channel_id: int):
    pool = get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM channels WHERE channel_id = $1", channel_id)

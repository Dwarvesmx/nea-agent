"""Ejecutor sintético del sobre real construido por el despachador CRM."""
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

import asyncpg
import httpx

from app.crm import CrmClient
from app.dispatch_store import PgDispatchStore
from app.dispatch_worker import drain_one
from app.main import create_app
from tests.test_dispatch_durable import cloud, post


async def main():
    dsn = os.environ["DATABASE_URL"]
    target = urlparse(dsn)
    if (target.hostname, target.port, target.path, target.username) != ("127.0.0.1", 55534, "/postgres", "demo_wa"):
        raise RuntimeError("Se requiere el laboratorio descartable de WhatsApp")
    connection = await asyncpg.connect(dsn)
    try:
        directory = await connection.fetchval("select current_setting('data_directory')")
        if "/demo-wa-pg-" not in directory.replace("\\", "/"):
            raise RuntimeError("Clúster de pruebas incorrecto")
        await connection.execute("create schema if not exists nea_demo_wa")
        await connection.execute("set search_path=nea_demo_wa")
        await connection.execute(Path("migrations/008_dispatch_inbox.sql").read_text())
    finally:
        await connection.close()

    payload = json.load(sys.stdin)
    pool = await asyncpg.create_pool(dsn, server_settings={"search_path": "nea_demo_wa"})
    url = "http://127.0.0.1:55535"
    try:
        async with httpx.AsyncClient(base_url=url, headers={"X-API-Key": os.environ["BOT_API_KEY"]}, trust_env=False) as http:
            ctx = cloud()
            await ctx.crm.aclose()
            ctx.crm = CrmClient(url, "unused", client=http)
            ctx.store = PgDispatchStore()
            ctx.store.pool = pool
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(ctx)), base_url="http://nea-test") as nea:
                first = await post(nea, ctx, payload)
                duplicate = await post(nea, ctx, payload)
                if first.status_code != 200 or duplicate.status_code != 200:
                    raise RuntimeError(f"Nea rechazó el contrato con {first.status_code}/{duplicate.status_code}")
            assert await drain_one(ctx)
            assert not await drain_one(ctx)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

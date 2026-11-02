"""Identidad durable: solo contra el PostgreSQL local explícitamente aislado."""
import asyncio
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest

from app import turn
from app.db import PgStore
from app.sender import SenderWorker
from tests.conftest import CRM_CONV_ID, CRM_URL, IDENTITY, make_ctx

URL = os.getenv("NEA_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="sin PostgreSQL local aislado")


@pytest.fixture
async def postgres_store():
    parsed = urlsplit(URL)
    assert parsed.hostname == "127.0.0.1" and parsed.path == "/vocero_zernio_test"
    assert os.getenv("NEA_TEST_PGDATA")
    conn = await asyncpg.connect(URL)
    row = await conn.fetchrow("select current_database() as db, current_setting('data_directory') as dir")
    assert row["db"] == "vocero_zernio_test"
    assert Path(row["dir"]).resolve() == Path(os.environ["NEA_TEST_PGDATA"]).resolve()
    schema = "nea_id_" + uuid.uuid4().hex
    store = PgStore(URL, schema)
    await store.connect()
    await store.migrate(Path(__file__).resolve().parents[1] / "migrations")
    try:
        yield store, schema
    finally:
        await store.aclose()
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def test_uuid_durable_antes_del_primer_intento_y_tras_reinicio(postgres_store, monkeypatch, respx_mock):
    store, schema = postgres_store
    monkeypatch.setenv("NEA_DURABLE_RESPONSE_IDS", "true")
    ctx = make_ctx()
    ctx.store = store
    conv = await store.get_or_create_conversation(IDENTITY)
    observed = []

    async def interrupted(request):
        body = json.loads(request.content)
        rows = await store.pool.fetch("select response_id from pending_send")
        assert len(rows) == 1 and rows[0]["response_id"] == body["responseId"]
        observed.append(body["responseId"])
        raise asyncio.CancelledError()

    route = respx_mock.post(f"{CRM_URL}/api/bot/messages").mock(side_effect=interrupted)
    with pytest.raises(asyncio.CancelledError):
        await turn._send(ctx, conv.id, CRM_CONV_ID, "igual")
    await store.aclose()
    await store.connect()
    # Vence la reserva del turno muerto, sin esperar tiempo real.
    await store.pool.execute("update pending_send set next_retry_at=now()-interval '1 second'")
    # Nuevo cliente y pool simulan un proceso que arranca con la fila pendiente.
    ctx2 = make_ctx()
    ctx2.store = store
    route.mock(return_value=httpx.Response(200, json={"messageId": "ficticio"}))
    await SenderWorker(ctx2).tick()
    assert json.loads(route.calls.last.request.content)["responseId"] == observed[0]
    assert uuid.UUID(observed[0]).version == 4
    assert await store.pool.fetchval("select delivered_at is not null from pending_send")
    await ctx.crm.aclose()
    await ctx2.crm.aclose()


async def test_reintentos_y_respuestas_iguales_tienen_identidades_correctas(postgres_store, monkeypatch, respx_mock):
    store, _ = postgres_store
    monkeypatch.setenv("NEA_DURABLE_RESPONSE_IDS", "true")
    async def instant(_): pass
    monkeypatch.setattr(turn.asyncio, "sleep", instant)
    ctx = make_ctx()
    ctx.store = store
    conv = await store.get_or_create_conversation(IDENTITY)
    route = respx_mock.post(f"{CRM_URL}/api/bot/messages").mock(side_effect=[
        httpx.ConnectError("simulado"), httpx.Response(200, json={"messageId": "1"}),
        httpx.Response(200, json={"messageId": "2"}),
    ])
    assert await turn._send(ctx, conv.id, CRM_CONV_ID, "igual")
    assert await turn._send(ctx, conv.id, CRM_CONV_ID, "igual")
    ids = [json.loads(call.request.content)["responseId"] for call in route.calls]
    assert ids[0] == ids[1] and ids[2] != ids[0]
    assert await store.pool.fetchval("select count(distinct response_id) from pending_send") == 2
    await ctx.crm.aclose()


async def test_dos_workers_conservan_uuid_y_solo_uno_registra_historial(postgres_store, respx_mock):
    store, _ = postgres_store
    ctx = make_ctx()
    ctx.store = store
    conv = await store.get_or_create_conversation(IDENTITY)
    response_id = str(uuid.uuid4())
    await store.enqueue_pending_send(conv.id, CRM_CONV_ID, "respuesta", response_id=response_id)
    entrants = 0
    both_entered = asyncio.Event()
    ids = []

    async def crm(request):
        nonlocal entrants
        ids.append(json.loads(request.content)["responseId"])
        entrants += 1
        if entrants == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=5)
        return httpx.Response(200, json={"messageId": "unico"})

    respx_mock.post(f"{CRM_URL}/api/bot/messages").mock(side_effect=crm)
    await asyncio.gather(SenderWorker(ctx).tick(), SenderWorker(ctx).tick())
    assert ids == [response_id, response_id]
    assert await store.pool.fetchval("select count(*) from bot_message where role='assistant'") == 1
    await ctx.crm.aclose()

"""Puente local: endpoint y consumidor reales, PgStore y cliente OpenAI reales.

No carga .env. El proveedor OpenRouter se simula en el ejecutor de Vocero.
El único endpoint auxiliar permite avanzar el worker sin esperas temporales.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from app.config import Settings
from app.crm import CrmClient
from app.db import PgStore
from app.dispatch import router
from app.dispatch_worker import drain_one
from app.llm import OpenAiLlm
from app.profile import ProfileProvider
from app.state import AppContext


@asynccontextmanager
async def lifespan(app):
    dsn = os.environ['DATABASE_URL']
    if not dsn.startswith('postgres://comercial:') or '@127.0.0.1:55546/' not in dsn:
        raise RuntimeError('Solo PostgreSQL descartable comercial')
    settings = Settings(_env_file=None, crm_base_url='http://127.0.0.1:55548',
                        crm_bot_api_key=os.environ['BOT_API_KEY'],
                        allowed_wa_ids='525500000001', crm_organization=os.environ['COMERCIAL_BOT_ORG_ID'])
    store = PgStore(dsn, 'nea_circuito')
    await store.connect()
    await store.migrate(Path('migrations'))
    crm = CrmClient(settings.crm_base_url, settings.crm_bot_api_key)
    llm = OpenAiLlm('synthetic-local-only', 'z-ai/glm-5.3-flash',
                    base_url='http://127.0.0.1:55548/openrouter')
    app.state.ctx = AppContext(settings=settings, store=store, crm=crm, llm=llm,
                               profile=ProfileProvider(crm), agenda_enabled=False)
    try:
        yield
    finally:
        await crm.aclose()
        await llm._client.close()
        await store.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(router)


@app.get('/fixture/ready')
async def ready():
    return {'ready': True}


@app.post('/fixture/drain')
async def drain():
    return {'processed': await drain_one(app.state.ctx)}


if __name__ == '__main__':
    os.environ['NEA_COMERCIAL_V1_ENABLED'] = 'true'
    os.environ['NEA_DURABLE_RESPONSE_IDS'] = 'true'
    uvicorn.run(app, host='127.0.0.1', port=55549, access_log=False)

import json

import httpx
import pytest

from app.llm import LlmReply
from tests.conftest import IDENTITY, evento_despacho, firmar, mock_crm_basics, vaciar_cola


async def entregar(client, ctx, payload, *, firma_valida=True):
    body = json.dumps(payload).encode()
    firma = firmar(body, ctx.settings.dispatch_secret) if firma_valida else "invalid"
    return await client.post("/vocero/dispatch", content=body, headers={"x-vocero-signature": firma})


def reanudacion():
    return {
        **evento_despacho(),
        "type": "commercial.resume",
        "dispatchId": "resume:demo-1",
        "resume": {"sessionId": "demo-1", "generation": 3, "assistant": "NOVA"},
        "messages": [],
    }


@pytest.mark.parametrize("pendiente", [False, True])
async def test_reanuda_sin_inventar_entradas_y_deduplica(ctx, client, respx_mock, pendiente):
    routes = mock_crm_basics(respx_mock)
    conversation = await ctx.store.get_or_create_conversation(IDENTITY)
    await ctx.store.add_message(conversation.id, "user", "Tengo una ferretería y pierdo pedidos por WhatsApp.")
    payload = reanudacion()
    if pendiente:
        payload["messages"] = evento_despacho(text="¿Cuánto cuesta el servicio?")["messages"]
    ctx.llm.replies = [LlmReply(content="Podemos ayudarte con los pedidos de tu ferretería.")]
    assert (await entregar(client, ctx, payload)).status_code == 200
    assert (await entregar(client, ctx, payload)).status_code == 200
    await vaciar_cola(ctx)
    assert len(ctx.llm.calls) == routes["messages"].call_count == 1
    messages = ctx.llm.calls[0]["messages"]
    assert any("ferretería" in m["content"] for m in messages if m["role"] == "user")
    assert any("NOVA" in m["content"] for m in messages if m["role"] == "system")
    assert sum(m["role"] == "user" for m in messages) == (2 if pendiente else 1)
    assert not any(tool["function"]["name"] == "offer_demo" for tool in ctx.llm.calls[0]["tools"])


async def test_rechaza_vacio_normal_firma_invalida_y_ruta_obsoleta(ctx, client, respx_mock):
    routes = mock_crm_basics(respx_mock)
    routes["context"].mock(return_value=httpx.Response(404))
    payload = reanudacion()
    assert (await entregar(client, ctx, {**evento_despacho(), "messages": []})).status_code == 422
    assert (await entregar(client, ctx, payload, firma_valida=False)).status_code == 401
    assert (await entregar(client, ctx, {**payload, "dispatchId": "resume:otro"})).status_code == 422
    assert (await entregar(client, ctx, payload)).status_code == 200
    await vaciar_cola(ctx)
    assert routes["context"].calls[0].request.url.params["resumeDispatch"] == "resume:demo-1"
    assert ctx.llm.calls == []
    assert routes["messages"].call_count == 0

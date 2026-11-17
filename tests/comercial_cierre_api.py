"""Recorrido aislado: herramientas Nea contra las rutas reales de Vocero.
La selección de herramientas es programada; no acredita autonomía del modelo.
"""
import asyncio
import json
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from app.crm import CrmClient
from app.state import Conversation
from app.tools import ToolRuntime
from app.turn import _tool_loop

async def main():
    url, quote, message = sys.argv[1:]
    crm = CrmClient(url, "synthetic-commercial-test-bot-key")
    class ModeloProgramado:
        calls = 0
        async def complete(self, messages, tools):
            self.calls += 1
            name = {1:"comercial_aceptar", 2:"comercial_contratar"}.get(self.calls)
            return SimpleNamespace(content="Ya quedó pagado" if not name else None,
                tool_calls=[SimpleNamespace(id=name,name=name,arguments={"cotizacionId":quote,"version":2})] if name else [])
    ctx=SimpleNamespace(crm=crm,llm=ModeloProgramado(),agenda_enabled=False)
    runtime=ToolRuntime(ctx,Conversation(1,"sintetico"),"conv",tz=ZoneInfo("UTC"),
        dispatch_id="cierre-integrado",comercial_inbound_id=message,comercial_enabled=True)
    text=await _tool_loop(ctx,[],runtime)
    assert runtime._comercial.pedido and runtime._comercial.pedido["id"]
    assert "pago todavía no está confirmado" in text
    assert runtime.handoff_reason is None
    print(json.dumps({"pedido":runtime._comercial.pedido,"respuesta":text}))
    await crm.aclose()

asyncio.run(main())

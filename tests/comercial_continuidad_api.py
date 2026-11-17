"""Caso de estética con runtime y API reales; decisiones LLM programadas.
Solo el límite externo de mensajes entrantes/salientes usa fixtures locales.
"""
import asyncio
import json
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import httpx
from app.crm import CrmClient
from app.tools import ToolRuntime
from app.state import Conversation
from app.turn import _tool_loop

async def main():
    url=sys.argv[1]
    crm=CrmClient(url,"synthetic-commercial-test-bot-key")
    async with httpx.AsyncClient() as fixture:
        async def message(text,direction='in'):
            r=await fixture.post(url+'/fixture/message',json={"text":text,"direction":direction});r.raise_for_status();return r.json()['id']
        results=[]
        for n,text in enumerate(['Tengo una estética y quiero cotizar Servicios y Citas','Sí','Sí, avísame']):
            inbound=await message(text)
            class ModeloProgramado:
                calls=0
                async def complete(self,messages,tools):
                    self.calls+=1
                    return SimpleNamespace(content='Propuesta creada, ¿quieres que prepare otra?' if self.calls>1 else None,
                        tool_calls=[] if self.calls>1 else [SimpleNamespace(id='preparar',name='comercial_preparar',arguments={"conceptos":[{"productoId":"citas-integrada","cantidad":1}]})])
            ctx=SimpleNamespace(crm=crm,llm=ModeloProgramado(),agenda_enabled=False)
            runtime=ToolRuntime(ctx,Conversation(1,'estetica-integrada'),'estetica-integrada',tz=ZoneInfo('UTC'),dispatch_id=f'estetica-{n}',comercial_inbound_id=inbound,comercial_enabled=True,comercial_inbound_text=text)
            answer=await _tool_loop(ctx,[],runtime)
            assert runtime.handoff_reason is None
            if n==0:
                assert '¿Te aviso' in answer
            else:
                assert 'autorización para el aviso' in answer and '¿' not in answer
            if n==1:
                assert ctx.llm.calls==0  # respuesta breve resuelta por estado, sin nueva creación
            results.append(runtime._comercial.actual)
            await message(answer,'out')
        assert len({q['cotizacionId'] for q in results})==1
        assert results[-1]['consentimiento']['registrado'] is True
        print(json.dumps({"cotizacionId":results[-1]['cotizacionId'],"version":results[-1]['version'],"estado":results[-1]['estado']}))
    await crm.aclose()

asyncio.run(main())

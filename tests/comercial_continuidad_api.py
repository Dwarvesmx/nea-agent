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
                    if n==0 and self.calls==1:
                        name='comercial_preparar';args={"conceptos":[{"productoId":"citas-integrada","cantidad":1}]}
                    elif n==0 and self.calls==2:
                        result=json.loads(messages[-1]['content'])['data'];name='comercial_solicitar_aprobacion';args={'cotizacionId':result['cotizacionId'],'version':result['version']}
                    elif n==1 and self.calls==1:
                        name='comercial_solicitar_aprobacion';args={'cotizacionId':results[0]['cotizacionId'],'version':1,'avisar':True}
                    elif n==2 and self.calls==1:
                        name='comercial_preparar';args={"conceptos":[{"productoId":"citas-integrada","cantidad":1}]}
                    else:
                        answer='Servicios y Citas tiene un precio orientativo de $2,490 MXN. Tu propuesta está pendiente de revisión. '+('¿Te aviso por este WhatsApp cuando tu cotización esté autorizada?' if n==0 else 'Tu autorización para el aviso está registrada.')
                        return SimpleNamespace(content=answer,tool_calls=[])
                    return SimpleNamespace(content=None,tool_calls=[SimpleNamespace(id=f'call-{self.calls}',name=name,arguments=args)])
            ctx=SimpleNamespace(crm=crm,llm=ModeloProgramado(),agenda_enabled=False)
            runtime=ToolRuntime(ctx,Conversation(1,'estetica-integrada'),'estetica-integrada',tz=ZoneInfo('UTC'),dispatch_id=f'estetica-{n}',comercial_inbound_id=inbound,comercial_enabled=True,comercial_inbound_text=text)
            answer=await _tool_loop(ctx,[],runtime)
            assert runtime.handoff_reason is None
            if n==0:
                assert '¿Te aviso' in answer
            else:
                assert 'autorización para el aviso' in answer and '¿' not in answer
            if n==1:
                assert runtime._comercial.actual['consentimiento']['registrado'] is True
            results.append(runtime._comercial.actual)
            await message(answer,'out')
        assert len({q['cotizacionId'] for q in results})==1
        assert results[-1]['consentimiento']['registrado'] is True
        print(json.dumps({"cotizacionId":results[-1]['cotizacionId'],"version":results[-1]['version'],"estado":results[-1]['estado']}))
    await crm.aclose()

asyncio.run(main())

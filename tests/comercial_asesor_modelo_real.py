"""Modelo real, decisión autónoma, runtime Nea y API comercial PostgreSQL aislada.
Perfil propuesto local; NO se presenta como exportación de la configuración productiva.
Sin WhatsApp ni cobros. La falla de API es inyección explícita en un único caso.
"""
import asyncio
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
from types import SimpleNamespace
import httpx
from app.crm import CrmClient
from app.llm import OpenAiLlm, LlmExhausted
from app.profile import BusinessProfile
from app.prompt import build_system_prompt
from app.state import Conversation
from app.tools import ToolRuntime
from app.turn import _tool_loop

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=ROOT/'tmp/lukas-asesor-20261117'

class ModeloObservado(OpenAiLlm):
    def _extras(self):
        # Límite de salida solo del ensayo: evita reservar 65536 tokens por
        # respuesta. No altera modelo, selección de herramientas ni producción.
        extra=super()._extras()
        extra['max_tokens']=2048
        return extra

    @staticmethod
    def _parse(resp):
        result=OpenAiLlm._parse(resp)
        if not result.content and not result.tool_calls:
            meta={'http_status':'2xx (SDK devolvió respuesta)', 'max_tokens':2048, 'model':getattr(resp,'model',None),'usage':{'prompt':getattr(getattr(resp,'usage',None),'prompt_tokens',None),'completion':getattr(getattr(resp,'usage',None),'completion_tokens',None)},'choices':[{'finish_reason':c.finish_reason,'native_finish_reason':(getattr(c,'model_extra',None) or {}).get('native_finish_reason'),'refusal':bool(getattr(c.message,'refusal',None)),'content_type':type(c.message.content).__name__,'content_length':len(c.message.content or []),'tool_calls':len(getattr(c.message,'tool_calls',None) or []),'has_reasoning':bool(getattr(c.message,'reasoning',None))} for c in resp.choices]}
            EVIDENCE.mkdir(parents=True,exist_ok=True)
            with (EVIDENCE/'respuestas-vacias.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(meta)+'\n')
        return result

class TrazaModelo:
    def __init__(self,llm):self.llm=llm;self.choices=[]
    async def complete(self,messages,tools):
        reply=await self.llm.complete(messages,tools=tools)
        self.choices.extend([{'name':t.name,'arguments':t.arguments} for t in reply.tool_calls])
        return reply

class TrazaCrm(CrmClient):
    def __init__(self,url,fail=False):super().__init__(url,'synthetic-commercial-test-bot-key');self.trace=[];self.fail=fail
    async def _request(self,method,path,**kwargs):
        body=kwargs.get('json') or {}
        if self.fail and body.get('action')=='solicitar_aprobacion' and body.get('mensajeAvisoId'):
            response=httpx.Response(503,json={'error':{'code':'crm_no_disponible'}})
        else:response=await super()._request(method,path,**kwargs)
        self.trace.append({'method':method,'path':path,'body':body,'status':response.status_code,'result':response.json()})
        return response

async def main():
    url=sys.argv[1]
    vals={}
    for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1);vals[k.strip()]=v.strip().strip(chr(34)).strip(chr(39))
    model=sys.argv[2] if len(sys.argv)>2 else 'google/gemini-3-flash-preview'
    llm=ModeloObservado(api_key=vals['LLM_API_KEY'],model=model,base_url=vals['LLM_BASE_URL'],timeout=60)
    instructions=(Path('docs/lukas-asesor-v1.md')).read_text(encoding='utf-8')
    profile=BusinessProfile(agent_name='Lukas',instructions=instructions,tone='Español natural de México, profesional y cercano',tools=frozenset({'escalar'}),llm_model=model)
    results=[]
    async with httpx.AsyncClient(timeout=40) as fixtures:
        async def scenario(name,text,estado=None,fail=False,history=None):
            if len(sys.argv)>3 and sys.argv[3] and name not in sys.argv[3].split(','):return
            r=await fixtures.post(url+'/fixture/start',json={'estado':estado});r.raise_for_status();ids=r.json();conv=ids['conversationId']
            async def message(t,direction='in'):
                r=await fixtures.post(url+'/fixture/message',json={'conversationId':conv,'text':t,'direction':direction});r.raise_for_status();return r.json()['id']
            crm=TrazaCrm(url,fail);tracked=TrazaModelo(llm)
            messages=[{'role':'system','content':build_system_prompt(profile=profile,context=None,conv=Conversation(1,'barberia-sintetica'),agenda=False,comercial=True,tz=ZoneInfo('America/Mexico_City'))}]
            if estado:messages.append({'role':'assistant','content':'La propuesta de Servicios y solicitudes de citas está aprobada por $2,490 MXN. ¿Aceptas sus condiciones?' if estado=='aprobada' else 'La propuesta de Servicios y solicitudes de citas está en revisión. ¿Te aviso por este WhatsApp cuando tu cotización esté autorizada?'})
            outputs=[];runtimes=[]
            for number,utterance in enumerate([text]+(history or [])):
                mid=await message(utterance);messages.append({'role':'user','content':utterance})
                ctx=SimpleNamespace(crm=crm,llm=tracked,agenda_enabled=False)
                runtime=ToolRuntime(ctx,Conversation(1,'barberia-sintetica'),conv,profile=profile,tz=ZoneInfo('America/Mexico_City'),dispatch_id=f'{name}-{number}',comercial_inbound_id=mid,comercial_enabled=True,comercial_inbound_text=utterance)
                try:answer=await _tool_loop(ctx,messages,runtime)
                except LlmExhausted:answer='[PRUEBA: modelo sin respuesta válida]' 
                outputs.append(answer);runtimes.append(runtime);messages.append({'role':'assistant','content':answer});await message(answer or '', 'out')
            state=(await fixtures.get(url+'/fixture/state',params={'conversationId':conv})).json()
            errors=[]
            if name=='incidente_lavanderia':
                if len(state['cotizaciones']) != 1:errors.append('creó una cotización no solicitada')
                if len((outputs[-1] or '').split()) < 10:errors.append('respuesta informativa insuficiente')
            if any('[PRUEBA:' in (x or '') for x in outputs):errors.append('modelo agotó reintentos sin respuesta válida')
            joined=' '.join(x or '' for x in outputs)
            if re.search(r'[a-f0-9]{8}-[a-f0-9-]{27,}|comercial_[a-z_]+|evidencia_aceptacion_invalida',joined,re.I):errors.append('detalle interno visible')
            if name!='humano' and any(x.handoff_reason for x in runtimes):errors.append('transferencia no solicitada')
            if name=='humano' and not runtimes[-1].handoff_reason:errors.append('no ejecutó transferencia explícita')
            if name in {'recomendar','precio','mixta_fallo','barberia_exacta'}:
                if not re.search(r'(?:2,?490)(?:\.00)?',outputs[-1] or '') or re.search(r'249,?000',outputs[-1] or ''):errors.append('precio MXN incorrecto o ausente')
                if not re.search(r'servici|inclu|p[aá]gina|whatsapp',outputs[-1] or '',re.I):errors.append('sin explicación de alcance')
                if len((outputs[-1] or '').split())<35:errors.append('respuesta comercial insuficiente')
            if name in {'consentimiento','barberia_exacta'}:
                if not state['cotizaciones'] or not state['cotizaciones'][0]['consentimiento']['registrado']:errors.append('consentimiento no persistido')
                if len(state['cotizaciones'])!=1:errors.append('duplicó cotización')
            if fail and any(q['consentimiento']['registrado'] for q in state['cotizaciones']):errors.append('falso consentimiento')
            if fail and not re.search(r'no.{0,25}(?:confirm|registr|autoriz)',joined,re.I):errors.append('no explicó fallo de registro')
            if name.startswith('personalizada') and not any(c['name']=='comercial_solicitar_aprobacion' for c in tracked.choices):errors.append('no solicitó revisión interna mediante herramienta')
            if name.startswith('personalizada') and (not state['cotizaciones'] or state['cotizaciones'][0]['estado']!='pendiente' or not any(c['name']=='comercial_preparar' for c in tracked.choices)):errors.append('no registró solicitud personalizada pendiente')
            if name=='incidente_multi':
                if len(state['cotizaciones'])!=2:errors.append('mezcla de proyectos o duplicación')
                nuevas=[q for q in state['cotizaciones'] if q['id']!=ids['cotizacionId']]
                if not nuevas or not nuevas[0]['consentimiento']['registrado']:errors.append('consentimiento del origen no registrado')
                if not re.search(r'orientativ|revisi|aprob',outputs[-1] or '',re.I) or len((outputs[-1] or '').split())<35:errors.append('respuesta multiintención insuficiente')
            if name=='informativa_web' and state['cotizaciones']:errors.append('consulta informativa creó cotización')
            if name in {'hosting_solo','inclusion_especial','paquete_incluido'} and (not state['cotizaciones'] or not any(c['name']=='comercial_preparar' for c in tracked.choices)):errors.append('no registró propuesta')
            if name=='hosting_solo' and state['cotizaciones']:
                snap=state['cotizaciones'][0]['snapshot']
                if any(c['productoId']=='web' for c in snap['conceptos']):errors.append('exigió página web')
            if name=='paquete_incluido' and state['cotizaciones']:
                snap=state['cotizaciones'][0]['snapshot']
                if any(c['productoId']=='extra-12' for c in snap['conceptos']):errors.append('duplicó inclusión')
            if name=='inclusion_especial' and re.search(r'(?:gratis|sin costo|sin cargo)',joined,re.I) and not re.search(r'(?:no|sin prometer|sujeto|pendiente)',joined,re.I):errors.append('prometió gratuidad')
            if name=='contratar' and (not state['pedidos'] or state['pedidos'][0]['estado']!='pendiente'):errors.append('sin pedido pendiente')
            if name=='segunda' and len(state['cotizaciones'])!=2:errors.append('segunda negociación no registrada')
            if name=='segunda' and (re.search(r'\$1,?490',joined) or not re.search(r'1,?499',joined)):errors.append('precio de web alterado o ausente')
            if name=='aprobada' and re.search(r'precio.{0,60}(?:orientativ|no definitivo)',joined,re.I):errors.append('importe aprobado presentado como pendiente')
            if name=='pendiente' and any(q['estado']!='pendiente' for q in state['cotizaciones']):errors.append('inventó aprobación')
            if name=='barberia_exacta' and not any(c['name']=='comercial_preparar' for c in tracked.choices):errors.append('no eligió preparación autónoma')
            if name=='contratar' and not all(any(c['name']==tool for c in tracked.choices) for tool in ['comercial_aceptar','comercial_contratar']):errors.append('no eligió cierre autónomo')
            result={'caso':name,'model':model,'configuracion':'propuesta canónica local, perfil productivo no recuperado; salida limitada a 2048 tokens' ,'messages':messages,'answers':outputs,'choices':tracked.choices,'trace':crm.trace,'state':state,'errors':errors};results.append(result)
            print(json.dumps({'caso':name,'resultado':'OK' if not errors else 'FALLO','errors':errors,'tools':[c['name'] for c in tracked.choices]},ensure_ascii=False),flush=True)
            EVIDENCE.mkdir(parents=True,exist_ok=True);(EVIDENCE/('modelo-real-'+model.replace('/','-')+'-resultados.json')).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            await crm.aclose()
        await scenario('incidente_lavanderia','Es un proyecto nuevo, distinto al taller de bicicletas: tengo una lavandería. ¿Qué página web recomiendas, cuánto cuesta y qué incluye? Solo quiero información, no deseo una cotización.',estado='pendiente')
        await scenario('incidente_multi','Quiero consultar un proyecto nuevo, diferente de los anteriores: tengo un taller y necesito mostrar servicios y precios. Solo información por ahora.',estado='pendiente',history=['Me gustaría que prepares una cotización para una página web. También autorizo que me avises por este WhatsApp cuando esté aprobada. ¿El precio de $1,499 es definitivo? ¿Cómo contrato?'])
        await scenario('personalizada','Quiero cotizar una herramienta personalizada para organizar los turnos de mi taller, no necesito página web. No conozco el precio; prepara mi propuesta para revisión.')
        await scenario('personalizada_2','Quiero cotizar una herramienta personalizada para organizar los turnos de mi taller, no necesito página web. No conozco el precio; prepara mi propuesta para revisión.')
        await scenario('personalizada_3','Quiero cotizar una herramienta personalizada para organizar los turnos de mi taller, no necesito página web. No conozco el precio; prepara mi propuesta para revisión.')
        await scenario('informativa_web','Solo quiero información: ¿cuánto cuesta una página web y qué incluye? No prepares cotización todavía.')
        await scenario('hosting_solo','Quiero cotizar hosting básico independiente para un sitio que ya tengo. No quiero una página nueva.')
        await scenario('inclusion_especial','Quiero cotizar una página web y evaluar incluir una sexta sección en el precio, sin asumir que sea gratis.')
        await scenario('paquete_incluido','Quiero cotizar la página web con el dominio y hosting incluidos durante el primer año, sin cobrar esos servicios otra vez.')
        await scenario('recomendar','Tengo una barbería. Quiero mostrar mis servicios, precios y recibir solicitudes de citas por WhatsApp. ¿Qué me recomiendas, cuánto cuesta y cómo contratar?')
        await scenario('precio','¿Cuánto cuesta Servicios y solicitudes de citas, qué incluye y cómo se contrata?')
        await scenario('consentimiento','Sí, avísame','pendiente')
        await scenario('consentimiento_fallo','Sí, avísame','pendiente',True)
        await scenario('pendiente','¿Ya está aprobada mi propuesta?','pendiente')
        await scenario('aprobada','¿Qué condiciones aprobaron y cuál es el siguiente paso?','aprobada')
        await scenario('contratar','Sí, acepto expresamente la cotización aprobada por $2,490 MXN. Registra mi contratación.','aprobada')
        await scenario('segunda','Quiero otra cotización para un proyecto nuevo y distinto: una página web para una cafetería. Ya tengo textos y fotos.','pendiente')
        await scenario('mixta_fallo','Sí, avísame cuando se apruebe, pero antes explícame el servicio, precio, alcance y contratación.','pendiente',True)
        await scenario('humano','Quiero hablar con una persona del equipo, por favor.')
        await scenario('barberia_exacta','Hola, quiero cotizar un proyecto nuevo: tengo una barbería, mostraré servicios y precios, y recibiré solicitudes por WhatsApp que yo confirmaré. ¿Qué recomiendas, cuánto cuesta y cómo contratar?',history=['Sí, avísame por este WhatsApp cuando se apruebe esta cotización. Pero antes explícame el servicio, precio, alcance y contratación.'])
    await llm._client.close()
    assert not any(r['errors'] for r in results), 'Consulta evidencia local de los casos fallidos.'

asyncio.run(main())

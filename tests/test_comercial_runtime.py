from types import SimpleNamespace
from zoneinfo import ZoneInfo
import httpx
from app.tools import ToolRuntime
from app.state import Conversation
from app.crm import CrmClient
from app.profile import BusinessProfile
from app.prompt import build_system_prompt


async def test_consulta_comercial_no_envia_solo_emoji_tras_reparaciones(respx_mock):
    from app.turn import _tool_loop
    crm = CrmClient("http://crm.test", "synthetic-commercial-key")
    respx_mock.get("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200, json={"cotizaciones": []}))
    class Llm:
        calls = 0
        async def complete(self, messages, tools):
            self.calls += 1
            return SimpleNamespace(content="🚲", tool_calls=[])
    ctx = SimpleNamespace(crm=crm, llm=Llm(), agenda_enabled=False)
    runtime = ToolRuntime(ctx, Conversation(1, "fixture"), "trusted", tz=ZoneInfo("UTC"),
                          dispatch_id="d", comercial_inbound_id="m", comercial_enabled=True,
                          comercial_inbound_text="Para mi lavandería, ¿qué página recomiendas y cuánto cuesta?")
    result = await _tool_loop(ctx, [], runtime)
    assert result.startswith("No pude preparar una respuesta clara")
    assert ctx.llm.calls == 3
    assert runtime.handoff_reason is None
    runtime_corto = ToolRuntime(ctx, Conversation(2, "fixture"), "trusted", tz=ZoneInfo("UTC"),
                                dispatch_id="d2", comercial_inbound_id="m2", comercial_enabled=True,
                                comercial_inbound_text="Hola")
    assert await _tool_loop(ctx, [], runtime_corto) == "🚲"
    await crm.aclose()


async def test_no_crea_cotizacion_cuando_cliente_la_rechaza(respx_mock):
    from app.comercial_v1 import ComercialTools
    crm = CrmClient("http://crm.test", "synthetic-commercial-key")
    route = respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200, json={}))
    tools = ComercialTools(crm, "conv", "dispatch", "message", enabled=True,
                           inbound_text="Solo quiero información. No deseo una cotización.")
    result = await tools.execute("comercial_preparar", {"conceptos": []}, "call")
    assert result == {"ok": False, "error": "cotizacion_no_solicitada"}
    assert not route.called
    assert not any('solicitó una cotización' in item for item in tools.observaciones("Te explico el servicio."))
    tools.inbound_text = "Sí, deseo una cotización para mi lavandería."
    assert not tools._rechaza_cotizacion()
    await crm.aclose()


def test_intencion_confirmada_y_extra_pendiente_no_bloquean_propuesta():
    prompt=build_system_prompt(profile=BusinessProfile(),context=None,
        conv=Conversation(1,'cafeteria'),agenda=False,comercial=True)
    assert 'sin pedir de nuevo esa intención' in prompt
    assert 'Para una solicitud de información no crees una propuesta' in prompt
    assert 'Un extra sin tarifa se registra pendiente' in prompt
    assert 'confirma contratación únicamente con pedido real' in prompt


async def test_aviso_inyecta_solo_mensaje_del_turno(respx_mock):
    import json
    crm = CrmClient("http://crm.test", "synthetic-commercial-key")
    route = respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"cotizacionId":"q","version":1,"estado":"pendiente","consentimiento":{"registrado":True,"preguntaPendiente":False}}))
    runtime = ToolRuntime(SimpleNamespace(crm=crm),Conversation(1,"fixture"),"trusted",
        tz=ZoneInfo("UTC"),dispatch_id="dispatch",comercial_inbound_id="consent-real",comercial_enabled=True)
    assert (await runtime.execute_comercial("comercial_solicitar_aprobacion",{"cotizacionId":"q","version":1,"avisar":True},"consent"))["ok"]
    body=json.loads(route.calls[0].request.content)
    assert runtime._comercial.respuesta_verificada("Aceptación del aviso registrada")=="Aceptación del aviso registrada"
    assert body["mensajeAvisoId"]=="consent-real"
    assert "avisar" not in body
    assert not (await runtime.execute_comercial("comercial_solicitar_aprobacion",{"cotizacionId":"q","version":1,"mensajeAvisoId":"otro"},"spoof"))["ok"]
    await crm.aclose()


def test_sin_agenda_comercial_no_impone_handoff_al_comprar():
    kwargs = dict(profile=BusinessProfile(), context=None, conv=Conversation(1, "fixture"), agenda=False)
    actual = build_system_prompt(**kwargs, comercial=True)
    anterior = build_system_prompt(**kwargs)
    assert "cuando el lead quiera avanzar, haz handoff" not in actual
    assert "consulta el catálogo" in actual
    assert "solicita aprobación interna" in actual
    assert "cuando el lead quiera avanzar, haz handoff" in anterior


def test_continuidad_comercial_no_inventa_aceptacion_o_enlace():
    for agenda in (True, False):
        prompt = build_system_prompt(profile=BusinessProfile(), context=None,
            conv=Conversation(1, "fixture"), agenda=agenda, comercial=True)
        assert "Solo ante aceptación explícita" in prompt
        assert "únicamente un enlace autorizado disponible" in prompt
        assert "No ofrezcas un asesor" in prompt
    otro = build_system_prompt(profile=BusinessProfile(), context=None,
        conv=Conversation(1, "fixture"), comercial=False)
    assert "ORQUESTACIÓN COMERCIAL" not in otro


async def test_registro_opcional_y_contexto_fijado(respx_mock):
    crm = CrmClient("http://crm.test", "synthetic-commercial-key")
    ctx = SimpleNamespace(crm=crm)
    conv = Conversation(1,"identidad-sintetica")
    off = ToolRuntime(ctx,conv,"trusted",tz=ZoneInfo("UTC"))
    assert off.comercial_tools() == []
    assert not (await off.execute_comercial("comercial_catalogo",{},"call"))["ok"]
    on = ToolRuntime(ctx,conv,"trusted",tz=ZoneInfo("UTC"),dispatch_id="dispatch",
                     comercial_inbound_id="inbound",comercial_enabled=True)
    assert len(on.comercial_tools()) == 8
    route = respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"cotizacionId":"q","version":1,"estado":"aceptada"}))
    for _ in range(2):
        assert (await on.execute_comercial("comercial_aceptar",{"cotizacionId":"q","version":1},"call"))["ok"]
    import json
    bodies=[json.loads(call.request.content) for call in route.calls]
    assert bodies[0] == bodies[1]
    assert bodies[0]["conversationId"] == "trusted"
    assert bodies[0]["mensajeId"] == "inbound"
    assert not (await on.execute_comercial("comercial_aceptar",{"cotizacionId":"q","version":1,"conversationId":"other"},"call"))["ok"]
    assert on.handoff_reason is None
    await crm.aclose()


async def test_sin_despacho_o_mensaje_no_hay_herramientas():
    ctx=SimpleNamespace(crm=object())
    for dispatch_id,inbound_id in [(None,"message"),("dispatch",None)]:
        runtime=ToolRuntime(ctx,Conversation(1,"test"),"trusted",tz=ZoneInfo("UTC"),
                            dispatch_id=dispatch_id,comercial_inbound_id=inbound_id,comercial_enabled=True)
        assert runtime.comercial_tools() == []


async def test_loop_registra_y_ejecuta_herramienta_con_id_real(respx_mock):
    from app.turn import _tool_loop
    from hashlib import sha256
    import json
    crm=CrmClient("http://crm.test","synthetic-commercial-key")
    route=respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"cotizacionId":"q","version":1,"estado":"pendiente","consentimiento":{"registrado":True,"preguntaPendiente":False}}))
    class Llm:
        calls=0
        async def complete(self,messages,tools):
            assert any(t["function"]["name"]=="comercial_preparar" for t in tools)
            self.calls+=1
            return SimpleNamespace(content="Propuesta pendiente" if self.calls>1 else None,
                tool_calls=[] if self.calls>1 else [SimpleNamespace(id="call-7",name="comercial_preparar",arguments={"conceptos":[{"productoId":"fixture","cantidad":1}]})])
    ctx=SimpleNamespace(crm=crm,llm=Llm(),agenda_enabled=False)
    runtime=ToolRuntime(ctx,Conversation(1,"test"),"trusted",tz=ZoneInfo("UTC"),
        dispatch_id="dispatch",comercial_inbound_id="inbound",comercial_enabled=True)
    assert await _tool_loop(ctx,[],runtime)=="Propuesta pendiente"
    assert json.loads(route.calls[0].request.content)["requestKey"]==sha256(b"dispatch:call-7").hexdigest()
    assert runtime.handoff_reason is None
    await crm.aclose()

async def test_cierre_valida_recibos_y_no_contrata_tras_error(respx_mock):
    from app.comercial_v1 import ComercialTools
    crm=CrmClient("http://crm.test","synthetic-commercial-key")
    t=ComercialTools(crm,"conv","dispatch","id-interno",enabled=True)
    ref={"cotizacionId":"q","version":2}
    route=respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(422,json={"error":{"code":"evidencia_aceptacion_invalida"}}))
    assert not (await t.execute("comercial_contratar",ref,"prematuro"))["ok"]
    assert not route.called
    assert not (await t.execute("comercial_aceptar",ref,"acepta"))["ok"]
    assert not (await t.execute("comercial_contratar",ref,"sin-aceptacion"))["ok"]
    assert "contratación todavía no quedó confirmada" in t.respuesta_verificada("Ya generé tu contratación")
    route.mock(return_value=httpx.Response(200,json={"estado":"aceptada"}))
    assert not (await t.execute("comercial_aceptar",ref,"recibo-incompleto"))["ok"]
    route.mock(return_value=httpx.Response(200,json={**ref,"estado":"aceptada"}))
    assert (await t.execute("comercial_aceptar",ref,"acepta-valida"))["ok"]
    route.mock(return_value=httpx.Response(200,json={"pedido":{}}))
    assert not (await t.execute("comercial_contratar",ref,"sin-pedido"))["ok"]
    assert "contratación todavía no quedó confirmada" in t.respuesta_verificada("Tu pedido está listo")
    route.mock(return_value=httpx.Response(200,json={"pedido":{"id":"pedido-real","cotizacion_id":"q","version":2,"estado":"pendiente"}}))
    assert (await t.execute("comercial_contratar",ref,"pedido"))["ok"]
    assert t.pedido["id"]=="pedido-real"
    assert "pago todavía no está confirmado" in t.respuesta_verificada("Pagado")
    await crm.aclose()

async def test_loop_no_anuncia_exito_ante_error_o_sin_operacion(respx_mock):
    from app.turn import _tool_loop
    crm=CrmClient("http://crm.test","synthetic-commercial-key")
    respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(422,json={"error":{"code":"evidencia_aceptacion_invalida"}}))
    class Llm:
        calls=0
        async def complete(self,messages,tools):
            self.calls+=1
            return SimpleNamespace(content="He registrado la aceptación y generado tu contratación",tool_calls=[] if self.calls>1 else [SimpleNamespace(id="aceptar",name="comercial_aceptar",arguments={"cotizacionId":"q","version":2})])
    ctx=SimpleNamespace(crm=crm,llm=Llm(),agenda_enabled=False)
    runtime=ToolRuntime(ctx,Conversation(1,"test"),"trusted",tz=ZoneInfo("UTC"),dispatch_id="d",comercial_inbound_id="id-interno",comercial_enabled=True)
    assert "contratación todavía no quedó confirmada" in await _tool_loop(ctx,[],runtime)
    ctx.llm.calls=1
    runtime=ToolRuntime(ctx,Conversation(1,"test"),"trusted",tz=ZoneInfo("UTC"),dispatch_id="d2",comercial_inbound_id="id2",comercial_enabled=True)
    assert "contratación todavía no quedó confirmada" in await _tool_loop(ctx,[],runtime)
    await crm.aclose()

async def test_retomar_aceptacion_persistida_y_consultar_pedido(respx_mock):
    from app.comercial_v1 import ComercialTools
    from app.dispatch import mensajes_del_despacho
    inbound=mensajes_del_despacho({"contact":{"identity":"fixture"},"conversation":{"channel":"whatsapp"},"messages":[{"id":"id-interno","type":"text","text":"Sí"}]})
    assert inbound[0].wa_message_id=="id-interno"
    crm=CrmClient("http://crm.test","synthetic-commercial-key")
    t=ComercialTools(crm,"conv","dispatch",inbound[0].wa_message_id,enabled=True)
    respx_mock.get("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"cotizaciones":[{"id":"q","version":2,"aprobada_version":2,"estado":"aceptada","accepted_message_id":"id-interno"}],"pedidos":[]}))
    assert (await t.execute("comercial_consultar_aprobacion",{"cotizacionId":"q"},"consultar"))["ok"]
    respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"pedido":{"id":"pedido","cotizacion_id":"q","version":2,"estado":"pendiente"}}))
    assert (await t.execute("comercial_contratar",{"cotizacionId":"q","version":2},"contratar"))["ok"]
    assert "pago todavía no está confirmado" in t.respuesta_verificada("Contratación registrada y pagada")
    await crm.aclose()

async def test_consentimiento_rechazado_no_inventa_registro_ni_crea_propuesta(respx_mock):
    from app.comercial_v1 import ComercialTools
    crm=CrmClient("http://crm.test","synthetic-commercial-key")
    respx_mock.get("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"negociacionActiva":"q","cotizaciones":[{"id":"q","version":1,"estado":"pendiente","consentimiento":{"registrado":False,"preguntaPendiente":True},"preguntaAvisoActual":True}]}))
    route=respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(422,json={"error":{"code":"consentimiento_no_acreditado"}}))
    tools=ComercialTools(crm,"conv","d","mensaje-si",enabled=True,inbound_text="Sí, avísame")
    assert await tools.iniciar([]) is None
    assert not route.called
    assert not (await tools.execute('comercial_solicitar_aprobacion',{'cotizacionId':'q','version':1,'avisar':True},'aviso'))['ok']
    text=tools.respuesta_verificada('Servicios y Citas cuesta $2,490 como referencia. El aviso está registrado.')
    assert '$2,490' in text and 'todavía no quedó autorizado' in text
    import json
    assert len(route.calls)==1 and json.loads(route.calls[0].request.content)['action']=='solicitar_aprobacion'
    assert json.loads(route.calls[0].request.content)['mensajeAvisoId']=='mensaje-si'
    await crm.aclose()

async def test_respuestas_incompletas_no_confirman_cotizacion_ni_consentimiento(respx_mock):
    from app.comercial_v1 import ComercialTools
    crm=CrmClient("http://crm.test","synthetic-commercial-key")
    route=respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"cotizacionId":"q","version":1,"estado":"pendiente"}))
    t=ComercialTools(crm,"conv","d","m",enabled=True)
    assert not (await t.execute('comercial_preparar',{'conceptos':[{'productoId':'p','cantidad':1}]},'crear'))['ok']
    assert 'propuesta todavía no quedó registrada' in t.respuesta_verificada('He registrado tu cotización')
    route.mock(return_value=httpx.Response(200,json={"cotizacionId":"q","version":1,"estado":"pendiente","consentimiento":{"registrado":False,"preguntaPendiente":True}}))
    assert not (await t.execute('comercial_solicitar_aprobacion',{'cotizacionId':'q','version':1,'avisar':True},'consentir'))['ok']
    assert 'aviso todavía no quedó confirmado' in t.respuesta_verificada('Ya registré tu aviso')
    await crm.aclose()

def test_importes_para_el_modelo_son_mxn_sin_alterar_centavos():
    from app.comercial_v1 import presentacion_mxn
    p=presentacion_mxn({'productos':[{'unico':249000,'mensual':None}],'descuento':199950,'ajusteUnico':-100})
    assert p['productos'][0]['unico']==249000
    assert p['productos'][0]['importesMXN']=={'unico':'$2,490.00 MXN','mensual':'Pendiente de cotizar'}
    assert p['importesMXN']['descuento']=='$1,999.50 MXN'
    assert p['importesMXN']['ajusteUnico']=='-$1.00 MXN'

def test_un_fallo_no_sustituye_la_explicacion_comercial():
    from app.comercial_v1 import ComercialTools
    t=ComercialTools(object(),'conv','d','m',enabled=True)
    t.operacion='solicitar_aprobacion';t.error_operacion=True
    answer=t.respuesta_verificada('El servicio cuesta $2,490 MXN e incluye hasta 6 servicios. Tu aviso quedó registrado.')
    assert '$2,490' in answer and 'hasta 6 servicios' in answer
    assert 'todavía no quedó confirmado' in answer
    assert 'quedó registrado' not in answer
    assert 'no está confirmado' in t.respuesta_verificada('La propuesta está pendiente, pero ya está pagada.')

def test_descripcion_de_servicios_no_es_una_afirmacion_de_registro():
    from app.comercial_v1 import ComercialTools
    t=ComercialTools(object(),'conv','d','m',enabled=True)
    texto='Servicios y solicitudes de citas está pensado para mostrar tus precios y recibir solicitudes por WhatsApp.'
    assert t.respuesta_verificada(texto)==texto

def test_precio_no_autorizado_no_se_presenta_como_tarifa():
    from app.comercial_v1 import ComercialTools
    t=ComercialTools(object(),'conv','d','m',enabled=True)
    t.importes_confirmados={149900,249000,0}
    assert t.importes_no_confirmados('La web cuesta $1,490 MXN')==['$1,490']
    assert 'importesMXN' in ' '.join(t.observaciones('La web cuesta $1,490 MXN'))
    assert '$1,490' not in t.respuesta_verificada('La web cuesta $1,490 MXN')
    assert t.respuesta_verificada('La web cuesta $1,499.00 MXN')=='La web cuesta $1,499.00 MXN'

async def test_propuesta_personalizada_sin_producto_preserva_solicitud(respx_mock):
    import json
    crm = CrmClient("http://crm.test", "synthetic-commercial-key")
    route = respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"cotizacionId":"q","version":1,"estado":"pendiente","consentimiento":{"registrado":False,"preguntaPendiente":False}}))
    runtime=ToolRuntime(SimpleNamespace(crm=crm),Conversation(1,"fixture"),"trusted",tz=ZoneInfo("UTC"),dispatch_id="dispatch",comercial_inbound_id="mensaje",comercial_enabled=True)
    resultado=await runtime.execute_comercial("comercial_preparar",{"conceptos":[],"pendientes":[{"descripcion":"A medida","solicitud":"Evaluar alcance"}]},"call")
    assert resultado["ok"]
    propuesta=json.loads(route.calls[0].request.content)["propuesta"]
    assert propuesta == {"conceptos":[],"pendientes":[{"descripcion":"A medida","solicitud":"Evaluar alcance"}]}
    await crm.aclose()

def test_propuesta_personalizada_pendiente_solicita_revision_interna():
    from app.comercial_v1 import ComercialTools
    t=ComercialTools(object(),'conv','d','m',enabled=True)
    t.actual={'cotizacionId':'q','version':1,'estado':'pendiente'}
    t.operacion='preparar'
    assert any('comercial_solicitar_aprobacion' in x for x in t.observaciones('Tu propuesta sigue en revisión.'))
    t.operacion='solicitar_aprobacion'
    assert not any('comercial_solicitar_aprobacion' in x for x in t.observaciones('Tu propuesta sigue en revisión.'))

def test_formato_comercial_conserva_parrafos_y_corrige_solo_afirmacion_falsa():
    from app.comercial_v1 import ComercialTools
    from app.formato import a_whatsapp
    t=ComercialTools(object(), 'conv', 'dispatch', 'message', enabled=True)
    t.operacion='solicitar_aprobacion'; t.error_operacion=True
    original='## Tu propuesta\n\n**Precio:** $2,490 MXN\n- Hasta seis servicios\n- Citas por WhatsApp\n\n¿Incluye dominio? ¿Cómo contrato?\n\nTu aviso quedó registrado.'
    procesado=t.respuesta_verificada(original)
    assert procesado.startswith('## Tu propuesta\n\n**Precio:** $2,490 MXN\n- Hasta seis servicios\n- Citas por WhatsApp\n\n')
    assert '¿Incluye dominio? ¿Cómo contrato?' in procesado
    assert 'aviso todavía no quedó confirmado' in procesado
    enviado=a_whatsapp(procesado)
    assert '*Tu propuesta*\n\n*Precio:* $2,490 MXN\n- Hasta seis servicios' in enviado
    assert '**' not in enviado


def test_respuesta_comercial_breve_y_separadores_no_cambian():
    from app.comercial_v1 import ComercialTools
    t=ComercialTools(object(), 'conv', 'dispatch', 'message', enabled=True)
    for texto in ['Claro, puedo ayudarte.', 'Precio: $2,490; incluye hosting.\n\n¿Te explico el alcance?']:
        assert t.respuesta_verificada(texto)==texto


def test_error_aviso_no_borra_precio_y_contratacion_en_parrafos_markdown():
    from app.comercial_v1 import ComercialTools
    t=ComercialTools(object(),'conv','d','m',enabled=True)
    t.actual={'cotizacionId':'q','version':1,'estado':'pendiente'}
    t.operacion='solicitar_aprobacion';t.error_operacion=True
    original='**Propuesta registrada**\nPrecio orientativo: $1,499 MXN.\n\n**Cómo contratar**\nPrimero revisión y aprobación, después tu aceptación y el pedido con pago pendiente.\n\nTu aviso quedó registrado.'
    salida=t.respuesta_verificada(original)
    assert '$1,499' in salida and 'Cómo contratar' in salida and 'aceptación' in salida
    assert 'Tu aviso quedó registrado' not in salida

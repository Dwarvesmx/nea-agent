from types import SimpleNamespace
from zoneinfo import ZoneInfo
import httpx
from app.tools import ToolRuntime
from app.state import Conversation
from app.crm import CrmClient
from app.profile import BusinessProfile
from app.prompt import build_system_prompt


def test_intencion_confirmada_y_extra_pendiente_no_bloquean_propuesta():
    prompt=build_system_prompt(profile=BusinessProfile(),context=None,
        conv=Conversation(1,'cafeteria'),agenda=False,comercial=True)
    assert 'no vuelvas a preguntar si quiere avanzar' in prompt
    assert 'ejecuta comercial_preparar en este turno' in prompt
    assert 'en pendientes para revisión interna' in prompt
    assert 'cotizacionId real' in prompt


async def test_aviso_inyecta_solo_mensaje_del_turno(respx_mock):
    import json
    crm = CrmClient("http://crm.test", "synthetic-commercial-key")
    route = respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"estado":"pendiente"}))
    runtime = ToolRuntime(SimpleNamespace(crm=crm),Conversation(1,"fixture"),"trusted",
        tz=ZoneInfo("UTC"),dispatch_id="dispatch",comercial_inbound_id="consent-real",comercial_enabled=True)
    assert (await runtime.execute_comercial("comercial_solicitar_aprobacion",{"cotizacionId":"q","version":1,"avisar":True},"consent"))["ok"]
    body=json.loads(route.calls[0].request.content)
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
    assert "CONTINUIDAD COMERCIAL" not in otro


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
    route=respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"cotizacionId":"q","version":1,"estado":"pendiente"}))
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
    assert "No se completó" in t.respuesta_verificada("Ya generé tu contratación")
    route.mock(return_value=httpx.Response(200,json={"estado":"aceptada"}))
    assert not (await t.execute("comercial_aceptar",ref,"recibo-incompleto"))["ok"]
    route.mock(return_value=httpx.Response(200,json={**ref,"estado":"aceptada"}))
    assert (await t.execute("comercial_aceptar",ref,"acepta-valida"))["ok"]
    route.mock(return_value=httpx.Response(200,json={"pedido":{}}))
    assert not (await t.execute("comercial_contratar",ref,"sin-pedido"))["ok"]
    assert "No se completó" in t.respuesta_verificada("Tu pedido está listo")
    route.mock(return_value=httpx.Response(200,json={"pedido":{"id":"pedido-real","cotizacion_id":"q","version":2,"estado":"pendiente"}}))
    assert (await t.execute("comercial_contratar",ref,"pedido"))["ok"]
    assert "pedido-real" in t.respuesta_verificada("Pagado")
    assert "pago permanece pendiente" in t.respuesta_verificada("Pagado")
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
    assert "No se completó" in await _tool_loop(ctx,[],runtime)
    ctx.llm.calls=1
    runtime=ToolRuntime(ctx,Conversation(1,"test"),"trusted",tz=ZoneInfo("UTC"),dispatch_id="d2",comercial_inbound_id="id2",comercial_enabled=True)
    assert "No tengo confirmación" in await _tool_loop(ctx,[],runtime)
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
    assert "pago permanece pendiente" in t.respuesta_verificada("Contratación registrada y pagada")
    await crm.aclose()

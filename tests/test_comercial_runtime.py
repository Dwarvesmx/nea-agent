from types import SimpleNamespace
from zoneinfo import ZoneInfo
import httpx
from app.tools import ToolRuntime
from app.state import Conversation
from app.crm import CrmClient
from app.profile import BusinessProfile
from app.prompt import build_system_prompt


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
    route = respx_mock.post("http://crm.test/api/bot/comercial").mock(return_value=httpx.Response(200,json={"estado":"aceptada"}))
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

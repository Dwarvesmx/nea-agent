"""Tres recorridos por ToolRuntime real y CRM HTTP real; solo laboratorio existente."""
import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo
import asyncpg
import httpx
from app.crm import CrmClient
from app.state import Conversation
from app.tools import ToolRuntime


async def main():
    url=urlparse(os.environ.get("DATABASE_URL",""))
    if os.environ.get("COMERCIAL_TEST")!="1" or url.hostname!="127.0.0.1" or url.port!=55546 or url.path!="/comercial_ui":
        raise RuntimeError("Solo laboratorio aislado")
    db=await asyncpg.connect(os.environ["DATABASE_URL"])
    await db.execute("set time zone 'UTC'")
    directory=await db.fetchval("select current_setting('data_directory')")
    assert "/tmp/comercial-pg-" in directory.replace("\\","/")
    base="http://127.0.0.1:55547"
    org="comercial-tool-org"
    await db.execute("insert into organization(id,name) values($1,'Herramientas sintéticas')",org)
    async with httpx.AsyncClient(base_url=base,headers={"origin":base},timeout=30) as admin:
        signup=await admin.post("/api/auth/sign-up/email",json={"name":"Operador de prueba","email":"tools@example.invalid","password":"synthetic-commercial-only"})
        assert signup.status_code==200,signup.status_code
        user=signup.json()["user"]["id"]
        await db.execute("insert into member(id,organization_id,user_id,role) values('tool-member',$1,$2,'owner')",org,user)
        for id in ("a","b"):
            await db.execute("insert into contact(id,organization_id,wa_identity,name) values($1,$2,$1,'Sintético')","tool-contact-"+id,org)
            await db.execute("insert into conversation(id,organization_id,contact_id) values($1,$2,$3)","tool-conv-"+id,org,"tool-contact-"+id)
        async def command(**data):
            r=await admin.post("/api/comercial",json={"requestKey":str(uuid4()),**data})
            assert r.status_code==200,(r.status_code,r.json().get("error"))
            return r.json()
        await command(action="catalogo",producto={"id":"fixture","nombre":"Servicio sintético","descripcion":"Exclusivo del laboratorio","tipo":"producto","caracteristicas":["Una entrega"],"exclusiones":["Sin recurrencia"],"unico":149900,"mensual":0,"precioEstado":"aprobado","limites":{},"compatibles":[],"activo":True,"condiciones":"Condiciones sintéticas"})
        crm=CrmClient(base,os.environ["BOT_API_KEY"])
        ctx=SimpleNamespace(crm=crm)
        def runtime(dispatch="dispatch-1",message="accept-1",conv="tool-conv-a"):
            return ToolRuntime(ctx,Conversation(1,"fixture"),conv,tz=ZoneInfo("UTC"),dispatch_id=dispatch,comercial_inbound_id=message,comercial_enabled=True)
        first=runtime()
        assert len(first.comercial_tools())==8
        async def tool(rt,name,args,call):
            result=await rt.execute_comercial(name,args,call)
            assert result.get("ok"),result.get("error")
            return result.get("data",result)
        await tool(first,"comercial_catalogo",{},"catalog")
        q=await tool(first,"comercial_preparar",{"conceptos":[{"productoId":"fixture","cantidad":1}]},"prepare")
        assert q["estado"]=="aprobada"
        assert q==await tool(first,"comercial_preparar",{"conceptos":[{"productoId":"fixture","cantidad":1}]},"prepare")
        ref={"cotizacionId":q["cotizacionId"],"version":q["version"]}
        await db.execute("insert into message(id,organization_id,conversation_id,direction,text) values('accept-1',$1,'tool-conv-a','in','Acepto esta versión')",org)
        next_turn=runtime("dispatch-2")
        await tool(next_turn,"comercial_aceptar",ref,"accept")
        p=await tool(next_turn,"comercial_contratar",ref,"contract")
        assert p["pedido"]["estado"]=="pendiente"
        print("PRUEBA 1: runtime real -> catálogo -> cotización aprobada -> aceptación -> contratación pendiente; reintento idempotente OK")
        special=await tool(first,"comercial_preparar",{"conceptos":[{"productoId":"fixture","cantidad":1}],"especial":"Alcance especial sintético"},"special")
        sr={"cotizacionId":special["cotizacionId"],"version":1}
        assert special["estado"]=="pendiente"
        await tool(first,"comercial_solicitar_aprobacion",sr,"request")
        await command(action="decidir",conversationId="tool-conv-a",**sr,decision="aprobar",motivo="Administrador revisó alcance sintético")
        resumed=runtime("dispatch-3","other-message")
        decision=await tool(resumed,"comercial_consultar_aprobacion",{"cotizacionId":sr["cotizacionId"]},"recover")
        assert decision["cotizaciones"][0]["estado"]=="aprobada"
        assert resumed.handoff_reason is None
        print("PRUEBA 2: propuesta especial -> solicitud -> aprobación administrativa real -> recuperación en otro turno sin handoff OK")
        assert not (await first.execute_comercial("comercial_preparar",{"conceptos":[{"productoId":"fixture","cantidad":1}],"importeUnico":1},"attack"))["ok"]
        for action,data in [("catalogo",{"producto":(await admin.get("/api/comercial")).json()["productos"][0]}),("decidir",{"conversationId":"tool-conv-a",**sr,"decision":"aprobar","motivo":"No autorizado"}),("pago",{"conversationId":"tool-conv-a",**ref,"estado":"pagado","evidencia":"No autorizada"})]:
            r=await crm._request("POST","/api/bot/comercial",json={"action":action,"requestKey":str(uuid4()),**data})
            assert r.status_code==403,(action,r.status_code)
        other=await runtime(conv="tool-conv-b").execute_comercial("comercial_consultar_aprobacion",{"cotizacionId":q["cotizacionId"]},"other")
        assert not other["ok"]
        assert await db.fetchval("select count(*) from comercial_pedido where estado='pagado'")==0
        print("PRUEBA 3: precios, autoaprobación y pago sin permiso rechazados por CRM real; cliente ajeno rechazado; cero cobros OK")
        await crm.aclose()
    await db.close()


asyncio.run(main())

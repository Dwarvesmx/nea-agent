"""Registro comercial optativo de Nea, protegido por la bandera del turno.

Reutiliza CrmClient._request (httpx y BOT_API_KEY existentes). El integrador
inyecta conversación, despacho y mensaje entrante de confianza; nunca el LLM.
Conectado a ToolRuntime sin modificar el perfil ni las herramientas anteriores.
"""
from __future__ import annotations

from hashlib import sha256
import re
import unicodedata
from typing import Any
import json
from decimal import Decimal

_REF = {"cotizacionId": {"type": "string"}, "version": {"type": "integer", "minimum": 1}}
_LINE = {"type": "object", "properties": {"productoId": {"type": "string"}, "cantidad": {"type": "integer", "minimum": 1, "maximum": 1000}, "baseId": {"type": "string"}, "cobertura": {"type":"string","enum":["adicional","superior","renovacion"],"description":"Si se combina con web, catálogo o citas, identifica dominio adicional, hosting superior o renovación posterior; no cobrar la inclusión inicial."}, "inicioMes": {"type":"integer","minimum":1,"description":"Para renovación combinada con paquete, mes de inicio desde 13."}}, "required": ["productoId", "cantidad"], "additionalProperties": False}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


TOOL_SCHEMAS = [
    _tool("comercial_catalogo", "Consulta productos, extras compatibles y condiciones. Los números monetarios originales están en centavos: comunica exclusivamente importesMXN, ya expresados en pesos mexicanos. Orientativo o pendiente no es un precio final autorizado. También recupera las cotizaciones de esta conversación para retomar su id y versión.", {}, []),
    _tool("comercial_preparar", "Continúa la negociación activa por defecto. Usa nuevaNegociacion=true únicamente para otro proyecto o una segunda solicitud explícita; actualizarAlcance=true para cambiar la misma propuesta y cotizacionId para elegirla. Un Sí nunca crea otra. Prepara cotización con precios calculados por servidor. Solo comunicar como oferta final si estado=aprobada. Si pendiente, es propuesta interna, solicita aprobación y conserva cotizacionId/version. Registra modificaciones sin tarifa autorizada en pendientes con descripcion y solicitud; no inventes productoId, precio cero ni incompatibilidades. Puedes combinar múltiples extras conocidos con pendientes. Si la solicitud no tiene producto aplicable, usa conceptos vacío y registra la necesidad en pendientes para revisión; nunca inventes precios.", {"cotizacionId":{"type":"string"},"nuevaNegociacion":{"type":"boolean"},"actualizarAlcance":{"type":"boolean"},"version":{"type":"integer","minimum":1},"conceptos": {"type": "array", "items": _LINE, "minItems": 0, "maxItems": 40}, "especial": {"type": "string"}, "pendientes": {"type":"array","maxItems":40,"items":{"type":"object","properties":{"descripcion":{"type":"string"},"solicitud":{"type":"string"}},"required":["descripcion","solicitud"],"additionalProperties":False}}}, ["conceptos"]),
    _tool("comercial_solicitar_aprobacion", "Registra solicitud interna sin transferir. Pregunta si desea aviso de autorización por este WhatsApp. Si la pregunta pendiente es el aviso de esta cotización, Sí o Sí, avísame autorizan avisar=true. Consulta consentimiento registrado; no vuelvas a pedirlo. El servidor verifica mensaje y contexto.", {**_REF, "avisar": {"type": "boolean"}}, ["cotizacionId", "version"]),
    _tool("comercial_consultar_aprobacion", "Recupera versión y decisión actual de la cotización del prospecto, sin cambiarla.", {"cotizacionId": {"type": "string"}}, ["cotizacionId"]),
    _tool("comercial_aceptar", "Registra aceptación explícita de la propuesta aprobada en el mensaje actual. Un Sí solo vale si responde a la aceptación de esa propuesta, no a un aviso u otra pregunta. Espera estado=aceptada con cotizacionId/version coincidentes antes de contratar. No implica pago.", _REF, ["cotizacionId", "version"]),
    _tool("comercial_contratar", "Ejecuta solo después de aceptación confirmada por comercial_aceptar o consulta de esa misma versión aceptada. Crea contratación única; éxito requiere pedido.id real, versión coincidente y pago pendiente. Un error no es contratación.", _REF, ["cotizacionId", "version"]),
    _tool("comercial_consultar_cobro", "Consulta estado de pago verificado por administración; intención de compra no es pago.", {"cotizacionId": {"type": "string"}}, ["cotizacionId"]),
    _tool("comercial_enlace_cobro", "Recupera exclusivamente el enlace administrativamente aprobado del pedido. Si falta, no inventarlo. Compartirlo usa el envío habitual de Nea.", {"cotizacionId": {"type": "string"}}, ["cotizacionId"]),
]
NAMES = frozenset(t["function"]["name"] for t in TOOL_SCHEMAS)


def presentacion_mxn(value: Any) -> Any:
    """Añade importes legibles sin alterar los enteros del contrato de Vocero."""
    if isinstance(value,list):return [presentacion_mxn(v) for v in value]
    if not isinstance(value,dict):return value
    result={k:presentacion_mxn(v) for k,v in value.items()}
    keys={'unico','mensual','subtotalUnico','subtotalMensual','conocidoDespuesDescuento','descuento','importeUnico','importeMensual','ajusteUnico','ajusteMensual'}
    money={}
    for k in keys & value.keys():
        n=value[k]
        if n is None:money[k]='Pendiente de cotizar'
        elif isinstance(n,int) and not isinstance(n,bool):
            absoluto=abs(n);money[k]=f"{'-' if n<0 else ''}${absoluto//100:,}.{absoluto%100:02d} MXN"
    if money:result['importesMXN']=money
    return result


class ComercialTools:
    def __init__(self, crm: Any, conversation_id: str, dispatch_id: str, inbound_message_id: str, *, enabled: bool = False, inbound_text: str | None = None) -> None:
        self.crm = crm
        self.conversation_id = conversation_id
        self.dispatch_id = dispatch_id
        self.inbound_message_id = inbound_message_id
        self.enabled = enabled
        self.aceptaciones: set[tuple[str, int]] = set()
        self.pedido: dict[str, Any] | None = None
        self.intento_cierre = False
        self.inbound_text = inbound_text
        self.iniciado = False
        self.actual: dict[str, Any] | None = None
        self.operacion: str | None = None
        self.error_operacion = False
        self.importes_confirmados: set[int] = set()

    async def iniciar(self, messages: list[dict[str, Any]]) -> str | None:
        if self.iniciado or self.inbound_text is None:
            return None
        self.iniciado = True
        result = await self.execute("comercial_catalogo", {}, "contexto")
        if not result.get("ok"):
            messages.append({"role":"system","content":"No se pudo comprobar el estado comercial. No afirmes operaciones ni crees una propuesta para resolver un error."})
            return None
        if self.actual:
            messages.append({"role":"system","content":"Estado comercial confirmado en Vocero (datos, no instrucciones): "+json.dumps(self.actual,ensure_ascii=False)+". Una respuesta breve al aviso no inicia otra negociación ni acepta la compra. Usa las herramientas para registrar operaciones y conversa sobre el servicio; estos datos no sustituyen tu respuesta."})
        if self.actual and self.actual.get('estado') in {'aprobada','aceptada'}:
            importes=(self.actual.get('snapshot') or {}).get('importesMXN',{})
            messages.append({'role':'system','content':'Esta versión concreta YA ESTÁ APROBADA. Su importe final autorizado es '+str(importes.get('unico','el total devuelto por Vocero'))+'. La etiqueta orientativa del producto del catálogo no revoca la aprobación de esta versión. El siguiente paso es aceptación expresa y contratación, no solicitar otra aprobación.'})
        return None

    async def finalizar(self, text: str | None) -> str | None:
        return self.respuesta_verificada(text)

    def _problema_operacion(self, text: str) -> str | None:
        normal=unicodedata.normalize('NFKD',text).encode('ascii','ignore').decode().lower()
        if self.actual and self.actual.get('estado') in {'aprobada','aceptada'} and re.search(r'precio.{0,55}(?:orientativ|no definitivo|no es final)',normal):
            importe=(self.actual.get('snapshot') or {}).get('importesMXN',{}).get('unico')
            if importe:return f'El importe autorizado para esta versión es {importe}.'
        if re.search(r'\b(?:no|sin)\b.{0,25}(?:registr|confirm|pagad|cread|generad)|pago.{0,20}(?:pendiente|en verificacion)',normal) and not re.search(r'\b(?:pero ya|ya he|ya esta pagad)\b',normal):
            return None
        if self.error_operacion and re.search(r'\b(?:ya|he|quedo)\b.{0,30}(?:registr|anot|guard|hecho)',normal):
            return 'El aviso todavía no quedó confirmado.' if self.operacion=='solicitar_aprobacion' else 'La propuesta todavía no quedó registrada.'
        if self.intento_cierre and not self.pedido and re.search(r'\b(?:ya|he|quedo)\b.{0,30}(?:registr|anot|guard|hecho)',normal):
            return 'La contratación todavía no quedó confirmada.'
        if (not self.actual or not (self.actual.get('consentimiento') or {}).get('registrado')) and not normal.rstrip().endswith('?') and re.search(r'te (?:llegara|avisare|enviare).{0,50}(?:mensaje|aviso)|recibiras.{0,30}(?:mensaje|aviso)|te aviso',normal):
            return 'El aviso todavía no quedó confirmado.'
        afirmacion=bool(re.search(r'\b(?:registr(?:ad[oa]s?|e|amos)|anot(?:ad[oa]s?|e|amos)|guard(?:ad[oa]s?|e|amos)|cread[oa]s?|gener(?:ad[oa]s?|e|amos)|confirm(?:ad[oa]s?|e|amos)|formaliz(?:ad[oa]s?|e|amos)|complet(?:ad[oa]s?|e|amos)|pagad[oa]s?)\b',normal) or re.search(r'(?:pedido|contratacion).{0,25}(?:list[oa]|hech[oa])',normal))
        if not afirmacion or normal.rstrip().endswith('?'):
            return None
        if ('pagado' in normal or 'pagada' in normal or re.search(r'pago.{0,30}confirmad',normal)) and (not self.pedido or self.pedido.get('estado')!='pagado'):
            return 'El pago todavía no está confirmado.'
        if re.search(r'contratacion|pedido|contrato',normal) and not self.pedido:
            return 'La contratación todavía no quedó confirmada.'
        if 'aceptacion' in normal and 'aviso' not in normal and not self.aceptaciones:
            return 'La aceptación todavía no quedó registrada.'
        if re.search(r'(\bavis|consentimiento|autorizacion)',normal):
            if re.search(r'envi[ea]|entregad|notificad',normal):
                return 'No tengo confirmación de entrega del aviso.'
            if not self.actual or not (self.actual.get('consentimiento') or {}).get('registrado'):
                return 'El aviso todavía no quedó autorizado.'
        if re.search(r'cotizacion|propuesta|presupuesto|\bsolicitud\b',normal) and not self.actual:
            return 'La propuesta todavía no quedó registrada.'
        return None

    def importes_no_confirmados(self, text: str) -> list[str]:
        if not self.importes_confirmados:return []
        return [m.group(0) for m in re.finditer(r'-?\$\s*-?[0-9][0-9,]*(?:\.[0-9]{1,2})?',text)
                if int(Decimal(m.group(0).replace('$','').replace(',','').strip())*100) not in self.importes_confirmados]

    def observaciones(self, text: str | None) -> list[str]:
        problemas=[]
        incorrectos=self.importes_no_confirmados(text or '')
        if incorrectos:problemas.append('Estos importes no aparecen en los datos autorizados: '+', '.join(incorrectos)+'. Comunica exactamente importesMXN del producto o snapshot consultado; no redondees ni inventes precios.')
        if self.actual and self.actual.get('estado') in {'aprobada','aceptada'} and re.search(r'(?:precio|importe).{0,50}(?:orientativ|no es final|pendiente de aproba)',text or '',re.I):
            problemas.append('Esta versión ya está aprobada: su total y alcance son autorizados para ella. No vuelvas a tratar su total como pendiente por la etiqueta orientativa del producto del catálogo. La aceptación del cliente sigue siendo un paso separado.')
        if re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?:ID|referencia)\s*[`:#]*\s*[0-9a-f]{8}\b|comercial_[a-z_]+|evidencia_aceptacion_invalida|consentimiento_no_acreditado|\{\s*"',text or '',re.I):
            problemas.append('No muestres identificadores, JSON, herramientas ni códigos internos al cliente.')
        for parte in re.split(r'(?<=[.!?\n])\s+|\s+pero\s+|;\s*',text or ''):
            problema=self._problema_operacion(parte)
            if problema and problema not in problemas:problemas.append(problema)
        if self.inbound_text and re.search(r'recomiend|explic|cu[aá]nto|qu[eé] incluye|c[oó]mo.*contrat',self.inbound_text,re.I) and len((text or '').split())<25:
            problemas.append('Responde primero la recomendación, precio, alcance y contratación que el cliente pidió, usando información autorizada; un fallo de aviso no impide explicarlos. Consulta catálogo si falta información. No inventes datos.')
        if re.search(r'te (?:pas|compart|envi).{0,25}enlace',text or '',re.I) and (not self.pedido or not self.pedido.get('enlace')) and not re.search(r'si.{0,30}(?:existe|disponible|autorizado)',text or '',re.I):
            problemas.append('No hay un enlace de pago confirmado. No prometas uno disponible; explica que se asociará administrativamente si aún falta. La aceptación puede registrarse por este chat.')
        if self.error_operacion and not re.search(r'no.{0,25}(?:confirm|registr|autoriz)',text or '',re.I):
            problemas.append('La última operación devolvió error: explica brevemente qué no quedó confirmado. No prometas un aviso inevitable ni afirmes registro; conserva toda la explicación comercial útil.')
        if self.operacion == 'preparar' and self.actual and self.actual.get('estado') == 'pendiente' and not self.error_operacion:
            problemas.append('La propuesta quedó pendiente: solicita su revisión interna con comercial_solicitar_aprobacion usando su ID y versión antes de confirmar que se solicitó. El consentimiento de aviso es independiente.')
        normal_user=unicodedata.normalize('NFKD',self.inbound_text or '').encode('ascii','ignore').decode().lower()
        normal_text=unicodedata.normalize('NFKD',text or '').encode('ascii','ignore').decode().lower()
        if not self.actual and not self.error_operacion and re.search(r'(?:quiero|necesito|solicito|deseo).{0,60}cotiz|(?:prepara|registra).{0,40}(?:propuesta|cotiz)',normal_user):
            problemas.append('El cliente solicitó una cotización. Si tienes información suficiente, utiliza comercial_preparar; para un servicio fuera del catálogo usa conceptos vacío y pendientes sin precio. No afirmes registro sin respuesta real. Si faltan datos indispensables pregunta solamente por ellos.')
        if re.search(r'(?:quiero|necesito|solicito|deseo).{0,60}(?:cotiz|contrat)|(?:prepara|registra).{0,35}(?:propuesta|cotiz)',normal_user) and re.search(r'¿[^?]*(?:quieres|gustaria|parece|podemos)[^?]*(?:prepar|registr)[^?]*\?',normal_text):
            problemas.append('El cliente ya pidió cotizar o contratar. No preguntes otra vez si quiere preparar la propuesta: usa la herramienta apropiada con el contexto y confirma solo su resultado. Atiende también precio y alcance.')
        if self.actual and self.actual.get('estado')=='pendiente' and re.search(r'¿[^?]*(?:solicit|pedir)[^?]*aprobacion[^?]*\?',normal_text):
            problemas.append('Solicitar revisión administrativa de una cotización pendiente es un paso interno: ejecútalo sin pedir otra autorización al cliente. El consentimiento voluntario de aviso es independiente.')
        return problemas

    def respuesta_verificada(self, text: str | None) -> str | None:
        # Última defensa: corrige solo afirmaciones operativas sin evidencia.
        # La reparación conversacional previa la hace el propio modelo.
        partes=[]
        for parte in re.split(r'(?<=[.!?\n])\s+|\s+pero\s+|;\s*',text or ''):
            partes.append(self._problema_operacion(parte) or parte)
        salida=' '.join(partes)
        salida=re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}','esta propuesta',salida,flags=re.I)
        salida=re.sub(r'(?:ID|referencia)\s*[`:#]*\s*[0-9a-f]{8}\b','el registro',salida,flags=re.I)
        salida=re.sub(r'comercial_[a-z_]+|evidencia_aceptacion_invalida|consentimiento_no_acreditado','gestión pendiente',salida)
        for importe in self.importes_no_confirmados(salida):salida=salida.replace(importe,'importe por confirmar')
        if re.search(r'\{\s*"',salida):
            return 'Esa gestión todavía no quedó confirmada. Puedo seguir ayudándote con la información del servicio.'
        if self.error_operacion and not re.search(r'no.{0,25}(?:confirm|registr|autoriz)',salida,re.I):
            salida+=' El aviso aún no quedó confirmado.' if self.operacion=='solicitar_aprobacion' else ' El registro de la propuesta aún no quedó confirmado.'
        return salida or None

    def schemas(self) -> list[dict[str, Any]]:
        return TOOL_SCHEMAS if self.enabled else []

    async def execute(self, name: str, args: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
        if not self.enabled or name not in NAMES:
            return {"ok": False, "error": "no_disponible"}
        definition = next(t["function"]["parameters"] for t in TOOL_SCHEMAS if t["function"]["name"] == name)
        if set(args) - set(definition["properties"]) or not set(definition["required"]) <= set(args):
            return {"ok": False, "error": "argumentos_invalidos"}
        if name in {'comercial_preparar','comercial_solicitar_aprobacion'}:
            self.operacion=name.removeprefix('comercial_')
            self.error_operacion=True
        try:
            if name in {"comercial_aceptar", "comercial_contratar"}:
                self.intento_cierre = True
                self.pedido = None
            if name == "comercial_contratar" and (args["cotizacionId"], args["version"]) not in self.aceptaciones:
                return {"ok": False, "error": "falta_aceptacion_confirmada"}
            if name in {"comercial_catalogo", "comercial_consultar_aprobacion", "comercial_consultar_cobro", "comercial_enlace_cobro"}:
                params = {"conversationId": self.conversation_id}
                if args.get("cotizacionId"):
                    params["cotizacionId"] = args["cotizacionId"]
                if args.get('cotizacionId') and self.actual and args['cotizacionId']!=self.actual['cotizacionId']:
                    self.actual=None
                response = await self.crm._request("GET", "/api/bot/comercial", params=params)
            else:
                if not tool_call_id or not self.dispatch_id:
                    return {"ok": False, "error": "falta_identidad_operacion"}
                key = sha256((self.dispatch_id + ":" + tool_call_id).encode()).hexdigest()
                action = name.removeprefix("comercial_")
                body = {"action": action, "conversationId": self.conversation_id, "requestKey": key}
                if action == "preparar":
                    body["propuesta"] = {k:v for k,v in args.items() if k not in {'cotizacionId','nuevaNegociacion','actualizarAlcance','version'}}
                    body['mensajeOrigenId']=self.inbound_message_id
                    body.update({k:args[k] for k in ('cotizacionId','nuevaNegociacion','actualizarAlcance','version') if k in args})
                    if 'cotizacionId' not in body and self.actual and not args.get('nuevaNegociacion'):
                        body['cotizacionId']=self.actual['cotizacionId']
                    if args.get('actualizarAlcance') and 'version' not in body and self.actual and body.get('cotizacionId')==self.actual['cotizacionId']:
                        body['version']=self.actual['version']
                else:
                    body.update({k:v for k,v in args.items() if k != "avisar"})
                    if action == "solicitar_aprobacion" and args.get("avisar") is True:
                        body["mensajeAvisoId"] = self.inbound_message_id
                if action == "aceptar":
                    body["mensajeId"] = self.inbound_message_id
                response = await self.crm._request("POST", "/api/bot/comercial", json=body)
            payload = presentacion_mxn(response.json())
            def importes(v):
                if isinstance(v,list):
                    for x in v:importes(x)
                elif isinstance(v,dict):
                    for k,x in v.items():
                        if k in {'unico','mensual','subtotalUnico','subtotalMensual','conocidoDespuesDescuento','descuento','importeUnico','importeMensual','ajusteUnico','ajusteMensual'} and isinstance(x,int) and not isinstance(x,bool):
                            self.importes_confirmados.add(x)
                            if k=='descuento':self.importes_confirmados.add(-x)
                        else:importes(x)
            if 200 <= response.status_code < 300:importes(payload)
            if not 200 <= response.status_code < 300:
                return {"ok": False, "error": payload.get("error", {}).get("code", "crm_error")}
            if name in {'comercial_preparar','comercial_solicitar_aprobacion'}:
                if not payload.get('cotizacionId') or not isinstance(payload.get('version'),int) or not payload.get('estado') or not isinstance(payload.get('consentimiento'),dict):
                    return {'ok':False,'error':'registro_no_confirmado'}
                if name=='comercial_solicitar_aprobacion' and (payload['cotizacionId']!=args['cotizacionId'] or payload['version']!=args['version'] or (args.get('avisar') is True and payload['consentimiento'].get('registrado') is not True)):
                    return {'ok':False,'error':'consentimiento_no_confirmado'}
                self.actual=payload
                self.error_operacion=False
            if name in {'comercial_catalogo','comercial_consultar_aprobacion'}:
                target=args.get('cotizacionId') or payload.get('negociacionActiva')
                actual=next((q for q in payload.get('cotizaciones',[]) if q.get('id')==target),None)
                if actual:
                    self.actual={k:actual.get(k) for k in ('version','estado','consentimiento','preguntaAvisoActual','snapshot')}
                    self.actual['cotizacionId']=actual['id']
            if name == "comercial_aceptar":
                if (payload.get("estado") != "aceptada" or payload.get("cotizacionId") != args["cotizacionId"] or payload.get("version") != args["version"]):
                    return {"ok": False, "error": "aceptacion_no_confirmada"}
                self.aceptaciones.add((args["cotizacionId"], args["version"]))
            if name in {"comercial_catalogo", "comercial_consultar_aprobacion"}:
                for q in payload.get("cotizaciones", []):
                    if q.get("estado") == "aceptada" and q.get("accepted_message_id") and q.get("version") == q.get("aprobada_version"):
                        self.aceptaciones.add((q["id"], q["version"]))
            if name in {"comercial_consultar_aprobacion", "comercial_consultar_cobro", "comercial_enlace_cobro"}:
                for pedido in payload.get("pedidos", []):
                    if (pedido.get("cotizacion_id") == args.get("cotizacionId") and isinstance(pedido.get("id"), str) and pedido["id"].strip()
                            and pedido.get("estado") in {"pendiente", "en_verificacion", "pagado", "cancelado"}):
                        self.pedido = pedido
            if name == "comercial_contratar":
                pedido = payload.get("pedido") or {}
                if (not isinstance(pedido.get("id"), str) or not pedido["id"].strip() or pedido.get("cotizacion_id") != args["cotizacionId"] or pedido.get("version") != args["version"] or pedido.get("estado") != "pendiente"):
                    return {"ok": False, "error": "pedido_no_confirmado"}
                self.pedido = pedido
            if name == "comercial_enlace_cobro":
                pedidos = payload.get("pedidos", [])
                pedido = pedidos[0] if pedidos else {}
                return {"ok": True, "enlace": pedido.get("enlace") if pedido.get("estado") not in {"cancelado", "pagado"} else None, "estado": pedido.get("estado", "sin_contratacion")}
            return {"ok": True, "data": payload}
        except Exception:
            # No propagar URL con token, evidencia ni detalles de transporte al modelo.
            return {"ok": False, "error": "crm_no_disponible"}

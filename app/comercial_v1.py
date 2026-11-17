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

_REF = {"cotizacionId": {"type": "string"}, "version": {"type": "integer", "minimum": 1}}
_LINE = {"type": "object", "properties": {"productoId": {"type": "string"}, "cantidad": {"type": "integer", "minimum": 1, "maximum": 1000}, "baseId": {"type": "string"}}, "required": ["productoId", "cantidad"], "additionalProperties": False}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


TOOL_SCHEMAS = [
    _tool("comercial_catalogo", "Consulta productos, extras compatibles y condiciones. Orientativo o pendiente no es un precio final autorizado. También recupera las cotizaciones de esta conversación para retomar su id y versión.", {}, []),
    _tool("comercial_preparar", "Prepara cotización con precios calculados por servidor. Solo comunicar como oferta final si estado=aprobada. Si pendiente, es propuesta interna, solicita aprobación y conserva cotizacionId/version. Registra modificaciones sin tarifa autorizada en pendientes con descripcion y solicitud; no inventes productoId, precio cero ni incompatibilidades. Puedes combinar múltiples extras conocidos con pendientes.", {"conceptos": {"type": "array", "items": _LINE, "minItems": 1, "maxItems": 40}, "especial": {"type": "string"}, "pendientes": {"type":"array","maxItems":40,"items":{"type":"object","properties":{"descripcion":{"type":"string"},"solicitud":{"type":"string"}},"required":["descripcion","solicitud"],"additionalProperties":False}}}, ["conceptos"]),
    _tool("comercial_solicitar_aprobacion", "Registra solicitud interna sin transferir. Pregunta si desea aviso de autorización por este WhatsApp. Solo si responde explícitamente «Sí, avísame cuando mi cotización esté autorizada», usa avisar=true; el servidor verifica su mensaje.", {**_REF, "avisar": {"type": "boolean"}}, ["cotizacionId", "version"]),
    _tool("comercial_consultar_aprobacion", "Recupera versión y decisión actual de la cotización del prospecto, sin cambiarla.", {"cotizacionId": {"type": "string"}}, ["cotizacionId"]),
    _tool("comercial_aceptar", "Registra aceptación explícita de la propuesta aprobada en el mensaje actual. Un Sí solo vale si responde a la aceptación de esa propuesta, no a un aviso u otra pregunta. Espera estado=aceptada con cotizacionId/version coincidentes antes de contratar. No implica pago.", _REF, ["cotizacionId", "version"]),
    _tool("comercial_contratar", "Ejecuta solo después de aceptación confirmada por comercial_aceptar o consulta de esa misma versión aceptada. Crea contratación única; éxito requiere pedido.id real, versión coincidente y pago pendiente. Un error no es contratación.", _REF, ["cotizacionId", "version"]),
    _tool("comercial_consultar_cobro", "Consulta estado de pago verificado por administración; intención de compra no es pago.", {"cotizacionId": {"type": "string"}}, ["cotizacionId"]),
    _tool("comercial_enlace_cobro", "Recupera exclusivamente el enlace administrativamente aprobado del pedido. Si falta, no inventarlo. Compartirlo usa el envío habitual de Nea.", {"cotizacionId": {"type": "string"}}, ["cotizacionId"]),
]
NAMES = frozenset(t["function"]["name"] for t in TOOL_SCHEMAS)


class ComercialTools:
    def __init__(self, crm: Any, conversation_id: str, dispatch_id: str, inbound_message_id: str, *, enabled: bool = False) -> None:
        self.crm = crm
        self.conversation_id = conversation_id
        self.dispatch_id = dispatch_id
        self.inbound_message_id = inbound_message_id
        self.enabled = enabled
        self.aceptaciones: set[tuple[str, int]] = set()
        self.pedido: dict[str, Any] | None = None
        self.intento_cierre = False

    def respuesta_verificada(self, text: str | None) -> str | None:
        normal = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
        afirma_registro = bool(re.search(r'(?:registr|gener|cread|confirmad|formaliz|complet|list[oa]).{0,65}(?:contratacion|pedido|aceptacion)|(?:contratacion|pedido|aceptacion).{0,65}(?:registr|gener|cread|confirmad|formaliz|complet|list[oa])', normal))
        if self.intento_cierre or afirma_registro:
            if self.pedido:
                estado = {"pendiente":"El pago permanece pendiente; no se ha confirmado ningún cobro.",
                          "en_verificacion":"El pago está en verificación, todavía no confirmado.",
                          "pagado":"El pago figura confirmado en Vocero.",
                          "cancelado":"El pedido figura cancelado en Vocero."}[self.pedido["estado"]]
                return f"Tu contratación está registrada con referencia {self.pedido['id']}. {estado}"
            if self.intento_cierre:
                return ("No se completó el registro de la contratación. "
                        "Seguimos aquí con tu propuesta; no hay un pedido confirmado en este intento.")
            return "No tengo confirmación del registro de la contratación. Continuamos con tu propuesta sin darla por contratada."
        return text

    def schemas(self) -> list[dict[str, Any]]:
        return TOOL_SCHEMAS if self.enabled else []

    async def execute(self, name: str, args: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
        if not self.enabled or name not in NAMES:
            return {"ok": False, "error": "no_disponible"}
        definition = next(t["function"]["parameters"] for t in TOOL_SCHEMAS if t["function"]["name"] == name)
        if set(args) - set(definition["properties"]) or not set(definition["required"]) <= set(args):
            return {"ok": False, "error": "argumentos_invalidos"}
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
                response = await self.crm._request("GET", "/api/bot/comercial", params=params)
            else:
                if not tool_call_id or not self.dispatch_id:
                    return {"ok": False, "error": "falta_identidad_operacion"}
                key = sha256((self.dispatch_id + ":" + tool_call_id).encode()).hexdigest()
                action = name.removeprefix("comercial_")
                body = {"action": action, "conversationId": self.conversation_id, "requestKey": key}
                if action == "preparar":
                    body["propuesta"] = args
                else:
                    body.update({k:v for k,v in args.items() if k != "avisar"})
                    if action == "solicitar_aprobacion" and args.get("avisar") is True:
                        body["mensajeAvisoId"] = self.inbound_message_id
                if action == "aceptar":
                    body["mensajeId"] = self.inbound_message_id
                response = await self.crm._request("POST", "/api/bot/comercial", json=body)
            payload = response.json()
            if not 200 <= response.status_code < 300:
                return {"ok": False, "error": payload.get("error", {}).get("code", "crm_error")}
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

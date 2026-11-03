import httpx
from app.profile import BusinessProfile, profile_from_payload
from app.knowledge import with_document_knowledge
from app.llm import LlmReply
from tests.conftest import CRM_URL, mock_crm_basics, despachar

async def test_documentos_alcanzan_prompt_sin_reemplazar_instrucciones(ctx,client,respx_mock,monkeypatch):
    profile=BusinessProfile(instructions="Conserva enfoque consultivo",escalation_rules="Precios no documentados: escalar",knowledge_path="/api/bot/knowledge",kb_text="Manual aprobado")
    async def resolve(_): return profile
    monkeypatch.setattr("app.turn.resolve_profile",resolve)
    mock_crm_basics(respx_mock)
    route=respx_mock.post(f"{CRM_URL}/api/bot/knowledge").mock(return_value=httpx.Response(200,json={"chunks":[{"name":"servicios.pdf","chunkId":"kb_1","text":"El mantenimiento cuesta 1200 MXN"}]}))
    ctx.llm.replies=[LlmReply(content="El mantenimiento cuesta 1200 MXN.")]
    await despachar(client,ctx,text="¿Cuánto cuesta el mantenimiento?",esperar=True)
    assert route.call_count==1
    prompt=ctx.llm.calls[0]["messages"][0]["content"]
    assert "1200 MXN" in prompt and "servicios.pdf" in prompt
    assert "Conserva enfoque consultivo" in prompt
    assert "Precios no documentados" in prompt and "Manual aprobado" in prompt
    assert profile.kb_text=="Manual aprobado"  # no contaminar caché entre conversaciones

async def test_fallo_documental_no_inventa_contenido(ctx,respx_mock):
    p=BusinessProfile(knowledge_path="/api/bot/knowledge",kb_text="aprobado")
    respx_mock.post(f"{CRM_URL}/api/bot/knowledge").mock(return_value=httpx.Response(503))
    assert await with_document_knowledge(p,ctx.crm,"precios")==p

async def test_contrato_anterior_no_consulta_documentos(ctx):
    p=BusinessProfile(kb_text="aprobado")
    assert await with_document_knowledge(p,ctx.crm,"consulta")==p

def test_ruta_documental_solo_local_conocida():
    assert profile_from_payload({"profile":{},"knowledge":{"path":"https://otro.example"}},default_name="Nea").knowledge_path is None

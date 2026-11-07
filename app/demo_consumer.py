"""Consumidor demo: sin memoria comercial, efectos de canal ni fallback."""
import os
import hashlib
from app.demo_contract import validate_demo_envelope
from app.demo_session import DemoSessionError
async def process_demo(ctx,payload):
    if ctx.settings.multi_org:raise ValueError("demo_multi_org_not_supported")
    validate_demo_envelope(payload)
    scope=payload['demo']['scope'];channel=payload.get('channelContext',{})
    identity=channel.get('identity',{})
    messages=payload.get('messages',[])
    if not messages or any(m.get('type')!='text' or not isinstance(m.get('text'),str) for m in messages):raise ValueError('demo_text_required')
    text='\n'.join(m['text'] for m in messages)
    if not 0<len(text)<=2000:raise ValueError('demo_text_limit')
    session=await ctx.crm.resolve_demo_session(organization_id=scope['organizationId'],conversation_id=scope['conversationId'],account_ref=channel['accountRef'],identity_kind=identity['kind'],identity_value=identity['value'],request_key=payload['dispatchId'])
    for key in ('organizationId','spaceId','sessionId','conversationId','authorizationId','profileVersionId','routeGeneration'):
        if session[key]!=scope[key]:raise ValueError('demo_scope_mismatch')
    key=payload['dispatchId'];digest=hashlib.sha256(text.encode()).hexdigest()
    reservation=await ctx.crm.demo_finance_operation(action='reserve',organization_id=scope['organizationId'],space_id=scope['spaceId'],session_id=scope['sessionId'],operation_key=key,input_digest=digest)
    if reservation['status'] not in ('reserved','uncertain','settled'):raise ValueError('reservation_unavailable')
    result=await ctx.crm.generate_demo(organization_id=scope['organizationId'],space_id=scope['spaceId'],session_id=scope['sessionId'],operation_key=key,text=text)

    if os.environ.get("DEMO_DELIVERY_ENABLED")=="true" and result.get("status")=="settled" and result.get("text"):
        result["delivery"]=await ctx.crm.dispatch_demo(organization_id=scope["organizationId"],session_id=scope["sessionId"],operation_key=key,text=result["text"])
    return result

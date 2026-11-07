import pytest
import httpx
from unittest.mock import AsyncMock
from app.main import create_app
from app.dispatch import _procesar
from app.dispatch_worker import drain_one
from app.demo_contract import validate_demo_envelope
from tests.test_dispatch_durable import cloud, payload, post


def demo():
    data=payload()
    data['demo']={'contractVersion':1,'scope':{'kind':'demo','organizationId':'org_a','spaceId':'space_a','sessionId':'session_a','conversationId':'cv_a','authorizationId':'auth_a','routeGeneration':1,'profileVersionId':'profile_a'}}
    data['channelContext']={'accountRef':'test-account','identity':{'kind':'phone','value':data['contact']['identity']}}
    return data


async def test_signed_demo_never_acks_or_enters_commercial_queue():
    ctx=cloud()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(ctx)),base_url='http://test') as client:
        valid=await post(client,ctx,demo())
        assert valid.status_code==503
        assert valid.json()=={'error':'demo_capability_unavailable'}
        assert not getattr(ctx.store,"dispatch_inbox",{})
        bad=demo();bad['demo']['scope']['organizationId']='org_b'
        assert (await post(client,ctx,bad)).status_code==422
        assert (await post(client,ctx,{**payload(),'demo':None})).status_code==422
        assert (await post(client,ctx,payload())).status_code==200
    await ctx.crm.aclose()


async def test_recovered_demo_never_handoffs_or_mutates_commercial_memory():
    ctx=cloud();ctx.crm.post_handoff=AsyncMock()
    await ctx.store.enqueue_dispatch('org_a',demo())
    with pytest.raises(ValueError,match='demo_capability_unavailable'):
        await drain_one(ctx)
    ctx.crm.post_handoff.assert_not_called()
    assert next(iter(ctx.store.dispatch_inbox.values()))['state']=='started'
    with pytest.raises(ValueError,match='commercial_processor'):
        await _procesar(ctx,demo())
    await ctx.crm.aclose()


@pytest.mark.parametrize('field,value',[('routeGeneration',True),('routeGeneration',0),('sessionId',''),('conversationId','cv_b'),('organizationId','org_b')])
def test_scope_validation(field,value):
    data=demo();data['demo']['scope'][field]=value
    with pytest.raises(ValueError):validate_demo_envelope(data)


@pytest.mark.parametrize("identity", [
    {"kind": "bsuid", "value": "521111111111"},
    {"kind": "phone", "value": "521111111112"},
    {"kind": "phone", "value": "bsuid:AA123"},
])
def test_demo_rejects_inconsistent_identity(identity):
    data = demo()
    data["channelContext"]["identity"] = identity
    with pytest.raises(ValueError):
        validate_demo_envelope(data)


async def test_dispatch_identity_cannot_replace_original_envelope():
    ctx=cloud()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(ctx)),base_url='http://test') as client:
        first=payload()
        assert (await post(client,ctx,first)).status_code==200
        changed=payload();changed['messages'][0]['text']='otro contexto'
        assert (await post(client,ctx,changed)).status_code==409
        assert (await post(client,ctx,first)).status_code==200
        assert next(iter(ctx.store.dispatch_inbox.values()))['payload']==first
    await ctx.crm.aclose()

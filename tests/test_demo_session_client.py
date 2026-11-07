import httpx
import pytest
from app.crm import CrmClient
from app.demo_session import DemoSessionError

@pytest.mark.parametrize('status,code',[(401,'unauthorized'),(403,'unauthorized'),(404,'unauthorized'),(409,'conflict'),(422,'invalid_request'),(429,'unavailable'),(503,'unavailable')])
async def test_session_errors_never_fallback(status,code):
    async def handler(request):return httpx.Response(status,json={})
    async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler)) as http:
        client=CrmClient('http://test','synthetic',client=http)
        with pytest.raises(DemoSessionError) as error:
            await client.resolve_demo_session(organization_id='a',conversation_id='c',account_ref='x',identity_kind='bsuid',identity_value='i',request_key='stable')
        assert error.value.code==code

async def test_timeout_is_uncertain_without_automatic_retry():
    calls=[]
    async def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('synthetic',request=request)
    async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DemoSessionError) as error:
            await CrmClient('http://test','synthetic',client=http).resolve_demo_session(organization_id='a',conversation_id='c',account_ref='x',identity_kind='bsuid',identity_value='i',request_key='stable')
        assert error.value.code=='result_uncertain'
        assert len(calls)==1

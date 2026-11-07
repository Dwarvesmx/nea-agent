import httpx,pytest
from app.demo_finance import DemoFinanceClient
from app.demo_session import DemoSessionError
@pytest.mark.parametrize('status,code',[(401,'unauthorized'),(409,'conflict'),(503,'unavailable')])
async def test_finance_errors(status,code):
 async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(lambda r:httpx.Response(status,json={}))) as http:
  with pytest.raises(DemoSessionError) as e:await DemoFinanceClient(http).operation(action='reserve',organization_id='o',space_id='p',session_id='s',operation_key='k',input_digest='a'*64)
  assert e.value.code==code
async def test_finance_timeout_never_retries():
 calls=[]
 def handler(r):calls.append(r);raise httpx.ReadTimeout('synthetic')
 async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler)) as http:
  with pytest.raises(DemoSessionError) as e:await DemoFinanceClient(http).operation(action='reserve',organization_id='o',space_id='p',session_id='s',operation_key='k',input_digest='a'*64)
  assert e.value.code=='result_uncertain';assert len(calls)==1

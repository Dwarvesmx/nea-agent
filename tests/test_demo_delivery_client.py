import httpx,pytest
from app.crm import CrmClient
from app.demo_session import DemoSessionError
async def test_dispatch_identity_and_no_retries():
 calls=[]
 async def handler(r):
  calls.append(r)
  return httpx.Response(200,json={'contractVersion':1,'status':'accepted','deliveryId':'stable'})
 async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler)) as http:
  result=await CrmClient('http://test','synthetic',client=http).dispatch_demo(organization_id='o',session_id='s',operation_key='event',text='respuesta')
  assert result['status']=='accepted';assert len(calls)==1
  import json,hashlib
  body=json.loads(calls[0].content);assert body['responseHash']==hashlib.sha256(b'respuesta').hexdigest();assert body['operationKey']=='event'
async def test_delivery_ack_lost_never_retries_or_handoffs():
 calls=[]
 async def handler(r):calls.append(r);raise httpx.ReadTimeout('synthetic',request=r)
 async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler)) as http:
  with pytest.raises(DemoSessionError) as error:await CrmClient('http://test','synthetic',client=http).dispatch_demo(organization_id='o',session_id='s',operation_key='event',text='respuesta')
  assert error.value.code=='delivery_result_uncertain';assert len(calls)==1

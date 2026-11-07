import asyncio,json,os,sys
import httpx
from app.crm import CrmClient
from app.demo_finance import DemoFinanceClient
from app.demo_session import DemoSessionError
async def main():
 mode=sys.argv[1]
 async with httpx.AsyncClient(base_url='http://127.0.0.1:55530',headers={'X-API-Key':os.environ['DEMO_SESSION_TEST_KEY']},trust_env=False,timeout=15) as http:
  crm=CrmClient('http://127.0.0.1:55530','unused',client=http)
  session=await crm.resolve_demo_session(organization_id='session-org',conversation_id='session-conversation',account_ref='synthetic-account',identity_kind='bsuid',identity_value='synthetic-participant',request_key='synthetic-event-1')
  if mode=='session':print(json.dumps(session));return
  if mode=='lost-ack':http.headers['x-test-drop-ack']='1'
  args=dict(action={'lost-query':'query','lost-release':'release'}.get(mode,mode if mode in ('query','release','settle') else 'reserve'),organization_id='other' if mode=='foreign' else 'session-org',space_id=session['spaceId'],session_id=session['sessionId'],operation_key='ack-lost' if mode.startswith('lost-') else 'second' if mode in ('second','release') else 'first',input_digest=('b' if mode=='conflict' else 'a')*64)
  try:
   if mode=='forged-cost':
    body={'contractVersion':1,'action':'settle','organizationId':'session-org','spaceId':session['spaceId'],'sessionId':session['sessionId'],'operationKey':'first','inputDigest':'a'*64,'cost':'0'}
    r=await http.post('/api/bot/demo/finance',json=body);result={'status':r.status_code}
   else:result=await crm.demo_finance_operation(**args)
  except DemoSessionError as e:result={'status':e.status,'code':e.code}
  print(json.dumps(result))
if __name__=='__main__':asyncio.run(main())

"""Probe real HTTP del cliente Nea; solo destino sintético fijo, sin modelos."""
import asyncio,json,os,sys
import httpx
from app.demo_session import DemoSessionError
from app.crm import CrmClient
async def main():
    mode=sys.argv[1]
    async with httpx.AsyncClient(base_url='http://127.0.0.1:55530',headers={'X-API-Key':os.environ['DEMO_SESSION_TEST_KEY'] if mode!='no-key' else 'invalid-synthetic'},trust_env=False) as http:
        try:
            result=await CrmClient("http://127.0.0.1:55530","unused",client=http).resolve_demo_session(organization_id='other-org' if mode=='foreign' else 'session-org',conversation_id='session-conversation',account_ref='unknown-account' if mode=='unknown-account' else 'synthetic-account',identity_kind='bsuid',identity_value='synthetic-participant',request_key='synthetic-event-1')
        except DemoSessionError as e:result={'code':e.code,'status':e.status}
        print(json.dumps(result))
if __name__=='__main__':asyncio.run(main())

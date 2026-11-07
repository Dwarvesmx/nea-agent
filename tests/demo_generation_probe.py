import asyncio,json,os,sys
from pathlib import Path
import asyncpg,httpx
from app.crm import CrmClient
from app.dispatch_store import PgDispatchStore
from app.dispatch_worker import drain_one
from app.main import create_app
from tests.test_dispatch_durable import cloud,post
async def main():
 if os.environ['DATABASE_URL']!='postgres://demo@127.0.0.1:55529/demo_f2a':raise RuntimeError('unsafe test database')
 conn=await asyncpg.connect(os.environ['DATABASE_URL'])
 directory=await conn.fetchval("select current_setting('data_directory')")
 if not directory.replace('\\','/').endswith('/tmp/demo-f2a02-pg'):raise RuntimeError('unsafe cluster')
 await conn.execute('create schema if not exists nea_demo_generation')
 await conn.execute('set search_path=nea_demo_generation')
 await conn.execute(Path('migrations/008_dispatch_inbox.sql').read_text())
 await conn.close()
 pool=await asyncpg.create_pool(os.environ['DATABASE_URL'],server_settings={'search_path':'nea_demo_generation'})
 async with httpx.AsyncClient(base_url='http://127.0.0.1:55530',headers={'X-API-Key':os.environ['DEMO_SESSION_TEST_KEY']},trust_env=False) as http:
  crm=CrmClient('http://127.0.0.1:55530','unused',client=http)
  session=await crm.resolve_demo_session(organization_id='session-org',conversation_id='session-conversation',account_ref='synthetic-account',identity_kind='bsuid',identity_value='synthetic-participant',request_key='session-start')
  scope={k:session[k] for k in ('organizationId','spaceId','sessionId','conversationId','authorizationId','profileVersionId','routeGeneration')};scope['kind']='demo'
  if sys.argv[1]=='scope-mismatch':scope['spaceId']='foreign-space'
  event='event-'+session['sessionId']+'-'+sys.argv[1];text='fallo incierto' if sys.argv[1]=='uncertain' else 'segunda pregunta' if sys.argv[1]=='two' else 'primera pregunta'
  payload={'dispatchId':event,'organization':{'id':'session-org','slug':'synthetic'},'conversation':{'id':'session-conversation'},'contact':{'identity':'bsuid:synthetic-participant'},'messages':[{'id':event,'type':'text','text':text}],'demo':{'contractVersion':1,'scope':scope},'channelContext':{'accountRef':'synthetic-account','identity':{'kind':'bsuid','value':'synthetic-participant'}}}
  ctx=cloud();await ctx.crm.aclose();ctx.crm=crm;ctx.store=PgDispatchStore();ctx.store.pool=pool
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(ctx)),base_url='http://nea-test') as nea:
   ack=await post(nea,ctx,payload)
   assert ack.status_code==200,ack.text
  if sys.argv[1]=="parallel":
   await asyncio.gather(drain_one(ctx),drain_one(ctx))
  else:await drain_one(ctx)
  result=await crm.generate_demo(organization_id='session-org',space_id=session['spaceId'],session_id=session['sessionId'],operation_key=event,text=text)
  print(json.dumps(result))
 await pool.close()
if __name__=='__main__':asyncio.run(main())

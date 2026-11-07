"""Cliente exclusivo de sesiones demo. Ningún error deriva al turno comercial."""
from typing import Any
import httpx

class DemoSessionError(Exception):
    def __init__(self,code:str,status:int=0):
        self.code,self.status=code,status
        super().__init__(code)

class DemoSessionClient:
    def __init__(self,client:httpx.AsyncClient):
        self._http=client

    async def resolve(self,*,organization_id:str,conversation_id:str,account_ref:str,
                      identity_kind:str,identity_value:str,request_key:str)->dict[str,Any]:
        body={'contractVersion':1,'organizationId':organization_id,'conversationId':conversation_id,
              'accountRef':account_ref,'channel':'whatsapp','requestKey':request_key,
              'identity':{'kind':identity_kind,'value':identity_value}}
        try:
            response=await self._http.post('/api/bot/demo/session',json=body)
        except httpx.HTTPError:
            # Repetir únicamente con la MISMA identidad/request_key: el commit pudo ocurrir.
            raise DemoSessionError('result_uncertain') from None
        if response.status_code in (401,403,404):raise DemoSessionError('unauthorized',response.status_code)
        if response.status_code==409:raise DemoSessionError('conflict',409)
        if response.status_code>=500 or response.status_code==429:raise DemoSessionError('unavailable',response.status_code)
        if response.status_code!=200:raise DemoSessionError('invalid_request',response.status_code)
        try:
            result=response.json()
            assert result['contractVersion']==1 and result['state']=='active'
            assert result['organizationId']==organization_id and result['conversationId']==conversation_id
            assert all(isinstance(result[k],str) and result[k] for k in ('sessionId','spaceId','authorizationId','profileVersionId','expiresAt','idleDeadline'))
        except (ValueError,KeyError,TypeError,AssertionError):
            raise DemoSessionError('invalid_response') from None
        return result

"""Mediación: no acepta precios ni evidencia arbitraria del llamador."""
import httpx
import re
from app.demo_session import DemoSessionError
class DemoFinanceClient:
    def __init__(self,http:httpx.AsyncClient):self._http=http
    async def operation(self,*,action,organization_id,space_id,session_id,operation_key,input_digest):
        body={'contractVersion':1,'action':action,'organizationId':organization_id,'spaceId':space_id,'sessionId':session_id,'operationKey':operation_key,'inputDigest':input_digest}
        try:r=await self._http.post('/api/bot/demo/finance',json=body)
        except httpx.HTTPError:raise DemoSessionError('result_uncertain') from None
        if r.status_code!=200:
            code='unauthorized' if r.status_code in (401,403,404) else 'conflict' if r.status_code==409 else 'unavailable' if r.status_code>=500 or r.status_code==429 else 'invalid_request'
            raise DemoSessionError(code,r.status_code)
        try:
            result=r.json()
            assert result.get('contractVersion')==1 and result.get('operationKey')==operation_key
            assert result['status'] in ('reserved','settled','released','uncertain')
            assert isinstance(result['operationId'],str) and result['operationId']
            assert re.fullmatch(r'\d+',result['reservedMicroUsd'])
            assert result['actualMicroUsd'] is None or re.fullmatch(r'\d+',result['actualMicroUsd'])
        except (ValueError,KeyError,TypeError,AssertionError):raise DemoSessionError('invalid_response') from None
        return result

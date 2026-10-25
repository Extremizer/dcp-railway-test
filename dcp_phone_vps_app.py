"""Reuse the owner-signed staging app; invite control is localhost+secret only."""
import hmac
import os
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dcp_phone_staging_app import app
from dcp_phone_staging_notice_once import send_once

class InvitationRequest(BaseModel):
    request_id: str = Field(min_length=8,max_length=64,pattern=r'^[a-zA-Z0-9_-]+$')

@app.post('/_ops/invite')
def invite(payload:InvitationRequest, request:Request, x_extremizer_control:str|None=Header(None)):
    expected=os.getenv('DCP_VPS_CONTROL_TOKEN','')
    if (request.client is None or request.client.host not in ('127.0.0.1','::1')
        or len(expected)<32 or not isinstance(x_extremizer_control,str)
        or not hmac.compare_digest(expected,x_extremizer_control)):
        raise HTTPException(404,'not_found')
    if (os.getenv('DCP_PHONE_STAGING_MOCK_ONLY')!='1'
        or os.getenv('DCP_CLOUD_PRODUCER_ENABLED')!='0'
        or os.getenv('DCP_LIVE_NAVIGATION_GO')!='0'
        or app.state.coordinator is None or app.state.browser is None):
        raise HTTPException(503,'staging_not_ready')
    return {'result':send_once(coordinator=app.state.coordinator,
         state_dir=os.environ['DCP_CLOUD_STATE_DIR'],request_id=payload.request_id,
         browser_status=app.state.browser.status())}

@app.middleware('http')
async def actual_browser_health(request,call_next):
    if request.url.path=='/health':
        connected=app.state.browser is not None and app.state.browser.status()=='MOCK_ONLY'
        return JSONResponse({'mode':'MOCK_ONLY','ready':connected,'browser_connected':connected,
                             'dcp_enabled':False,'admission':False},status_code=200 if connected else 503,
                            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    return await call_next(request)

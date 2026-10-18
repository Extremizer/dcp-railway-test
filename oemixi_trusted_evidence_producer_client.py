"""Producer-only publisher. No direct DB access or Telegram/user evidence."""
import json,urllib.request
from urllib.parse import urlparse
from oemixi_trusted_evidence_channel import PATH,encode_evidence,headers,validate_observation

def publish_trusted_observation(base_url,secret,observation,*,opener=None):
    url=urlparse(base_url)
    if url.scheme!="https" or not url.hostname:
        return {"status":"EVIDENCE_UNAVAILABLE"}
    if not validate_observation(observation):
        return {"status":"EVIDENCE_UNVERIFIED"}
    body=encode_evidence(observation)
    req=urllib.request.Request(base_url.rstrip("/")+PATH,data=body,method="POST",
        headers={**headers(secret,body),"Content-Type":"application/json"})
    try:
        with (opener or urllib.request.urlopen)(req,timeout=6) as response:
            if response.status!=200:return {"status":"EVIDENCE_UNAVAILABLE"}
            result=json.loads(response.read(4097))
            if isinstance(result,dict) and result.get("status")=="EVIDENCE_ACCEPTED":
                return result
    except Exception:
        pass
    return {"status":"EVIDENCE_UNAVAILABLE"}

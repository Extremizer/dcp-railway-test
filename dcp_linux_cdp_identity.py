"""Read-only existing Linux Chromium CDP identity inspection; never navigates."""
from __future__ import annotations
import json
import urllib.request
from urllib.parse import urlsplit
import websocket

DISCOVERY="http://127.0.0.1:9222/json"
DCP_HOST="www.dealercostparts.com"
EXACT_CART="https://www.dealercostparts.com/cart"
MARKERS=("just a moment","verify you are human","verifying you are human",
         "checking your browser","performing security verification","cloudflare")
PREFLIGHT_JS=r"""(() => {
 const text=(document.body?.innerText||'').toLowerCase();
 return {url:location.href, host:location.hostname,
  challenged:['just a moment','verify you are human','verifying you are human',
  'checking your browser','performing security verification','cloudflare'].some(x=>text.includes(x)),
  cart: location.href==='https://www.dealercostparts.com/cart',
  logout:[...document.querySelectorAll('#persistant-nav a[href]')].some(a=>{
   try{const x=new URL(a.getAttribute('href'),location.href);
    return x.origin===location.origin && x.pathname==='/account/logoff'}
   catch(_){return false}
  })};
})()"""

class CdpUnverified(RuntimeError):pass

def tabs():
    try:
        with urllib.request.urlopen(DISCOVERY,timeout=3) as response:
            if response.status!=200:raise CdpUnverified("CDP_DISCOVERY_UNAVAILABLE")
            data=json.load(response)
        if not isinstance(data,list):raise CdpUnverified("CDP_TABS_INVALID")
        return data
    except (OSError,ValueError,TypeError) as exc:
        raise CdpUnverified("CDP_DISCOVERY_UNAVAILABLE") from exc

def evaluate(tab,script):
    url=tab.get("webSocketDebuggerUrl","")
    parsed=urlsplit(url)
    if parsed.scheme!="ws" or parsed.hostname not in ("localhost","127.0.0.1") or parsed.port!=9222:
        raise CdpUnverified("CDP_WEBSOCKET_NOT_LOOPBACK")
    if tab.get("type")!="page" or tab.get("url")!=EXACT_CART:
        raise CdpUnverified("CDP_CART_CONTEXT_NOT_EXACT")
    connection=websocket.create_connection(url,timeout=5,suppress_origin=True)
    try:
        connection.send(json.dumps({"id":7801,"method":"Runtime.evaluate",
          "params":{"expression":script,"returnByValue":True,"awaitPromise":False}}))
        for _ in range(12):
            reply=json.loads(connection.recv())
            if reply.get("id")!=7801:continue
            result=reply.get("result",{})
            if result.get("exceptionDetails"):
                raise CdpUnverified("CDP_EVALUATION_FAILED")
            value=result.get("result",{}).get("value")
            if not isinstance(value,dict):
                raise CdpUnverified("CDP_EVALUATION_INVALID")
            return value
        raise CdpUnverified("CDP_REPLY_MISSING")
    finally:connection.close()

def preflight():
    """Linux equivalent of READY/CHALLENGE/AUTH_REQUIRED. No implicit navigation."""
    try:
        choices=[t for t in tabs() if t.get("type")=="page" and t.get("url")==EXACT_CART]
        if len(choices)!=1:return "AUTH_REQUIRED"
        data=evaluate(choices[0],PREFLIGHT_JS)
        if data.get("challenged"):return "CLOUDFLARE"
        if data.get("url")!=EXACT_CART or data.get("host")!=DCP_HOST:return "AUTH_REQUIRED"
        if data.get("cart") is not True or data.get("logout") is not True:return "AUTH_REQUIRED"
        return "READY"
    except (CdpUnverified, OSError, ValueError):
        return "BROWSER_DOWN"

def exact_dealer_identity(expected):
    """Reuses PR64 exact logout + unique displayname + constant-time comparison."""
    from polaris_dealer_session_identity_guard import verify_dealer_session
    return verify_dealer_session(expected=expected,preflight=preflight,
                                 tabs=tabs,evaluate=evaluate)

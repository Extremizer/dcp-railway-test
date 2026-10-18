"""Read-only DCP Polaris exact catalog verifier on authorized producer host.

No cart access or mutation. Uses existing Chrome CDP evaluate and same-origin
GET partsearch. Not mounted in Railway until trusted evidence channel exists.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib,json,re
from urllib.parse import urlsplit
import oemixibot_dcp_price as dcp

def build_authorized_polaris_verifier(*,expected_account):
    """Explicit producer-only binding; never instantiate in Railway consumer."""
    if not isinstance(expected_account,str) or not expected_account.strip():
        raise ValueError("expected_dealer_account_required")
    from oemixi_admin_polaris_browser_price import _session_verifier
    from dp_sync_agent import effective_status
    def read_only_preflight():
        status=effective_status()
        if status.get("status")=="online" and status.get("dcp_status")=="READY":
            return "READY"
        return "AUTH_REQUIRED"
    def verify(manufacturer,oem):
        return verify_exact_polaris(
            manufacturer,oem,
            session_guard=lambda: _session_verifier(
                expected=expected_account,preflight=read_only_preflight),
        )
    return verify


def verify_exact_polaris(manufacturer,oem,*,tabs=None,evaluate=None,session_guard=None):
    if manufacturer!="Polaris" or not re.fullmatch(r"[A-Z0-9-]{4,40}",str(oem or "")):
        return None
    if not callable(session_guard) or session_guard() is not True:
        return None
    tabs=tabs or dcp._tabs
    evaluate=evaluate or dcp._runtime_evaluate
    try:
        candidates=[t for t in tabs() if dcp._is_dcp_page(t)
            and urlsplit(str(t.get("url") or "")).scheme=="https"]
        if len(candidates)!=1:
            return None
        tab=candidates[0]
        canonical,slug=dcp._parts_slug("Polaris")
        endpoint=f"/oemparts/partsearch/{slug}?partsearch={oem}"
        expr="""(async()=>{{
          const before=location.href;
          if(location.protocol!=='https:' || location.hostname!=='www.dealercostparts.com')
             return {{stop:'origin'}};
          const text=document.body?.innerText||'';
          if(!/sign out/i.test(text) || /just a moment|cloudflare|verify you are human/i.test(text))
             return {{stop:'auth'}};
          const r=await fetch({url},{{credentials:'include',cache:'no-store',method:'GET'}});
          if(r.status!==200)return {{stop:'http'}};
          const html=await r.text();
          const doc=new DOMParser().parseFromString(html,'text/html');
          const output=doc.body?.innerText||'';
          if(!/sign out/i.test(output) || /just a moment|cloudflare|verify you are human/i.test(output))
             return {{stop:'auth'}};
          const forms=[...doc.querySelectorAll('form[id^="add_"]')]
               .filter(f=>f.querySelector('input[name="sku"]')?.value==={oem});
          if(forms.length!==1)return {{stop:'ambiguous'}};
          const f=forms[0];
          const current=(f.querySelector('.itemnumnew')?.textContent||
              f.querySelector('.itemnum')?.textContent||
              f.querySelector('input[name="sku"]')?.value||'').trim();
          const exactMsrp=[...doc.querySelectorAll('div.c2.act_'+{oem}+' span.dbl')];
          const msrp=(f.getAttribute('data-retail')||
              f.querySelector('input[name="retail"]')?.value||
              f.querySelector('input[name="msrp"]')?.value||
              f.querySelector('.c2 .dbl')?.textContent||
              (exactMsrp.length===1 ? exactMsrp[0].textContent : '')||'').trim();
          const name=(f.querySelector('.c1a')?.textContent||'').trim();
          return {{origin:location.origin,before,after:location.href,exact_sku:
              f.querySelector('input[name="sku"]')?.value,current_oem:current,
              name,msrp,catalog:'Parts',source_path:{path}}};
        }})()""".format(url=json.dumps(endpoint),oem=json.dumps(oem),path=json.dumps(endpoint))
        data=evaluate(tab,expr)
        if not isinstance(data,dict) or data.get("stop"):
            return None
        if (data.get("origin")!="https://www.dealercostparts.com"
            or data.get("before")!=data.get("after")
            or data.get("exact_sku")!=oem or data.get("current_oem")!=oem
            or data.get("catalog")!="Parts" or data.get("source_path")!=endpoint):
            return None
        msrp=dcp._money(str(data.get("msrp") or ""))
        if msrp is None or msrp<=0:
            return None
        stamp=datetime.now(timezone.utc).isoformat()
        ref="https://www.dealercostparts.com"+endpoint
        return {"verified":True,"manufacturer":"Polaris","requested_oem":oem,
                "current_oem":oem,"catalog":"Parts","name":data.get("name"),
                "msrp_usd":msrp,"source_kind":"live_public_form_metadata",
                "source_ref":ref,"verified_at":stamp}
    except Exception:
        return None

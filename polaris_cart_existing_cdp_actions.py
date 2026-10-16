"""Existing CDP-session GET executor for observed cart decrease/remove routes.

Never starts Chrome, logs in, navigates or exposes checkout. Injection required.
This executor is NOT used unless enabled by a higher-level controlled adapter.
"""
import json
from urllib.parse import urlsplit

def existing_cart_action(tab, path, *, evaluate, approved_context):
    """One exact already-observed GET action, with pre/post cart identity checks.

    approved_context must revalidate the existing preflight, dealer account
    identity and exact current cart tab. Do not infer auth from SIGN OUT.
    """
    segments=path.split("/")
    if (len(segments)!=4 or segments[0]!="" or segments[1]!="cart"
        or segments[2] not in {"remove","decrease"}
        or not segments[3] or not all(c.isalnum() or c in "_-" for c in segments[3])):
        raise ValueError("UNAPPROVED_CART_ACTION")
    if approved_context(tab) is not True:
        raise PermissionError("UNVERIFIED_DEALER_CART")
    url=str(tab.get("url") or "")
    u=urlsplit(url)
    if (u.scheme,u.netloc,u.path,u.query,u.fragment)!=("https","www.dealercostparts.com","/cart","",""):
        raise PermissionError("WRONG_CART_CONTEXT")
    expression="""(async()=> {
      const before=location.href;
      if (before!=='https://www.dealercostparts.com/cart') return {ok:false};
      const response=await fetch(PATH,{method:'GET',credentials:'include',redirect:'manual',cache:'no-store'});
      return {ok:response.ok,code:response.status,before,after:location.href};
    })()""".replace("PATH",json.dumps(path))
    result=evaluate(tab,expression)
    if not isinstance(result,dict) or result.get("ok") is not True or result.get("before")!=url or result.get("after")!=url:
        raise RuntimeError("CART_ACTION_UNVERIFIED")
    if approved_context(tab) is not True:
        raise PermissionError("DEALER_SESSION_LOST_AFTER_ACTION")
    return True

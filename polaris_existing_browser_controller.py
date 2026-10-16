"""Single-tab Polaris probe adapter using normal browser navigation and DOM actions.

No background fetch transport is used here. The controller operates only on one
already-authorized DealerCostParts tab supplied by the caller. Mutation remains
disabled by default.
"""
from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from polaris_add_form_contract import validate_form
from polaris_cart_snapshot import canonical
from polaris_probe_errors import ProbeFailure,classify_failure

DCP_ORIGIN="https://www.dealercostparts.com"
PARTS_PATH="/oemparts/c/polaris_atv/parts"
SEARCH_PATH="/oemparts/partsearch/polaris_atv"
OEM_RE=re.compile(r"[A-Z0-9-]{4,40}")
TOKEN_RE=re.compile(r"[A-Za-z0-9_-]{4,200}")

class ExistingBrowserProbeAdapter:
    def __init__(self,*,get_tab,evaluate,verify_session,resolve_authority,
                 allow_mutation=False,timeout_seconds=12.0,poll_seconds=0.2):
        self._get_tab=get_tab
        self._evaluate=evaluate
        self._verify_session=verify_session
        self._resolve_authority=resolve_authority
        self.allow_mutation=allow_mutation
        self._timeout=float(timeout_seconds)
        self._poll=float(poll_seconds)
        self.last_failure_code=None

    @staticmethod
    def _oem(value):
        value=str(value or "").strip().upper()
        if OEM_RE.fullmatch(value) is None:
            raise ValueError("OEM_UNVERIFIED")
        return value

    def _tab(self):
        tab=self._get_tab()
        if not isinstance(tab,dict):
            raise PermissionError("DCP_TAB_UNAVAILABLE")
        u=urlsplit(str(tab.get("url") or ""))
        if (u.scheme,u.netloc)!=("https","www.dealercostparts.com"):
            raise PermissionError("WRONG_DCP_ORIGIN")
        title=str(tab.get("title") or "").lower()
        if "just a moment" in title or "один момент" in title:
            raise PermissionError("CLOUDFLARE")
        return tab

    def verify(self):
        self.last_failure_code=None
        try:
            self._tab()
            if self._verify_session() is not True:
                self.last_failure_code="AUTH_REQUIRED"
                return False
            return True
        except Exception as exc:
            self.last_failure_code=classify_failure(exc,"AUTH_REQUIRED")
            return False

    def _require_verified(self):
        tab=self._tab()
        if self._verify_session() is not True:
            raise PermissionError("DEALER_SESSION_UNVERIFIED")
        return tab

    def _matches(self,tab,path,partsearch=None):
        u=urlsplit(str(tab.get("url") or ""))
        if u.path!=path:
            return False
        if partsearch is None:
            return True
        return parse_qs(u.query).get("partsearch")==[partsearch]

    def _wait(self,path,partsearch=None):
        deadline=time.monotonic()+self._timeout
        last=None
        while time.monotonic()<deadline:
            try:
                tab=self._tab()
                last=tab
                if self._matches(tab,path,partsearch) and self._verify_session() is True:
                    return tab
            except PermissionError as exc:
                if str(exc)=="CLOUDFLARE":
                    raise
            time.sleep(self._poll)
        raise ProbeFailure("DCP_TRANSITION_TIMEOUT")

    def _navigate(self,url,path,partsearch=None):
        last_exc=None
        for attempt in range(2):
            tab=self._require_verified()
            expr="(()=>{location.assign(URL);return true})()".replace("URL",json.dumps(url))
            if self._evaluate(tab,expr) is not True:
                raise ProbeFailure("DCP_NAVIGATION_NOT_STARTED")
            try:
                return self._wait(path,partsearch)
            except ProbeFailure as exc:
                last_exc=exc
                if exc.code!="DCP_TRANSITION_TIMEOUT" or attempt:
                    raise
        raise last_exc or ProbeFailure("DCP_TRANSITION_TIMEOUT")

    def _ensure_cart(self):
        try:
            tab=self._require_verified()
            if self._matches(tab,"/cart"):
                return tab
        except PermissionError:
            raise
        return self._navigate(DCP_ORIGIN+"/cart","/cart")

    def _current_forms(self,oem):
        tab=self._require_verified()
        if not self._matches(tab,SEARCH_PATH,oem):
            raise PermissionError("WRONG_SEARCH_CONTEXT")
        expression="""(()=>{
          const forms=[...document.querySelectorAll('form[id^="add_"]')].map(f=>({
            id:f.id||'',
            action:f.getAttribute('action')||'',
            method:(f.getAttribute('method')||'').toUpperCase(),
            manf:f.querySelector('input[name="manf"]')?.value||'',
            sku:f.querySelector('input[name="sku"]')?.value||'',
            qty:f.querySelector('input[name="qty"]')?.value||'',
            old_sku:(f.querySelector('.itemnumstrike')?.textContent||'').trim(),
            new_sku:(f.querySelector('.itemnumnew')?.textContent||'').trim()
          }));
          return {forms};
        })()"""
        data=self._evaluate(tab,expression)
        if not isinstance(data,dict) or not isinstance(data.get("forms"),list):
            raise ProbeFailure("ADD_FORM_READ_FAILED")
        if self._verify_session() is not True:
            raise PermissionError("DEALER_SESSION_CHANGED")
        return data["forms"]

    def _submit_search(self,oem):
        tab=self._require_verified()
        u=urlsplit(str(tab.get("url") or ""))
        if u.path not in {PARTS_PATH,SEARCH_PATH}:
            tab=self._navigate(DCP_ORIGIN+PARTS_PATH,PARTS_PATH)
        expression="""(()=>{
          const f=document.querySelector('#PartSearchForm');
          if(!f) return {started:false,reason:'FORM_MISSING'};
          if((f.getAttribute('action')||'')!=='/oemparts/partsearch/polaris_atv')
            return {started:false,reason:'ACTION'};
          if((f.getAttribute('method')||'').toUpperCase()!=='POST')
            return {started:false,reason:'METHOD'};
          const p=f.querySelector('[name="partsearch"]');
          const m=f.querySelector('[name="modelsearch"]');
          if(!p||!m) return {started:false,reason:'FIELDS'};
          p.value=OEM;m.value='';
          HTMLFormElement.prototype.submit.call(f);
          return {started:true};
        })()""".replace("OEM",json.dumps(oem))
        data=self._evaluate(tab,expression)
        if not isinstance(data,dict) or data.get("started") is not True:
            raise ProbeFailure("SEARCH_SUBMIT_NOT_STARTED")
        try:
            self._wait(SEARCH_PATH,oem)
        except ProbeFailure as exc:
            if exc.code!="DCP_TRANSITION_TIMEOUT":
                raise
            tab=self._require_verified()
            data=self._evaluate(tab,expression)
            if not isinstance(data,dict) or data.get("started") is not True:
                raise ProbeFailure("SEARCH_SUBMIT_NOT_STARTED")
            self._wait(SEARCH_PATH,oem)

    def find_exact_oem(self,oem):
        oem=self._oem(oem)
        try:
            tab=self._require_verified()
            on_exact=self._matches(tab,SEARCH_PATH,oem)
        except PermissionError:
            raise
        if not on_exact:
            self._submit_search(oem)
        proof=validate_form(oem,self._current_forms(oem))
        authority=self._resolve_authority(oem)
        if not isinstance(authority,dict):
            raise ValueError("OEM_AUTHORITY_MISMATCH")
        required={"manufacturer":"Polaris","item_category":"Parts","requested_oem":oem}
        if any(authority.get(k)!=v for k,v in required.items()):
            raise ValueError("OEM_AUTHORITY_MISMATCH")
        if authority.get("exact_match") is not True:
            raise ValueError("OEM_AUTHORITY_MISMATCH")
        authority_resolved=authority.get("resolved_oem")
        if authority_resolved not in (None,"",proof["resolved_oem"]):
            raise ValueError("OEM_AUTHORITY_MISMATCH")
        return proof

    def snapshot(self):
        tab=self._ensure_cart()
        expression="""(()=>{
          const rows=[...document.querySelectorAll('.cartitem.container_16')].map(row=>{
            const item=row.querySelector('[data-itemid][data-qty][data-sellprice]');
            const remove=row.querySelector('a[href^="/cart/remove/"]');
            return {
              sku:item?.getAttribute('data-itemid')||'',
              qty:item?.getAttribute('data-qty')||'',
              unit_price:item?.getAttribute('data-sellprice')||'',
              remove_href:remove?.getAttribute('href')||''
            };
          });
          return {rows};
        })()"""
        data=self._evaluate(tab,expression)
        if not isinstance(data,dict) or not isinstance(data.get("rows"),list):
            raise ProbeFailure("CART_SNAPSHOT_READ_FAILED")
        lines=[]
        for row in data["rows"]:
            href=str(row.get("remove_href") or "")
            token=href.rsplit("/",1)[-1] if href.startswith("/cart/remove/") else ""
            if TOKEN_RE.fullmatch(token) is None:
                raise ValueError("CART_TOKEN_UNVERIFIED")
            qty=int(str(row.get("qty") or "0"))
            unit=Decimal(str(row.get("unit_price") or "0"))
            lines.append({
                "sku":str(row.get("sku") or ""),
                "token":token,
                "qty":qty,
                "unit_price":str(unit),
                "subtotal":str(unit*qty),
                "currency":"USD",
            })
        snap={"lines":lines}
        canonical(snap)
        if self._verify_session() is not True:
            raise PermissionError("DEALER_SESSION_CHANGED")
        return snap

    def add_one(self,oem):
        if self.allow_mutation is not True:
            raise PermissionError("MUTATION_NOT_AUTHORIZED")
        oem=self._oem(oem)
        proof=self.find_exact_oem(oem)
        tab=self._require_verified()
        expression="""(()=>{
          const f=document.querySelector(FORM);
          const b=document.querySelector(BUTTON);
          if(!f||!b) return {clicked:false,reason:'MISSING'};
          if((f.getAttribute('action')||'')!=='/cart/addoemsinglepart')
            return {clicked:false,reason:'ACTION'};
          if((f.getAttribute('method')||'').toUpperCase()!=='POST')
            return {clicked:false,reason:'METHOD'};
          if((f.querySelector('input[name="manf"]')?.value||'')!=='polaris_atv')
            return {clicked:false,reason:'MANF'};
          if((f.querySelector('input[name="sku"]')?.value||'')!==OEM)
            return {clicked:false,reason:'SKU'};
          if((f.querySelector('input[name="qty"]')?.value||'')!=='1')
            return {clicked:false,reason:'QTY'};
          b.click();
          return {clicked:true};
        })()""".replace("FORM",json.dumps("#add_"+oem)).replace(
            "BUTTON",json.dumps("#addtocart_"+oem)).replace("OEM",json.dumps(oem))
        data=self._evaluate(tab,expression)
        if not isinstance(data,dict) or data.get("clicked") is not True:
            raise ProbeFailure("ADD_CLICK_NOT_STARTED")
        time.sleep(self._poll)
        if self._verify_session() is not True:
            raise PermissionError("DEALER_SESSION_LOST_AFTER_ADD")
        return {
            "submitted":True,
            "requested_oem":proof["requested_oem"],
            "resolved_oem":proof["resolved_oem"],
            "cart_delta_verified":False,
        }

    def _cart_action(self,token,operation):
        if self.allow_mutation is not True:
            raise PermissionError("MUTATION_NOT_AUTHORIZED")
        token=str(token or "")
        if TOKEN_RE.fullmatch(token) is None:
            raise ValueError("CART_TOKEN_UNVERIFIED")
        if operation not in {"remove","decrease"}:
            raise ValueError("UNAPPROVED_CART_ACTION")
        tab=self._ensure_cart()
        path=f"/cart/{operation}/{token}"
        expression="""(()=>{
          const a=[...document.querySelectorAll('a[href^="/cart/"]')]
            .find(x=>(x.getAttribute('href')||'')===PATH);
          if(!a) return {started:false};
          location.assign(a.href);
          return {started:true};
        })()""".replace("PATH",json.dumps(path))
        data=self._evaluate(tab,expression)
        if not isinstance(data,dict) or data.get("started") is not True:
            raise ProbeFailure("CART_ACTION_NOT_STARTED")
        self._wait("/cart")
        return True

    def remove_line(self,token):
        return self._cart_action(token,"remove")

    def decrement_one(self,token):
        return self._cart_action(token,"decrease")

    def price(self,oem,snapshot):
        oem=self._oem(oem)
        lines=[x for x in canonical(snapshot)["lines"] if x["sku"]==oem]
        if len(lines)!=1:
            raise ValueError("AMBIGUOUS_SKU")
        return lines[0]["unit_price"]

"""Read-only WEB ADMIN analytics page. Mounted only behind unified analytics flag."""
from __future__ import annotations
import html
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from unified_analytics_core import query

_FIELDS = ("source_bot","oem","manufacturer","result_status","from_date","to_date")
_SOURCE_LABELS = {"probnik":"Пробник","pricing":"Проценка","oemixibot":"OEMixiBOT"}

def build_router(*,canonical_db,require_admin,audit):
    router=APIRouter(include_in_schema=False)
    @router.get("/admin/analytics",response_class=HTMLResponse)
    def page(request:Request,source_bot:str="",oem:str="",manufacturer:str="",
             result_status:str="",from_date:str="",to_date:str="",
             page:int=Query(1,ge=1,le=2001),actor=Depends(require_admin)):
        if not actor:raise HTTPException(status_code=403,detail="admin_required")
        if source_bot and source_bot not in _SOURCE_LABELS:
            raise HTTPException(status_code=400,detail="invalid_source")
        for value in (oem,manufacturer,result_status,from_date,to_date):
            if len(value)>128:raise HTTPException(status_code=400,detail="filter_too_long")
        filters={key:value or None for key,value in locals().copy().items() if key in _FIELDS}
        audit(actor=actor,action="analytics_ui_read",filters={k:bool(v) for k,v in filters.items()})
        limit=50
        try:
            data=query(canonical_db,filters=filters,limit=limit,offset=(page-1)*limit)
        except Exception:
            # Avoid exposing SQL details, local paths, identities, or secret values.
            raise HTTPException(status_code=503,detail="analytics_unavailable")
        esc=lambda value:html.escape(str(value if value is not None else ""),quote=True)
        inputs="".join(
            '<label>'+esc(label)+'<input name="'+key+'" value="'+esc(locals_filters.get(key,""))+'" maxlength="128"></label>'
            for key,label in (("oem","OEM"),("manufacturer","Производитель"),("result_status","Результат"),("from_date","Дата от (ISO)"),("to_date","Дата до (ISO)"))
            for locals_filters in [filters]
        )
        choices='<option value="">Все источники</option>'+''.join(
            '<option value="'+key+'"'+(' selected' if source_bot==key else '')+'>'+esc(label)+'</option>'
            for key,label in _SOURCE_LABELS.items())
        rows=[]
        for item in data["items"]:
            cols=(item.get("created_at"),_SOURCE_LABELS.get(item.get("source_bot"),item.get("source_bot")),
                  item.get("oem"),item.get("manufacturer"),item.get("result_status"),
                  item.get("display_price_amount"),item.get("display_price_currency"),
                  "Да" if item.get("cart_added") else "Нет","Да" if item.get("order_id") else "Нет")
            rows.append("<tr>"+''.join("<td>"+esc(v)+"</td>" for v in cols)+"</tr>")
        def url(p):
            params={key:value for key,value in filters.items() if value}
            params["page"]=p
            return "/admin/analytics?"+urlencode(params)
        total=int(data["total"])
        pages=max(1,(total+limit-1)//limit)
        previous='<a href="'+esc(url(page-1))+'">← Назад</a>' if page>1 else ""
        following='<a href="'+esc(url(page+1))+'">Далее →</a>' if page<pages else ""
        page_html="""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EXTREMIZER — Аналитика</title><style>
:root{font-family:system-ui,Arial,sans-serif;color:#141516;background:#f5f6f7}
body{margin:0}header{background:#141516;color:#fff;padding:18px 5%;display:flex;gap:28px;align-items:center;flex-wrap:wrap}
header a{color:#bff803}main{max-width:1250px;margin:auto;padding:20px}
form{display:flex;flex-wrap:wrap;gap:12px;align-items:end;background:#fff;border:1px solid #ddd;padding:16px;border-radius:12px}
label{display:grid;gap:5px;font-size:13px}input,select,button{padding:11px;border:1px solid #aaa;border-radius:8px;min-height:44px;box-sizing:border-box}
button{background:#141516;color:#bff803;cursor:pointer}.table{overflow-x:auto;background:#fff;border-radius:12px;margin-top:18px;border:1px solid #ddd}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:13px;text-align:left;border-bottom:1px solid #eee}
th{background:#eff0f2;white-space:nowrap}nav{display:flex;gap:20px;align-items:center;margin:18px 0}
@media(max-width:700px){main{padding:12px}form label{flex:1 1 42%}header{gap:10px}}
</style></head><body><header><strong>EXTREMIZER WEB ADMIN</strong><a href="/admin">Заказы</a><strong>Аналитика</strong></header>
<main><h1>Аналитика</h1><form method="get"><label>Источник<select name="source_bot">"""+choices+"""</select></label>"""+inputs+"""<button type="submit">Применить</button><a href="/admin/analytics">Сбросить</a></form>
<p>Всего заявок: <strong>"""+str(total)+"""</strong></p><div class="table"><table><thead><tr>"""+''.join("<th>"+s+"</th>" for s in ("Дата","Источник","OEM","Производитель","Результат","Цена","Валюта","Корзина","Заказ"))+"""</tr></thead><tbody>"""+(''.join(rows) or '<tr><td colspan="9">Нет заявок</td></tr>')+"""</tbody></table></div>
<nav>"""+previous+'<span>Страница '+str(page)+' / '+str(pages)+'</span>'+following+"""</nav></main></body></html>"""
        return HTMLResponse(page_html)
    return router

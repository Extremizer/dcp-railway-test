"""Keep all accepted WEB ADMIN/checkout bytes outside explicit DCP control scope."""
import ast
import hashlib

POINT10_WEB_APP_BLOB="c6c7ae46b313220c44e4ee726948f65543c287c7"
POINT10_WEB_OUTSIDE_DP_CONTROL_BLOB="3e8641f9c9e2a85b028d8598cc41199df8205f65"


def blob(source):
    data=source.encode()
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()


def protected_web_source(source):
    tree=ast.parse(source)
    expected={"DPSyncResultRequest","dp_sync_next","dp_sync_result"}
    targets=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in expected]
    if len(targets)!=3 or {n.name for n in targets}!=expected:
        raise AssertionError("DCP control scope must contain exactly three nodes")
    for n in targets:
        if n.name not in {"dp_sync_next","dp_sync_result"}:continue
        method,path=("get","/internal/dp-sync/next") if n.name=="dp_sync_next" else ("post","/internal/dp-sync/result")
        ds=n.decorator_list
        if (len(ds)!=1 or not isinstance(ds[0],ast.Call) or not isinstance(ds[0].func,ast.Attribute)
            or not isinstance(ds[0].func.value,ast.Name) or ds[0].func.value.id!="app"
            or ds[0].func.attr!=method or len(ds[0].args)!=1
            or not isinstance(ds[0].args[0],ast.Constant) or ds[0].args[0].value!=path):
            raise AssertionError("DCP route identity changed")
    targets += [n for n in tree.body if isinstance(n,ast.Import) and len(n.names)==1
                and n.names[0].name in {"dp_result_completion","dp_sync_protocol"} and n.names[0].asname is None]
    removed=set()
    for n in targets:
        start=min([n.lineno]+[d.lineno for d in getattr(n,"decorator_list",[])])
        removed.update(range(start-1,n.end_lineno))
    return "".join(line for i,line in enumerate(source.splitlines(keepends=True)) if i not in removed)


def require_web_admin_unchanged(source):
    if blob(source)==POINT10_WEB_APP_BLOB:return
    if blob(protected_web_source(source))!=POINT10_WEB_OUTSIDE_DP_CONTROL_BLOB:
        raise AssertionError("web_app.py changed outside authorized DCP result/claim scope")

"""Keep all accepted WEB ADMIN/checkout bytes outside explicit DCP control scope."""
import ast
import hashlib

POINT10_WEB_APP_BLOB="c6c7ae46b313220c44e4ee726948f65543c287c7"
POINT10_WEB_OUTSIDE_DP_CONTROL_BLOB="7c0f24af3601a53dfb30ae11a14ea8eccaa10e34"


OEMIXI_GATEWAY_IMPORT="import oemixibot_railway_gateway_mount\n"
OEMIXI_CATEGORY_MOUNT="""if os.getenv("OEMIXIBOT_TOKEN", "").strip():
    app.include_router(
        oemixi_category_authority_transport.build_router(
            db_file=core.ORDERS_DB_FILE,
            root_secret=os.getenv("OEMIXIBOT_TOKEN", "").strip(),
        )
    )


"""
OEMIXI_CATEGORY_AND_GATEWAY_MOUNT=OEMIXI_CATEGORY_MOUNT+"""_oemixi_gateway_secret = os.getenv("OEMIXIBOT_GATEWAY_SECRET", "").strip()
if _oemixi_gateway_secret:
    # Release-Control-only mount. A dedicated scoped secret is required; the
    # Telegram token and DCP credentials are never reused for this transport.
    oemixibot_railway_gateway_mount.attach_oemixi_gateway(
        app,
        coordination_db=core.ORDERS_DB_FILE,
        gateway_secret=_oemixi_gateway_secret,
        dealer_max_age_hours=24.0,
        msrp_max_age_hours=720.0,
        wait_seconds=5.0,
    )


"""


def blob(source):
    data=source.encode()
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()


def protected_web_source(source):
    # Release Control authorizes exactly one optional OEMixi secure-gateway
    # mount. Normalize only that byte-exact mount back to the previously pinned
    # category-only source before applying the original WEB ADMIN/DCP guard.
    if OEMIXI_CATEGORY_AND_GATEWAY_MOUNT in source:
        if (source.count(OEMIXI_CATEGORY_AND_GATEWAY_MOUNT)!=1
            or source.count(OEMIXI_GATEWAY_IMPORT)!=1):
            raise AssertionError("OEMixi gateway mount identity changed")
        source=source.replace(OEMIXI_GATEWAY_IMPORT,"",1)
        source=source.replace(
            OEMIXI_CATEGORY_AND_GATEWAY_MOUNT,
            OEMIXI_CATEGORY_MOUNT,
            1,
        )
    elif (OEMIXI_GATEWAY_IMPORT in source
          or "_oemixi_gateway_secret" in source
          or "attach_oemixi_gateway(" in source):
        raise AssertionError("OEMixi gateway mount identity changed")
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

"""Opt-in read-only ADMIN analytics router; not mounted into production."""
from fastapi import APIRouter,Depends,HTTPException,Query
from unified_analytics_core import query

def create_admin_router(*,canonical_db,require_admin,audit):
    if not callable(require_admin) or not callable(audit):raise ValueError("admin_auth_and_audit_required")
    router=APIRouter(prefix="/admin/internal/unified-analytics",include_in_schema=False)
    @router.get("/requests")
    def requests(source_bot:str|None=None,oem:str|None=None,manufacturer:str|None=None,
                 result_status:str|None=None,telegram_user_id:int|None=None,
                 source_chat_id:int|None=None,from_date:str|None=None,to_date:str|None=None,
                 limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=100000),
                 actor=Depends(require_admin)):
        if not actor:raise HTTPException(status_code=403,detail="admin_required")
        filters=locals().copy()
        audit(actor=actor,action="analytics_read",filters={k:v for k,v in filters.items()
              if k not in ('actor','telegram_user_id','source_chat_id') and v is not None})
        return query(canonical_db,filters=filters,limit=limit,offset=offset)
    return router

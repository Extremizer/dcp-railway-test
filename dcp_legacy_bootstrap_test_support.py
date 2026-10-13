"""Pinned source and mock helpers. No installed bot/agent imports."""
import ast,hashlib,hmac,json,secrets,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
from types import ModuleType,SimpleNamespace
from typing import Any
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

FIXTURE=json.loads(Path(__file__).with_name('dcp_legacy_v1_fixture.json').read_text())
from test_dp_result_completion import KEY  # Same test producer credential across paired protocol versions.

def source(name):
    r=FIXTURE['files'][name];s=r['selected_source']
    if hashlib.sha256(s.encode()).hexdigest()!=r['selected_sha256']:raise AssertionError('fixture corruption')
    return s

def bridge():
    m=ModuleType('isolated_legacy_bridge');exec(compile(source('dp_live_bridge.py'),'legacy_bridge','exec'),m.__dict__);return m

def http(db,legacy):
    finder=SimpleNamespace(manufacturer_alias=lambda m:m,normalize_oem=lambda o:str(o).strip().upper())
    cache={'ORDERS_DB_FILE':db,'finder':finder,'sqlite3':sqlite3,'datetime':datetime,'init_orders_db':lambda:None}
    nodes=[n for n in ast.parse(source('extremizer_bot.py')).body if isinstance(n,ast.FunctionDef)]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'legacy_cache','exec'),cache)
    app=FastAPI();ns={'app':app,'Header':Header,'HTTPException':HTTPException,'BaseModel':BaseModel,'Any':Any,
        'core':SimpleNamespace(ORDERS_DB_FILE=db,finder=finder,upsert_dealer_price_cache=cache['upsert_dealer_price_cache']),
        'dp_live_bridge':legacy,'_dp_sync_key':lambda:KEY,'time':time,'secrets':secrets,'hmac':hmac,'hashlib':hashlib,
        'sqlite3':sqlite3,'datetime':datetime,'timezone':timezone}
    exec(compile(source('web_app.py'),'legacy_http','exec'),ns);return TestClient(app)

def headers(path):
    ts=int(time.time());return {'X-DP-Sync-Ts':str(ts),'X-DP-Sync-Sig':hmac.new(KEY,f'v1\n{path}\n{ts}'.encode(),hashlib.sha256).hexdigest()}

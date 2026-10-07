#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared authenticated API used by WEB ADMIN; same service is used by Telegram."""
from __future__ import annotations
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
import supplier_admin_auth
import supplier_delivery

class RecipientIn(BaseModel):
    name:str; phone:str; city:str; method:str
    address:str|None=None; pickup_point:str|None=None
class CreateSupplierOrdersIn(BaseModel):
    client_order_id:str; recipient:RecipientIn
class StatusIn(BaseModel):
    status:str; details:str|None=None
class ProblemIn(BaseModel):
    item_id:int; confirmed_qty:float=Field(ge=0)
class TrackingIn(BaseModel):
    carrier:str; tracking_number:str
class BatchSendIn(BaseModel):
    supplier_order_ids:list[int]=Field(min_length=1)
class SupplierChannelIn(BaseModel):
    channel:str; recipient:str; custom_name:str|None=None

def _web_admin(
    request: Request,
    x_extremizer_admin_token: str | None = Header(default=None),
):
    try:
        return supplier_admin_auth.require_web_admin_access(
            header_token=x_extremizer_admin_token,
            session_cookie=request.cookies.get(supplier_admin_auth.WEB_ADMIN_COOKIE),
        )
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))

def build_supplier_router(service):
    r=APIRouter(prefix="/api/admin/supplier-orders",tags=["supplier-orders"])
    @r.get("")
    def queue(admin=Depends(_web_admin)): return {"groups":service.grouped_queue()}
    @r.get("/{supplier_order_id}")
    def get_one(supplier_order_id:int,admin=Depends(_web_admin)):
        try:return service.get(supplier_order_id)
        except KeyError:raise HTTPException(404,"supplier order not found")
    @r.post("/create")
    def create(body:CreateSupplierOrdersIn,admin=Depends(_web_admin)):
        try:return {"orders":service.create_from_client_order(body.client_order_id,body.recipient.model_dump())}
        except ValueError as e:raise HTTPException(409,str(e))
    @r.post("/batch/send")
    def batch_send(body:BatchSendIn,admin=Depends(_web_admin)):
        return {"results":supplier_delivery.send_batch(service,body.supplier_order_ids)}
    @r.post("/{supplier_order_id}/send")
    def send_one(supplier_order_id:int,admin=Depends(_web_admin)):
        try:return supplier_delivery.send_one(service,supplier_order_id)
        except (ValueError,RuntimeError,KeyError) as e:raise HTTPException(409,str(e))
    @r.post("/{supplier_order_id}/status")
    def status(supplier_order_id:int,body:StatusIn,admin=Depends(_web_admin)):
        try:return service.change_status(supplier_order_id,body.status,body.details)
        except (ValueError,KeyError) as e:raise HTTPException(409,str(e))
    @r.post("/{supplier_order_id}/problem")
    def problem(supplier_order_id:int,body:ProblemIn,admin=Depends(_web_admin)):
        try:return service.report_item_problem(supplier_order_id,body.item_id,body.confirmed_qty)
        except (ValueError,KeyError) as e:raise HTTPException(409,str(e))
    @r.post("/{supplier_order_id}/tracking")
    def tracking(supplier_order_id:int,body:TrackingIn,admin=Depends(_web_admin)):
        try:return service.add_tracking(supplier_order_id,body.carrier,body.tracking_number)
        except (ValueError,KeyError) as e:raise HTTPException(409,str(e))
    return r

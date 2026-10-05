#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single SupplierOrderService instance over the unified orders DB."""
from __future__ import annotations
from supplier_order_service import SupplierOrderService

_service=None
_db_key=None

def get_supplier_order_service(db_file):
    global _service,_db_key
    key=str(db_file)
    if _service is None or _db_key!=key:
        _service=SupplierOrderService(db_file)
        _db_key=key
    return _service

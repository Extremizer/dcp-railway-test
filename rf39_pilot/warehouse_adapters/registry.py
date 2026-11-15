#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from .orangeatv import OrangeATVAdapter
from .motoservice76 import Motoservice76Adapter
from .vladextremelife import VladExtremeLifeAdapter

_ADAPTERS = {
    "orangeatv": OrangeATVAdapter(),
    "motoservice76": Motoservice76Adapter(),
    "vladextremelife": VladExtremeLifeAdapter(),
}


def get_adapter(adapter_type: str):
    return _ADAPTERS.get(str(adapter_type or "").strip())


def supported_adapter_types() -> list[str]:
    return sorted(_ADAPTERS)

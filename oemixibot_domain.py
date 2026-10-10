"""OEMixiBOT v1 domain foundation. Isolated from production handlers."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class ItemStatus(str, Enum):
    ACCEPTED = "Принята"
    PROCESSING = "В обработке"
    TO_US_WAREHOUSE = "Едет на склад США"
    US_WAREHOUSE = "На складе США"
    TO_MOSCOW = "Едет в Москву"
    MOSCOW_WAREHOUSE = "На складе в Москве"
    SHIPPED_TO_DEALER = "Отправлен дилеру"
    RECEIVED_BY_DEALER = "Получен дилером"

class ExceptionStatus(str, Enum):
    DELAYED = "Задержка"
    UNAVAILABLE = "Не поставляется"
    DEALER_REFUSED = "Отказ дилером"
@dataclass(frozen=True)
class Dimensions:
    length_cm: float
    width_cm: float
    height_cm: float

    @property
    def volume_kg(self) -> float:
        return self.length_cm * self.width_cm * self.height_cm / 6000.0

@dataclass
class OemPhysicalData:
    oem: str
    actual_weight_kg: Optional[float] = None
    dimensions: Optional[Dimensions] = None
    volume_effective_kg: Optional[float] = None

    @property
    def volume_calculated_kg(self) -> Optional[float]:
        return self.dimensions.volume_kg if self.dimensions else None
@dataclass
class ItemSlice:
    order_id: str
    dealer_id: str
    oem: str
    qty: int
    status: ItemStatus
    exception: Optional[ExceptionStatus] = None
    supplier_tracking: Optional[str] = None
    usa_shipment_id: Optional[str] = None

    def split(self, qty: int) -> tuple["ItemSlice", "ItemSlice"]:
        if qty <= 0 or qty >= self.qty:
            raise ValueError("split qty must be between 1 and qty-1")
        moved = ItemSlice(**{**self.__dict__, "qty": qty})
        rest = ItemSlice(**{**self.__dict__, "qty": self.qty - qty})
        return moved, rest

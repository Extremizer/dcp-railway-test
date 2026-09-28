import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
DB = Path(tempfile.gettempdir()) / "extremizer_candidate_cloudtest.db"
if DB.exists():
    DB.unlink()

os.environ["EXTREMIZER_ORDERS_DB_FILE"] = str(DB)
sys.path.insert(0, str(ROOT))

import extremizer_bot as bot
import warehouse_admin
import warehouse_store
import warehouse_stock_service
import stock_engine

bot.init_orders_db()
warehouse_admin.configure(DB, bot.RATE_ADMIN_USER_ID, bot.safe_reply_text, bot.log)
warehouse_store.seed_initial_warehouses(DB)

warehouses = warehouse_store.list_warehouses(True, DB)
print("WAREHOUSES", [(w["code"], w["public_name"], w["adapter_type"]) for w in warehouses], flush=True)

tests = [
    (1, "417300574"),
    (2, "518327485"),
    (3, "417300574"),
]
for warehouse_id, oem in tests:
    result = warehouse_stock_service.refresh_warehouse_oem(
        warehouse_id, oem, DB
    )
    stock = stock_engine.get_available_stock(warehouse_id, oem, DB)
    print(
        "CLOUD_CHECK",
        warehouse_id,
        oem,
        result.status,
        result.quantity,
        stock.get("available_quantity"),
        stock.get("source_type"),
        flush=True,
    )

msk = stock_engine.get_available_stock(1, "417300574", DB)
assert msk["available_quantity"] == 20
r = stock_engine.reserve_stock(
    1,
    "417300574",
    2,
    order_id="CLOUD-TEST",
    order_item_id=9000001,
    db_file=DB,
)
assert r["ok"] and r["available_quantity"] == 18
assert stock_engine.release_order_reservations(
    "CLOUD-TEST", "cloud test", DB
) == 1
assert stock_engine.get_available_stock(
    1, "417300574", DB
)["available_quantity"] == 20

safe = warehouse_stock_service.client_stock_summary("417300574", DB)
for row in safe:
    assert "http" not in str(row).lower()
    assert "Orange ATV" not in str(row)

print("WAREHOUSE_CANDIDATE_CLOUDTEST_OK", flush=True)

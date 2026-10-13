"""Byte preservation outside the two authorized Procenka DP consumer spans.

Baseline: accepted PR #33 HEAD 3325f309. Existing finance/web guards retained.
"""
import ast
import hashlib

BASELINE_OUTSIDE_DP_SHA256 = "8367231d05ee2a2e7bb7ac7e842322f7e0bdd12b336951843190201ac341153d"


def protected_procenka_source(source):
    lines = source.splitlines(keepends=True)
    targets = [n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "enrich_found_result_with_dealer_price"]
    if len(targets) != 1:
        raise AssertionError("exactly one approved DP enrichment function required")
    n = targets[0]
    del lines[n.lineno-1:n.end_lineno]
    source = "".join(lines)
    start = source.index('                _live=await asyncio.to_thread(dp_live_bridge.request_live_dp,ORDERS_DB_FILE,_m,x["oem"],wait_seconds=10.0)')
    end = source.index('            if _dp is not None:', start)
    return source[:start] + source[end:]


def require_procenka_unchanged(source):
    if hashlib.sha256(protected_procenka_source(source).encode()).hexdigest() != BASELINE_OUTSIDE_DP_SHA256:
        raise AssertionError("Procenka changed outside approved DP single-writer scope")

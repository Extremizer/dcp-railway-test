"""Canonical manufacturer-first Telegram search state, shared by all three bots.

The DCP V6.6 CATALOGS list is the authority for button names/order.
No manufacturer inference is permitted in an OEM search session.
"""
import re
import dealercostparts_manufacturer_finder_v6_6 as finder

MANUFACTURERS = tuple(dict.fromkeys(c.manufacturer for c in finder.CATALOGS))
CALLBACK_TO_MANUFACTURER = {f"mfg:{i}": value for i, value in enumerate(MANUFACTURERS)}

def selected(state):
    value = state.get("manufacturer")
    return value if value in MANUFACTURERS else None

def choose(state, value):
    if value not in MANUFACTURERS:
        return False
    clear_search(state)
    state["manufacturer"] = value
    return True

def clear_search(state):
    for key in ("manufacturer","last_oem","last_oems","last_jobs",
                "last_search_was_mixed","found_items","pending_oem",
                "admin_last_oem","admin_oem_result"):
        state.pop(key, None)
    for key in list(state):
        if key.startswith("offer:"):
            state.pop(key, None)

def exact_oem(raw):
    """One OEM token, never a manufacturer header or mixed-brand request."""
    raw = str(raw or "").strip()
    if not raw or len(raw.split()) != 1 or finder.manufacturer_alias(raw) in MANUFACTURERS:
        return None
    try:
        return finder.normalize_oem(raw)
    except (ValueError, AttributeError, TypeError):
        return None

def oem_batch(raw, *, max_count=20):
    """Only OEM tokens; textual manufacturer labels are never treated as commands."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    # Detect brand headers, including when they precede a valid OEM.
    folded = raw.casefold()
    if any(re.search(r"(?<![\w])"+re.escape(name.casefold())+r"(?![\w])", folded)
           for name in MANUFACTURERS):
        return None
    values = [part for part in re.split(r"[\s,;]+", raw) if part]
    if not values or len(values)>max_count:
        return None
    normalized = [exact_oem(p) for p in values]
    return normalized if all(normalized) else None

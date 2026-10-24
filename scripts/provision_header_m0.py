"""Provision owner-approved Global Header v1.1 assets for isolated staging only.

Usage: eval "$(python scripts/provision_header_m0.py /path/canonical.zip /tmp/header-assets)"
Then launch the *isolated* staging server in the same shell.
No asset substitution; no production side effects.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import shlex
import sys
import zipfile

ZIP_SHA = "4d6827e12f1f8d5bda25309bbbd637a191663dcdab24e0511d299446318bd4ee"
ASSETS = {
    "wordmark": ("02_CANONICAL_WORDMARK/Extremizer_vector(5).svg", "0651e2d482c496ac135497910506b1ae7d0672d55d6e47f9cf7f87f87b3c5afa", "EXTREMIZER_HEADER_WORDMARK_PATH", "wordmark.svg"),
    "search": ("04_HEADER_ICONS/поиск 777!!! для шапки(1).png", "26d942c4f05047ae12aab0de024dbc9723a8179789385c16649d13df2b89d2d8", "EXTREMIZER_HEADER_SEARCH_PATH", "search.png"),
    "cart": ("04_HEADER_ICONS/корзина для шапки(1).png", "920a3463a55a0b0dd3ea21155b3f3ba185ed60df066cec40bb3591e15af2a28b", "EXTREMIZER_HEADER_CART_PATH", "cart.png"),
    "private": ("04_HEADER_ICONS/личка 77 для шапки(1).png", "c40e3950728ae670d1ca7c1a02d507a1a5d5eaa7f4b7aad09c4396b4b928cb3c", "EXTREMIZER_HEADER_PRIVATE_PATH", "private.png"),
}
def provision(zip_path: Path, dest: Path) -> dict[str, Path]:
    blob = zip_path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != ZIP_SHA:
        raise ValueError("Canonical ZIP SHA-256 mismatch")
    verified = {}
    with zipfile.ZipFile(zip_path) as archive:
        for kind,(suffix,expected,env,filename) in ASSETS.items():
            matches=[name for name in archive.namelist() if name.endswith("/"+suffix)]
            if len(matches)!=1:
                raise ValueError(f"Expected exactly one original {kind}")
            raw=archive.read(matches[0])
            if hashlib.sha256(raw).hexdigest()!=expected:
                raise ValueError(f"Original {kind} SHA-256 mismatch")
            verified[kind]=raw
    dest.mkdir(parents=True,exist_ok=True)
    paths={}
    for kind,(_,_,env,filename) in ASSETS.items():
        output=dest/filename
        output.write_bytes(verified[kind])
        paths[env]=output.resolve()
    return paths

def main():
    if len(sys.argv)!=3:
        raise SystemExit("Usage: provision_header_m0.py CANONICAL_ZIP STAGING_ASSET_DIR")
    paths=provision(Path(sys.argv[1]),Path(sys.argv[2]))
    for env,path in paths.items():
        print(f"export {env}={shlex.quote(str(path))}")
if __name__=="__main__":
    main()

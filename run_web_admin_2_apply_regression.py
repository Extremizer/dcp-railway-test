"""Run Apply regressions with production SQLite and outbound connections denied."""
import pathlib
import sys
import tempfile
import unittest
import urllib.parse


def install_isolation_guard():
    temp_root = pathlib.Path(tempfile.gettempdir()).resolve()
    def audit(event, args):
        if event == "sqlite3.connect":
            value = str(args[0])
            if value == ":memory:":
                return
            if value.startswith("file:"):
                value = urllib.parse.unquote(urllib.parse.urlsplit(value).path)
            path = pathlib.Path(value).resolve()
            if temp_root not in path.parents or path.name != "fixture.sqlite3":
                raise RuntimeError("Regression attempted non-fixture SQLite access")
        if event in {"socket.connect", "socket.getaddrinfo", "urllib.Request"}:
            raise RuntimeError("Regression attempted outbound network access")
    sys.addaudithook(audit)


if __name__ == "__main__":
    install_isolation_guard()
    modules = [
        "test_web_admin_2_apply_isolated", "test_web_admin_2_apply_csrf",
        "test_web_admin_2_apply_http", "test_web_admin_2_apply_real_sqlite",
    ]
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)

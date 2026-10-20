import importlib
import sys
import time

from sanic_session.base import SessionDict
from sanic_session.utils import ExpiringDict


def test_get_returns_value_before_expiry():
    e = ExpiringDict()
    e.set("foo", "bar", 300)
    assert e.get("foo") == "bar"
    assert "foo" in e


def test_expired_value_is_removed():
    e = ExpiringDict()
    e.set("foo", "bar", 0.01)
    time.sleep(0.02)
    assert e.get("foo") is None
    assert len(e) == 0


def test_no_expiry():
    e = ExpiringDict()
    for expiry in (0, None):
        e.set("foo", "bar", expiry)
        assert e.get("foo") == "bar"


def test_falsy_values_are_kept():
    e = ExpiringDict()
    e.set("foo", "", 300)
    assert e.get("foo") == ""
    assert "foo" in e


def test_delete():
    e = ExpiringDict()
    e.set("foo", "bar", 300)
    e.delete("foo")
    e.delete("missing")
    assert e.get("foo") is None


def test_abandoned_entries_are_purged():
    e = ExpiringDict(purge_interval=0)
    for n in range(1000):
        e.set(f"k{n}", "v", 0.01)
    time.sleep(0.02)
    e.set("new", "v", 300)
    assert len(e) == 1


def test_lookup_does_not_copy_the_store():
    e = ExpiringDict()
    for n in range(100000):
        e.set(f"k{n}", "v", 300)
    start = time.perf_counter()
    for _ in range(1000):
        e.get("k5")
    assert (time.perf_counter() - start) / 1000 < 0.0005


def test_session_dict_tracks_changes():
    s = SessionDict({"a": 1}, sid="x")
    assert not s.modified
    s.get("a")
    assert not s.modified
    s["b"] = 2
    assert s.modified


def test_package_imports_when_a_driver_breaks_on_import(monkeypatch):
    class Broken:
        def find_spec(self, name, path=None, target=None):
            if name == "aiomcache":
                raise TypeError("duplicate base class TimeoutError")

    names = ("sanic_session", "aiomcache")
    loaded = [m for m in sys.modules if m.split(".")[0] in names]
    for mod in loaded:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(sys, "meta_path", [Broken()] + sys.meta_path)
    module = importlib.import_module("sanic_session")
    assert module.InMemorySessionInterface

import time
from typing import Any, Union


class _Missing(object):
    """
    Copyright (c) 2015 by Armin Ronacher and contributors.  See AUTHORS
    in FLASK_LICENSE for more details.
    """

    def __repr__(self):
        return "no value"

    def __reduce__(self):
        return "_missing"


_missing = _Missing()


class UpdateDictMixin(object):
    """
    Copyright (c) 2015 by Armin Ronacher and contributors.  See AUTHORS
    in FLASK_LICENSE for more details.
    """

    on_update = None

    def calls_update(name):
        def oncall(self, *args, **kw):
            rv = getattr(super(UpdateDictMixin, self), name)(*args, **kw)
            if self.on_update is not None:
                self.on_update(self)
            return rv

        oncall.__name__ = name
        return oncall

    def setdefault(self, key, default=None):
        modified = key not in self
        rv = super(UpdateDictMixin, self).setdefault(key, default)
        if modified and self.on_update is not None:
            self.on_update(self)
        return rv

    def pop(self, key, default=_missing):
        modified = key in self
        if default is _missing:
            rv = super(UpdateDictMixin, self).pop(key)
        else:
            rv = super(UpdateDictMixin, self).pop(key, default)
        if modified and self.on_update is not None:
            self.on_update(self)
        return rv

    __setitem__ = calls_update("__setitem__")
    __delitem__ = calls_update("__delitem__")
    clear = calls_update("clear")
    popitem = calls_update("popitem")
    update = calls_update("update")
    del calls_update


class CallbackDict(UpdateDictMixin, dict):

    """A dict that calls a function passed every time something is changed.
    The function is passed the dict instance.

    Copyright (c) 2015 by Armin Ronacher and contributors.  See AUTHORS
    in FLASK_LICENSE for more details.

    """

    def __init__(self, initial=None, on_update=None):
        dict.__init__(self, initial or ())
        self.on_update = on_update

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, dict.__repr__(self))


class ExpiringDict:
    """A store whose entries expire.

    Lookups are O(1).  Expired entries are removed when they are read, and
    all of them at most every ``purge_interval`` seconds (checked on ``set``),
    so abandoned entries do not accumulate.
    """

    def __init__(self, purge_interval: float = 60):
        self._data = {}  # key -> (value, expires_at or None for no expiry)
        self.purge_interval = purge_interval
        self._next_purge = time.monotonic() + purge_interval

    def set(self, key: Union[str, int], val: Any, expiry: Union[int, None]):
        """Store ``val`` for ``expiry`` seconds (0 or None: no expiry)."""
        expires_at = time.monotonic() + expiry if expiry else None
        self._data[key] = (val, expires_at)
        if time.monotonic() >= self._next_purge:
            self.purge()

    def get(self, key: Union[str, int]):
        """Return the stored value, or None if missing or expired."""
        item = self._data.get(key)
        if item is None:
            return None
        val, expires_at = item
        if expires_at is not None and time.monotonic() > expires_at:
            del self._data[key]
            return None
        return val

    def delete(self, key: Union[str, int]):
        self._data.pop(key, None)

    def purge(self):
        """Remove every expired entry."""
        now = time.monotonic()
        self._data = {
            k: v for k, v in self._data.items() if v[1] is None or v[1] >= now
        }
        self._next_purge = now + self.purge_interval

    def __contains__(self, key):
        return self.get(key) is not None

    def __len__(self):
        return len(self._data)

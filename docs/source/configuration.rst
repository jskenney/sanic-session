.. _configuration:

Configuration
=========================================

When initializing a session interface, you have a number of optional arguments for configuring your session.

**domain** (str, optional):
    Optional domain which will be attached to the cookie. Defaults to None.
**expiry** (int, optional):
    Seconds until the session expires. Defaults to *2592000* (30 days). The cookie gets a matching ``Max-Age`` and a UTC ``Expires``. ``0`` or ``None`` means no expiry: the cookie lasts until the browser closes, and the store keeps the session with no time limit. memcached keeps an entry at most 30 days, so with longer values a session unused for 30 days expires in the store (a warning says so).
**httponly** (bool, optional):
    Adds the ``HttpOnly`` flag to the session cookie. Defaults to True.
**cookie_name** (str, optional):
    Name used for the client cookie. Defaults to "session". A ``__Host-`` name (e.g. ``__Host-session``) works, because the cookie is always sent with ``Path=/``; keep ``secure=True`` and no ``domain``.
**prefix** (str, optional):
    Storage keys will take the format of ``prefix+<session_id>``. Defaults to "session:".
**sessioncookie** (bool, optional):
    Omits ``Max-Age`` and ``Expires``, so the browser deletes the cookie when it closes. The ``expiry`` option is still honored in the store. Disabled by default.
**samesite** (str, optional):
    One of ``'Lax'`` (default), ``'Strict'`` or ``'None'``; ``None`` leaves the attribute out.
**session_name** (str, optional):
    | Name of the session that will be accessible through the request.
    | e.g. If ``session_name`` is ``alt_session``, it should be accessed like that: ``request.ctx.alt_session``
    | e.g. And if ``session_name`` is left to default, it should be accessed like that: ``request.ctx.session``

    .. note::

        If you choose to build your application using more than one session object, make sure that they have different:

            1. ``cookie_name``
            2. ``prefix`` (Only if the two cookies share the same store)
            3. And obviously, different: ``session_name``
**secure** (bool, optional):
    Adds the ``Secure`` flag, so browsers only send the cookie over HTTPS (``http://localhost`` also counts as secure). Defaults to True; set False only for plain-HTTP development on another host.

Behaviour
---------

* **Saving:** a session is written to the store, and its cookie sent, only when it was changed during the request. An emptied session (``request.ctx.session.clear()``) is deleted from the store and its cookie removed. Requests that never touch the session cause no store traffic.
* **Session ids:** ids are random (``uuid4().hex``). A cookie that is not a session id, or names a session that is not in the store, gets a new id, so a client cannot choose its own. Call ``await interface.regenerate(request)`` when a user logs on or their privileges change, to move the session to a new id.
* **Store errors:** if the store cannot be read, or holds data that is not valid, the request gets a new, empty session and a warning is logged. If it cannot be written, the response is replaced with ``503 Service Unavailable`` (and an error is logged), so a client never believes a change such as a logon was kept when it was not.
* **Middleware order:** the session is opened before the application's request middleware (so they can use it), and saved before its response middleware run. Change the session in handlers or request middleware.
* **Concurrency:** each request saves the whole session when it changed, so when two requests for the same session overlap, the last to finish wins and changes the other made to different keys are lost.

**Example:**

.. code-block:: python

    session_interface = InMemorySessionInterface(
        domain='.example.com', expiry=3600, sessioncookie=True,
        cookie_name="myapp", prefix="session:", samesite="Strict")

Will result in a session that:

- Will be valid only on *example.com*.
- Will expire on the server side after 1 hour without changes.
- Will be deleted when the user closes their browser.
- Will be named "myapp" on the client, and "session:<sid>" in the store.
- Will not be sent with cross-site requests.

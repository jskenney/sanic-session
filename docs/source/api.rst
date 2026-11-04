.. _api:

API Documentation
=====================

Session
------------------------------------------------

.. autoclass:: sanic_session.Session
    :members:

InMemorySessionInterface
------------------------------------------------

.. autoclass:: sanic_session.InMemorySessionInterface
    :members: open, save, regenerate

MemcacheSessionInterface
-----------------------------------------------

.. autoclass:: sanic_session.MemcacheSessionInterface
    :members: open, save, regenerate

SessionStoreError
-----------------------------------------------

.. autoclass:: sanic_session.SessionStoreError

SessionConflictError
-----------------------------------------------

.. autoclass:: sanic_session.SessionConflictError

SessionSerializationError
-----------------------------------------------

.. autoclass:: sanic_session.SessionSerializationError

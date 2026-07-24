"""Shared CSRF protection extension.

The ``CSRFProtect`` instance lives in its own module (mirroring
``scoring_engine.cache``) so that individual view modules can mark
machine-to-machine endpoints as exempt with ``@csrf.exempt`` without importing
``scoring_engine.web`` and creating a circular import.

``init_app`` is called from :func:`scoring_engine.web.create_app`.
"""

from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()

"""Shared CSRF protection extension and error-response helpers.

The ``CSRFProtect`` instance lives in its own module (mirroring
``scoring_engine.cache``) so that individual view modules can mark
machine-to-machine endpoints as exempt with ``@csrf.exempt`` without importing
``scoring_engine.web`` and creating a circular import.

``init_app`` is called from :func:`scoring_engine.web.create_app`, which also
registers the :class:`~flask_wtf.csrf.CSRFError` handler that uses the two
helpers below.
"""

from urllib.parse import urlparse

from flask import request
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()

#: Body encodings a browser uses when it submits a native <form method="POST">.
FORM_MIMETYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def is_browser_form_submission():
    """True when the current request is a native browser ``<form>`` submission.

    This is the JSON-vs-HTML discriminator for the CSRF error handler, and it
    is deliberately built on **request headers**, not on the URL prefix.

    A ``request.path.startswith("/api/")`` test looks like the obvious answer
    but is simply wrong for this app: the admin and profile screens submit
    real, non-JavaScript forms straight at ``/api/...`` endpoints (see
    ``templates/admin/manage.html``, ``admin/settings.html``, ``admin/sla.html``
    and ``profile.html``, all of which POST to ``url_for('api....')``).  The
    path therefore says nothing about whether the caller can render HTML.

    Headers do say something, and every scripted caller in this app sets one:

    * jQuery adds ``X-Requested-With: XMLHttpRequest`` to same-origin requests,
      which covers every ``$.ajax()`` call in the templates -- the entire admin
      UI, the inject workflow and the service editors.
    * Dropzone (``templates/inject.html``) sets ``X-Requested-With`` *and*
      ``Accept: application/json`` from its own XHR.
    * A ``fetch()`` posting JSON is caught by ``request.is_json``.

    A browser posting a real form sets none of those: no ``X-Requested-With``,
    a form body encoding, and an ``Accept`` header that ranks ``text/html``
    above ``*/*``.  All three must line up before we answer with HTML, so the
    default for anything ambiguous (curl, a scripted client, a bare
    ``Accept: */*``) stays the machine-readable JSON response.
    """
    if request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest":
        return False
    if request.is_json:
        return False
    if request.mimetype not in FORM_MIMETYPES:
        return False
    accept = request.accept_mimetypes
    return accept.quality("text/html") > accept.quality("application/json")


def form_retry_target(fallback):
    """Where to send a browser whose form POST was rejected for a bad token.

    Back to the page the form lives on: re-rendering it mints a token from the
    *current* session, so the user simply resubmits instead of staring at a
    dead-end error document.  Only same-origin referrers are honoured, so this
    cannot be turned into an open redirect by a crafted ``Referer``.
    """
    referrer = request.referrer
    if referrer:
        parsed = urlparse(referrer)
        same_origin = not parsed.netloc or parsed.netloc == request.host
        if same_origin and parsed.path.startswith("/"):
            return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
    return fallback

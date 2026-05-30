"""Shared SSL context built from certifi's bundled CA roots.

PyInstaller freezes whatever CA bundle ships with the Python build at
freeze time. When a root in that bundle expires (or the bundle predates
a CA the server now uses), every HTTPS call from the frozen exe fails
with CERTIFICATE_VERIFY_FAILED until the next rebuild. GitHub #175,
#178 and #179 are the same root cause.

certifi's cacert.pem is updated against Mozilla on every release, so
pinning ``certifi>=2024.0`` in pyproject.toml and bundling certifi's
data files via cdumm.spec keeps the trust store current.
"""
import ssl

import certifi


def make_ssl_context() -> ssl.SSLContext:
    """Return a default-verifying SSL context backed by certifi's roots."""
    return ssl.create_default_context(cafile=certifi.where())

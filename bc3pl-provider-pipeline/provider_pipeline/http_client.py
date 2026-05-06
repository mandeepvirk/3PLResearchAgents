from __future__ import annotations

import ssl


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError as error:
        raise RuntimeError(
            "Missing certifi CA bundle. Run `python3 -m pip install -r requirements.txt`."
        ) from error

    return ssl.create_default_context(cafile=certifi.where())

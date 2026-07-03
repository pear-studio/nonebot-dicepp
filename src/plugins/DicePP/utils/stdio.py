"""Runtime stdio bootstrap helpers."""

from __future__ import annotations

import sys
from typing import Iterable, TextIO


def configure_redirected_stdio_utf8(
    streams: Iterable[TextIO | None] | None = None,
) -> None:
    """Make redirected text streams emit UTF-8 bytes.

    Windows frozen runtimes can inherit redirected stdout/stderr text streams
    using the active ANSI code page. Dashboard captures those streams as raw
    bytes and later reads the runtime log as UTF-8, so redirected streams must
    be UTF-8 before logging handlers bind to them.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        if stream is None or _is_tty(stream):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _is_tty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except Exception:
        return False

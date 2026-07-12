#!/usr/bin/env python3
"""Compatibility entrypoint for the Fusion Reader v2 HTTP server."""

from fusion_reader_v2.web.server import (
    Handler,
    INDEX_HTML,
    PORT,
    ROOT,
    RUNTIME_INFO,
    create_http_server,
    main,
)

__all__ = [
    "Handler",
    "INDEX_HTML",
    "PORT",
    "ROOT",
    "RUNTIME_INFO",
    "create_http_server",
    "main",
]


if __name__ == "__main__":
    main()

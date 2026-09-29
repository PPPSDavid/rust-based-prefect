"""CLI entry: ``ironflow-mcp`` / ``python -m ironflow_mcp``."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ironflow-mcp",
        description=(
            "Read-only IronFlow MCP server (FastMCP). "
            "Uses IRONFLOW_API_URL / IRONFLOW_API_AUTH_STRING."
        ),
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http"],
        help="MCP transport (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP bind host (transport=http only).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="HTTP bind port (transport=http only; default 8001).",
    )
    args = parser.parse_args(argv)

    from .server import create_server

    mcp = create_server()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()

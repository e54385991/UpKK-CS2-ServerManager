"""Security and compatibility coverage for the AI assistant foundation."""

from __future__ import annotations

import json

import httpx


def _sse_response(*chunks: dict) -> httpx.Response:
    body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks)
    body += "data: [DONE]\n\n"
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream; charset=utf-8"},
        content=body.encode(),
    )

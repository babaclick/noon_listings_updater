"""One public port for Render: Clerk sign-in on this origin, Streamlit behind it."""

from __future__ import annotations

import inspect
import json

import httpx
import uvicorn
import websockets
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from clerk_page import sign_in_html
from settings import clerk_publishable_key, listen_port, public_origin, streamlit_port

_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}
_client: httpx.AsyncClient | None = None


def _streamlit_base() -> str:
    return f"http://127.0.0.1:{streamlit_port()}"


def _http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=False)
    return _client


async def sign_in_page(_: Request) -> HTMLResponse:
    key = clerk_publishable_key()
    if not key:
        return HTMLResponse("<p>Clerk is not configured.</p>", status_code=503)
    try:
        page = sign_in_html(key)
    except ValueError as exc:
        return HTMLResponse(f"<p>{exc}</p>", status_code=503)
    return HTMLResponse(page)


async def sign_in_session(request: Request) -> Response:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin and origin != public_origin():
        return Response(status_code=403)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return Response(status_code=400)
    token = str(payload.get("token") or "").strip()
    if not token:
        return Response(status_code=400)
    response = Response(status_code=204)
    response.set_cookie(
        "noon_clerk_token",
        token,
        max_age=120,
        httponly=True,
        secure=public_origin().startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


async def proxy_http(request: Request) -> Response:
    url = _streamlit_base() + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP and key.lower() != "host"
    }
    upstream = await _http_client().request(
        request.method,
        url,
        headers=headers,
        content=await request.body(),
    )
    response_headers = [
        (key, value)
        for key, value in upstream.headers.multi_items()
        if key.lower() not in _HOP
    ]
    return Response(upstream.content, status_code=upstream.status_code, headers=response_headers)


def _header_argument() -> str:
    parameters = inspect.signature(websockets.connect).parameters
    if "additional_headers" in parameters:
        return "additional_headers"
    return "extra_headers"


async def proxy_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    path = websocket.url.path
    query = websocket.url.query
    upstream_url = f"ws://127.0.0.1:{streamlit_port()}{path}"
    if query:
        upstream_url += "?" + query
    headers = {}
    cookie = websocket.headers.get("cookie")
    if cookie:
        headers["Cookie"] = cookie
    try:
        connect_kwargs = {
            _header_argument(): headers,
            "ping_interval": 20,
            "max_size": None,
        }
        if "proxy" in inspect.signature(websockets.connect).parameters:
            connect_kwargs["proxy"] = None
        async with websockets.connect(upstream_url, **connect_kwargs) as upstream:
            async def from_browser() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def from_streamlit() -> None:
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)

            import asyncio

            tasks = [
                asyncio.create_task(from_browser()),
                asyncio.create_task(from_streamlit()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result() if not task.cancelled() else None
    except (WebSocketDisconnect, OSError, websockets.WebSocketException):
        pass
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


def build_app() -> Starlette:
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
    return Starlette(
        routes=[
            Route("/sign-in", sign_in_page, methods=["GET"]),
            Route("/sign-in/", sign_in_page, methods=["GET"]),
            Route("/sign-in/session", sign_in_session, methods=["POST"]),
            Route("/", proxy_http, methods=methods),
            Route("/{path:path}", proxy_http, methods=methods),
            WebSocketRoute("/{path:path}", proxy_ws),
        ]
    )


def serve() -> None:
    uvicorn.run(build_app(), host="0.0.0.0", port=listen_port(), log_level="info")

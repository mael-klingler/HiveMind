"""
API routes: Agent Session Proxy (HTTP + WebSocket)
"""

import asyncio
import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from database import get_ticket
from logging_setup import log, metrics
from config import AGENT_NAMESPACE

router = APIRouter()

PROXY_PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD", "")


def _verify_proxy_auth(request: Request) -> bool:
    if not PROXY_PASSWORD:
        return True
    if request.query_params.get("password") == PROXY_PASSWORD:
        return True
    if request.cookies.get("opencode_password") == PROXY_PASSWORD:
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == PROXY_PASSWORD:
        return True
    return False


AGENT_SESSION_TIMEOUT = 30
_agent_http_client: Optional[httpx.AsyncClient] = None


def _get_agent_http_client() -> httpx.AsyncClient:
    global _agent_http_client
    if _agent_http_client is None or _agent_http_client.is_closed:
        _agent_http_client = httpx.AsyncClient(timeout=AGENT_SESSION_TIMEOUT)
    return _agent_http_client


def _resolve_pod_url(ticket_id: str) -> Optional[str]:
    ticket = get_ticket(ticket_id)
    if not ticket:
        return None
    pod_name = f"agent-worker-{ticket_id.lower()}"
    namespace = os.getenv("AGENT_NAMESPACE", "hivemind")

    from k8s_client import get_pod_ip
    pod_ip = get_pod_ip(pod_name, namespace)
    if pod_ip:
        return f"http://{pod_ip}:4096"

    from k8s_client import get_pod_phase
    phase = get_pod_phase(pod_name, namespace)
    if not phase:
        return None

    return f"http://{pod_name}.agent-session.{namespace}.svc.cluster.local:4096"


async def _proxy_request(ticket_id: str, path: str, request: Request) -> Response:
    base_url = _resolve_pod_url(ticket_id)
    if not base_url:
        raise HTTPException(status_code=404, detail=f"No active agent for ticket {ticket_id}")

    url = f"{base_url}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"

    client = _get_agent_http_client()

    headers = {}
    for key, value in request.headers.items():
        if key.lower() not in ("host", "transfer-encoding", "connection"):
            headers[key] = value

    body = await request.body()

    try:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        err_msg = str(e)
        if "Name or service not known" in err_msg or "Connection refused" in err_msg:
            raise HTTPException(status_code=503, detail=f"Agent pod not ready yet (still starting): {e}")
        raise HTTPException(status_code=502, detail=f"Agent pod not reachable: {e}")

    metrics.inc("hivemind_proxy_requests_total", labels={"method": request.method})

    response_headers = {}
    for key, value in resp.headers.items():
        if key.lower() not in ("transfer-encoding", "content-encoding", "connection"):
            response_headers[key] = value

    content = resp.content
    content_type = resp.headers.get("content-type", "")

    prefix = f"/agent-session/{ticket_id}"

    if "text/html" in content_type:
        html = content.decode("utf-8", errors="replace")
        html = html.replace('href="/', f'href="{prefix}/')
        html = html.replace('src="/', f'src="{prefix}/')
        html = html.replace("href='/", f"href='{prefix}/")
        html = html.replace("src='/", f"src='{prefix}/")
        html = html.replace('content="/', f'content="{prefix}/')
        html = html.replace('action="/', f'action="{prefix}/')
        proxy_script = (
            f'<script>(function(){{'
            f'var P="{prefix}";'
            f'var _f=window.fetch;'
            f'window.fetch=function(input,init){{'
            f'if(typeof input==="string"){{'
            f'if(input.startsWith("/")&&!input.startsWith(P))input=P+input;'
            f'}} else if(input instanceof Request){{'
            f'var nu=new URL(input.url,location.origin);'
            f'if(nu.pathname.startsWith("/")&&!nu.pathname.startsWith(P)){{'
            f'nu.pathname=P+nu.pathname;'
            f'input=new Request(nu.toString(),input);'
            f'}}}}'
            f'return _f.call(this,input,init);'
            f'}};'
            f'var _WS=window.WebSocket;'
            f'window.WebSocket=function(url,protocols){{'
            f'var a=new URL(url,location.origin);'
            f'if(a.pathname.startsWith("/")&&!a.pathname.startsWith(P))a.pathname=P+a.pathname;'
            f'return new _WS(a.toString(),protocols);'
            f'}};'
            f'window.WebSocket.prototype=_WS.prototype;'
            f'window.WebSocket.CONNECTING=_WS.CONNECTING;'
            f'window.WebSocket.OPEN=_WS.OPEN;'
            f'window.WebSocket.CLOSING=_WS.CLOSING;'
            f'window.WebSocket.CLOSED=_WS.CLOSED;'
            f'}})()</script>'
        )
        html = html.replace("</head>", f"{proxy_script}</head>")
        content = html.encode("utf-8")
        response_headers["content-length"] = str(len(content))

    return Response(
        content=content,
        status_code=resp.status_code,
        headers=response_headers,
    )


@router.api_route("/agent-session/{ticket_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def agent_session_proxy(ticket_id: str, path: str, request: Request):
    if not _verify_proxy_auth(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _proxy_request(ticket_id, path, request)


@router.get("/agent-session/{ticket_id}")
async def agent_session_root(ticket_id: str, request: Request):
    if not _verify_proxy_auth(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _proxy_request(ticket_id, "", request)


@router.websocket("/agent-session/{ticket_id}/ws")
async def agent_session_ws(websocket: WebSocket, ticket_id: str):
    if PROXY_PASSWORD:
        pw = websocket.query_params.get("password", "")
        if pw != PROXY_PASSWORD:
            for cookie_name, cookie_value in websocket.cookies.items():
                if cookie_name == "opencode_password" and cookie_value == PROXY_PASSWORD:
                    break
            else:
                auth_header = websocket.headers.get("authorization", "")
                if not (auth_header.startswith("Bearer ") and auth_header[7:] == PROXY_PASSWORD):
                    await websocket.close(code=4001, reason="Authentication required")
                    return

    base_url = _resolve_pod_url(ticket_id)
    if not base_url:
        await websocket.close(code=4040, reason=f"No active agent for ticket {ticket_id}")
        return

    await websocket.accept()

    ws_url = base_url.replace("http://", "ws://") + "/ws"

    import websockets
    try:
        async with websockets.connect(ws_url) as agent_ws:
            async def forward_to_agent():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await agent_ws.send(data)
                except (WebSocketDisconnect, Exception):
                    pass

            async def forward_from_agent():
                try:
                    async for message in agent_ws:
                        await websocket.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(forward_to_agent(), forward_from_agent())
    except Exception as e:
        await websocket.close(code=1011, reason=str(e))
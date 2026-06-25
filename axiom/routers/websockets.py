from fastapi import APIRouter, WebSocket

from axiom.api_domains import live_ws

router = APIRouter(tags=["websockets"])


@router.websocket("/api/ws/live")
@router.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    await live_ws.websocket_endpoint(ws)

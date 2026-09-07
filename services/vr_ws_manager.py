import asyncio
import datetime
import time
from typing import Optional

from fastapi import WebSocket


class VRConnectionManager:
    def __init__(self):
        self.active_sessions: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_sessions[session_id] = websocket

    def disconnect(self, session_id: str):
        self.active_sessions.pop(session_id, None)

    def is_connected(self, session_id: str) -> bool:
        return session_id in self.active_sessions

    async def send_to_session(self, session_id: str, data: dict) -> bool:
        """
        Returns True if the message was sent, False if there was no
        connection or it was dead/unresponsive.

        IMPORTANT: send_json() has no built-in timeout. If the underlying
        connection dropped silently (e.g. a cloudflare tunnel restarted,
        or the headset lost network without a clean TCP close), Starlette
        may never raise an exception - the await can hang forever with no
        error. That freezes whatever HTTP request called this (e.g. the
        /start-training endpoint), and the caller never gets a response.
        wait_for() bounds that wait so a stale connection fails fast
        instead of hanging the whole request indefinitely.
        """
        print("[VR_DEBUG] send_to_session -> target:", session_id)
        print("[VR_DEBUG] send_to_session -> currently connected:", list(self.active_sessions.keys()))

        ws = self.active_sessions.get(session_id)
        if not ws:
            print("[VR_DEBUG] send_to_session -> NO MATCH, session_id not in active_sessions")
            return False

        try:
            await asyncio.wait_for(ws.send_json(data), timeout=5.0)
            return True
        except (asyncio.TimeoutError, Exception):
            # Connection is dead/stale - drop it so future calls fail
            # fast (is_connected() returns False) instead of hanging
            # again on the same broken socket.
            self.disconnect(session_id)
            return False


class DashboardConnectionManager:
    """
    Tracks connected dashboard tabs. Each connection optionally carries a
    session_id tag:
      - dashboard.html (overview/list page) connects with NO session_id ->
        wants only lightweight lifecycle events (session_connected,
        session_ended, training_started) for ANY session, so it can refresh
        its lists/cards.
      - session.html (single-session live view) connects WITH a session_id
        -> additionally wants the high-volume per-session streams
        (live_update, vr_frame) for just that one session.

    This split matters because vr_frame carries a base64 JPEG on every
    message - broadcasting that to every open dashboard tab regardless of
    which session they're watching wastes bandwidth and CPU for no reason.
    """

    def __init__(self):
        # ws -> session_id it's watching (None = global/list-page listener)
        self.connections: dict[WebSocket, str | None] = {}
        # per-session last-sent timestamp, for vr_frame throttling
        self._last_frame_sent: dict[str, float] = {}

    async def connect(self, websocket: WebSocket, session_id: str | None = None):
        await websocket.accept()
        self.connections[websocket] = session_id

    def subscribe(self, websocket: WebSocket, session_id: str):
        """
        session.html opens its /ws/dashboard connection on page load,
        BEFORE the session actually exists (session is only created once
        the doctor fills the form). So it can't pass session_id at connect
        time - instead it sends {"type":"subscribe","session_id":"..."}
        once the session is created, and we tag the existing connection.
        """
        if websocket in self.connections:
            self.connections[websocket] = session_id

    def disconnect(self, websocket: WebSocket):
        self.connections.pop(websocket, None)

    async def _send_all(self, targets: list[WebSocket], data: dict):
        """
        Parallel fan-out instead of sequential awaits. Previously each
        target was awaited one-by-one with a 5s timeout each - a handful of
        stale/dead tabs could stack up to tens of seconds of delay and,
        since this is awaited from inside the /ws/vr loop, that stalled
        processing of the NEXT message from the headset too. gather() fires
        all sends together so total wait is bounded by the slowest single
        send (max 5s), not the sum of all of them.
        """
        if not targets:
            return
        results = await asyncio.gather(
            *[asyncio.wait_for(ws.send_json(data), timeout=5.0) for ws in targets],
            return_exceptions=True,
        )
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                self.disconnect(ws)

    async def broadcast_global(self, data: dict):
        """Lifecycle events - goes to every connected dashboard tab."""
        await self._send_all(list(self.connections.keys()), data)

    async def broadcast_to_session(self, session_id: str, data: dict):
        """High-volume per-session stream - only to tabs watching this session."""
        targets = [ws for ws, sid in self.connections.items() if sid == session_id]
        await self._send_all(targets, data)

    def should_send_frame(self, session_id: str, min_interval: float = 0.15) -> bool:
        """
        Server-side throttle for vr_frame (~6-7 fps ceiling regardless of
        how fast the headset sends them). Balanced setting - smoother than
        the old 3-4fps cap, still low enough to avoid overloading the
        headset connection or dashboard bandwidth. Backend-only change -
        the VR app can keep sending at whatever rate it already does, we
        just drop the extra frames here before they hit the network to
        dashboards.
        """
        now = time.monotonic()
        last = self._last_frame_sent.get(session_id, 0.0)
        if now - last < min_interval:
            return False
        self._last_frame_sent[session_id] = now
        return True

    def clear_session(self, session_id: str):
        self._last_frame_sent.pop(session_id, None)


vr_manager = VRConnectionManager()
dashboard_manager = DashboardConnectionManager()
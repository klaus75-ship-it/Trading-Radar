from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .models import AccountInfo, Bar, OrderCheck, SymbolSnapshot, bar_from_payload


class BridgeError(RuntimeError):
    pass


@dataclass
class PendingRequest:
    event: threading.Event
    response: dict[str, Any] | None = None


class BridgeServer:
    def __init__(self, host: str, port: int, request_timeout_seconds: float = 5.0):
        self.host = host
        self.port = port
        self.request_timeout_seconds = request_timeout_seconds
        self._server_socket: socket.socket | None = None
        self._client_socket: socket.socket | None = None
        self._client_lock = threading.Lock()
        self._pending: dict[str, PendingRequest] = {}
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name="mt5-bridge-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._client_lock:
            if self._client_socket is not None:
                self._client_socket.close()
                self._client_socket = None
        if self._server_socket is not None:
            self._server_socket.close()

    def wait_for_client(self, timeout: float | None = None) -> bool:
        return self._ready.wait(timeout)

    def account(self) -> AccountInfo:
        return AccountInfo.from_payload(self.request({"type": "account"}))

    def snapshot(self, symbol: str) -> SymbolSnapshot:
        return SymbolSnapshot.from_payload(self.request({"type": "snapshot", "symbol": symbol}))

    def bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        payload = self.request(
            {
                "type": "bars",
                "symbol": symbol,
                "timeframe": timeframe,
                "count": count,
            }
        )
        return [bar_from_payload(item) for item in payload.get("bars", [])]

    def order_check(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        stop_distance: float,
    ) -> OrderCheck:
        sl = price - stop_distance if side == "buy" else price + stop_distance
        payload = self.request(
            {
                "type": "order_check",
                "symbol": symbol,
                "side": side,
                "volume": volume,
                "price": price,
                "sl": sl,
            }
        )
        return OrderCheck.from_payload(payload)

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        pending = PendingRequest(event=threading.Event())
        with self._pending_lock:
            self._pending[request_id] = pending

        message = {**payload, "id": request_id}
        self._send_json_line(message)

        if not pending.event.wait(self.request_timeout_seconds):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise BridgeError(f"MT5 bridge request timed out: {payload.get('type')}")

        response = pending.response or {}
        if not response.get("ok", False):
            error = response.get("error", "unknown bridge error")
            raise BridgeError(f"MT5 bridge error for {payload.get('type')}: {error}")
        return response

    def _serve(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(1)
        server.settimeout(0.5)
        self._server_socket = server

        while not self._stop.is_set():
            try:
                client, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return

            with self._client_lock:
                if self._client_socket is not None:
                    self._client_socket.close()
                self._client_socket = client
                self._ready.set()

            self._read_client(client)
            self._ready.clear()

    def _read_client(self, client: socket.socket) -> None:
        fileobj = client.makefile("r", encoding="utf-8", newline="\n")
        try:
            for line in fileobj:
                if self._stop.is_set():
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._handle_message(payload)
        finally:
            with self._client_lock:
                if self._client_socket is client:
                    self._client_socket = None

    def _handle_message(self, payload: dict[str, Any]) -> None:
        request_id = payload.get("id")
        if request_id is None:
            return
        with self._pending_lock:
            pending = self._pending.pop(str(request_id), None)
        if pending is not None:
            pending.response = payload
            pending.event.set()

    def _send_json_line(self, payload: dict[str, Any]) -> None:
        deadline = time.time() + self.request_timeout_seconds
        while time.time() < deadline:
            with self._client_lock:
                client = self._client_socket
            if client is not None:
                data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
                try:
                    client.sendall(data)
                    return
                except OSError as exc:
                    raise BridgeError(f"failed to send request to MT5 bridge: {exc}") from exc
            time.sleep(0.05)
        raise BridgeError("MT5 bridge is not connected")


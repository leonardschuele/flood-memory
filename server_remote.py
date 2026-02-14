"""HTTP/SSE transport for flood-memory MCP server."""

import sys
import json
import os
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from store import MemoryStore
from server import (
    TOOLS,
    SERVER_INFO,
    PROTOCOL_VERSION,
    make_response,
    make_error,
    handle_tools_call,
)

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

AUTH_TOKEN = os.environ.get("FLOOD_MEMORY_AUTH_TOKEN", "")


class MCPHandler(BaseHTTPRequestHandler):
    """Handles MCP JSON-RPC requests over HTTP."""

    def log_message(self, format, *args):
        logger.info(format, *args)

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, Mcp-Session-Id",
        )

    def _check_auth(self):
        """Return True if auth passes, False if 401 was sent."""
        if not AUTH_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {AUTH_TOKEN}":
            return True
        self.send_response(401)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({"error": "Unauthorized"}).encode()
        self.wfile.write(body)
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != "/mcp":
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()
            return

        if not self._check_auth():
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            self.send_response(400)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
            return

        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        # Notifications (no id) get 202 Accepted with no body
        if msg_id is None:
            self.send_response(202)
            self._send_cors_headers()
            self.end_headers()
            return

        # Route to handler
        store = self.server.store
        if method == "initialize":
            resp = make_response(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        elif method == "ping":
            resp = make_response(msg_id, {})
        elif method == "tools/list":
            resp = make_response(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            resp = make_response(msg_id, handle_tools_call(params, store))
        else:
            resp = make_error(msg_id, -32601, f"Method not found: {method}")

        body = json.dumps(resp)

        # SSE support: wrap response if client accepts text/event-stream
        accept = self.headers.get("Accept", "")
        if "text/event-stream" in accept:
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(f"data: {body}\n\n".encode())
        else:
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())


def main():
    host = os.environ.get("FLOOD_MEMORY_HOST", "0.0.0.0")
    port = int(os.environ.get("FLOOD_MEMORY_PORT", "8080"))

    data_dir = Path(os.environ.get("FLOOD_MEMORY_DIR", Path.home() / "flood" / "memory"))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "memory.db"

    store = MemoryStore(db_path, check_same_thread=False)

    server = HTTPServer((host, port), MCPHandler)
    server.store = store

    if not AUTH_TOKEN:
        logger.warning("FLOOD_MEMORY_AUTH_TOKEN not set — running without auth")

    logger.info("flood-memory remote server on %s:%d, db at %s", host, port, db_path)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
        logger.info("Server stopped.")


if __name__ == "__main__":
    main()

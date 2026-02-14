import sys
import json
import os
import logging
from pathlib import Path

from store import MemoryStore

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

SERVER_INFO = {"name": "flood-memory", "version": "0.1.0"}
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "remember",
        "description": "Store a memory node",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The memory to store"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Tags for categorization"},
                "links": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Node IDs to link to"},
                "source": {"type": "string", "default": "", "description": "Conversation label or context"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "recall",
        "description": "Search memory by text query, tags, or both. Returns matching nodes sorted by relevance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "", "description": "Text to search for"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Filter by tags (AND logic)"},
                "limit": {"type": "integer", "default": 10, "description": "Max results to return"},
            },
        },
    },
    {
        "name": "connections",
        "description": "Traverse the link graph from a starting node via BFS",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Starting node ID"},
                "depth": {"type": "integer", "default": 1, "description": "How many hops to traverse"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "forget",
        "description": "Delete a memory node by ID. Cleans up back-links in connected nodes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "ID of the node to delete"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "update",
        "description": "Partial update of an existing memory node. Only provided fields are changed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "ID of the node to update"},
                "content": {"type": "string", "description": "New content (replaces existing)"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "New tags (replaces existing)"},
                "links": {"type": "array", "items": {"type": "string"}, "description": "New links (replaces existing, bidirectional sync applied)"},
            },
            "required": ["node_id"],
        },
    },
]


def make_response(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def make_error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def tool_result(data, is_error=False):
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2)}],
        "isError": is_error,
    }


def handle_tools_call(params, store):
    name = params.get("name")
    args = params.get("arguments", {})

    try:
        if name == "remember":
            result = store.remember(
                content=args["content"],
                tags=args.get("tags", []),
                links=args.get("links", []),
                source=args.get("source", ""),
            )
            return tool_result(result)

        elif name == "recall":
            query = args.get("query", "")
            tags = args.get("tags", [])
            if not query and not tags:
                return tool_result("At least one of query or tags is required", is_error=True)
            result = store.recall(query=query, tags=tags, limit=args.get("limit", 10))
            return tool_result(result)

        elif name == "connections":
            result = store.connections(
                node_id=args["node_id"],
                depth=args.get("depth", 1),
            )
            if result is None:
                return tool_result("Node not found", is_error=True)
            return tool_result(result)

        elif name == "forget":
            result = store.forget(node_id=args["node_id"])
            if result is None:
                return tool_result("Node not found", is_error=True)
            return tool_result(result)

        elif name == "update":
            result = store.update(
                node_id=args["node_id"],
                content=args.get("content"),
                tags=args.get("tags"),
                links=args.get("links"),
            )
            if result is None:
                return tool_result("Node not found", is_error=True)
            return tool_result(result)

        else:
            return tool_result(f"Unknown tool: {name}", is_error=True)

    except Exception as e:
        logger.exception("Error in tools/call for %s", name)
        return tool_result(str(e), is_error=True)


def main():
    data_dir = Path(os.environ.get("FLOOD_MEMORY_DIR", Path.home() / "flood" / "memory"))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "memory.db"

    store = MemoryStore(db_path)
    logger.info("flood-memory server started, db at %s", db_path)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON: %s", line[:200])
                continue

            method = msg.get("method")
            msg_id = msg.get("id")
            params = msg.get("params", {})

            # Notifications (no id) get no response
            if msg_id is None:
                continue

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

            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    finally:
        store.close()


if __name__ == "__main__":
    main()

# Flood Memory MCP Server — Build Spec

## What to build
A Python MCP server with 5 tools: `remember`, `recall`, `connections`, `forget`, `update`. SQLite backend. No external dependencies — pure stdlib. Runs over stdio (JSON-RPC). This is a memory system for an AI assistant, not an artifact store.

## Data model

One table, one FTS index:

```sql
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,        -- uuid4
    content TEXT NOT NULL,       -- the actual memory, no length limit
    tags TEXT DEFAULT '[]',      -- JSON array of strings
    links TEXT DEFAULT '[]',     -- JSON array of node IDs (bidirectional)
    source TEXT DEFAULT '',      -- conversation label or ID
    created_at TEXT NOT NULL,    -- ISO 8601
    last_accessed TEXT NOT NULL, -- ISO 8601, updated on every recall hit
    access_count INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE nodes_fts USING fts5(content, content=nodes, content_rowid=rowid, tokenize='porter');

-- Triggers to keep FTS in sync with the nodes table
CREATE TRIGGER nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    INSERT INTO nodes_fts(rowid, content) VALUES (new.rowid, new.content);
END;
```

FTS indexes content only — tag filtering is handled in Python after the FTS search. FTS sync is managed via content-sync triggers (above), keeping a single source of truth.

When a node is created with links, the linked nodes should also have their `links` field updated to include the new node ID (bidirectional). If a link target ID does not exist, silently skip it (log a warning, don't fail). Memory shouldn't be fragile.

## MCP Tools

### remember
Store a memory node.

Input schema:
```json
{
    "content": { "type": "string", "description": "The memory to store" },
    "tags": { "type": "array", "items": { "type": "string" }, "default": [] },
    "links": { "type": "array", "items": { "type": "string" }, "description": "Node IDs to link to", "default": [] },
    "source": { "type": "string", "description": "Conversation label or context", "default": "" }
}
```
Required: content. Returns: the full created node as JSON.

### recall
Search memory by text query, tags, or both. Returns matching nodes sorted by relevance.

Input schema:
```json
{
    "query": { "type": "string", "description": "Text to search for", "default": "" },
    "tags": { "type": "array", "items": { "type": "string" }, "description": "Filter by tags", "default": [] },
    "limit": { "type": "integer", "default": 10 }
}
```
At least one of query or tags required. Three modes:
- **Query only**: FTS5 text search, return results sorted by relevance.
- **Tags only**: Direct SQL query on nodes table, filter tags in Python (or `json_each`). No FTS involved.
- **Query + tags**: FTS5 text search first, then filter results by tags in Python.

Update `last_accessed` and increment `access_count` on every returned node. Returns: array of matching nodes.

### connections
Traverse the link graph from a starting node.

Input schema:
```json
{
    "node_id": { "type": "string", "description": "Starting node ID" },
    "depth": { "type": "integer", "default": 1, "description": "How many hops to traverse" }
}
```
Required: node_id. BFS traversal through links up to depth. Update `last_accessed` and increment `access_count` on every traversed node (touching a node = accessing it). Returns: the starting node plus all connected nodes within depth, with a `distance` field added to each.

### forget
Delete a memory node by ID.

Input schema:
```json
{
    "node_id": { "type": "string", "description": "ID of the node to delete" }
}
```
Required: node_id. Removes the node and cleans up any links in other nodes that reference it. Returns: confirmation with the deleted node ID.

### update
Partial update of an existing memory node.

Input schema:
```json
{
    "node_id": { "type": "string", "description": "ID of the node to update" },
    "content": { "type": "string", "description": "New content (replaces existing)" },
    "tags": { "type": "array", "items": { "type": "string" }, "description": "New tags (replaces existing)" },
    "links": { "type": "array", "items": { "type": "string" }, "description": "New links (replaces existing, bidirectional sync applied)" }
}
```
Required: node_id. All other fields optional — only provided fields are updated. When links change, bidirectional sync is maintained (old back-links removed, new back-links added). Returns: the full updated node as JSON.

## MCP Protocol

Implement JSON-RPC 2.0 over stdio. Handle these methods:
- `initialize` → return capabilities (tools only)
- `notifications/initialized` → no response
- `ping` → empty response
- `tools/list` → return the 5 tool definitions
- `tools/call` → route to the appropriate handler

Server info: name = "flood-memory", version = "0.1.0", protocol version = "2024-11-05"

## File structure
```
flood-memory/
├── server.py    -- MCP protocol handler + main loop
├── store.py     -- SQLite storage + FTS + graph traversal
├── test.py      -- Tests for store + MCP integration
├── setup.py     -- Creates data dir, runs tests, prints config (cross-platform)
└── README.md    -- Brief usage docs
```

## Storage location
Default: `~/flood/memory/` (override with `FLOOD_MEMORY_DIR` env var). SQLite db goes here as `memory.db`.

## Config output
setup.py should detect the platform and Python executable, then print the JSON snippets for both Claude Desktop (`claude_desktop_config.json`) and Claude Code (`.mcp.json`):

```json
{
    "flood-memory": {
        "command": "python",
        "args": ["<absolute path>/server.py"],
        "env": {
            "FLOOD_MEMORY_DIR": "~/flood/memory"
        }
    }
}
```

On systems where `python3` is the correct command, setup.py should detect this and adjust the snippet accordingly.

## What NOT to build
- No embeddings, no semantic search — FTS5 is the starting point
- No summarization or consolidation
- No decay or auto-pruning
- No categories or ontology beyond tags
- No dashboard or visualization
- No multi-user support

These may emerge from use. Don't pre-build them.

## Remote transport (HTTP/SSE)

`server_remote.py` provides an HTTP transport as an alternative to stdio, using stdlib `http.server`.

### Endpoint
- **POST /mcp** — JSON-RPC 2.0 request/response. Same method routing as stdio transport.
- **OPTIONS /mcp** — CORS preflight. Returns 204 with CORS headers.

### Authentication
Bearer token via `FLOOD_MEMORY_AUTH_TOKEN` env var. All POST requests must include `Authorization: Bearer <token>`. OPTIONS (preflight) is exempt. Returns 401 if token is missing or wrong. If the env var is unset, auth is disabled (with a warning).

### CORS
Every response includes:
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: POST, GET, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization, Mcp-Session-Id
```

### SSE support
If the request includes `Accept: text/event-stream`, the JSON-RPC response is wrapped as SSE:
```
Content-Type: text/event-stream
Cache-Control: no-cache

data: {"jsonrpc":"2.0","id":1,"result":{...}}

```
Otherwise, plain `application/json` is returned.

### Notifications
JSON-RPC messages without an `id` field (e.g., `notifications/initialized`) receive HTTP 202 Accepted with no body.

### Configuration
| Env var | Default | Description |
|---------|---------|-------------|
| `FLOOD_MEMORY_AUTH_TOKEN` | (none) | Bearer token for auth |
| `FLOOD_MEMORY_HOST` | `0.0.0.0` | Bind address |
| `FLOOD_MEMORY_PORT` | `8080` | Listen port |
| `FLOOD_MEMORY_DIR` | `~/flood/memory` | Data directory (shared with stdio) |

### File structure update
```
flood-memory/
├── server.py         -- MCP stdio transport
├── server_remote.py  -- MCP HTTP/SSE transport
├── store.py          -- SQLite storage + FTS + graph traversal
├── test.py           -- Tests for store + stdio + remote
├── setup.py          -- Setup script (prints both local and remote config)
└── README.md         -- Usage docs
```

## Test coverage
- CRUD: create node, retrieve by ID, verify content/tags/links
- Search: FTS query returns correct results, tag-only filtering works, combined query+tags works
- Links: bidirectional link creation, connections traversal at depth 1 and 2, skip nonexistent link targets
- Access tracking: last_accessed and access_count update on recall and connections traversal
- Forget: delete node, verify back-links cleaned up in connected nodes
- Update: partial update of content/tags/links, verify bidirectional link sync on link changes
- MCP: initialize handshake, tools/list, tools/call for each tool (all 5)
- Remote: auth (valid/missing/wrong token), CORS preflight, initialize, tools/list, tools/call (all 5), SSE response, notification handling

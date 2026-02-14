# flood-memory

Persistent memory for AI assistants. A pure-stdlib Python MCP server backed by SQLite + FTS5.

## Tools

| Tool | Description |
|---|---|
| `remember` | Store a memory with optional tags, links, and source label |
| `recall` | Search by text (FTS5), tags, or both |
| `connections` | BFS traversal of the link graph from any node |
| `update` | Partial update of content, tags, or links |
| `forget` | Delete a node and clean up back-links |

## Setup

```
python setup.py
```

This creates the data directory, runs tests, and prints config snippets for Claude Desktop and Claude Code.

### Claude Code

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "flood-memory": {
      "command": "python",
      "args": ["/absolute/path/to/server.py"],
      "env": {
        "FLOOD_MEMORY_DIR": "~/flood/memory"
      }
    }
  }
}
```

### Claude Desktop

Add to the `mcpServers` key in `claude_desktop_config.json` using the same format.

## How it works

- **Storage**: Single SQLite database at `~/flood/memory/memory.db` (override with `FLOOD_MEMORY_DIR`)
- **Search**: FTS5 full-text search with Porter stemming on content. Tags filtered in Python.
- **Links**: Bidirectional. Linking A to B also links B to A. Cleanup is automatic on delete/update.
- **Access tracking**: `last_accessed` and `access_count` update on every `recall` and `connections` hit.
- **Protocol**: JSON-RPC 2.0 over stdio (MCP protocol version 2024-11-05)

## Tests

```
python -m unittest test -v
```

## Requirements

Python 3.9+ (uses `sqlite3` with FTS5 and `json_each` support). No external dependencies.

## File structure

```
server.py   - MCP protocol handler + stdio loop
store.py    - SQLite storage, FTS, graph traversal
test.py     - 33 unit + integration tests
setup.py    - Setup script (creates dirs, runs tests, prints config)
```

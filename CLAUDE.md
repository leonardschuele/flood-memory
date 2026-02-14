# CLAUDE.md

## Project
flood-memory — Pure-stdlib Python MCP server for AI assistant memory. SQLite + FTS5 backend, JSON-RPC 2.0 over stdio.

## Key files
- `store.py` — MemoryStore class, all DB logic (schema, CRUD, FTS, graph traversal)
- `server.py` — MCP protocol handler, tool definitions, stdio main loop
- `test.py` — 33 tests (unittest), includes subprocess-based MCP integration tests
- `setup.py` — Cross-platform setup script
- `flood-memory-mcp-spec.md` — Authoritative spec (kept updated with all design decisions)

## Commands
- Run tests: `python -m unittest test -v`
- Run setup: `python setup.py`
- Run server directly: `python server.py` (reads from stdin, writes to stdout)

## Architecture rules
- No external dependencies. Pure stdlib only.
- FTS5 content-sync with triggers — don't manually insert into nodes_fts
- FTS indexes content only. Tags are filtered in Python after search.
- All links are bidirectional. store.py manages back-links automatically.
- Access tracking (last_accessed, access_count) updates on recall AND connections
- Invalid link targets are silently skipped with a log warning, never fail
- FTS queries must go through `_sanitize_fts_query()` to escape special chars (hyphens = NOT operator)

## Conventions
- Spec-first: update `flood-memory-mcp-spec.md` when making design decisions
- Windows/MINGW environment: use `python` not `python3`
- `.mcp.json` is gitignored (local config)
- Keep it minimal — don't pre-build features that aren't needed yet

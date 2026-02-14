#!/usr/bin/env python
"""Cross-platform setup for flood-memory MCP server."""

import os
import sys
import json
import shutil
import secrets
import subprocess
from pathlib import Path


def detect_python():
    """Return the best python command for config snippets."""
    # On Windows, 'python' is standard; on Unix, 'python3' is often needed
    if sys.platform == "win32":
        return "python"
    # Check if 'python3' exists
    if shutil.which("python3"):
        return "python3"
    return "python"


def main():
    server_dir = Path(__file__).resolve().parent
    server_path = server_dir / "server.py"
    default_data_dir = Path.home() / "flood" / "memory"

    print("=== flood-memory setup ===\n")

    # 1. Create data directory
    print(f"Data directory: {default_data_dir}")
    default_data_dir.mkdir(parents=True, exist_ok=True)
    print("  Created (or already exists).\n")

    # 2. Run tests
    print("Running tests...")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "test", "-v"],
        cwd=str(server_dir),
    )
    print()

    if result.returncode != 0:
        print("Tests FAILED. Fix issues before using the server.")
        sys.exit(1)

    print("All tests passed.\n")

    # 3. Print config snippets
    python_cmd = detect_python()

    config = {
        "flood-memory": {
            "command": python_cmd,
            "args": [str(server_path)],
            "env": {
                "FLOOD_MEMORY_DIR": str(default_data_dir)
            },
        }
    }

    print("=== Claude Desktop config (claude_desktop_config.json) ===")
    print("Add to the \"mcpServers\" key:\n")
    print(json.dumps(config, indent=2))

    print("\n=== Claude Code config (.mcp.json) ===")
    print("Add to the \"mcpServers\" key:\n")
    print(json.dumps(config, indent=2))

    # 4. Remote server config
    remote_path = server_dir / "server_remote.py"
    auth_token = secrets.token_hex(16)

    remote_config = {
        "flood-memory-remote": {
            "type": "streamable-http",
            "url": "http://localhost:8080/mcp",
            "headers": {
                "Authorization": f"Bearer {auth_token}"
            },
        }
    }

    print("\n=== Remote server (HTTP/SSE) ===")
    print(f"Run:  FLOOD_MEMORY_AUTH_TOKEN={auth_token} {python_cmd} {remote_path}")
    print(f"\nConfig for remote clients (add to \"mcpServers\"):\n")
    print(json.dumps(remote_config, indent=2))

    print("\n=== Done ===")


if __name__ == "__main__":
    main()

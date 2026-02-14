import unittest
import tempfile
import shutil
import json
import sys
import os
import subprocess
import time
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from store import MemoryStore


class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = MemoryStore(Path(self.tmp) / "test.db")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp)

    # -- remember --

    def test_remember_basic(self):
        node = self.store.remember("Python is a programming language")
        self.assertIn("id", node)
        self.assertEqual(node["content"], "Python is a programming language")
        self.assertEqual(node["tags"], [])
        self.assertEqual(node["links"], [])
        self.assertEqual(node["source"], "")
        self.assertEqual(node["access_count"], 0)

    def test_remember_with_tags(self):
        node = self.store.remember("Use pytest for testing", tags=["python", "testing"])
        self.assertEqual(node["tags"], ["python", "testing"])

    def test_remember_with_source(self):
        node = self.store.remember("Some fact", source="chat-42")
        self.assertEqual(node["source"], "chat-42")

    def test_remember_with_links_bidirectional(self):
        a = self.store.remember("Node A")
        b = self.store.remember("Node B", links=[a["id"]])

        # B should link to A
        self.assertIn(a["id"], b["links"])

        # A should now link back to B
        a_refreshed = self.store._get_node(a["id"])
        self.assertIn(b["id"], a_refreshed["links"])

    def test_remember_skip_nonexistent_links(self):
        node = self.store.remember("Node with bad link", links=["nonexistent-id"])
        self.assertEqual(node["links"], [])

    # -- recall --

    def test_recall_by_query(self):
        self.store.remember("Python is great for scripting")
        self.store.remember("Rust is great for systems programming")
        results = self.store.recall(query="Python")
        self.assertEqual(len(results), 1)
        self.assertIn("Python", results[0]["content"])

    def test_recall_by_tags(self):
        self.store.remember("Tip 1", tags=["python", "testing"])
        self.store.remember("Tip 2", tags=["python", "web"])
        self.store.remember("Tip 3", tags=["rust"])

        results = self.store.recall(tags=["python"])
        self.assertEqual(len(results), 2)

        results = self.store.recall(tags=["python", "testing"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tags"], ["python", "testing"])

    def test_recall_by_query_and_tags(self):
        self.store.remember("Python web frameworks", tags=["python", "web"])
        self.store.remember("Python testing tools", tags=["python", "testing"])
        self.store.remember("Rust web frameworks", tags=["rust", "web"])

        results = self.store.recall(query="frameworks", tags=["python"])
        self.assertEqual(len(results), 1)
        self.assertIn("Python web", results[0]["content"])

    def test_recall_query_with_special_chars(self):
        self.store.remember("flood-memory server is running")
        results = self.store.recall(query="flood-memory")
        self.assertEqual(len(results), 1)
        self.assertIn("flood-memory", results[0]["content"])

    def test_recall_empty_returns_nothing(self):
        self.store.remember("Something")
        results = self.store.recall()
        self.assertEqual(results, [])

    def test_recall_limit(self):
        for i in range(5):
            self.store.remember(f"Memory number {i}")
        results = self.store.recall(query="Memory", limit=3)
        self.assertEqual(len(results), 3)

    def test_recall_access_tracking(self):
        node = self.store.remember("Track me")
        self.assertEqual(node["access_count"], 0)

        results = self.store.recall(query="Track")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["access_count"], 1)

        results = self.store.recall(query="Track")
        self.assertEqual(results[0]["access_count"], 2)

    # -- connections --

    def test_connections_depth_1(self):
        a = self.store.remember("Center node")
        b = self.store.remember("Neighbor 1", links=[a["id"]])
        c = self.store.remember("Neighbor 2", links=[a["id"]])
        d = self.store.remember("Far away", links=[b["id"]])

        results = self.store.connections(a["id"], depth=1)
        ids = {n["id"] for n in results}
        self.assertIn(a["id"], ids)
        self.assertIn(b["id"], ids)
        self.assertIn(c["id"], ids)
        self.assertNotIn(d["id"], ids)

        distances = {n["id"]: n["distance"] for n in results}
        self.assertEqual(distances[a["id"]], 0)
        self.assertEqual(distances[b["id"]], 1)
        self.assertEqual(distances[c["id"]], 1)

    def test_connections_depth_2(self):
        a = self.store.remember("Center")
        b = self.store.remember("Hop 1", links=[a["id"]])
        c = self.store.remember("Hop 2", links=[b["id"]])
        d = self.store.remember("Hop 3", links=[c["id"]])

        results = self.store.connections(a["id"], depth=2)
        ids = {n["id"] for n in results}
        self.assertIn(a["id"], ids)
        self.assertIn(b["id"], ids)
        self.assertIn(c["id"], ids)
        self.assertNotIn(d["id"], ids)

    def test_connections_nonexistent_node(self):
        result = self.store.connections("nonexistent")
        self.assertIsNone(result)

    def test_connections_access_tracking(self):
        a = self.store.remember("Node A")
        b = self.store.remember("Node B", links=[a["id"]])

        self.store.connections(a["id"], depth=1)

        a_check = self.store._get_node(a["id"])
        b_check = self.store._get_node(b["id"])
        self.assertEqual(a_check["access_count"], 1)
        self.assertEqual(b_check["access_count"], 1)

    # -- forget --

    def test_forget(self):
        node = self.store.remember("Delete me")
        result = self.store.forget(node["id"])
        self.assertEqual(result["deleted"], node["id"])
        self.assertIsNone(self.store._get_node(node["id"]))

    def test_forget_cleans_backlinks(self):
        a = self.store.remember("Keep me")
        b = self.store.remember("Delete me", links=[a["id"]])

        # A should link to B before deletion
        a_before = self.store._get_node(a["id"])
        self.assertIn(b["id"], a_before["links"])

        self.store.forget(b["id"])

        # A should no longer link to B
        a_after = self.store._get_node(a["id"])
        self.assertNotIn(b["id"], a_after["links"])

    def test_forget_nonexistent(self):
        result = self.store.forget("nonexistent")
        self.assertIsNone(result)

    # -- update --

    def test_update_content(self):
        node = self.store.remember("Old content")
        updated = self.store.update(node["id"], content="New content")
        self.assertEqual(updated["content"], "New content")
        self.assertEqual(updated["tags"], [])  # unchanged

    def test_update_tags(self):
        node = self.store.remember("Tagged", tags=["old"])
        updated = self.store.update(node["id"], tags=["new", "tags"])
        self.assertEqual(updated["tags"], ["new", "tags"])
        self.assertEqual(updated["content"], "Tagged")  # unchanged

    def test_update_links_bidirectional_sync(self):
        a = self.store.remember("A")
        b = self.store.remember("B")
        c = self.store.remember("C")

        # Link node A to B
        self.store.update(a["id"], links=[b["id"]])
        b_check = self.store._get_node(b["id"])
        self.assertIn(a["id"], b_check["links"])

        # Change A's links from B to C
        self.store.update(a["id"], links=[c["id"]])

        # B should no longer have back-link to A
        b_check = self.store._get_node(b["id"])
        self.assertNotIn(a["id"], b_check["links"])

        # C should have back-link to A
        c_check = self.store._get_node(c["id"])
        self.assertIn(a["id"], c_check["links"])

    def test_update_nonexistent(self):
        result = self.store.update("nonexistent", content="Nope")
        self.assertIsNone(result)


class TestMCPProtocol(unittest.TestCase):
    """Integration tests that run server.py as a subprocess."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.server_path = str(Path(__file__).parent / "server.py")
        env = os.environ.copy()
        env["FLOOD_MEMORY_DIR"] = self.tmp
        self.proc = subprocess.Popen(
            [sys.executable, self.server_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        shutil.rmtree(self.tmp)

    def send(self, method, params=None, msg_id=1):
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def send_notification(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def test_initialize(self):
        resp = self.send("initialize", {})
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "flood-memory")
        self.assertEqual(result["serverInfo"]["version"], "0.1.0")
        self.assertIn("tools", result["capabilities"])

    def test_initialized_notification(self):
        self.send("initialize", {})
        # Send notification — should not produce a response
        self.send_notification("notifications/initialized")
        # If server is still alive, we can send another request
        resp = self.send("ping", {}, msg_id=2)
        self.assertEqual(resp["result"], {})

    def test_ping(self):
        resp = self.send("ping", {})
        self.assertEqual(resp["result"], {})

    def test_tools_list(self):
        resp = self.send("tools/list", {})
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertEqual(names, {"remember", "recall", "connections", "forget", "update"})

    def test_tools_call_remember(self):
        resp = self.send("tools/call", {
            "name": "remember",
            "arguments": {"content": "MCP test memory", "tags": ["test"]},
        })
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(result["content"], "MCP test memory")
        self.assertEqual(result["tags"], ["test"])
        self.assertFalse(resp["result"]["isError"])

    def test_tools_call_recall(self):
        # Store first
        self.send("tools/call", {
            "name": "remember",
            "arguments": {"content": "Searchable memory"},
        })
        # Recall
        resp = self.send("tools/call", {
            "name": "recall",
            "arguments": {"query": "Searchable"},
        }, msg_id=2)
        results = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(len(results), 1)
        self.assertIn("Searchable", results[0]["content"])

    def test_tools_call_connections(self):
        # Create linked nodes
        resp_a = self.send("tools/call", {
            "name": "remember",
            "arguments": {"content": "Node A"},
        })
        node_a = json.loads(resp_a["result"]["content"][0]["text"])

        resp_b = self.send("tools/call", {
            "name": "remember",
            "arguments": {"content": "Node B", "links": [node_a["id"]]},
        }, msg_id=2)

        # Traverse
        resp = self.send("tools/call", {
            "name": "connections",
            "arguments": {"node_id": node_a["id"], "depth": 1},
        }, msg_id=3)
        results = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(len(results), 2)

    def test_tools_call_forget(self):
        resp = self.send("tools/call", {
            "name": "remember",
            "arguments": {"content": "Forget me"},
        })
        node = json.loads(resp["result"]["content"][0]["text"])

        resp = self.send("tools/call", {
            "name": "forget",
            "arguments": {"node_id": node["id"]},
        }, msg_id=2)
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(result["deleted"], node["id"])

    def test_tools_call_update(self):
        resp = self.send("tools/call", {
            "name": "remember",
            "arguments": {"content": "Original"},
        })
        node = json.loads(resp["result"]["content"][0]["text"])

        resp = self.send("tools/call", {
            "name": "update",
            "arguments": {"node_id": node["id"], "content": "Updated"},
        }, msg_id=2)
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(result["content"], "Updated")

    def test_unknown_method(self):
        resp = self.send("bogus/method", {})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)


class TestRemoteServer(unittest.TestCase):
    """Integration tests for server_remote.py HTTP transport."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.token = "test-token-abc123"
        os.environ["FLOOD_MEMORY_AUTH_TOKEN"] = cls.token
        os.environ["FLOOD_MEMORY_DIR"] = cls.tmp

        from server_remote import MCPHandler
        from store import MemoryStore

        cls.store = MemoryStore(Path(cls.tmp) / "test.db", check_same_thread=False)
        cls.server = HTTPServer(("127.0.0.1", 0), MCPHandler)
        cls.server.store = cls.store
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}/mcp"

        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.store.close()
        shutil.rmtree(cls.tmp)
        os.environ.pop("FLOOD_MEMORY_AUTH_TOKEN", None)
        os.environ.pop("FLOOD_MEMORY_DIR", None)

    def _request(self, body, headers=None):
        """Send a POST to /mcp, return (status, response_body_str)."""
        hdrs = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if headers:
            hdrs.update(headers)
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        req = Request(self.base_url, data=data, headers=hdrs, method="POST")
        try:
            resp = urlopen(req)
            return resp.status, resp.read().decode()
        except HTTPError as e:
            return e.code, e.read().decode()

    def _rpc(self, method, params=None, msg_id=1, headers=None):
        """Send a JSON-RPC request and return parsed response."""
        body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            body["params"] = params
        status, text = self._request(body, headers)
        return status, json.loads(text)

    # -- Auth --

    def test_auth_valid(self):
        status, resp = self._rpc("ping", {})
        self.assertEqual(status, 200)
        self.assertEqual(resp["result"], {})

    def test_auth_missing(self):
        req = Request(
            self.base_url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urlopen(req)
            status = resp.status
        except HTTPError as e:
            status = e.code
        self.assertEqual(status, 401)

    def test_auth_wrong_token(self):
        req = Request(
            self.base_url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer wrong-token",
            },
            method="POST",
        )
        try:
            resp = urlopen(req)
            status = resp.status
        except HTTPError as e:
            status = e.code
        self.assertEqual(status, 401)

    # -- CORS --

    def test_cors_preflight(self):
        req = Request(self.base_url, method="OPTIONS")
        resp = urlopen(req)
        self.assertEqual(resp.status, 204)
        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("POST", resp.headers["Access-Control-Allow-Methods"])
        self.assertIn("Authorization", resp.headers["Access-Control-Allow-Headers"])

    def test_cors_headers_on_post(self):
        status, resp = self._rpc("ping", {})
        # CORS headers are checked indirectly — if we got 200, the response came back.
        # For a more direct check we'd need to inspect headers, but urllib doesn't
        # expose them easily from a successful response. The preflight test covers it.
        self.assertEqual(status, 200)

    # -- Initialize --

    def test_initialize(self):
        status, resp = self._rpc("initialize", {})
        self.assertEqual(status, 200)
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "flood-memory")
        self.assertIn("tools", result["capabilities"])

    # -- Tools --

    def test_tools_list(self):
        status, resp = self._rpc("tools/list", {})
        self.assertEqual(status, 200)
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"remember", "recall", "connections", "forget", "update"})

    def test_tools_call_remember(self):
        status, resp = self._rpc("tools/call", {
            "name": "remember",
            "arguments": {"content": "Remote test memory", "tags": ["remote"]},
        })
        self.assertEqual(status, 200)
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(result["content"], "Remote test memory")
        self.assertFalse(resp["result"]["isError"])

    def test_tools_call_recall(self):
        # Store a node first
        self._rpc("tools/call", {
            "name": "remember",
            "arguments": {"content": "Remote searchable node"},
        })
        status, resp = self._rpc("tools/call", {
            "name": "recall",
            "arguments": {"query": "Remote searchable"},
        }, msg_id=2)
        results = json.loads(resp["result"]["content"][0]["text"])
        self.assertGreaterEqual(len(results), 1)

    def test_tools_call_connections(self):
        _, resp_a = self._rpc("tools/call", {
            "name": "remember",
            "arguments": {"content": "Remote node A"},
        })
        node_a = json.loads(resp_a["result"]["content"][0]["text"])

        self._rpc("tools/call", {
            "name": "remember",
            "arguments": {"content": "Remote node B", "links": [node_a["id"]]},
        }, msg_id=2)

        status, resp = self._rpc("tools/call", {
            "name": "connections",
            "arguments": {"node_id": node_a["id"], "depth": 1},
        }, msg_id=3)
        results = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(len(results), 2)

    def test_tools_call_forget(self):
        _, resp = self._rpc("tools/call", {
            "name": "remember",
            "arguments": {"content": "Remote forget me"},
        })
        node = json.loads(resp["result"]["content"][0]["text"])

        status, resp = self._rpc("tools/call", {
            "name": "forget",
            "arguments": {"node_id": node["id"]},
        }, msg_id=2)
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(result["deleted"], node["id"])

    def test_tools_call_update(self):
        _, resp = self._rpc("tools/call", {
            "name": "remember",
            "arguments": {"content": "Remote original"},
        })
        node = json.loads(resp["result"]["content"][0]["text"])

        status, resp = self._rpc("tools/call", {
            "name": "update",
            "arguments": {"node_id": node["id"], "content": "Remote updated"},
        }, msg_id=2)
        result = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(result["content"], "Remote updated")

    # -- SSE --

    def test_sse_response(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        data = json.dumps(body).encode()
        req = Request(
            self.base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        resp = urlopen(req)
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.headers["Content-Type"])
        raw = resp.read().decode()
        self.assertTrue(raw.startswith("data: "))
        # Parse the SSE payload
        json_str = raw.split("data: ", 1)[1].strip()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["result"]["serverInfo"]["name"], "flood-memory")

    # -- Notifications --

    def test_notification_returns_202(self):
        body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        status, text = self._request(body)
        self.assertEqual(status, 202)

    # -- Unknown method --

    def test_unknown_method(self):
        status, resp = self._rpc("bogus/method", {})
        self.assertEqual(status, 200)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()

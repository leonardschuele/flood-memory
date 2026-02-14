import sqlite3
import uuid
import json
import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self, db_path, check_same_thread=True):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                links TEXT DEFAULT '[]',
                source TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count INTEGER DEFAULT 0
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                content,
                content=nodes,
                content_rowid=rowid,
                tokenize='porter'
            );

            CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
                INSERT INTO nodes_fts(rowid, content) VALUES (new.rowid, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
                INSERT INTO nodes_fts(nodes_fts, rowid, content)
                    VALUES ('delete', old.rowid, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
                INSERT INTO nodes_fts(nodes_fts, rowid, content)
                    VALUES ('delete', old.rowid, old.content);
                INSERT INTO nodes_fts(rowid, content) VALUES (new.rowid, new.content);
            END;
        """)

    def close(self):
        self.conn.close()

    # -- internal helpers --

    def _node_to_dict(self, row):
        return {
            "id": row["id"],
            "content": row["content"],
            "tags": json.loads(row["tags"]),
            "links": json.loads(row["links"]),
            "source": row["source"],
            "created_at": row["created_at"],
            "last_accessed": row["last_accessed"],
            "access_count": row["access_count"],
        }

    def _get_node(self, node_id):
        cur = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        row = cur.fetchone()
        return self._node_to_dict(row) if row else None

    def _update_access(self, node_ids):
        if not node_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        for nid in node_ids:
            self.conn.execute(
                "UPDATE nodes SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                (now, nid),
            )
        self.conn.commit()

    def _add_back_link(self, target_id, source_id):
        node = self._get_node(target_id)
        if node is None:
            return False
        links = node["links"]
        if source_id not in links:
            links.append(source_id)
            self.conn.execute(
                "UPDATE nodes SET links = ? WHERE id = ?",
                (json.dumps(links), target_id),
            )
        return True

    def _remove_back_link(self, target_id, source_id):
        node = self._get_node(target_id)
        if node is None:
            return
        links = node["links"]
        if source_id in links:
            links.remove(source_id)
            self.conn.execute(
                "UPDATE nodes SET links = ? WHERE id = ?",
                (json.dumps(links), target_id),
            )

    # -- public API --

    def remember(self, content, tags=None, links=None, source=""):
        tags = tags or []
        links = links or []
        node_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        valid_links = []
        for link_id in links:
            if self._get_node(link_id) is not None:
                valid_links.append(link_id)
            else:
                logger.warning("Skipping link to nonexistent node: %s", link_id)

        self.conn.execute(
            "INSERT INTO nodes (id, content, tags, links, source, created_at, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (node_id, content, json.dumps(tags), json.dumps(valid_links), source, now, now),
        )

        for link_id in valid_links:
            self._add_back_link(link_id, node_id)

        self.conn.commit()
        return self._get_node(node_id)

    @staticmethod
    def _sanitize_fts_query(query):
        """Quote each token so FTS5 operators (- : OR AND NOT) are treated as literals."""
        tokens = query.split()
        return " ".join(f'"{t}"' for t in tokens)

    def recall(self, query="", tags=None, limit=10):
        tags = tags or []
        if not query and not tags:
            return []

        if query:
            safe_query = self._sanitize_fts_query(query)
            cur = self.conn.execute(
                "SELECT nodes.* FROM nodes_fts "
                "JOIN nodes ON nodes.rowid = nodes_fts.rowid "
                "WHERE nodes_fts MATCH ? ORDER BY rank",
                (safe_query,),
            )
            candidates = [self._node_to_dict(row) for row in cur.fetchall()]
        else:
            cur = self.conn.execute("SELECT * FROM nodes")
            candidates = [self._node_to_dict(row) for row in cur.fetchall()]

        if tags:
            candidates = [n for n in candidates if all(t in n["tags"] for t in tags)]

        results = candidates[:limit]

        node_ids = [n["id"] for n in results]
        self._update_access(node_ids)
        return [self._get_node(nid) for nid in node_ids]

    def connections(self, node_id, depth=1):
        start = self._get_node(node_id)
        if start is None:
            return None

        visited = {node_id: 0}
        queue = deque([(node_id, 0)])
        order = []

        while queue:
            current_id, current_depth = queue.popleft()
            node = self._get_node(current_id)
            if node is None:
                continue
            order.append((current_id, current_depth))

            if current_depth < depth:
                for link_id in node["links"]:
                    if link_id not in visited:
                        visited[link_id] = current_depth + 1
                        queue.append((link_id, current_depth + 1))

        node_ids = [nid for nid, _ in order]
        self._update_access(node_ids)

        results = []
        for nid, dist in order:
            node = self._get_node(nid)
            if node:
                node["distance"] = dist
                results.append(node)
        return results

    def forget(self, node_id):
        node = self._get_node(node_id)
        if node is None:
            return None

        for link_id in node["links"]:
            self._remove_back_link(link_id, node_id)

        self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        self.conn.commit()
        return {"deleted": node_id}

    def update(self, node_id, content=None, tags=None, links=None):
        node = self._get_node(node_id)
        if node is None:
            return None

        if content is not None:
            self.conn.execute("UPDATE nodes SET content = ? WHERE id = ?", (content, node_id))

        if tags is not None:
            self.conn.execute("UPDATE nodes SET tags = ? WHERE id = ?", (json.dumps(tags), node_id))

        if links is not None:
            old_links = set(node["links"])

            valid_new = []
            for lid in links:
                if lid == node_id:
                    continue
                if self._get_node(lid) is not None:
                    valid_new.append(lid)
                else:
                    logger.warning("Skipping link to nonexistent node: %s", lid)

            new_links = set(valid_new)

            for removed in old_links - new_links:
                self._remove_back_link(removed, node_id)
            for added in new_links - old_links:
                self._add_back_link(added, node_id)

            self.conn.execute(
                "UPDATE nodes SET links = ? WHERE id = ?",
                (json.dumps(valid_new), node_id),
            )

        self.conn.commit()
        return self._get_node(node_id)

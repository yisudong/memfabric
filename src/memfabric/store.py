"""
SQLite append-only event store — 核心存储层

设计：所有写入是 append-only，永不修改或删除已有行。
每次记忆变更产生新 event，通过 parent_event_id 形成因果链。
"""

import sqlite3
import json
import uuid
import time
import os
from pathlib import Path
from typing import Optional


def _uuid7() -> str:
    """自实现 UUID7 — 时间有序的 UUID，毫秒级精度"""
    ts = int(time.time() * 1000)  # 毫秒
    rand_bytes = os.urandom(10)
    # UUID7 格式: 48-bit timestamp + 4-bit version + 12-bit rand + 2-bit variant + 62-bit rand
    hi = (ts << 16) | (0x7 << 12) | (int.from_bytes(rand_bytes[:2], 'big') & 0xFFF)
    mid = int.from_bytes(rand_bytes[2:4], 'big')
    mid = (mid & 0x0FFF) | 0x7000
    lo = int.from_bytes(rand_bytes[4:10], 'big')
    lo = (lo & 0x3FFFFFFFFFFFFFFF) | 0x8000000000000000
    return f"{hi:08x}-{mid:04x}-{lo:016x}"


def _now() -> str:
    """返回微秒精度 ISO 8601 时间戳，确保事件有序"""
    t = time.time()
    us = int(t * 1_000_000) % 1_000_000
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{us:06d}Z"


def _now_offset(offset_seconds: float) -> str:
    """返回偏移后的时间戳（offset_seconds 可为负）"""
    t = time.time() + offset_seconds
    us = int(t * 1_000_000) % 1_000_000
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{us:06d}Z"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    namespace   TEXT NOT NULL DEFAULT 'default',
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    operation   TEXT NOT NULL CHECK(operation IN ('add','update','link','forget')),
    parent_event_id TEXT,
    tags        TEXT DEFAULT '[]',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_key
    ON events(namespace, key, created_at);

CREATE INDEX IF NOT EXISTS idx_events_agent
    ON events(agent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_events_namespace
    ON events(namespace, created_at);

-- 最新状态物化视图：每个 key 的当前值（已 forget 的 key 不出现）
-- 使用 subquery 取每条 key 的最新 event，rowid 作 tiebreaker
CREATE VIEW IF NOT EXISTS current_state AS
SELECT
    e.key,
    e.namespace,
    e.value,
    e.agent_id,
    e.tags,
    e.created_at,
    e.event_id
FROM events e
WHERE e.event_id IN (
    SELECT e2.event_id FROM events e2
    WHERE e2.namespace = e.namespace AND e2.key = e.key
    ORDER BY e2.created_at DESC, e2.rowid DESC
    LIMIT 1
)
AND e.operation != 'forget';
"""


class EventStore:
    """append-only 事件存储"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()
        return self._conn

    def add(self, agent_id: str, key: str, value: str,
            namespace: str = "default", tags: list[str] | None = None,
            parent_event_id: str | None = None) -> dict:
        """写入一条新记忆（append-only）"""
        event_id = _uuid7()
        created_at = _now()

        # 找到上一个版本的 event（用于建立因果链）
        if parent_event_id is None:
            cursor = self.conn.execute(
                "SELECT event_id FROM events WHERE namespace=? AND key=? "
                "AND operation!='forget' ORDER BY created_at DESC LIMIT 1",
                (namespace, key)
            )
            prev = cursor.fetchone()
            if prev:
                parent_event_id = prev["event_id"]

        self.conn.execute(
            """INSERT INTO events (event_id, agent_id, namespace, key, value, operation, parent_event_id, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, agent_id, namespace, key, value, "add", parent_event_id,
             json.dumps(tags or []), created_at)
        )
        self.conn.commit()

        return {
            "event_id": event_id,
            "key": key,
            "namespace": namespace,
            "operation": "add",
            "parent_event_id": parent_event_id,
            "created_at": created_at,
        }

    def update(self, agent_id: str, key: str, value: str,
               namespace: str = "default", tags: list[str] | None = None) -> dict:
        """更新记忆（实际是 add 新 event）"""
        cursor = self.conn.execute(
            "SELECT event_id FROM events WHERE namespace=? AND key=? "
            "AND operation!='forget' ORDER BY created_at DESC LIMIT 1",
            (namespace, key)
        )
        prev = cursor.fetchone()
        parent_event_id = prev["event_id"] if prev else None

        return self.add(agent_id, key, value, namespace, tags, parent_event_id)

    def get(self, key: str, namespace: str = "default") -> dict | None:
        """获取记忆最新值 + 完整血缘"""
        cursor = self.conn.execute(
            "SELECT * FROM current_state WHERE namespace=? AND key=?",
            (namespace, key)
        )
        row = cursor.fetchone()
        if not row:
            return None

        return {
            "key": row["key"],
            "value": row["value"],
            "namespace": row["namespace"],
            "agent_id": row["agent_id"],
            "tags": json.loads(row["tags"]),
            "updated_at": row["created_at"],
            "event_id": row["event_id"],
            "lineage": self._get_lineage(row["event_id"]),
        }

    def forget(self, agent_id: str, key: str, reason: str = "",
               namespace: str = "default") -> dict:
        """软删除记忆（标记 forget，保留历史）"""
        event_id = _uuid7()
        created_at = _now()

        cursor = self.conn.execute(
            "SELECT event_id FROM events WHERE namespace=? AND key=? "
            "AND operation!='forget' ORDER BY created_at DESC LIMIT 1",
            (namespace, key)
        )
        prev = cursor.fetchone()
        parent_event_id = prev["event_id"] if prev else None

        self.conn.execute(
            """INSERT INTO events (event_id, agent_id, namespace, key, value, operation, parent_event_id, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, agent_id, namespace, key, reason or "forgotten", "forget",
             parent_event_id, "[]", created_at)
        )
        self.conn.commit()

        return {
            "event_id": event_id,
            "key": key,
            "namespace": namespace,
            "operation": "forget",
            "reason": reason,
            "created_at": created_at,
        }

    def link(self, agent_id: str, source_key: str, target_key: str,
             relation_type: str, namespace: str = "default") -> dict:
        """建立两个记忆之间的关联"""
        event_id = _uuid7()
        created_at = _now()
        link_value = json.dumps({
            "source": source_key,
            "target": target_key,
            "relation": relation_type,
        })

        self.conn.execute(
            """INSERT INTO events (event_id, agent_id, namespace, key, value, operation, parent_event_id, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (event_id, agent_id, namespace,
             f"link:{source_key}->{target_key}", link_value, "link",
             json.dumps([relation_type]), created_at)
        )
        self.conn.commit()

        return {
            "event_id": event_id,
            "operation": "link",
            "source": source_key,
            "target": target_key,
            "relation": relation_type,
            "created_at": created_at,
        }

    def search_keyword(self, query: str, namespace: str | list[str] | None = None,
                       limit: int = 10) -> list[dict]:
        """关键词搜索（SQL LIKE 匹配）"""
        namespaces = _normalize_namespaces(namespace)
        placeholders = ",".join("?" * len(namespaces))
        # 简单的 tokenized OR 搜索
        tokens = [t.strip() for t in query.split() if len(t.strip()) >= 2]
        if not tokens:
            return []

        conditions = " OR ".join(["value LIKE ?" for _ in tokens])
        params = [f"%{t}%" for t in tokens] + list(namespaces) + [limit]

        sql = f"""
            SELECT DISTINCT key, namespace, value, agent_id, tags, created_at, event_id
            FROM current_state
            WHERE ({conditions})
              AND namespace IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT ?
        """
        cursor = self.conn.execute(sql, params)
        return [_row_to_dict(row) for row in cursor.fetchall()]

    def list_namespace(self, namespace: str = "default", limit: int = 50) -> list[dict]:
        """列出命名空间下所有记忆"""
        cursor = self.conn.execute(
            "SELECT * FROM current_state WHERE namespace=? ORDER BY created_at DESC LIMIT ?",
            (namespace, limit)
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]

    def _get_lineage(self, event_id: str) -> list[dict]:
        """回溯完整因果链"""
        lineage = []
        current = event_id
        visited = set()
        while current and current not in visited:
            visited.add(current)
            cursor = self.conn.execute(
                "SELECT * FROM events WHERE event_id=?",
                (current,)
            )
            row = cursor.fetchone()
            if not row:
                break
            lineage.append({
                "event_id": row["event_id"],
                "agent_id": row["agent_id"],
                "operation": row["operation"],
                "value": row["value"][:200] + "..." if len(row["value"]) > 200 else row["value"],
                "created_at": row["created_at"],
            })
            current = row["parent_event_id"]
        return lineage

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


def _normalize_namespaces(namespace: str | list[str] | None) -> list[str]:
    if namespace is None:
        return ["default"]
    if isinstance(namespace, str):
        return [namespace]
    return list(namespace)


def _row_to_dict(row) -> dict:
    return {
        "key": row["key"],
        "value": row["value"],
        "namespace": row["namespace"],
        "agent_id": row["agent_id"],
        "tags": json.loads(row["tags"]),
        "updated_at": row["created_at"],
        "event_id": row["event_id"],
    }

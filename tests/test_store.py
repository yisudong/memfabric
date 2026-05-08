"""
基本测试 — 验证核心存储和 MCP 工具正常工作
"""

import sys
import json
import tempfile
import os
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memfabric.store import EventStore
from memfabric.config import MemFabricConfig
from memfabric.server import MemFabric


def test_event_store_basic():
    """测试 append-only 存储基本 CRUD"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = EventStore(Path(tmpdir) / "test.db")

        # 写入
        r1 = store.add("agent-alice", "user-name", "西米哥", tags=["identity"])
        assert r1["operation"] == "add"
        assert r1["key"] == "user-name"

        # 读取
        r2 = store.get("user-name")
        assert r2 is not None
        assert r2["value"] == "西米哥"
        assert len(r2["lineage"]) == 1  # 一次写入，一个历史

        # 更新
        r3 = store.update("agent-bob", "user-name", "西米哥（更新）", tags=["identity"])
        assert r3["operation"] == "add"

        # 读取更新后
        r4 = store.get("user-name")
        assert r4["value"] == "西米哥（更新）"
        assert len(r4["lineage"]) == 2  # 两次写入，两个历史

        # 关键词搜索
        results = store.search_keyword("西米")
        assert len(results) == 1
        assert results[0]["key"] == "user-name"

        # 忘记
        r5 = store.forget("agent-alice", "user-name", "不再需要")
        assert r5["operation"] == "forget"

        # 忘记后读取不到
        r6 = store.get("user-name")
        assert r6 is None

        store.close()


def test_memfabric_engine():
    """测试 MemFabric 核心引擎"""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mf = MemFabric(home=home)

        # 写入
        r1 = mf.memory_add("agent-alice", "favorite-color", "蓝色", tags=["preference"])
        assert r1["ok"] is True

        # 搜索
        r2 = mf.memory_search("agent-alice", "喜欢什么颜色")
        assert r2["ok"] is True
        # 关键词搜索应该能找到
        found = any("蓝色" in str(r.get("value", "")) or "favorite-color" in str(r.get("key", ""))
                    for r in r2["results"])
        assert found, f"关键词搜索没有找到结果: {r2['results']}"

        # 精确查询
        r3 = mf.memory_get("agent-alice", "favorite-color")
        assert r3["ok"] is True
        assert r3["found"] is True
        assert r3["value"] == "蓝色"

        # 召回
        r4 = mf.memory_recall("agent-alice", "颜色偏好")
        assert r4["ok"] is True

        # 忘记
        r5 = mf.memory_forget("agent-alice", "favorite-color", "测试清理")
        assert r5["ok"] is True

        # 忘记后查不到
        r6 = mf.memory_get("agent-alice", "favorite-color")
        assert r6["found"] is False


def test_mcp_server_jsonrpc():
    """测试 MCP JSON-RPC 响应格式"""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mf = MemFabric(home=home)

        # 模拟 MCP initialize 请求
        init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

        from memfabric.server import handle_request
        resp = handle_request(mf, init_req)
        data = json.loads(resp)
        assert data["id"] == 1
        assert "result" in data
        assert data["result"]["serverInfo"]["name"] == "memfabric"

        # 模拟 tools/list 请求
        list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = handle_request(mf, list_req)
        data = json.loads(resp)
        assert data["id"] == 2
        assert len(data["result"]["tools"]) == 6

        # 模拟 tools/call 请求
        call_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "memory_add",
                "arguments": {
                    "key": "test-key",
                    "value": "测试值",
                    "tags": ["test"],
                },
            },
        }
        resp = handle_request(mf, call_req)
        data = json.loads(resp)
        assert data["id"] == 3
        content = json.loads(data["result"]["content"][0]["text"])
        assert content["ok"] is True
        assert content["key"] == "test-key"


def test_namespace_isolation():
    """测试命名空间隔离"""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mf = MemFabric(home=home)

        # Agent A 写入自己的命名空间
        mf.memory_add("agent-a", "secret", "A的秘密", namespace="agent:agent-a")

        # Agent A 能读到
        r1 = mf.memory_search("agent-a", "秘密")
        assert any("A的秘密" in str(r.get("value", "")) for r in r1["results"])

        # Agent B 在自己的命名空间搜不到 A 的秘密
        r2 = mf.memory_search("agent-b", "秘密")
        # Agent B 的可读命名空间不包含 agent:agent-a
        assert not any("A的秘密" in str(r.get("value", "")) for r in r2["results"])

        # 共享命名空间双方都能读
        mf.memory_add("agent-a", "shared-info", "共享信息", namespace="shared")
        r3 = mf.memory_search("agent-b", "共享信息", namespace=["shared"])
        assert any("共享信息" in str(r.get("value", "")) for r in r3["results"])


def test_lineage():
    """测试因果追踪"""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mf = MemFabric(home=home)

        # 多次修改同一 key（同一命名空间）
        mf.memory_add("alice", "config", "v1", namespace="shared")
        mf.memory_add("bob", "config", "v2", namespace="shared")  # 不同 agent 修改

        r = mf.memory_get("alice", "config", namespace="shared")
        assert r["found"] is True
        assert r["total_events"] == 2, f"期望 2 个事件, 实际 {r['total_events']}"
        assert r["value"] == "v2"  # 最新值
        assert len(r["history"]) == 2
        # 第一个修改者是 alice
        assert r["history"][0]["agent_id"] == "alice"
        assert "v1" in r["history"][0]["value"]
        # 第二个修改者是 bob
        assert r["history"][1]["agent_id"] == "bob"
        assert "v2" in r["history"][1]["value"]


if __name__ == "__main__":
    test_event_store_basic()
    print("✓ test_event_store_basic 通过")

    test_memfabric_engine()
    print("✓ test_memfabric_engine 通过")

    test_mcp_server_jsonrpc()
    print("✓ test_mcp_server_jsonrpc 通过")

    test_namespace_isolation()
    print("✓ test_namespace_isolation 通过")

    test_lineage()
    print("✓ test_lineage 通过")

    print("\n所有测试通过 ✓")

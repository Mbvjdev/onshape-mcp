"""Tests for MCP server tool definitions and routing."""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from onshape_mcp import server as srv


def test_list_tools_returns_many_tools():
    assert len(srv.TOOLS) >= 16, f"expected 16+ tools, got {len(srv.TOOLS)}"


def test_tool_schemas_have_required_fields():
    seen = set()
    for tool in srv.TOOLS:
        assert tool.name, "tool missing name"
        assert tool.name not in seen, f"duplicate tool name: {tool.name}"
        seen.add(tool.name)
        assert tool.description, f"{tool.name}: missing description"
        schema = tool.inputSchema
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"
        assert "properties" in schema
        assert "required" in schema


def test_known_tools_present():
    names = {t.name for t in srv.TOOLS}
    expected = {
        "list_documents", "create_document", "get_document_info",
        "list_parts", "list_features", "get_feature_info", "delete_feature",
        "create_sketch", "add_circle", "add_line", "add_rectangle",
        "extrude", "revolve", "export_stl", "get_thumbnail", "onshape_help",
    }
    missing = expected - names
    assert not missing, f"missing tools: {missing}"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_handle_call_tool_routes_to_client():
    """handle_call_tool must dispatch by name and call the matching client method."""
    fake_client = MagicMock()
    fake_client.list_documents.return_value = [{"id": "X", "name": "Doc"}]

    with patch.object(srv, "get_client", return_value=fake_client):
        out = asyncio.run(srv.handle_call_tool("list_documents", {"query": "foo", "limit": 5}))
    fake_client.list_documents.assert_called_once_with(query="foo", limit=5)
    assert out[0].type == "text"
    assert "Doc" in out[0].text


def test_handle_call_tool_create_sketch_routes():
    fake_client = MagicMock()
    fake_client.create_sketch.return_value = {"sketch_id": "S1"}
    with patch.object(srv, "get_client", return_value=fake_client):
        out = asyncio.run(srv.handle_call_tool(
            "create_sketch",
            {"did": "d", "wid": "w", "eid": "e", "name": "S", "plane": "FRONT"},
        ))
    fake_client.create_sketch.assert_called_once()
    kwargs = fake_client.create_sketch.call_args.kwargs
    assert kwargs["plane"] == "FRONT"
    assert "S1" in out[0].text


def test_handle_call_tool_unknown():
    fake_client = MagicMock()
    with patch.object(srv, "get_client", return_value=fake_client):
        out = asyncio.run(srv.handle_call_tool("not_a_tool", {}))
    assert "Unknown tool" in out[0].text


def test_handle_call_tool_error_path():
    fake_client = MagicMock()
    fake_client.list_documents.side_effect = RuntimeError("boom")
    with patch.object(srv, "get_client", return_value=fake_client):
        out = asyncio.run(srv.handle_call_tool("list_documents", {}))
    assert "Error" in out[0].text and "boom" in out[0].text


def test_handle_call_tool_onshape_help():
    fake_client = MagicMock()
    with patch.object(srv, "get_client", return_value=fake_client):
        out = asyncio.run(srv.handle_call_tool("onshape_help", {"topic": "units"}))
    assert "METERS" in out[0].text

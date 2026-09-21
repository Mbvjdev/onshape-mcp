"""Tests for MCP server tool definitions and routing."""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from mcp.shared.memory import create_connected_server_and_client_session

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
    """A failing tool must propagate the failure, not return it as content.

    Returning the message as ordinary content made the SDK mark the call as a success
    (isError=false). test_failed_tool_call_is_error_true_over_the_wire asserts the
    client-visible half of this contract.
    """
    fake_client = MagicMock()
    fake_client.list_documents.side_effect = RuntimeError("boom")
    with patch.object(srv, "get_client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(srv.handle_call_tool("list_documents", {}))


async def test_failed_tool_call_is_error_true_over_the_wire():
    """The client-visible contract: a failing tool call comes back isError=true.

    Driven through the server's own registered CallToolRequest handler over the
    in-memory transport, so this asserts what an MCP client actually receives — not
    just the return value of handle_call_tool().
    """
    fake_client = MagicMock()
    fake_client.list_documents.side_effect = RuntimeError("boom")
    with patch.object(srv, "get_client", return_value=fake_client):
        async with create_connected_server_and_client_session(srv.app) as session:
            result = await session.call_tool("list_documents", {})
    assert result.isError is True
    assert "boom" in result.content[0].text


async def test_successful_tool_call_is_not_an_error_over_the_wire():
    """The same path must still report ordinary successes as successes."""
    fake_client = MagicMock()
    fake_client.list_documents.return_value = [{"id": "X", "name": "Doc"}]
    with patch.object(srv, "get_client", return_value=fake_client):
        async with create_connected_server_and_client_session(srv.app) as session:
            result = await session.call_tool("list_documents", {})
    assert result.isError is False
    assert "Doc" in result.content[0].text


async def test_onshape_help_is_not_an_error_without_credentials():
    """onshape_help is answered before the client is built — it must stay a success."""
    with patch.object(srv, "get_client", side_effect=AssertionError("help must not authenticate")):
        async with create_connected_server_and_client_session(srv.app) as session:
            result = await session.call_tool("onshape_help", {"topic": "units"})
    assert result.isError is False
    assert "METERS" in result.content[0].text


def test_handle_call_tool_onshape_help():
    with patch.object(srv, "get_client", side_effect=AssertionError("help must not authenticate")):
        out = asyncio.run(srv.handle_call_tool("onshape_help", {"topic": "units"}))
    assert "METERS" in out[0].text


def test_handle_call_tool_get_regen_errors_routes():
    """get_regen_errors must dispatch with did/wid/eid as keyword args."""
    fake_client = MagicMock()
    fake_client.get_regen_errors.return_value = {"featureStates": {}, "problems": []}
    with patch.object(srv, "get_client", return_value=fake_client):
        out = asyncio.run(srv.handle_call_tool(
            "get_regen_errors", {"did": "d", "wid": "w", "eid": "e"},
        ))
    fake_client.get_regen_errors.assert_called_once_with(did="d", wid="w", eid="e")
    assert "featureStates" in out[0].text


def test_handle_call_tool_validate_featurescript_routes():
    fake_client = MagicMock()
    fake_client.validate_featurescript.return_value = {"valid": True, "errors": []}
    with patch.object(srv, "get_client", return_value=fake_client):
        out = asyncio.run(srv.handle_call_tool(
            "validate_featurescript",
            {"did": "d", "wid": "w", "eid": "e", "script": "function(context is Context, id) {}"},
        ))
    fake_client.validate_featurescript.assert_called_once_with(
        did="d", wid="w", eid="e", script="function(context is Context, id) {}"
    )
    assert "valid" in out[0].text


def test_handle_call_tool_build_component_routes():
    fake_client = MagicMock()
    fake_client.build_component.return_value = {"valid": True, "created_bodies": {}}
    with patch.object(srv, "get_client", return_value=fake_client):
        out = asyncio.run(srv.handle_call_tool(
            "build_component",
            {"did": "d", "wid": "w", "eid": "e", "script": "function(context is Context, id) {}"},
        ))
    fake_client.build_component.assert_called_once_with(
        did="d", wid="w", eid="e", script="function(context is Context, id) {}"
    )
    assert "created_bodies" in out[0].text

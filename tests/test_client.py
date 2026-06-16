"""Tests for OnshapeClient — all HTTP layer is mocked."""

import importlib
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import MockResponse


# ── Documents ───────────────────────────────────────────────────────

def test_list_documents(mock_client, mock_http, sample_documents):
    mock_http.set_route("GET", "/documents", MockResponse(200, json_data=sample_documents))
    docs = mock_client.list_documents(query="test", limit=10)
    assert len(docs) == 2
    assert docs[0]["id"] == "did_111"
    assert docs[0]["owner"] == "alice"


def test_get_document_info(mock_client, mock_http, sample_document_info, sample_elements):
    # First the document fetch, then the elements fetch
    mock_http.set_route("GET", "/documents/did_111", MockResponse(200, json_data=sample_document_info))
    mock_http.set_route("GET", "/elements", MockResponse(200, json_data=sample_elements))
    info = mock_client.get_document_info("did_111")
    assert info["id"] == "did_111"
    assert info["workspace"]["id"] == "wid_aaa"
    assert len(info["elements"]) == 2
    assert info["elements"][0]["id"] == "eid_ps1"


# ── Features ────────────────────────────────────────────────────────

def test_list_features(mock_client, mock_http, sample_features):
    mock_http.set_route("GET", "/features", MockResponse(200, json_data=sample_features))
    feats = mock_client.list_features("did", "wid", "eid")
    assert len(feats) == 2
    assert feats[0]["featureId"] == "FID_sketch1"
    assert feats[0]["name"] == "Sketch 1"
    assert feats[1]["featureType"] == "extrude"


# ── Parts ───────────────────────────────────────────────────────────

def test_list_parts(mock_client, mock_http, sample_parts):
    mock_http.set_route("GET", "/parts/d/", MockResponse(200, json_data=sample_parts))
    parts = mock_client.list_parts("did", "wid", "eid")
    assert len(parts) == 2
    assert parts[0]["partId"] == "PID_1"
    assert parts[0]["material"] == "Steel"
    assert parts[0]["mass"] == 0.123
    assert parts[1]["material"] is None


# ── Sketch (onpy-backed) ────────────────────────────────────────────

def test_create_sketch_calls_onpy(mock_client):
    """create_sketch should delegate to onpy. Skip cleanly if onpy is unavailable."""
    try:
        import onpy  # noqa: F401
    except ImportError:
        pytest.skip("onpy not installed")

    fake_sketch = MagicMock()
    fake_sketch.id = "FID_sketch_new"

    fake_doc = MagicMock()
    fake_el = MagicMock()
    fake_el.id = "eid"
    fake_doc.elements = [fake_el]

    fake_client_cls = MagicMock()
    fake_client_cls.return_value.get_document.return_value = fake_doc

    fake_partstudio_cls = MagicMock()
    fake_sketch_cls = MagicMock(return_value=fake_sketch)
    fake_plane_cls = MagicMock()
    fake_orient = MagicMock()
    fake_orient.TOP = "TOP"
    fake_orient.FRONT = "FRONT"
    fake_orient.RIGHT = "RIGHT"
    fake_offset_cls = MagicMock()

    with patch.object(
        mock_client, "_get_onpy_client",
        return_value=(
            fake_client_cls, fake_partstudio_cls, fake_plane_cls,
            fake_orient, fake_offset_cls, fake_sketch_cls,
        ),
    ):
        result = mock_client.create_sketch(
            "did", "wid", "eid", name="MySketch", plane="TOP",
        )
    assert result["sketch_id"] == "FID_sketch_new"
    assert result["plane"] == "TOP"
    fake_sketch_cls.assert_called_once()


# ── Revolve (FeatureScript) ─────────────────────────────────────────

def test_revolve_feature_script(mock_client, mock_http):
    mock_http.set_route(
        "POST", "/featurescript",
        MockResponse(200, json_data={"result": {"value": "ok"}}),
    )
    result = mock_client.revolve(
        "did", "wid", "eid", "sketch_id",
        angle_deg=180,
    )
    assert result["operation"] == "NEW"
    assert result["angle_deg"] == 180
    # Verify the FS script was actually sent
    calls = [c for c in mock_http.calls if c[0] == "POST" and "featurescript" in c[1]]
    assert len(calls) == 1
    script = calls[0][3]["script"]
    assert "revolve(" in script
    assert "sketch_id" in script


# ── STL export ──────────────────────────────────────────────────────

def test_export_stl(mock_client, mock_http, tmp_path):
    binary = b"solid binary stl data" + b"\x00" * 100
    mock_http.set_route(
        "GET", "/stl",
        MockResponse(200, content=binary, json_data={}),
    )
    out = tmp_path / "out.stl"
    result = mock_client.export_stl("did", "wid", "eid", str(out))
    assert out.exists()
    assert out.read_bytes() == binary
    assert result["size_bytes"] == len(binary)


# ── Rate-limit / 429 retry ──────────────────────────────────────────

def test_rate_limit_backoff(mock_client, mock_http):
    """First 429 then 200 → request should retry and succeed."""
    mock_http.set_route(
        "GET", "/documents",
        [
            MockResponse(429, json_data={}, headers={"Retry-After": "0"}),
            MockResponse(200, json_data={"items": []}),
        ],
    )
    docs = mock_client.list_documents()
    assert docs == []
    # Two GETs were made
    gets = [c for c in mock_http.calls if c[0] == "GET"]
    assert len(gets) == 2


# ── Cache behavior ──────────────────────────────────────────────────

def test_cache_hit(mock_client, mock_http, sample_documents):
    mock_http.set_route("GET", "/documents", MockResponse(200, json_data=sample_documents))
    mock_client.list_documents(limit=20)
    mock_client.list_documents(limit=20)
    # Only one underlying HTTP call thanks to the cache
    gets = [c for c in mock_http.calls if c[0] == "GET"]
    assert len(gets) == 1

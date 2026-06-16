"""Tests for ReadCache."""

from onshape_mcp.cache import ReadCache


def test_set_get():
    c = ReadCache()
    c.set("GET", "/documents", {"limit": 20}, value={"items": []})
    assert c.get("GET", "/documents", {"limit": 20}) == {"items": []}


def test_miss():
    c = ReadCache()
    assert c.get("GET", "/documents") is None
    # Different params → cache miss
    c.set("GET", "/documents", {"limit": 20}, value="A")
    assert c.get("GET", "/documents", {"limit": 21}) is None


def test_different_ttl_buckets():
    c = ReadCache()
    assert c._pick_cache("/documents/d/abc") == "document"
    assert (
        c._pick_cache("/partstudios/d/abc/w/x/e/y/features")
        == "features"
    )
    assert c._pick_cache("/parts/d/abc/w/x/e/y") == "parts"
    assert (
        c._pick_cache("/partstudios/d/abc/w/x/e/y/shadedviews/")
        == "thumbnail"
    )
    assert c._pick_cache("/some/random/path") == "default"


def test_invalidate():
    c = ReadCache()
    c.set("GET", "/parts/d/abc/w/x/e/y", value="parts-data")
    c.set("GET", "/partstudios/d/abc/w/x/e/y/features", value="feat-data")
    c.invalidate_document("abc")
    assert c.get("GET", "/parts/d/abc/w/x/e/y") is None
    assert c.get("GET", "/partstudios/d/abc/w/x/e/y/features") is None


def test_invalidate_all():
    c = ReadCache()
    c.set("GET", "/documents", value="docs")
    c.set("GET", "/parts/d/abc/w/x/e/y", value="parts")
    c.invalidate()
    assert c.get("GET", "/documents") is None
    assert c.get("GET", "/parts/d/abc/w/x/e/y") is None

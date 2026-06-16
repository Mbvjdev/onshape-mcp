# onshape-mcp

MCP server for Onshape CAD — semantic tools wrapping the Onshape REST API with rate limiting, caching, and auth handling. Designed for AI assistants (Claude, DeepSeek, GPT) to do real CAD work.

**What:** 18 tools that translate "make a Ø175mm disc with four Ø12mm bolt holes" → API calls, without you thinking about btTypes, transient IDs, or rate limits.

**Why:** Onshape's API is powerful but raw — 17 API calls for a bolt pattern, rate limits that trigger at 10+ calls/minute, and FeatureScript required for transient face IDs. This server abstracts all that away behind semantic tools.

**Status:** v0.2 — sketches, extrudes, revolves, fillets, chamfers, parts, features, STL export, and thumbnails all work. 28 tests. Used with Claude via Hermes Agent.

## Installation

```bash
git clone https://github.com/Mbvjdev/onshape-mcp.git
cd onshape-mcp
pip install -e .
```

Dependencies:
- `mcp` — MCP Python SDK (stdio server)
- `httpx` — HTTP client for REST calls
- `cachetools` — TTL cache
- `onpy` — Onshape Python library (for feature creation, handles btTypes correctly)

## Auth

The server reads Onshape API keys in this order:

1. `ONSHAPE_DEV_ACCESS` + `ONSHAPE_DEV_SECRET` env vars
2. `ONSHAPE_ACCESS_KEY` + `ONSHAPE_SECRET_KEY` env vars
3. `~/.onpy/config.json`

## Usage

### Standalone (MCP stdio server)
```bash
PYTHONPATH=src python -m onshape_mcp.server
```

### With Hermes Agent
Configure in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  onshape:
    command: "/path/to/venv/bin/python"
    args: ["-m", "onshape_mcp.server"]
    env:
      ONSHAPE_DEV_ACCESS: "${ONSHAPE_DEV_ACCESS}"
      ONSHAPE_DEV_SECRET: "${ONSHAPE_DEV_SECRET}"
      PYTHONPATH: "/path/to/onshape-mcp/src"
    timeout: 180
```

Hermes discovers the tools on restart. They appear as `mcp_onshape_*`.

## Tools (18)

### Documents
| Tool | Description |
|------|-------------|
| `list_documents` | Search/list documents. Returns name, ID, owner. |
| `create_document` | Create new document. Returns doc ID + workspace ID. |
| `get_document_info` | Document details: workspace, elements (Part Studios). |

### Parts & Features
| Tool | Description |
|------|-------------|
| `list_parts` | List all parts in a Part Studio: name, type, material, mass. |
| `list_features` | List all features with types and suppression status. |
| `get_feature_info` | Details about a specific feature. |
| `delete_feature` | Delete a feature (⚠️ children before parents). |

### Sketching
| Tool | Description |
|------|-------------|
| `create_sketch` | Create sketch on TOP/FRONT/RIGHT plane, optionally with offset. |
| `add_circle` | Add circle: center (x,y) + radius. ALL in METERS. |
| `add_line` | Add line: start → end point. |
| `add_rectangle` | Add rectangle: two opposite corners. |

### 3D Operations
| Tool | Description |
|------|-------------|
| `extrude` | Extrude sketch → 3D body. Operations: NEW, ADD, REMOVE. |
| `revolve` | Revolve sketch around axis via FeatureScript. For round parts. |
| `fillet` | Round edges of a feature. Radius in meters. |
| `chamfer` | Bevel edges of a feature. Distance in meters. |

### Export
| Tool | Description |
|------|-------------|
| `export_stl` | Export Part Studio as STL (mm/cm/m/inch/foot). |
| `get_thumbnail` | Get shaded 3D view as PNG — "see" the model. |

### Help
| Tool | Description |
|------|-------------|
| `onshape_help` | Quick reference: units, planes, operations, rate limits, pitfalls. |

## Units

**EVERYTHING is in METERS.** This is Onshape's native unit.

```
1 mm = 0.001 m
1 cm = 0.01 m
1 m  = 1.0 m
```

Quick reference:
- Ø10mm hole → `radius=0.005`
- 50mm offset from TOP → `offset=0.05`
- 76mm extrude → `distance=0.076`
- Ø175mm circle → `radius=0.0875`

## Rate Limiting

Handled automatically:
- **Sliding window:** Max 10 calls per 60 seconds (conservative, avoids throttle)
- **Minimum interval:** 2 seconds between calls
- **Exponential backoff:** On 429: 5s → 10s → 20s → ... max 120s
- **Cache:** GET responses cached 30-120 seconds (type-dependent)
- **Pre-acquire:** onpy operations pre-reserve rate limit tokens

If calls take a while: the rate limiter is pacing things. Be patient.

## Project Structure

```
onshape-mcp/
├── pyproject.toml
├── README.md
├── pytest.ini
├── src/onshape_mcp/
│   ├── __init__.py
│   ├── server.py              ← MCP server (stdio, 18 tools)
│   ├── client.py              ← OnshapeClient (REST + onpy wrapper)
│   ├── rate_limiter.py        ← Global singleton, sliding window + backoff
│   └── cache.py               ← TTL cache (30s-5min)
└── tests/
    ├── conftest.py            ← Mock HTTP, fixtures
    ├── test_client.py         ← 9 tests (mocked API)
    ├── test_server.py         ← 8 tests (tool routing)
    ├── test_rate_limiter.py   ← 6 tests (delay, backoff, singleton)
    └── test_cache.py          ← 5 tests (set/get, TTL, invalidate)
```

## Development

```bash
# Run tests (no API calls needed — fully mocked)
pytest tests/ -v

# Test imports
python -c "from onshape_mcp.client import OnshapeClient; print('OK')"

# Run MCP server manually (for debugging)
python -m onshape_mcp.server
```

## Known Limitations

- **Revolve silent failures:** Profile MUST NOT cross the revolve axis. All sketch points must be on one side. Includes `validate_revolve_profile()` to catch this before calling the API.
- **Extrude REMOVE:** Uses REST + FeatureScript. Can fail on complex geometry.
- **Sketch polygons:** Each `add_line` = 1+ API call. 10+ lines trigger rate limits.
- **Part Studio corruption:** A malformed REST POST can corrupt a Part Studio. The server uses onpy for feature creation to prevent this.
- **Document deletion:** Onshape doesn't allow permanent deletion via API (403). Use UI.
- **Session-only sketches:** Sketches must be created in the same session. Reconnecting to existing sketches requires a fresh `create_sketch` call.

## License

MIT

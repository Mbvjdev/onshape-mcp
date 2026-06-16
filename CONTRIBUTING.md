# Contributing to onshape-mcp

Love the idea of AI-assisted CAD? PRs welcome. Here's how.

## Setup

```bash
git clone https://github.com/Mbvjdev/onshape-mcp.git
cd onshape-mcp
pip install -e ".[dev]"
```

Install onpy (needed for feature creation — raw REST is unreliable for btTypes):

```bash
pip install onpy
```

## Architecture

```
┌─────────────────────────────────────┐
│ Hermes / Claude Desktop / MCP client│
│  "Make a Ø100mm disc with 4 holes"  │
└──────────────┬──────────────────────┘
               │ MCP protocol (stdio)
┌──────────────▼──────────────────────┐
│ server.py                           │
│  Tool definitions + routing         │
│  18 tools with JSON schemas         │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ client.py                           │
│  OnshapeClient                      │
│  ┌──────────┐  ┌──────────────────┐ │
│  │ REST     │  │ onpy (features)  │ │
│  │ (reads,  │  │ (sketches,       │ │
│  │  export, │  │  extrudes,       │ │
│  │  thumbs) │  │  revolves)       │ │
│  └──────────┘  └──────────────────┘ │
│  rate_limiter.py (global singleton) │
│  cache.py (TTL read cache)          │
└──────────────┬──────────────────────┘
               │ HTTP
┌──────────────▼──────────────────────┐
│         Onshape API                  │
│      cad.onshape.com/api/v6         │
└─────────────────────────────────────┘
```

### Core Principles

1. **Semantic tools over raw API.** The LLM never sees btTypes, FeatureScript, or transient IDs. Tools represent CAD operations at the level a human (or AI) thinks about them.

2. **Rate limiting is mandatory.** Onshape throttles at ~10 calls/minute. Every API call — including onpy's internal FeatureScript preflights — must be accounted for. Use `_pre_acquire(n)` before onpy calls, and the global rate limiter handles raw REST calls automatically.

3. **Meters, always.** Onshape's native unit is meters. Every tool input and output uses meters. Convert at the edges if needed (STL export has a `units` parameter), but the internal representation is always meters.

4. **onpy for features, REST for everything else.** onpy handles btType strings correctly. Raw REST POSTs with manual btTypes can corrupt Part Studios. Only use raw REST for operations onpy doesn't support (extrude-remove, revolve via FeatureScript, fillet, chamfer).

## Adding a New Tool

1. **Add the client method** in `client.py`:
   ```python
   def my_new_op(self, did, wid, eid, ...) -> dict:
       self._pre_acquire(2)  # if using onpy
       # ... implementation
       self.cache.invalidate_document(did)
       return {"result": "..."}
   ```

2. **Add the tool definition** in `server.py` (in the `TOOLS` list):
   ```python
   Tool(
       name="my_new_op",
       description="What this tool does...",
       inputSchema={
           "type": "object",
           "properties": {
               "did": {"type": "string", "description": "Document ID"},
               # ...
           },
           "required": ["did", "wid", "eid"],
       },
   ),
   ```

3. **Add the handler** in `handle_call_tool()`:
   ```python
   elif name == "my_new_op":
       result = client.my_new_op(...)
       return [TextContent(type="text", text=json.dumps(result, indent=2))]
   ```

4. **Add tests** in `tests/test_client.py` (mocked HTTP) and `tests/test_server.py` (tool routing).

## Testing

```bash
# All tests (no API calls — fully mocked)
pytest tests/ -v

# Specific file
pytest tests/test_rate_limiter.py -v

# With coverage
pip install pytest-cov
pytest tests/ --cov=src/onshape_mcp --cov-report=html
```

Tests use `MockHttp` (`tests/conftest.py`) to intercept all HTTP calls. No real Onshape API calls are made during tests.

### Test Design Rules
- Tests must not call the real Onshape API (rate limits, costs, flakiness)
- Use `mock_client` fixture for client tests (injects MockHttp)
- Use `reset_rate_limiter` fixture for rate limiter tests (zero-delay singleton)
- Mock onpy with `unittest.mock.patch` for feature creation tests

## FeatureScript Operations

For operations that require FeatureScript (revolve, fillet, chamfer, extrude-remove):

1. Build the FeatureScript string using the operation's pattern
2. `POST /partstudios/d/{did}/w/{wid}/e/{eid}/featurescript`
3. Check the result for errors/notices
4. Invalidate cache

FeatureScript failures are often **silent** (HTTP 200, but no geometry created). Always:
- Validate inputs before calling (e.g., `validate_revolve_profile`)
- Check the result structure for notices/errors
- Raise descriptive exceptions when things go wrong

## Rate Limiter

The global `RateLimiter` singleton is in `rate_limiter.py`. Key rules:

- **Never create a local RateLimiter.** Always use `get_rate_limiter()`.
- **Pre-acquire before onpy calls.** onpy's HTTP layer bypasses the rate limiter, so call `_pre_acquire(n)` before any onpy operation.
- **Don't hold the lock during sleep.** The rate limiter computes wait time under lock, then releases the lock during `time.sleep()`.

The default settings (10 calls/60s, 2s minimum interval) are conservative. Derived from real-world testing against Onshape's free tier. Adjust `max_calls` and `min_interval` in `RateLimiter.__init__()` if you have a paid account with higher limits.

## Common Pitfalls

- **`get_document_info` expects `defaultWorkspace`** (singular, an object), not `workspaces` (plural, array). The Onshape API is inconsistent here.
- **`create_document` uses onpy, not raw REST.** Free accounts get 409 from `POST /documents` but onpy handles this correctly.
- **Feature IDs don't reconstruct Sketch objects.** `_resolve_sketch` uses an in-memory cache from `create_sketch`. You can't reconnect to sketches from previous sessions.
- **API keys are NEVER in the repo.** The `.gitignore` blocks `.env` and `.onpy/`. Test fixtures use placeholder keys.

## Getting Help

- **Issues:** [github.com/Mbvjdev/onshape-mcp/issues](https://github.com/Mbvjdev/onshape-mcp/issues)
- **Onshape API docs:** [onshape-public.github.io/docs](https://onshape-public.github.io/docs/)
- **MCP spec:** [modelcontextprotocol.io](https://modelcontextprotocol.io/)
- **Hermes Agent:** [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)

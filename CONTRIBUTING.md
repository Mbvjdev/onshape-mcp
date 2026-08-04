# Contributing to onshape-mcp

Thanks for making AI-assisted CAD less painful. This project is deliberately conservative: CAD actions touch real user documents and Onshape rate limits are account-wide.

## Local setup

```bash
git clone https://github.com/Mbvjdev/onshape-mcp.git
cd onshape-mcp
python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest tests/ -v
```

The test suite mocks every HTTP request. Never use personal Onshape keys in tests, fixtures, screenshots, commits, or CI variables.

## Design rules

1. **Semantic tools over raw API payloads.** The model should call CAD operations, not construct `btType` payloads or transient-ID queries.
2. **Use meters internally.** Onshape API-facing dimensions are meters. Document conversions clearly at the tool boundary.
3. **Respect the shared rate limiter.** Raw REST requests go through `_request()`. Before an `onpy` operation, reserve every expected internal call with `_pre_acquire()`.
4. **Pass MCP credentials explicitly to `onpy`.** `onpy` does not consume `ONSHAPE_*` environment variables on its own. Use `_new_onpy_client()` so it cannot prompt interactively or depend on `~/.onpy/config.json`.
5. **Prefer safe failure to corrupting a Part Studio.** Validate inputs, report limitations, and keep raw feature POSTs to a minimum.
6. **Do not log secrets.** Errors may name a configuration field, but never include credential values or authorization headers.

## Adding a tool

1. Add the implementation to `src/onshape_mcp/client.py`.
2. Define its MCP schema in `src/onshape_mcp/server.py`.
3. Route it in `handle_call_tool()`.
4. Add mocked client and routing tests.
5. Run the full test suite and inspect the diff for accidental credentials or local paths.

## Testing

```bash
./.venv/bin/python -m pytest tests/ -v
./.venv/bin/python -m compileall -q src tests
```

Tests must never make a network call to Onshape. `tests/conftest.py` provides a mock HTTP client and deliberately non-secret test values.

## Pull requests

- Keep changes focused and describe the user-visible behavior.
- Include tests for both success and expected error paths.
- Do not add generated files, personal configurations, `.env` files, key material, or Onshape document exports.
- Preserve compatibility with Python 3.12 and the currently pinned dependency ranges.

## Security reports

Follow [SECURITY.md](SECURITY.md). Do not open a public issue containing an access key, secret, document URL that should stay private, or reproduction data from a customer project.
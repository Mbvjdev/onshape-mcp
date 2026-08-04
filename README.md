# onshape-mcp

[![CI](https://github.com/Mbvjdev/onshape-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Mbvjdev/onshape-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A local [Model Context Protocol](https://modelcontextprotocol.io/) server that gives [Hermes Agent](https://hermes-agent.nousresearch.com/docs) practical, rate-limited tools for creating and inspecting Onshape CAD.

It turns model requests such as “create a 100 mm disc with a 30 mm center hole” into semantic CAD operations, while handling Onshape API authentication, rate limiting, caching, FeatureScript operations, and the gnarly parts of the REST API.

> This repository contains **no API keys, personal Hermes configuration, or Onshape documents**. Credentials stay in each user's local `~/.hermes/.env` file and are passed only to this MCP subprocess.

## Start here

**[Read the complete Hermes + Onshape setup guide.](GETTING_STARTED.md)** It covers a clean Python environment, Onshape developer keys, secure Hermes configuration, verification, and a first CAD prompt.

The short version:

```bash
# Python 3.12+
git clone https://github.com/Mbvjdev/onshape-mcp.git
cd onshape-mcp
python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

Then add the two Onshape key values to `~/.hermes/.env`, copy the appropriate fragment from [`examples/`](examples/), and run:

```bash
hermes mcp test onshape
```

## What Hermes gets

| Area | Tools |
| --- | --- |
| Documents | Search/list, create, and inspect documents and Part Studios |
| Parts and features | Inspect parts, feature trees, and individual features; delete features in dependency-safe order |
| Sketching | Create sketches on standard planes; add circles, lines, and rectangles |
| 3D operations | Extrude, revolve, fillet, and chamfer |
| Output | Export STL files and retrieve shaded thumbnails |
| Guidance | In-tool help for units, planes, operations, rate limits, and common pitfalls |

All dimensional tool inputs use **meters**, because that is Onshape's API unit. `10 mm` is `0.01`, and a `100 mm` diameter circle has a radius of `0.05`.

## Why an MCP layer?

The raw Onshape API is powerful but unfriendly for agents:

- Feature POST payloads rely on internal `btType` values and transient IDs.
- `onpy` may perform extra HTTP calls internally, so naive clients hit account-level rate limits very quickly.
- Some operations use FeatureScript because their REST variants are fragile.
- A malformed feature request can leave a Part Studio in a bad state.

`onshape-mcp` exposes a smaller, CAD-oriented interface and applies a shared conservative rate limiter, read cache, backoff after `429`, and credential handling that also works for `onpy` feature creation.

## Security model

- The recommended configuration references `${ONSHAPE_DEV_ACCESS}` and `${ONSHAPE_DEV_SECRET}`. Hermes resolves those at runtime from `~/.hermes/.env`; the actual values never belong in `config.yaml` or this repository.
- Hermes intentionally filters the environment given to local MCP servers. The example config explicitly passes only the two Onshape values needed by this server.
- Do not put credentials in shell commands, chat logs, issues, commits, screenshots, or copied configuration fragments.
- If a key is committed by mistake, revoke it in Onshape first. Removing a line in a later commit does **not** remove it from Git history.

See [SECURITY.md](SECURITY.md) for reporting and incident guidance.

## Known operating constraints

- Onshape rate limits are account-wide. The server deliberately trades speed for reliability; large line-based sketches can take time.
- Sketch objects are session-scoped in the current `onpy` integration. Create and populate a sketch in the same Hermes conversation.
- `add_rectangle` creates four connected lines because `onpy` does not provide a native rectangle method.
- Revolve profiles must be closed, must not cross the axis, and must not self-intersect. Onshape can otherwise return success without creating a body.
- Complex subtract operations may be better completed manually in the Onshape UI.

## Development

```bash
# The test suite is fully mocked: no API calls and no Onshape credentials.
./.venv/bin/python -m pytest tests/ -v
```

Contributions are welcome; read [CONTRIBUTING.md](CONTRIBUTING.md) first. Every new operation needs mocked tests and must respect the shared rate limiter.

## License

[MIT](LICENSE).
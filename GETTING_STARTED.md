# Getting started: Hermes Agent + Onshape

This guide creates a clean, local setup. It keeps your Onshape credentials out of Git and gives Hermes a local MCP server with 18 CAD tools.

## What you need

- A current [Hermes Agent installation](https://hermes-agent.nousresearch.com/docs)
- Python **3.12 or newer**
- An Onshape account and an API key pair from the [Onshape Developer Portal](https://dev-portal.onshape.com/)
- Git

The server works with other MCP clients too, but this guide uses Hermes.

## 1. Clone and install the server

Choose a directory you control. The examples below use `~/onshape-mcp`.

### macOS or Linux

```bash
git clone https://github.com/Mbvjdev/onshape-mcp.git ~/onshape-mcp
cd ~/onshape-mcp
python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

### Windows PowerShell

```powershell
git clone https://github.com/Mbvjdev/onshape-mcp.git $HOME\onshape-mcp
cd $HOME\onshape-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Using a project-specific virtual environment is intentional. It prevents an unrelated Python installation or `PYTHONPATH` from supplying incompatible MCP packages.

## 2. Create an Onshape API key pair

1. Sign in at the [Onshape Developer Portal](https://dev-portal.onshape.com/).
2. Create an API key pair with the access required for the documents you intend to use.
3. Keep the access key and secret key private. Treat the secret like a password.

Open Hermes' secrets file — **not a file inside this repository** — and add the two values:

```dotenv
# ~/.hermes/.env
ONSHAPE_DEV_ACCESS=replace-with-your-access-key
ONSHAPE_DEV_SECRET=replace-with-your-secret-key
```

[` .env.example`](.env.example) contains exactly the two variable names with empty values. Do not copy your populated secrets back into the clone, do not commit them, and do not paste them in issues or chat.

> The server also supports `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY` and an existing `~/.onpy/config.json`, but the `ONSHAPE_DEV_*` pair above is the recommended Hermes setup.

## 3. Configure Hermes

Open your Hermes config:

```bash
hermes config edit
```

Merge one of the example entries under your existing top-level `mcp_servers:` key. Do not replace other MCP servers you already use.

- macOS/Linux: [`examples/hermes-config.macos-linux.yaml`](examples/hermes-config.macos-linux.yaml)
- Windows: [`examples/hermes-config.windows.yaml`](examples/hermes-config.windows.yaml)

For example, on macOS/Linux, if you cloned to `~/onshape-mcp`:

```yaml
mcp_servers:
  onshape:
    command: "${HOME}/onshape-mcp/.venv/bin/onshape-mcp"
    env:
      ONSHAPE_DEV_ACCESS: "${ONSHAPE_DEV_ACCESS}"
      ONSHAPE_DEV_SECRET: "${ONSHAPE_DEV_SECRET}"
    timeout: 180
    connect_timeout: 60
```

The `${VAR}` references are expanded by Hermes when it connects. That means the literal config is safe to keep in `~/.hermes/config.yaml` or share as an example: the private values stay in `~/.hermes/.env`.

## 4. Verify transport and credentials

First verify that Hermes can launch and discover the MCP server:

```bash
hermes mcp test onshape
hermes mcp list
```

Then start a **new Hermes session** (or use `/reload-mcp` in an interactive Hermes session) and send this read-only prompt:

```text
List my three most recently modified Onshape documents. Do not modify anything.
```

A successful document listing proves both the MCP connection and your Onshape authentication.

## 5. Make a first model

Once read-only access works, use a fresh test document rather than an important production design:

```text
Create an Onshape document named "Onshape MCP smoke test". Inspect its elements to find Part Studio 1. Create a sketch named "Base" on the TOP plane, add a circle centered at the origin with a 50 mm radius, and extrude it 10 mm as a new body. Then list the parts and tell me what was created. Use the MCP tools and convert all dimensions to their meter values before calling them.
```

This creates a 100 mm diameter, 10 mm thick test disc. You can inspect the result in Onshape and delete the document when you are finished.

More ready-to-use prompts are in [`examples/first-prompts.md`](examples/first-prompts.md).

## Troubleshooting

### `hermes mcp test onshape` cannot launch the server

- Verify the `command:` path points to the `onshape-mcp` executable inside this clone's `.venv`.
- Re-run the installation command with the `.venv` Python, not a global `pip`.
- On Windows, use the Windows config fragment; the executable normally ends in `.exe`.
- Run `hermes mcp list` to see whether Hermes has the expected server entry.

### Hermes sees the tools, but `list_documents` says keys are missing or authentication fails

- Confirm both `ONSHAPE_DEV_ACCESS` and `ONSHAPE_DEV_SECRET` are present in `~/.hermes/.env` with no surrounding quotes, whitespace, or placeholder text.
- Confirm the two names appear under `mcp_servers.onshape.env` in `config.yaml` as `${...}` references.
- Create a new Hermes session after changing credentials.
- Generate a new key pair in the Onshape Developer Portal if the existing pair was revoked or copied incorrectly.

### Requests return `429` or CAD operations seem slow

Onshape limits API traffic at the account level. The server intentionally spaces requests, caches reads, and backs off after `429`. Let it recover; do not repeatedly restart or retry the same large operation.

Each line in a sketch is an API operation. A rectangle uses four connected lines, and complex profiles are best made in small batches or completed in the Onshape UI.

### A sketch cannot be found after a new conversation

The current `onpy` integration keeps live sketch objects in the MCP server session. Create a new sketch and populate it within the same Hermes conversation; use `list_features` to inspect older sketches rather than trying to reopen them for edits.

### A revolve finishes but no body appears

The profile must be closed, non-self-intersecting, and strictly on one side of the revolve axis. For a default Z-axis revolve, keep every profile point at `X > 0`.

## Updating

```bash
cd ~/onshape-mcp
git pull
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest tests/ -q
```

Restart Hermes or reload MCP servers after an update. Review the Git diff before updating a local tool that can access your CAD account.

## Next steps

- Browse the available capabilities with `hermes mcp configure onshape`.
- Use `onshape_help` for built-in reminders about units, planes, operations, rate limits, and pitfalls.
- Read [CONTRIBUTING.md](CONTRIBUTING.md) before extending the server.
- Review [SECURITY.md](SECURITY.md) if you believe a key or vulnerability was exposed.
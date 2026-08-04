# Security policy

## Scope

This project launches a local MCP server that can access the Onshape documents available to the configured API key pair. Treat its credentials and CAD data as sensitive.

## Reporting a vulnerability

Do not include API keys, secrets, private document identifiers, customer data, or full exploit instructions in a public GitHub issue.

Use GitHub's private security-advisory reporting channel for this repository when available. If private reporting is not available, open a minimal public issue that requests a secure contact path and includes only a high-level impact summary.

## If a credential is exposed

1. Revoke or rotate the affected key pair in the Onshape Developer Portal immediately.
2. Remove the credential from all local configs, shell history, screenshots, logs, and CI variables where it appeared.
3. If it reached Git, purge it from every reachable branch and tag; a follow-up commit that deletes the line is not enough.
4. Confirm the replacement key lives only in `~/.hermes/.env` and is referenced with `${...}` in `~/.hermes/config.yaml`.
5. Report the incident without including the old or new key values.

## Repository guarantees

- No working API keys or personal MCP configuration are intentionally stored in this repository.
- `.env` files, local virtual environments, and common private key formats are ignored by Git.
- CI runs tests without real Onshape credentials and scans repository history for credential-shaped content.

## Safe configuration pattern

```yaml
mcp_servers:
  onshape:
    command: "${HOME}/onshape-mcp/.venv/bin/onshape-mcp"
    env:
      ONSHAPE_DEV_ACCESS: "${ONSHAPE_DEV_ACCESS}"
      ONSHAPE_DEV_SECRET: "${ONSHAPE_DEV_SECRET}"
```

The secret values belong in `~/.hermes/.env`, never in this file, the repository config, or a commit.
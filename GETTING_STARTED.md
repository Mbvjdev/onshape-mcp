# Getting Started: Onshape CAD via AI

This guide gets you from zero to "AI, make me a bracket with four bolt holes" in ~5 minutes.

## What You'll Need

- **Python 3.12+** with pip
- **Onshape account** (free works) + [API keys](https://dev-portal.onshape.com/)
- **Hermes Agent** (or any MCP-compatible client like Claude Desktop)

## Step 1: Install Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) is an AI agent framework with built-in MCP support. It connects LLMs (Claude, DeepSeek, GPT) to tools like this Onshape server.

```bash
# Install Hermes Agent
pip install hermes-agent

# Or clone for the latest version:
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
pip install -e .
```

Configure your LLM provider. Hermes works with OpenAI, Anthropic, DeepSeek, OpenRouter, and local models (Ollama, llama.cpp). Example:

```bash
# Set up API keys in ~/.hermes/.env
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.hermes/.env
echo 'OPENAI_API_KEY=sk-...' >> ~/.hermes/.env

# Or use Hermes' built-in model catalog
hermes model list
hermes model set claude-sonnet-4
```

## Step 2: Get Onshape API Keys

1. Go to [dev-portal.onshape.com](https://dev-portal.onshape.com/)
2. Sign in with your Onshape account
3. Create a new API key pair
4. Store them:

```bash
# In ~/.hermes/.env
echo 'ONSHAPE_DEV_ACCESS=your_access_key_here' >> ~/.hermes/.env
echo 'ONSHAPE_DEV_SECRET=your_secret_key_here' >> ~/.hermes/.env
```

> **Note:** Dev keys work with free Onshape accounts for reading and creating documents.

## Step 3: Install onshape-mcp

```bash
git clone https://github.com/Mbvjdev/onshape-mcp.git ~/onshape-mcp
cd ~/onshape-mcp
pip install -e .
```

You'll also need the `onpy` library for feature creation:

```bash
pip install onpy
```

## Step 4: Configure Hermes

Add the Onshape MCP server to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  onshape:
    command: "python"
    args: ["-m", "onshape_mcp.server"]
    env:
      ONSHAPE_DEV_ACCESS: "${ONSHAPE_DEV_ACCESS}"
      ONSHAPE_DEV_SECRET: "${ONSHAPE_DEV_SECRET}"
      PYTHONPATH: "/Users/you/onshape-mcp/src"    # adjust path!
    timeout: 180
```

The `${VAR}` syntax makes Hermes resolve environment variables from `~/.hermes/.env`.

> **Alternative:** Use Claude Desktop or any MCP client. Add to `claude_desktop_config.json`:
> ```json
> {
>   "mcpServers": {
>     "onshape": {
>       "command": "python",
>       "args": ["-m", "onshape_mcp.server"],
>       "env": {
>         "ONSHAPE_DEV_ACCESS": "your_key",
>         "ONSHAPE_DEV_SECRET": "your_secret",
>         "PYTHONPATH": "/path/to/onshape-mcp/src"
>       }
>     }
>   }
> }
> ```

## Step 5: Restart & Verify

Restart Hermes so it discovers the MCP server:

```bash
hermes --new
# or if you're in TUI mode: /new
```

Check the tools are loaded:

```
# In Hermes:
What Onshape tools do you have available?
```

You should see 18 tools: `mcp_onshape_list_documents`, `mcp_onshape_create_sketch`, `mcp_onshape_extrude`, etc.

## Step 6: Your First CAD Command

Try something simple:

```
List my Onshape documents
```

If that works, try:

```
Create a new document called "AI Test". Then make a Part Studio 
with a sketch on the TOP plane. Add a circle centered at (0,0) 
with radius 0.05 (which is 100mm diameter). Extrude it 10mm as 
a new body. Export the STL to /tmp/test.stl.
```

## Common Issues

### "Onshape auth failed" at startup
- Check your API keys in `~/.hermes/.env`
- Dev keys from the Onshape developer portal are different from your account password
- Try deleting `~/.onpy/config.json` if it has stale keys

### Rate limited (429 errors)
- The server handles this automatically with backoff
- Wait 2-3 minutes, rate limits reset
- The server limits itself to 10 calls/minute to avoid throttling

### "Sketch not found in session cache"
- Sketches must be created with `create_sketch` in the same session
- You can't reconnect to a sketch created in a previous conversation
- Create a fresh sketch and work from there

### Tools don't appear
- Make sure `mcp_servers.onshape` is in `config.yaml` (not `mcp` or `servers`)
- Check Hermes startup logs: `hermes logs`
- Verify `pip install onpy` succeeded
- The PYTHONPATH must point to the `src` directory inside onshape-mcp

## Next Steps

- **Read the [Tools reference](README.md#tools-18)** for all 18 tools
- **Check [CONTRIBUTING.md](CONTRIBUTING.md)** if you want to add features
- **File issues** at [github.com/Mbvjdev/onshape-mcp](https://github.com/Mbvjdev/onshape-mcp)

---

Built with [Hermes Agent](https://github.com/NousResearch/hermes-agent) and [onpy](https://github.com/onshape-public/onpy).

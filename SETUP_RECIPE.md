# Graphiti MCP Server Setup Recipe

Step-by-step guide to get the Graphiti MCP server running with Azure OpenAI and FalkorDB.

**Uses LiteLLM provider** - the recommended solution for Azure OpenAI compatibility.

---

## Prerequisites

- Docker Desktop with WSL2 integration
- uv (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Azure OpenAI resource with:
  - LLM deployment (e.g., `model-router`, `gpt-4o`)
  - Embedding deployment (e.g., `text-embedding-3-small`)

---

## Step 1: Start FalkorDB

```bash
cd /home/gyasisutton/dev/tools/graphiti-mcp
docker compose up -d
```

Verify it's running:
```bash
docker exec falkordb-graphiti redis-cli PING
# Should return: PONG
```

---

## Step 2: Configure Environment

Create/edit `.env` file:

```bash
# Database
FALKORDB_URI=redis://localhost:6379
FALKORDB_DATABASE=graphiti_memory

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.cognitiveservices.azure.com
AZURE_OPENAI_API_KEY=your-api-key
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_DEPLOYMENT=your-llm-deployment
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small

# Graphiti
GRAPHITI_GROUP_ID=developer-yourusername
USER_ID=yourusername
```

**Note**: API version `2024-12-01-preview` is required for json_schema response_format support.

---

## Step 3: Install Dependencies

```bash
cd /home/gyasisutton/dev/tools/graphiti-mcp
uv sync
```

---

## Step 4: Test Server

```bash
uv run python main.py --config config/config-litellm-azure.yaml --transport stdio
```

You should see:
```
INFO - Successfully initialized Graphiti client
INFO - Starting MCP server with transport: stdio
```

Press Ctrl+C to stop.

**Why LiteLLM?** The LiteLLM provider uses `OpenAIGenericClient` with `json_schema` response_format, which is compatible with Azure OpenAI. The direct Azure client uses `responses.parse()` which Azure does not support.

---

## Step 5: Add to Claude Code and/or Cursor

### Claude Code (`~/.claude.json`)

Add under `mcpServers`:

```json
"graphiti": {
  "command": "uv",
  "args": [
    "--directory",
    "/home/gyasisutton/dev/tools/graphiti-mcp",
    "run",
    "python",
    "main.py",
    "--config",
    "config/config-litellm-azure.yaml",
    "--transport",
    "stdio"
  ]
}
```

### Cursor (`~/.cursor/mcp.json`)

Add under `mcpServers`:

```json
"graphiti": {
  "command": "uv",
  "args": [
    "--directory",
    "/home/gyasisutton/dev/tools/graphiti-mcp",
    "run",
    "python",
    "main.py",
    "--config",
    "config/config-litellm-azure.yaml",
    "--transport",
    "stdio"
  ]
}
```

---

## LiteLLM Provider Details

The LiteLLM provider uses `OpenAIGenericClient` with `json_schema` response_format, which is compatible with Azure OpenAI's structured output support.

**Model format**: Prefix Azure deployment names with `azure/`:
- `azure/model-router` - Your Azure deployment name
- `azure/gpt-4o` - Example for gpt-4o deployment

**Environment variables**:
- `AZURE_OPENAI_ENDPOINT` - Your Azure endpoint URL
- `AZURE_OPENAI_API_KEY` - Your Azure API key
- `AZURE_OPENAI_API_VERSION` - API version (**required**: `2024-12-01-preview`)

**Technical note**: The direct Azure client (`AzureOpenAILLMClient`) uses `responses.parse()` for structured outputs, which Azure OpenAI does not support. LiteLLM solves this by using the standard `chat.completions.create()` API with `response_format={"type": "json_schema", ...}`.

---

## Step 6: Restart Claude Code / Cursor

```bash
# Close and reopen Claude Code
# Or use: /mcp to check if graphiti is loaded
```

---

## Verify It Works

In Claude Code, the graphiti tools should be available:
- `mcp__graphiti__add_memory`
- `mcp__graphiti__search_nodes`
- `mcp__graphiti__search_memory_facts`
- `mcp__graphiti__get_status`

Test:
```
Call mcp__graphiti__get_status
```

Should return: `{"status":"ok","message":"Graphiti MCP server is running..."}`

---

## Troubleshooting

### FalkorDB not running
```bash
docker compose up -d
docker ps  # Check status
```

### Dependencies missing
```bash
rm -rf .venv uv.lock
uv sync
```

### Azure OpenAI errors
- Check `.env` has correct endpoint and API key
- Verify deployment names match your Azure portal

---

## Daily Usage

FalkorDB persists data automatically. Just ensure Docker is running:

```bash
docker compose up -d  # If not already running
```

Claude Code will auto-start the MCP server when needed.

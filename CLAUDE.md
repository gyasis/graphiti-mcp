# Graphiti MCP Server - Claude Code Instructions

## Project Overview
This is the Graphiti MCP server setup for AI agent memory with FalkorDB backend.

## IMPORTANT: Use LiteLLM Provider for Azure OpenAI

The **LiteLLM provider** is **REQUIRED** for Azure OpenAI deployments. It avoids the `responses.parse()` API issues by using `OpenAIGenericClient` with `json_schema` response_format.

**Config file**: `config/config-litellm-azure.yaml`
**Model format**: `azure/<deployment_name>` (e.g., `azure/model-router`)
**API version**: `2024-12-01-preview`

## Quick Commands

### Start FalkorDB (requires Docker Desktop WSL2 integration)
```bash
cd /home/gyasisutton/dev/tools/graphiti-mcp
docker compose up -d
```

### Verify FalkorDB is running
```bash
docker exec falkordb-graphiti redis-cli PING
# Should return: PONG
```

### Install/Update dependencies
```bash
cd /home/gyasisutton/dev/tools/graphiti-mcp
uv sync
```

### Test Graphiti connection
```bash
cd /home/gyasisutton/dev/tools/graphiti-mcp
uv run python -c "
from dotenv import load_dotenv
load_dotenv()
import os
from openai import AsyncOpenAI
from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.llm_client.azure_openai_client import AzureOpenAILLMClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.azure_openai import AzureOpenAIEmbedderClient
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

azure_endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
azure_api_key = os.environ.get('AZURE_OPENAI_API_KEY')
azure_deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT')
azure_embedding = os.environ.get('AZURE_OPENAI_EMBEDDING_DEPLOYMENT')

driver = FalkorDriver(host='localhost', port=6379, database='graphiti_memory')
azure_client = AsyncOpenAI(base_url=f'{azure_endpoint}/openai/v1/', api_key=azure_api_key)
llm_config = LLMConfig(model=azure_deployment, small_model=azure_deployment)
llm_client = AzureOpenAILLMClient(azure_client=azure_client, config=llm_config)
embedder = AzureOpenAIEmbedderClient(azure_client=azure_client, model=azure_embedding)
cross_encoder = OpenAIRerankerClient(client=llm_client, config=llm_config)
graphiti = Graphiti(graph_driver=driver, llm_client=llm_client, embedder=embedder, cross_encoder=cross_encoder)
print('Connected!')
"
```

### Run MCP Server manually (for testing)

```bash
cd /home/gyasisutton/dev/tools/graphiti-mcp
uv run python main.py --config config/config-litellm-azure.yaml --transport stdio
```

## MCP Configuration

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

## Available Graphiti Tools

| Tool | Purpose |
|------|---------|
| `add_memory` | Store problem/solution events (supports text, JSON, and message formats) |
| `search_nodes` | Find similar problems (semantic search for node summaries) |
| `search_memory_facts` | Find tool patterns (search facts/relationships/edges) |
| `get_episodes` | Retrieve workflow history |
| `get_entity_edge` | Get specific entity edge by UUID |
| `delete_episode` | Remove episodes |
| `delete_entity_edge` | Delete entity relationships |
| `clear_graph` | Reset knowledge graph |
| `get_status` | Get server and database connection status |

## Multi-Tenancy Architecture

Graphiti uses `group_id` as the FalkorDB database/graph name for multi-tenancy:

### How It Works
- Each `group_id` maps to a separate FalkorDB database/graph
- `driver.clone(database=group_id)` switches graph context
- All queries via cloned driver go to that specific graph
- Data is completely isolated between groups

### Driver Cloning Pattern

**Critical**: All MCP tools must clone the driver before database operations:

```python
# Correct pattern (used in all tools):
client = await graphiti_service.get_client()
group_id = graphiti_service.config.graphiti.group_id
driver = client.driver.clone(database=group_id)

# Use cloned driver for all operations
result = await SomeNode.get_by_uuid(driver, uuid)
```

**Why**: When `add_memory(group_id="X")` is called, Graphiti mutates `client.driver._database` to "X". Subsequent operations using `client.driver` would query the wrong database.

### UUID Lookup Scope Limitation

UUID-based operations (delete_entity_edge, delete_episode, get_entity_edge) use the **config's default group_id**:
- UUIDs must exist in the same group as `config.graphiti.group_id`
- Cross-group UUID lookups are not supported
- This maintains backward compatibility without changing tool signatures

### Multi-Group Operations

Tools that accept `group_ids` parameter query multiple databases:
- `get_episodes(group_ids=["A", "B"])` - Queries both graphs, aggregates results
- `clear_graph(group_ids=["A", "B"])` - Clears both graphs independently
- `search_nodes(group_ids=["A", "B"])` - Searches across both graphs

## LLM Architecture: Tiered Fallback Chain

### Why Tiered Fallback?

Graphiti uses LLMs for entity extraction, relationship extraction, and node deduplication. These tasks require **structured JSON output** via `json_schema` response_format. Azure's `model-router` may route requests to **reasoning models** (e.g., `gpt-5-nano`) that consume tokens on internal chain-of-thought before producing output. When the reasoning "tax" exceeds the token budget, the output is empty and `json.loads('')` fails.

### Model Tiers

| Tier | Model | Where Used | Why |
|------|-------|-----------|-----|
| **Tier 1** (Primary) | `azure/model-router` | Entity extraction, dedup, relationship extraction | Smart routing across Azure fleet. May route to reasoning models |
| **Tier 2** (Fallback) | `azure/gpt-4.1-mini` | Same as Tier 1, activated on Tier 1 failure | Non-reasoning model. Zero reasoning tokens = all budget goes to JSON output |
| **Tier 3** (Cross-cloud) | `gemini/gemini-2.0-flash` | Same as Tier 1, activated when both Azure tiers fail | Cross-cloud resilience. Different provider = independent failure domain |
| **Embeddings** | `text-embedding-3-small` | Vector embeddings for semantic search | Azure OpenAI, not affected by reasoning model issues |

### How Detection Works

`RateLimitedLLMWrapper._validate_response()` in `src/services/rate_limiter.py` detects:
- **Empty content + reasoning tokens > 0**: Reasoning model exhausted token budget
- **finish_reason = content_filter**: Azure content filter triggered
- **finish_reason = length**: Output truncated (max_tokens too low)

On detection, the wrapper automatically cascades to the next tier.

### Reasoning Models vs Non-Reasoning Models

**DO NOT use reasoning models as primary for entity extraction.** They waste tokens on chain-of-thought that provides no value for structured extraction tasks.

| Model Type | Examples | Reasoning Tokens | Good For Extraction? |
|-----------|---------|-----------------|---------------------|
| **Non-reasoning** | gpt-4o, gpt-4o-mini, gpt-4.1-mini | 0 | Yes |
| **Reasoning** | gpt-5-nano, gpt-5-mini, o3, o4-mini, DeepSeek-R1 | 500-8000+ | No — wastes token budget |

### max_tokens Setting

Set in `config/config-litellm-azure.yaml`. Currently **16384** to accommodate:
- Reasoning model overhead (~500-2000 tokens for thinking)
- Complex entity extraction (large episode bodies with many entities)
- JSON schema structural overhead

For non-reasoning models (Tier 2/3), this is generous but harmless — they only use what they need.

### Configuring Models

All model configuration is in `.env`:

```bash
# Tier 1: Primary (set in config YAML as llm.model)
AZURE_OPENAI_DEPLOYMENT=model-router

# Tier 2: Azure non-reasoning fallback
GRAPHITI_TIER2_MODEL=azure/gpt-4.1-mini

# Tier 3: Gemini cross-cloud fallback
GEMINI_API_KEY=your-key
GRAPHITI_TIER3_MODEL=gemini/gemini-2.0-flash
```

To change Tier 2 to a different model (e.g., `gpt-4o-mini`), update `GRAPHITI_TIER2_MODEL=azure/gpt-4o-mini` in `.env`.

### LiteLLM Provider

LiteLLM wraps all LLM calls. Model prefix determines the provider:
- `azure/` → Azure OpenAI (uses `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION`)
- `gemini/` → Google Gemini (uses `GEMINI_API_KEY`)
- Config file: `config/config-litellm-azure.yaml`
- API version: `2024-12-01-preview` (required for `json_schema` support)

## Critical Best Practices

### Database Query Pattern: ALWAYS Use driver.execute_query()

**CRITICAL**: When querying graph databases in Graphiti MCP, ALWAYS use `driver.execute_query()` instead of `session.run()`.

**Why**: Different graph databases have different query API behaviors:
- **Neo4j**: `session.run()` returns results directly
- **FalkorDB/Kuzu/Neptune**: `session.run()` returns `None` (results must be fetched via driver)

**Pattern**:
```python
# CORRECT: Database-agnostic, works across all backends
result = driver.execute_query("""
    MATCH (e:Entity {uuid: $uuid})
    RETURN e.name, e.summary, e.created_at
""", parameters={"uuid": episode_uuid})

# WRONG: Database-specific, breaks FalkorDB/Kuzu/Neptune
result = session.run("""
    MATCH (e:Entity {uuid: $uuid})
    RETURN e.name, e.summary, e.created_at
""", uuid=episode_uuid)
```

**Datetime Handling**:
```python
# FalkorDB returns ISO strings, Neo4j returns datetime objects
created_at = record["e.created_at"]
if isinstance(created_at, str):
    # FalkorDB, Kuzu, Neptune
    created_at_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
else:
    # Neo4j
    created_at_dt = created_at
```

**Impact**: Ensures cross-database compatibility across all 4 supported backends (FalkorDB, Neo4j, Kuzu, Neptune)

**Reference Implementation**: See `graph_verifier` tool in `src/graphiti_mcp_server.py` lines 1227-1260

## Monkey-Patches Applied at Startup

The server applies targeted patches to graphiti-core at startup (`src/graphiti_mcp_server.py` lines 132-260):

| Patch | What It Does | Why |
|-------|-------------|-----|
| `MAX_RETRIES = 2` | Allows 2 retries for transient JSON parse errors | Was set to 0, which made every transient LLM failure permanent |
| `_safe_get_entity_type_description` | Handles None entity types in dedup | Prevents `NoneType.__format__` crashes during entity deduplication |
| Empty response guard | Raises ValueError on empty LLM content | Detects reasoning model token exhaustion before json.loads('') crashes |

## Notes

- FalkorDB persists data to Docker volume `falkordb_data`
- Group ID `developer_gyasisutton` for cross-device sync
- Default config: `config/config-litellm-azure.yaml`
- LLM calls go through 3-tier fallback: model-router → gpt-4.1-mini → gemini-2.0-flash
- `sanitize_for_redisearch` is skipped for `source='json'` episodes to preserve JSON structure
- See `.env.example` for all configurable environment variables

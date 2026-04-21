# Changes from Original Graphiti MCP Server

This documents modifications made to the official [getzep/graphiti](https://github.com/getzep/graphiti) MCP server to support Azure OpenAI with FalkorDB.

## Source

Original code cloned from: `https://github.com/getzep/graphiti.git` (mcp_server directory)

---

## Modification 1: Cross Encoder Fix for Azure OpenAI

**File**: `src/graphiti_mcp_server.py`
**Location**: Lines 214-223 (inside `_initialize_graphiti` method)

### Problem

The original code does not pass a `cross_encoder` parameter to the `Graphiti()` constructor. This causes graphiti-core to create a default `OpenAIRerankerClient()` which requires `OPENAI_API_KEY` environment variable - failing when using Azure OpenAI.

### Solution

Added explicit cross_encoder initialization using the Azure OpenAI LLM client:

```python
# Create cross encoder using the LLM client
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.llm_client.config import LLMConfig as CoreLLMConfig

# Create LLMConfig for the cross encoder
reranker_config = CoreLLMConfig(
    model=self.config.llm.model,
    small_model=self.config.llm.model,
)
cross_encoder = OpenAIRerankerClient(client=llm_client, config=reranker_config)
```

Then pass `cross_encoder=cross_encoder` to both FalkorDB and Neo4j Graphiti constructors.

---

## Modification 2: Removed Parent Directory Dependency

**File**: `pyproject.toml`
**Change**: Removed lines that referenced parent directory

### Original (removed)

```toml
[tool.uv.sources]
graphiti-core = { path = "../", editable = true }
```

### Reason

The original MCP server was inside the graphiti monorepo and referenced the parent for graphiti-core. Our standalone setup installs graphiti-core from PyPI.

---

## Files Added for Our Setup

| File | Purpose |
|------|---------|
| `.env` | Azure OpenAI credentials and config |
| `config/config-azure-falkordb.yaml` | Custom config for Azure + FalkorDB |
| `docker-compose.yml` | FalkorDB with persistent storage |
| `CLAUDE.md` | Quick reference for Claude Code |
| `CHANGES.md` | This file |
| `SETUP_RECIPE.md` | Step-by-step installation guide |

---

## Modification 3: LiteLLM Provider for Azure OpenAI (COMPLETED)

**Files modified**: `pyproject.toml`, `src/config/schema.py`, `src/services/factories.py`
**New file**: `config/config-litellm-azure.yaml`

### Problem

The `AzureOpenAILLMClient` from graphiti-core uses the `responses.parse()` API for structured outputs, which is not supported by Azure OpenAI. This results in 404 errors when trying to use the Azure OpenAI LLM client.

### Solution (Implemented)

Added a new `litellm` provider that uses `OpenAIGenericClient` with `json_schema` response_format. This approach:

1. Uses the standard `chat.completions.create()` API (not `responses.parse()`)
2. Leverages Azure OpenAI's structured output support via `response_format={"type": "json_schema", ...}`
3. Requires API version `2024-12-01-preview` for json_schema support

**Implementation details**:

The LiteLLM provider creates an `OpenAIGenericClient` that:
- Wraps LiteLLM's `acompletion()` function in an AsyncOpenAI-compatible interface
- Passes structured output schemas via `response_format` parameter
- Works with Azure OpenAI's json_schema support

**Changes in `pyproject.toml`:**
```toml
dependencies = [
    ...
    "litellm>=1.40.0",
]
```

**Changes in `src/config/schema.py`:**
```python
class LiteLLMProviderConfig(BaseModel):
    """LiteLLM provider configuration for provider-agnostic LLM access."""
    api_key: str | None = None
    base_url: str | None = None  # For Azure: the endpoint URL
    api_version: str | None = None  # For Azure: API version
```

**Changes in `src/services/factories.py`:**
```python
case 'litellm':
    # LiteLLM provider - uses OpenAIGenericClient with json_schema response_format
    # This avoids the responses.parse() API that Azure doesn't support
    ...
```

### Usage

For Azure OpenAI, use the model format: `azure/<deployment_name>`

Example config (`config/config-litellm-azure.yaml`):
```yaml
llm:
  provider: "litellm"
  model: "azure/model-router"  # Azure deployment name prefixed with "azure/"

  providers:
    litellm:
      api_key: ${AZURE_OPENAI_API_KEY}
      base_url: ${AZURE_OPENAI_ENDPOINT}
      api_version: ${AZURE_OPENAI_API_VERSION:2024-12-01-preview}
```

**Important**: API version `2024-12-01-preview` is required for json_schema response_format support.

---

## Configuration Summary

- **LLM Provider**: LiteLLM with Azure OpenAI (`azure/model-router`) - **REQUIRED for Azure**
- **Embedder**: Azure OpenAI (`text-embedding-3-small`)
- **Database**: FalkorDB (Redis-compatible graph DB)
- **Transport**: stdio (for Claude Code / Cursor MCP integration)
- **API Version**: `2024-12-01-preview`

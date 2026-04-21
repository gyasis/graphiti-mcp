# Technical Context: Technologies & Setup

## Development Environment

### Operating System
- **Host**: Windows 11 with WSL2
- **WSL Distribution**: Ubuntu (latest)
- **Shell**: Bash
- **Working Directory**: `/home/gyasisutton/dev/tools/graphiti-mcp`

### Docker Configuration
- **Docker Desktop**: Required for WSL2 integration
- **Integration**: Ubuntu distribution enabled in Docker Desktop settings
- **Network**: Bridge network for container communication
- **Volumes**: `falkordb_data` for persistence

### Python Environment
- **Version**: Python 3.13
- **Package Manager**: uv (modern, fast package manager)
- **Virtual Environment**: `.venv/` (auto-managed by uv)
- **Lock File**: `uv.lock` (ensures reproducible builds)

## Core Technologies

### 1. Model Context Protocol (MCP)
**Version**: Compatible with MCP specification
**Transports**:
- `stdio`: Default for Claude Code and Cursor IDE
- `http`: Available at `http://localhost:8000/mcp/`

**Why MCP**:
- Standardized protocol for AI assistant tool integration
- Broad client support (Claude, Cursor, VS Code)
- Enables persistent memory across sessions

### 2. Graphiti Core Library
**Purpose**: Temporally-aware knowledge graph framework
**Key Features**:
- Entity extraction from unstructured text
- Relationship discovery via LLM
- Semantic search with embeddings
- Community detection for global search

**Installation**:
```bash
uv sync  # Installs graphiti-core and dependencies
```

### 3. LiteLLM Provider
**Purpose**: Unified LLM interface with structured output support
**Why Needed**: Azure OpenAI doesn't support `responses.parse()` API
**How it Works**: Wraps Azure OpenAI with `json_schema` response_format

**Configuration Files**:
1. `config/config-litellm-azure.yaml` - Standard (gpt-4-turbo)
2. `config/config-litellm-azure-balanced.yaml` - Balanced (gpt-4-mini) **DEFAULT**
3. `config/config-litellm-azure-fast.yaml` - Fast (gpt-3.5-turbo)

**Key Settings**:
```yaml
llm:
  provider: "litellm"
  providers:
    litellm:
      api_base: "${AZURE_OPENAI_ENDPOINT}/openai"
      api_key: "${AZURE_OPENAI_API_KEY}"
      api_version: "2024-12-01-preview"  # Required for json_schema
      model: "azure/model-router-mini"   # Deployment name
```

### 4. Azure OpenAI Service
**Deployment Names**:
- `model-router`: gpt-4-turbo (entity extraction, relationship discovery)
- `model-router-mini`: gpt-4-mini (balanced performance, 50-60% faster)
- `model-router-fast`: gpt-3.5-turbo (fastest, prototyping only)
- `text-embedding-3-small`: Embeddings for semantic search

**Endpoint**: Configured via `AZURE_OPENAI_ENDPOINT` environment variable
**API Key**: Configured via `AZURE_OPENAI_API_KEY` environment variable
**API Version**: `2024-12-01-preview` (required for structured output)

### 5. FalkorDB
**Type**: Redis-based graph database
**Why Chosen**:
- Lightweight and fast
- Easy Docker deployment
- Cypher-compatible query language
- Lower resource requirements than Neo4j

**Docker Setup**:
```bash
docker compose up -d  # Starts FalkorDB + MCP server
```

**Connection**:
- **Host**: localhost (from WSL2)
- **Port**: 6379
- **Database**: `graphiti_memory`
- **Web UI**: http://localhost:3000

**Persistence**: Docker volume `falkordb_data`

## Environment Configuration

### Required Environment Variables
```bash
# Azure OpenAI (Required)
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<your-api-key>
AZURE_OPENAI_DEPLOYMENT=model-router-mini
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small

# Graph Database (FalkorDB)
FALKORDB_URI=redis://localhost:6379
FALKORDB_DATABASE=graphiti_memory

# MCP Configuration
GROUP_ID=developer_gyasisutton  # Cross-device sync namespace
SEMAPHORE_LIMIT=10              # Concurrency control
```

### Configuration File Hierarchy
1. **`.env`**: Local secrets and environment-specific values (gitignored)
2. **`config/*.yaml`**: Structured configuration with environment variable expansion
3. **Command-line args**: Override any config value

### Environment Variable Expansion in YAML
```yaml
# Syntax: ${VAR_NAME:default_value}
api_key: "${AZURE_OPENAI_API_KEY}"           # Required, no default
database: "${FALKORDB_DATABASE:graphiti}"    # Optional, defaults to "graphiti"
```

## Package Management with uv

### Why uv?
- **Speed**: 10-100x faster than pip
- **Reliability**: Better dependency resolution than pip
- **Lockfiles**: `uv.lock` ensures reproducible builds
- **Modern**: Uses pyproject.toml instead of requirements.txt

### Common Commands
```bash
# Install/update dependencies
uv sync

# Install with optional extras (additional LLM providers)
uv sync --extra providers

# Run script in virtual environment
uv run python main.py

# Run MCP server
uv run python main.py --config config/config-litellm-azure-balanced.yaml --transport stdio

# Add dependency
uv add <package>

# Remove dependency
uv remove <package>

# Update all dependencies
uv sync --upgrade
```

### Dependency Groups
- **Core**: MCP server, Graphiti core, LiteLLM
- **Providers** (optional): Anthropic, Google Gemini, Groq, Voyage AI, Sentence Transformers

## MCP Client Integration

### Claude Code (`~/.claude.json`)
```json
{
  "mcpServers": {
    "graphiti": {
      "command": "uv",
      "args": [
        "--directory",
        "/home/gyasisutton/dev/tools/graphiti-mcp",
        "run",
        "python",
        "main.py",
        "--config",
        "config/config-litellm-azure-balanced.yaml",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "graphiti": {
      "command": "uv",
      "args": [
        "--directory",
        "/home/gyasisutton/dev/tools/graphiti-mcp",
        "run",
        "python",
        "main.py",
        "--config",
        "config/config-litellm-azure-balanced.yaml",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

## Database Backend Options

### FalkorDB (Default, Current)
- **Type**: Redis-based graph database
- **Performance**: Fast for read-heavy workloads
- **Scalability**: Limited by Redis memory
- **Use Case**: Development, testing, small-medium knowledge graphs

### Neo4j (Alternative)
- **Type**: Full-featured graph database
- **Performance**: Optimized for complex queries
- **Scalability**: Enterprise-grade, billions of nodes
- **Use Case**: Production, large knowledge graphs

### Kuzu (Alternative)
- **Type**: Embedded graph database
- **Performance**: Fast for embedded use cases
- **Scalability**: Single-process, file-based
- **Use Case**: Embedded applications, offline use

### Neptune (Alternative)
- **Type**: AWS managed graph database
- **Performance**: Fully managed, auto-scaling
- **Scalability**: Cloud-native, highly scalable
- **Use Case**: AWS deployments, cloud-native apps

## Graph Database Query Patterns

### Critical: Use driver.execute_query()
```python
# CORRECT: Works across all databases
result = driver.execute_query("""
    MATCH (e:Entity {uuid: $uuid})
    RETURN e.name, e.summary, e.created_at
""", parameters={"uuid": episode_uuid})

# WRONG: Database-specific (breaks FalkorDB, Kuzu, Neptune)
result = session.run("""
    MATCH (e:Entity {uuid: $uuid})
    RETURN e.name, e.summary, e.created_at
""", uuid=episode_uuid)
```

### Datetime Handling Pattern
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

## Performance Characteristics

### LLM Response Times (Entity Extraction)
- **gpt-4-turbo**: ~3-5 seconds per episode (baseline)
- **gpt-4-mini**: ~1.5-2 seconds per episode (50-60% faster)
- **gpt-3.5-turbo**: ~0.5-1 second per episode (80% faster, lower quality)

### Graph Query Performance
- **FalkorDB**: <50ms for simple queries, <200ms for complex
- **Neo4j**: <100ms for simple queries, <500ms for complex

### Embedding Generation
- **text-embedding-3-small**: ~100ms per entity
- **Batch processing**: Up to 100 entities per batch

## Development Workflow

### Local Development Setup
1. **Clone repository**: `gh repo clone getzep/graphiti`
2. **Navigate to MCP server**: `cd graphiti/mcp_server`
3. **Install dependencies**: `uv sync`
4. **Configure environment**: `cp .env.example .env` and edit
5. **Start FalkorDB**: `docker compose up -d`
6. **Test connection**: `uv run python -c "import test script"`
7. **Configure MCP client**: Add to `~/.claude.json` or `~/.cursor/mcp.json`

### Testing Workflow
1. **Start MCP server**: `uv run python main.py --transport stdio`
2. **Test in Claude Code**: Invoke MCP tools
3. **Check logs**: Monitor console output for errors
4. **Verify graph**: Use `graph_verifier` tool or FalkorDB web UI
5. **Clear graph**: Use `clear_graph` tool if needed

### Debugging Workflow
1. **Enable verbose logging**: Set `LOG_LEVEL=DEBUG` in `.env`
2. **Check database connection**: Use `get_status` tool
3. **Inspect graph data**: Use FalkorDB web UI at http://localhost:3000
4. **Test queries manually**: Use `docker exec falkordb-graphiti redis-cli`
5. **Check LLM API**: Verify Azure OpenAI endpoint and API key

## Security Considerations

### API Key Protection
- **Storage**: `.env` file (gitignored, never commit)
- **Access**: Environment variables only
- **Logging**: NEVER log API keys
- **Transmission**: HTTPS only

### Graph Data Privacy
- **Local-only**: No external transmission by default
- **Namespace Isolation**: `group_id` separates data
- **No Authentication**: Assumes trusted local environment
- **Production**: Deploy behind authenticated reverse proxy

## Common Issues & Solutions

### Issue: Docker Desktop not integrated with WSL2
**Symptom**: `docker compose` fails with connection error
**Solution**: Docker Desktop → Settings → Resources → WSL Integration → Enable for Ubuntu

### Issue: FalkorDB container not starting
**Symptom**: `docker compose up` fails
**Solution**: Check Docker Desktop is running, check port 6379 not in use

### Issue: 429 Rate Limit Errors
**Symptom**: LLM API returns 429 errors
**Solution**: Reduce `SEMAPHORE_LIMIT` in `.env` (default: 10, try 5)

### Issue: graph_verifier returns empty results
**Symptom**: Tool returns no recent episodes despite data in graph
**Solution**: Ensure using `driver.execute_query()` not `session.run()`

### Issue: MCP server fails to start
**Symptom**: Claude Code shows "MCP server failed"
**Solution**: Check `~/.claude.json` paths are absolute, verify `.env` exists

### Issue: Environment variables not loading
**Symptom**: MCP server uses wrong values despite `.env` file
**Solution**: Ensure `.env` values override shell environment (dotenv_values precedence)

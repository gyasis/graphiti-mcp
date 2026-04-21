# System Patterns: Architecture & Technical Decisions

## Overall Architecture

### High-Level Design
```
MCP Client (Claude Code/Cursor)
    |
    | stdio transport
    |
    v
Graphiti MCP Server (Python)
    |
    +-- MCP Protocol Layer (tools, resources, prompts)
    |
    +-- Graphiti Core Library
    |   |
    |   +-- LLM Client (LiteLLM Provider)
    |   |   +-- Azure OpenAI (via LiteLLM)
    |   |   +-- Entity Extraction
    |   |   +-- Relationship Discovery
    |   |
    |   +-- Embedder Client
    |   |   +-- Azure OpenAI Embeddings
    |   |   +-- Semantic Search
    |   |
    |   +-- Graph Driver (Abstraction Layer)
    |       +-- FalkorDB (Redis-based)
    |       +-- Neo4j
    |       +-- Kuzu
    |       +-- Neptune
    |
    v
Graph Database (FalkorDB/Neo4j/Kuzu/Neptune)
```

### Key Components

#### 1. MCP Protocol Layer
- **File**: `src/graphiti_mcp_server.py`
- **Responsibilities**:
  - Expose Graphiti capabilities as MCP tools
  - Handle stdio and HTTP transports
  - Manage tool invocation and responses
  - Provide health checks and status

#### 2. LiteLLM Provider Integration
- **Purpose**: Bridge Azure OpenAI structured output gap
- **Problem Solved**: Azure OpenAI doesn't support `responses.parse()` API
- **Solution**: LiteLLM's `OpenAIGenericClient` with `json_schema` response_format
- **Configuration**: `config/config-litellm-azure*.yaml`
- **Model Format**: `azure/<deployment_name>` (e.g., `azure/model-router`)

#### 3. Graph Driver Abstraction
- **Critical Pattern**: Use `driver.execute_query()` for ALL database queries
- **Anti-Pattern**: `session.run()` - database-specific behavior
- **Supported Backends**:
  - FalkorDB (Redis-based, lightweight)
  - Neo4j (enterprise-grade, full-featured)
  - Kuzu (embedded graph database)
  - Neptune (AWS managed graph database)

#### 4. Entity Extraction Pipeline
- **Input**: Episode (text, JSON, or message)
- **Process**:
  1. LLM extracts entities (Preferences, Requirements, Procedures, etc.)
  2. Embeddings generated for semantic search
  3. Deduplication via entity similarity
  4. Relationship discovery between entities
  5. Community detection for global search
- **Output**: Graph nodes (entities) and edges (relationships)

## Design Patterns

### 1. Database Abstraction Pattern
**Problem**: Different graph databases have different query APIs
**Solution**: Driver abstraction layer with unified `execute_query()` interface

**Example**:
```python
# CORRECT: Database-agnostic
result = driver.execute_query(query)

# WRONG: Database-specific (breaks FalkorDB, Kuzu, Neptune)
result = session.run(query)
```

**Datetime Handling**:
```python
# FalkorDB returns ISO strings, Neo4j returns datetime objects
if isinstance(created_at, str):
    created_at_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
else:
    created_at_dt = created_at
```

### 2. LiteLLM Provider Pattern
**Problem**: Azure OpenAI doesn't support `responses.parse()` API
**Solution**: LiteLLM provider wraps Azure OpenAI with structured output support

**Configuration**:
```yaml
llm:
  provider: "litellm"
  providers:
    litellm:
      api_base: "${AZURE_OPENAI_ENDPOINT}/openai"
      api_key: "${AZURE_OPENAI_API_KEY}"
      api_version: "2024-12-01-preview"
      model: "azure/model-router"
```

**Performance Tiers**:
- `azure/model-router`: gpt-4-turbo (highest quality, slowest)
- `azure/model-router-mini`: gpt-4-mini (balanced, 50-60% faster)
- `azure/model-router-fast`: gpt-3.5-turbo (fastest, lower quality)

### 3. MCP Synchronous Processing Pattern
**Problem**: MCP stdio transport doesn't support async background processing
**Solution**: Changed `add_memory` from async queue to synchronous processing

**Rationale**:
- MCP stdio requires immediate tool responses
- Background queue processing broke stdio contract
- Synchronous processing ensures tool completion before response

**Impact**:
- Slower ingestion for large batches
- Guaranteed completion before tool returns
- Compatible with stdio and HTTP transports

### 4. Group ID Namespacing Pattern
**Problem**: Need cross-device memory synchronization
**Solution**: `group_id` parameter namespaces graph data

**Example**:
```python
# Same group_id across devices = shared memory
group_id = "developer_gyasisutton"
graphiti.add_episode(name="...", episode_body="...", group_id=group_id)
```

**Use Cases**:
- Cross-device synchronization (laptop ↔ desktop)
- Multi-project isolation (separate graphs per project)
- Multi-user support (separate graphs per user)

## Technical Constraints

### 1. LLM Rate Limits
**Challenge**: Entity extraction requires multiple LLM calls per episode
**Solution**: `SEMAPHORE_LIMIT` environment variable controls concurrency

**Guidelines**:
- OpenAI Tier 1: `SEMAPHORE_LIMIT=1-2`
- OpenAI Tier 3: `SEMAPHORE_LIMIT=10-15` (default)
- Azure OpenAI: Consult quota, start conservative

**Symptoms of Too High**:
- 429 rate limit errors
- Increased API costs

**Symptoms of Too Low**:
- Slow episode throughput
- Underutilized API quota

### 2. Docker Desktop WSL2 Integration
**Requirement**: Docker Desktop must have WSL2 integration enabled
**Why**: FalkorDB runs in Docker, MCP server runs in WSL2

**Setup**:
- Docker Desktop → Settings → Resources → WSL Integration
- Enable integration for Ubuntu distribution

### 3. Python Package Management
**Tool**: uv (modern package manager)
**Why**: Faster than pip, better dependency resolution, lockfile support

**Commands**:
```bash
uv sync                    # Install dependencies
uv sync --extra providers  # Install additional LLM providers
uv run <script>            # Run script in venv
```

### 4. Environment Variable Precedence
**Order** (highest to lowest):
1. Command-line arguments
2. Environment variables
3. `.env` file
4. `config.yaml` defaults

**Critical Pattern**: `.env` values MUST override shell environment
**Implementation**: `dotenv_values()` merged with `os.environ` with `.env` taking precedence

## Data Flow Patterns

### Episode Ingestion Flow
```
add_memory(name, episode_body, source, group_id)
    |
    v
Validate episode data
    |
    v
LLM extracts entities (Preferences, Requirements, etc.)
    |
    v
Generate embeddings for entities
    |
    v
Deduplicate entities via similarity
    |
    v
LLM discovers relationships between entities
    |
    v
Store nodes (entities) and edges (relationships) in graph
    |
    v
Update graph indices (embedding search, community detection)
    |
    v
Return episode UUID and entity counts
```

### Search Flow (Hybrid Mode)
```
search_nodes(query, max_nodes=10)
    |
    v
Generate query embedding
    |
    v
Vector similarity search (semantic)
    |
    v
Keyword search (BM25)
    |
    v
Graph traversal (community-based)
    |
    v
Merge and rank results
    |
    v
Return top N entities with summaries
```

## Security Patterns

### 1. API Key Management
- **Storage**: `.env` file (gitignored)
- **Access**: Environment variables only
- **Logging**: API keys NEVER logged
- **Transmission**: HTTPS only for remote LLM APIs

### 2. Graph Access Control
- **Namespace Isolation**: `group_id` parameter
- **No Authentication**: Local-only by default
- **Production**: Deploy behind authenticated reverse proxy

## Performance Patterns

### 1. Embedding Caching
- Graphiti core library caches embeddings
- Prevents redundant embedding generation
- Reduces LLM API costs

### 2. Batch Processing
- Episodes processed in batches when possible
- Controlled by `SEMAPHORE_LIMIT`
- Reduces total processing time

### 3. Incremental Graph Updates
- No full graph recomputation
- Only affected communities updated
- Scales to large knowledge graphs

## Error Handling Patterns

### 1. Database Connection Failures
- Fail-fast on startup if database unreachable
- Health check endpoint `/health` for monitoring
- Clear error messages with connection details

### 2. LLM API Failures
- Retry with exponential backoff
- 429 rate limits: Reduce `SEMAPHORE_LIMIT`
- 500 errors: Log and skip episode

### 3. MCP Protocol Errors
- Validate tool parameters before processing
- Return structured error responses
- Log errors without exposing sensitive data

## Testing Patterns

### 1. Local Development
- Use FalkorDB Docker container
- `config-litellm-azure-balanced.yaml` for speed
- Single group_id for testing

### 2. Cross-Database Testing
- Test with FalkorDB (primary)
- Validate with Neo4j (compatibility)
- Use `graph_verifier` tool for verification

### 3. Performance Testing
- Benchmark with multiple LiteLLM configs
- Measure entity extraction time per episode
- Track token usage and costs

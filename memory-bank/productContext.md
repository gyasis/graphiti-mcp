# Product Context: Why This Exists

## The Problem Space

### AI Agent Memory Limitations
AI assistants like Claude Code suffer from session-based memory loss. Each conversation starts fresh, requiring users to re-explain project context, preferences, and prior solutions. This creates:
- Wasted time re-establishing context
- Lost institutional knowledge about project patterns
- Inability to learn from past problem-solving workflows
- No cross-device memory continuity

### Traditional Solutions Fall Short
- **Conversation History**: Limited context window, no semantic search, no entity relationships
- **Vector RAG**: Good for document retrieval, poor for temporal relationships and evolving knowledge
- **Database Storage**: Requires manual structuring, no automatic entity extraction

## The Graphiti Solution

### Knowledge Graph Approach
Graphiti treats AI interactions as a temporally-aware knowledge graph where:
- Episodes are time-stamped events (conversations, workflows, decisions)
- Entities are extracted automatically (Problems, Solutions, Workflows, ToolPatterns)
- Relationships capture how entities connect and evolve
- Semantic search retrieves relevant context from any point in history

### Why MCP?
Model Context Protocol (MCP) provides a standardized way for AI assistants to access external tools and data sources. The Graphiti MCP server exposes knowledge graph capabilities as MCP tools, allowing Claude Code and Cursor to:
- Store successful workflows and solutions
- Retrieve similar past problems and their solutions
- Track project patterns and user preferences
- Maintain continuity across sessions and devices

## User Needs Being Solved

### For Developer (gyasisutton)
1. **Cross-Device Continuity**: Work on laptop, resume on desktop with full context
2. **Workflow Memory**: Remember successful tool patterns and debugging approaches
3. **Project Intelligence**: Build institutional knowledge about codebase quirks
4. **Problem-Solution Mapping**: Never solve the same problem twice

### For AI Assistant (Claude Code)
1. **Session Continuity**: Resume projects without re-learning context
2. **Pattern Recognition**: Identify similar problems based on past solutions
3. **Preference Awareness**: Remember user coding preferences and project conventions
4. **Effective Tool Use**: Learn which tool sequences work best for specific tasks

## Why Azure OpenAI + FalkorDB?

### Azure OpenAI Choice
- Enterprise-grade reliability and security
- Existing organizational investment
- Structured output support via LiteLLM provider
- Cost management through deployment controls

### FalkorDB Choice
- Redis-based: Fast, lightweight, easy Docker deployment
- Native graph query language (Cypher-compatible)
- Lower resource requirements than Neo4j
- Simpler setup for development/testing

## Competitive Positioning

### vs. Traditional RAG
- **Graphiti**: Temporal relationships, entity extraction, evolving knowledge
- **RAG**: Static documents, vector similarity, no relationships

### vs. LangGraph/LangChain Memory
- **Graphiti**: Persistent cross-session knowledge graph
- **LangChain**: In-memory conversation buffers, limited persistence

### vs. Manual Note-Taking
- **Graphiti**: Automatic entity extraction, semantic search, relationship discovery
- **Manual**: Requires conscious effort, no semantic retrieval, hard to query

## Success Metrics
- Reduction in context re-establishment time
- Increase in successful tool pattern reuse
- Growth of knowledge graph (entities, relationships)
- Cross-device session continuity rate
- User satisfaction with memory recall accuracy

# Project Brief: Graphiti MCP Server

## Project Identity
**Name**: Graphiti MCP Server
**Type**: AI Agent Memory Infrastructure
**Repository**: Fork of getzep/graphiti (MCP server implementation)
**Primary User**: gyasisutton
**Environment**: WSL2 Ubuntu on Windows, Docker Desktop integration

## Core Mission
Provide a Model Context Protocol (MCP) server that exposes Graphiti's temporally-aware knowledge graph capabilities to AI assistants, enabling persistent memory and context across sessions using Azure OpenAI with FalkorDB backend.

## What This Project Is
- MCP server implementation wrapping Graphiti core library
- Knowledge graph-based memory system for AI agents
- Multi-database support (FalkorDB, Neo4j, Kuzu, Neptune)
- LiteLLM provider integration for Azure OpenAI compatibility
- Cross-device memory synchronization via group_id namespacing

## What This Project Is NOT
- A standalone knowledge graph database
- A traditional RAG (Retrieval-Augmented Generation) system
- A production-ready enterprise solution (experimental MCP implementation)
- A complete replacement for conversation history

## Key Capabilities
1. **Episode Management**: Store conversations, workflows, and structured data
2. **Entity Extraction**: Automatic extraction of entities and relationships via LLM
3. **Semantic Search**: Hybrid search across nodes (entities) and facts (relationships)
4. **Cross-Database Support**: Abstracted driver layer for multiple graph databases
5. **MCP Protocol**: Exposes tools via stdio and HTTP transports
6. **Azure OpenAI Integration**: LiteLLM provider for structured output compatibility

## Primary Technology Stack
- **Language**: Python 3.13
- **Package Manager**: uv (modern Python package management)
- **Graph Database**: FalkorDB (Redis-based, default)
- **LLM Provider**: Azure OpenAI via LiteLLM
- **Protocol**: Model Context Protocol (MCP)
- **Containerization**: Docker + Docker Compose
- **Transport**: stdio (for Claude Code/Cursor)

## Project Goals
1. Enable Claude Code and Cursor IDE to maintain persistent memory
2. Provide reliable cross-database compatibility
3. Minimize LLM API costs through efficient batching and caching
4. Support multi-device synchronization via group_id namespacing
5. Maintain compatibility with MCP stdio transport requirements

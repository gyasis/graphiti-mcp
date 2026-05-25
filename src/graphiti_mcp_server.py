#!/usr/bin/env python3
"""
Graphiti MCP Server - Exposes Graphiti functionality through the Model Context Protocol (MCP)
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server import Context
from graphiti_core import Graphiti
from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EpisodeType, EpisodicNode, get_episodic_node_from_record
from graphiti_core.search.search_filters import SearchFilters
from graphiti_core.utils.maintenance.graph_data_operations import clear_data
from pydantic import BaseModel
from starlette.responses import JSONResponse

from config.schema import GraphitiConfig, ServerConfig
from models.response_types import (
    EpisodeSearchResponse,
    ErrorResponse,
    FactSearchResponse,
    NodeResult,
    NodeSearchResponse,
    StatusResponse,
    SuccessResponse,
)
from services.factories import DatabaseDriverFactory, EmbedderFactory, LLMClientFactory
from services.queue_service import QueueService
from services.rate_limiter import get_global_rate_limiter, get_task_manager
from utils.formatting import format_fact_result

# Load .env file from mcp_server directory
mcp_server_dir = Path(__file__).parent.parent
env_file = mcp_server_dir / '.env'
if env_file.exists():
    # override=True ensures .env values take precedence over shell environment
    load_dotenv(env_file, override=True)
else:
    # Try current working directory as fallback
    load_dotenv(override=True)


# Semaphore limit for concurrent Graphiti operations.
#
# This controls how many episodes can be processed simultaneously. Each episode
# processing involves multiple LLM calls (entity extraction, deduplication, etc.),
# so the actual number of concurrent LLM requests will be higher.
#
# TUNING GUIDELINES:
#
# LLM Provider Rate Limits (requests per minute):
# - OpenAI Tier 1 (free):     3 RPM   -> SEMAPHORE_LIMIT=1-2
# - OpenAI Tier 2:            60 RPM   -> SEMAPHORE_LIMIT=5-8
# - OpenAI Tier 3:           500 RPM   -> SEMAPHORE_LIMIT=10-15
# - OpenAI Tier 4:         5,000 RPM   -> SEMAPHORE_LIMIT=20-50
# - Anthropic (default):     50 RPM   -> SEMAPHORE_LIMIT=5-8
# - Anthropic (high tier): 1,000 RPM   -> SEMAPHORE_LIMIT=15-30
# - Azure OpenAI (varies):  Consult your quota -> adjust accordingly
#
# SYMPTOMS:
# - Too high: 429 rate limit errors, increased costs from parallel processing
# - Too low: Slow throughput, underutilized API quota
#
# MONITORING:
# - Watch logs for rate limit errors (429)
# - Monitor episode processing times
# - Check LLM provider dashboard for actual request rates
#
# DEFAULT: 10 (suitable for OpenAI Tier 3, mid-tier Anthropic)
SEMAPHORE_LIMIT = int(os.getenv('SEMAPHORE_LIMIT', 10))

# Timeout (in seconds) for a single add_episode call inside add_memory.
#
# graphiti-core's add_episode performs entity resolution by comparing extracted
# entities against ALL existing nodes via vector similarity search + LLM
# deduplication.  On large graphs (3,000+ nodes) the cascading LLM calls can
# exceed the MCP transport timeout (600s for stdio).  This inner timeout
# provides an earlier, graceful abort so the MCP connection stays alive and the
# caller gets a meaningful error instead of a transport-level kill.
#
# Set via environment variable ADD_EPISODE_TIMEOUT_SECONDS.
# Default: 120 seconds (well under the 600s MCP ceiling).
ADD_EPISODE_TIMEOUT_SECONDS = int(os.getenv('ADD_EPISODE_TIMEOUT_SECONDS', 120))

# --------------------------------------------------------------------------- #
# REDISEARCH FIX: Remove group_id from FalkorDB fulltext indexes               #
#                                                                              #
# FalkorDB's RediSearch tokenizer interprets hyphens as negation operators.    #
# group_id values like "developer_gyasisutton" cause "Syntax error at offset   #
# 21 near developer" during fulltext indexing on node WRITE operations.        #
#                                                                              #
# group_id is already in FalkorDB range indexes for exact-match filtering.     #
# Including it in fulltext indexes is REDUNDANT and causes this bug.           #
#                                                                              #
# Fix: Monkey-patch get_fulltext_indices() to strip group_id from all 4       #
# fulltext index definitions before graphiti-core creates them at startup.     #
# --------------------------------------------------------------------------- #
try:
    import re
    import graphiti_core.graph_queries as _graph_queries

    _original_get_fulltext_indices = _graph_queries.get_fulltext_indices

    def _patched_get_fulltext_indices(provider):
        """Remove group_id from all fulltext index definitions."""
        indices = _original_get_fulltext_indices(provider)
        patched = []
        for idx_sql in indices:
            # Remove group_id references in various SQL formats:
            #   , 'group_id'                    (FalkorDB createNodeIndex)
            #   , e.group_id                    (Cypher CREATE FULLTEXT INDEX)
            #   , n.group_id                    (Cypher CREATE FULLTEXT INDEX)
            cleaned = re.sub(r",\s*'group_id'", '', idx_sql)
            cleaned = re.sub(r',\s*[en]\.group_id', '', cleaned)
            patched.append(cleaned)
        return patched

    _graph_queries.get_fulltext_indices = _patched_get_fulltext_indices
    logging.getLogger(__name__).info(
        'Patched get_fulltext_indices: removed group_id from all fulltext indexes '
        '(prevents RediSearch hyphen tokenization errors)'
    )
except Exception as e:
    logging.getLogger(__name__).warning(
        f'Could not patch get_fulltext_indices: {e}. '
        f'group_ids with hyphens may cause RediSearch syntax errors.'
    )

# --------------------------------------------------------------------------- #
# PERFORMANCE FIX: Override entity resolution search config                    #
#                                                                              #
# The default NODE_HYBRID_SEARCH_RRF uses cosine_similarity which requires     #
# embedding generation per extracted entity (~7s each via Azure OpenAI).       #
# On large graphs this creates cascading delays that compound with LLM dedup.  #
#                                                                              #
# Fix: Override to BM25-only search with a limit of 5 candidates.             #
# BM25 is instant (keyword-based) and 5 candidates is sufficient for dedup.   #
# This reduces entity resolution from ~60s+ to ~5s.                           #
# --------------------------------------------------------------------------- #
try:
    from graphiti_core.search.search_config import SearchConfig
    from graphiti_core.search.search_config_recipes import NODE_HYBRID_SEARCH_RRF
    from graphiti_core.search.search_config import NodeSearchConfig
    from graphiti_core.search.search_config import NodeSearchMethod, NodeReranker

    # Replace the module-level config used by _collect_candidate_nodes
    import graphiti_core.utils.maintenance.node_operations as _node_ops

    _FAST_NODE_SEARCH = SearchConfig(
        node_config=NodeSearchConfig(
            search_methods=[NodeSearchMethod.bm25],
            reranker=NodeReranker.rrf,
        ),
        limit=5,
    )
    # Monkey-patch the constant used in _collect_candidate_nodes
    _node_ops.NODE_HYBRID_SEARCH_RRF = _FAST_NODE_SEARCH
    logging.getLogger(__name__).info(
        'Patched NODE_HYBRID_SEARCH_RRF → BM25-only, limit=5 (skips slow embeddings)'
    )
except Exception as e:
    logging.getLogger(__name__).warning(
        f'Could not patch NODE_HYBRID_SEARCH_RRF: {e}. '
        f'Entity resolution will use default (slow) cosine_similarity search.'
    )

# --------------------------------------------------------------------------- #
# TARGETED FIX: Patch None.__doc__ bug in graphiti-core 0.28.2                 #
#                                                                              #
# TWO locations use `type_model.__doc__` without null-checking:                #
#   1. _build_entity_types_context (line 126): used by extract_nodes           #
#   2. _resolve_with_llm (lines 270-273): used by entity deduplication         #
#                                                                              #
# When entity_types_dict.get(label) returns None (label not found),            #
# None.__doc__ returns 'The type of the None singleton.' (Python 3.13)         #
# or None (Python <3.13). Either way, downstream LLM prompts break.           #
#                                                                              #
# Fix: Patch both functions with safe __doc__ coalescing.                      #
# NOTE: MAX_RETRIES patch removed — upstream 0.28.2 already defaults to 2.    #
# NOTE: Full extract_nodes/resolve_with_llm replacements removed — upstream   #
#       0.28.2 refactored these (removed reflexion loop, changed to           #
#       duplicate_name). Only the __doc__ bug remains unfixed upstream.        #
# --------------------------------------------------------------------------- #
try:
    import graphiti_core.utils.maintenance.node_operations as _node_ops

    # --- Patch 1: _build_entity_types_context (extract_nodes path) ---
    _original_build_ctx = _node_ops._build_entity_types_context

    def _safe_build_entity_types_context(entity_types):
        """Patched: coalesce None.__doc__ to a default description."""
        context = _original_build_ctx(entity_types)
        for entry in context:
            desc = entry.get('entity_type_description')
            if desc is None or desc == 'The type of the None singleton.':
                entry['entity_type_description'] = (
                    f"{entry.get('entity_type_name', 'Unknown')} entity type"
                )
        return context

    _node_ops._build_entity_types_context = _safe_build_entity_types_context

    # --- Patch 2: _resolve_with_llm (dedup path) ---
    # The bug is at lines 270-273:
    #   entity_types_dict.get(...).__doc__  →  AttributeError when key not found
    # Fix: wrap the original function and patch entity_types dict to have safe __doc__
    _original_resolve_with_llm = _node_ops._resolve_with_llm

    class _SafeDocModel:
        """Shim with a safe __doc__ for entity types not in the dict."""
        __doc__ = 'Default Entity Type'

    async def _patched_resolve_with_llm(
        llm_client, extracted_nodes, indexes, state,
        episode, previous_episodes, entity_types,
    ):
        """Wraps _resolve_with_llm with a defaultdict-like entity_types that never returns None."""
        if entity_types is not None:
            # Create a copy that returns _SafeDocModel for missing keys
            from collections import defaultdict
            safe_types = defaultdict(lambda: _SafeDocModel, entity_types)
            # Also fix any existing types with None __doc__
            for name, model in entity_types.items():
                if getattr(model, '__doc__', None) is None:
                    # Can't set __doc__ on arbitrary classes, so wrap it
                    class _FixedDoc(model):
                        __doc__ = f'{name} entity type'
                    safe_types[name] = _FixedDoc
        else:
            from collections import defaultdict
            safe_types = defaultdict(lambda: _SafeDocModel)

        return await _original_resolve_with_llm(
            llm_client, extracted_nodes, indexes, state,
            episode, previous_episodes, safe_types,
        )

    _node_ops._resolve_with_llm = _patched_resolve_with_llm

    logging.getLogger(__name__).info(
        'Patched _build_entity_types_context and _resolve_with_llm: '
        'safe __doc__ handling for graphiti-core 0.28.2'
    )
except Exception as e:
    logging.getLogger(__name__).warning(
        f'Could not patch None.__doc__ fixes: {e}. '
        f'Entity extraction/dedup may crash on unknown entity types.'
    )

# --------------------------------------------------------------------------- #
# TARGETED FIX: multi-label node search breaks Cypher on FalkorDB              #
#                                                                              #
# graphiti_core.search.search_filters.node_search_filter_query_constructor     #
# emits a `n:A|B` label-disjunction for EVERY non-KUZU provider (line ~100).   #
# That syntax is Neo4j-5 only; FalkorDB cannot parse `n:A|B` in a WHERE clause #
# — so search_nodes(entity_types=["X","Y"]) raises a Cypher syntax error.     #
# ONE label (`n:X`) works; TWO+ break. (Matches the long-standing             #
# "query one entity_type at a time" workaround.)                              #
#                                                                              #
# Fix: rebuild a 2+ label filter as a parenthesized OR of native predicates    #
#   (n:A OR n:B)  — valid on FalkorDB AND Neo4j, and a true union of types.    #
# search_utils.py imports the constructor BY NAME, so we must patch the        #
# reference held there (that's the one search_nodes actually calls).           #
# Labels pass SAFE_CYPHER_IDENTIFIER_PATTERN, so interpolation is injection-safe. #
# --------------------------------------------------------------------------- #
try:
    import graphiti_core.search.search_filters as _sf
    import graphiti_core.search.search_utils as _su
    from graphiti_core.driver.driver import GraphProvider as _GP
    from graphiti_core.helpers import validate_node_labels as _vnl

    _orig_node_filter_ctor = _sf.node_search_filter_query_constructor

    def _safe_node_search_filter_query_constructor(filters, provider):
        fq, fp = _orig_node_filter_ctor(filters, provider)
        labels = getattr(filters, 'node_labels', None)
        if labels and len(labels) > 1 and provider != _GP.KUZU:
            _vnl(labels)  # re-validate: SAFE_CYPHER_IDENTIFIER_PATTERN → safe to interpolate
            grouped = '(' + ' OR '.join(f'n:{lbl}' for lbl in labels) + ')'
            fq = [grouped if (q.startswith('n:') and '|' in q) else q for q in fq]
        return fq, fp

    _sf.node_search_filter_query_constructor = _safe_node_search_filter_query_constructor
    _su.node_search_filter_query_constructor = _safe_node_search_filter_query_constructor
    logging.getLogger(__name__).info(
        'Patched node_search_filter_query_constructor: 2+ entity_types now emit '
        '(n:A OR n:B) for FalkorDB — search_nodes multi-type no longer breaks Cypher'
    )
except Exception as e:
    logging.getLogger(__name__).warning(
        f'Could not patch multi-label node search fix: {e}. '
        f'search_nodes with 2+ entity_types may raise a Cypher syntax error on FalkorDB.'
    )


# Configure structured logging with timestamps
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    stream=sys.stderr,
)

# Configure specific loggers
logging.getLogger('uvicorn').setLevel(logging.INFO)
logging.getLogger('uvicorn.access').setLevel(logging.WARNING)  # Reduce access log noise
logging.getLogger('mcp.server.streamable_http_manager').setLevel(
    logging.WARNING
)  # Reduce MCP noise


# Patch uvicorn's logging config to use our format
def configure_uvicorn_logging():
    """Configure uvicorn loggers to match our format after they're created."""
    for logger_name in ['uvicorn', 'uvicorn.error', 'uvicorn.access']:
        uvicorn_logger = logging.getLogger(logger_name)
        # Remove existing handlers and add our own with proper formatting
        uvicorn_logger.handlers.clear()
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        uvicorn_logger.addHandler(handler)
        uvicorn_logger.propagate = False


logger = logging.getLogger(__name__)

# Create global config instance - will be properly initialized later
config: GraphitiConfig

# MCP server instructions
GRAPHITI_MCP_INSTRUCTIONS = """
Graphiti is a memory service for AI agents built on a knowledge graph. Graphiti performs well
with dynamic data such as user interactions, changing enterprise data, and external information.

Graphiti transforms information into a richly connected knowledge network, allowing you to
capture relationships between concepts, entities, and information. The system organizes data as episodes
(content snippets), nodes (entities), and facts (relationships between entities), creating a dynamic,
queryable memory store that evolves with new information. Graphiti supports multiple data formats, including
structured JSON data, enabling seamless integration with existing data pipelines and systems.

Facts contain temporal metadata, allowing you to track the time of creation and whether a fact is invalid
(superseded by new information).

Key capabilities:
1. Add episodes (text, messages, or JSON) to the knowledge graph with the add_memory tool
2. Search for nodes (entities) in the graph using natural language queries with search_nodes
3. Find relevant facts (relationships between entities) with search_memory_facts
4. Retrieve specific entity edges or episodes by UUID
5. Manage the knowledge graph with tools like delete_episode, delete_entity_edge, and clear_graph

IMPORTANT - Background Task Behavior:
The add_memory tool runs as a BACKGROUND TASK. This means:
- The tool returns immediately while entity extraction continues in the background
- You will receive automatic progress notifications via MCP protocol (no polling needed)
- On success: You'll receive an info notification with entity/edge counts
- On failure: You'll receive an error notification with the failure reason
- You do NOT need to call get_async_task_status - progress is automatic

The server connects to a database for persistent storage and uses language models for certain operations.
Each piece of information is organized by group_id, allowing you to maintain separate knowledge domains.

When adding information, provide descriptive names and detailed content to improve search quality.
When searching, use specific queries and consider filtering by group_id for more relevant results.

For optimal performance, ensure the database is properly configured and accessible, and valid
API keys are provided for any language model operations.
"""

# MCP server instance
mcp = FastMCP(
    'Graphiti Agent Memory',
    instructions=GRAPHITI_MCP_INSTRUCTIONS,
)

# Global services
graphiti_service: Optional['GraphitiService'] = None
queue_service: QueueService | None = None

# Global client for backward compatibility
graphiti_client: Graphiti | None = None
semaphore: asyncio.Semaphore


def validate_group_id(group_id: str | None, context: str = 'operation') -> str:
    """Validate that group_id is not None or empty.

    Args:
        group_id: The group_id to validate
        context: Operation context for error messages

    Returns:
        The validated group_id

    Raises:
        ValueError: If group_id is None or empty
    """
    if group_id is None or not group_id.strip():
        raise ValueError(f'{context}: group_id cannot be None or empty')
    return group_id


def validate_group_ids(
    group_ids: list[str] | None, fallback: str | None, context: str = 'operation'
) -> list[str]:
    """Validate and resolve group_ids list with fallback.

    Args:
        group_ids: Optional list of group IDs
        fallback: Fallback group_id if group_ids is None
        context: Operation context for error messages

    Returns:
        List of validated group IDs

    Raises:
        ValueError: If no group_ids can be determined
    """
    effective = group_ids if group_ids else ([fallback] if fallback else [])
    if not effective:
        raise ValueError(f'{context}: No group_ids specified and no fallback available')
    return [validate_group_id(gid, context) for gid in effective]


def sanitize_for_redisearch(text: str) -> str:
    """
    Sanitize text content for safe RediSearch indexing in FalkorDB.

    When storing content in FalkorDB's fulltext-indexed fields, certain characters
    can cause "RediSearch: Syntax error at offset X" errors during automatic indexing.
    Characters like ~, @, :, /, -, etc. are interpreted as query operators.

    This function replaces problematic characters with spaces, similar to how
    FalkorDriver.sanitize() handles search queries. This ensures content can be
    stored and indexed without errors while maintaining searchability.

    Args:
        text: The raw text content to sanitize

    Returns:
        Text with RediSearch special characters replaced by spaces

    Example:
        Input:  "File at ~/.pem_1/herself_dev/private_key.pem caused error"
        Output: "File at    pem 1 herself dev private key pem caused error"
    """
    if not text:
        return text

    # RediSearch special characters that can cause indexing errors
    # Based on FalkorDB's sanitize() and RediSearch query syntax
    separator_map = str.maketrans(
        {
            ',': ' ',
            '.': ' ',
            '<': ' ',
            '>': ' ',
            '{': ' ',
            '}': ' ',
            '[': ' ',
            ']': ' ',
            '"': ' ',
            "'": ' ',
            ':': ' ',
            ';': ' ',
            '!': ' ',
            '@': ' ',
            '#': ' ',
            '$': ' ',
            '%': ' ',
            '^': ' ',
            '&': ' ',
            '*': ' ',
            '(': ' ',
            ')': ' ',
            '-': ' ',
            '+': ' ',
            '=': ' ',
            '~': ' ',
            '?': ' ',
            '/': ' ',
            '\\': ' ',
            '|': ' ',
            '`': ' ',
        }
    )

    sanitized = text.translate(separator_map)
    # Collapse multiple spaces into single space
    sanitized = ' '.join(sanitized.split())

    return sanitized


class GraphitiService:
    """Graphiti service using the unified configuration system."""

    def __init__(self, config: GraphitiConfig, semaphore_limit: int = 10):
        self.config = config
        self.semaphore_limit = semaphore_limit
        self.semaphore = asyncio.Semaphore(semaphore_limit)
        self.client: Graphiti | None = None
        self.entity_types = None

    async def initialize(self) -> None:
        """Initialize the Graphiti client with factory-created components."""
        try:
            # Create clients using factories
            llm_client = None
            embedder_client = None

            # Create LLM client based on configured provider
            try:
                llm_client = LLMClientFactory.create(self.config.llm)
                logger.info(f'LLM client created: {type(llm_client).__name__}')
            except Exception as e:
                logger.error(f'Failed to create LLM client: {e}')
                raise RuntimeError(f'LLM client creation failed: {e}') from e

            if llm_client is None:
                raise RuntimeError('LLM client is None after creation - check configuration')

            # Create embedder client based on configured provider
            try:
                embedder_client = EmbedderFactory.create(self.config.embedder)
            except Exception as e:
                logger.warning(f'Failed to create embedder client: {e}')

            # Get database configuration
            db_config = DatabaseDriverFactory.create_config(self.config.database)

            # Build entity types from configuration
            custom_types = None
            if self.config.graphiti.entity_types:
                custom_types = {}
                for entity_type in self.config.graphiti.entity_types:
                    # Create a dynamic Pydantic model for each entity type
                    # Note: Don't use 'name' as it's a protected Pydantic attribute
                    entity_model = type(
                        entity_type.name,
                        (BaseModel,),
                        {
                            '__doc__': entity_type.description,
                        },
                    )
                    custom_types[entity_type.name] = entity_model

            # Store entity types for later use
            self.entity_types = custom_types

            # Initialize Graphiti client with appropriate driver
            try:
                # Create cross encoder using the LLM client
                from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
                from graphiti_core.llm_client.config import LLMConfig as CoreLLMConfig

                # Create LLMConfig for the cross encoder
                reranker_config = CoreLLMConfig(
                    model=self.config.llm.model,
                    small_model=self.config.llm.model,
                )
                cross_encoder = OpenAIRerankerClient(client=llm_client, config=reranker_config)

                if self.config.database.provider.lower() == 'falkordb':
                    # For FalkorDB, create a FalkorDriver instance directly
                    from graphiti_core.driver.falkordb_driver import FalkorDriver

                    falkor_driver = FalkorDriver(
                        host=db_config['host'],
                        port=db_config['port'],
                        password=db_config['password'],
                        database=db_config['database'],
                    )

                    self.client = Graphiti(
                        graph_driver=falkor_driver,
                        llm_client=llm_client,
                        embedder=embedder_client,
                        cross_encoder=cross_encoder,
                        max_coroutines=self.semaphore_limit,
                    )
                else:
                    # For Neo4j (default), use the original approach
                    self.client = Graphiti(
                        uri=db_config['uri'],
                        user=db_config['user'],
                        password=db_config['password'],
                        llm_client=llm_client,
                        embedder=embedder_client,
                        cross_encoder=cross_encoder,
                        max_coroutines=self.semaphore_limit,
                    )
            except Exception as db_error:
                # Check for connection errors
                error_msg = str(db_error).lower()
                if 'connection refused' in error_msg or 'could not connect' in error_msg:
                    db_provider = self.config.database.provider
                    if db_provider.lower() == 'falkordb':
                        raise RuntimeError(
                            f'\n{"=" * 70}\n'
                            f'Database Connection Error: FalkorDB is not running\n'
                            f'{"=" * 70}\n\n'
                            f'FalkorDB at {db_config["host"]}:{db_config["port"]} is not accessible.\n\n'
                            f'To start FalkorDB:\n'
                            f'  - Using Docker Compose: cd mcp_server && docker compose up\n'
                            f'  - Or run FalkorDB manually: docker run -p 6379:6379 falkordb/falkordb\n\n'
                            f'{"=" * 70}\n'
                        ) from db_error
                    elif db_provider.lower() == 'neo4j':
                        raise RuntimeError(
                            f'\n{"=" * 70}\n'
                            f'Database Connection Error: Neo4j is not running\n'
                            f'{"=" * 70}\n\n'
                            f'Neo4j at {db_config.get("uri", "unknown")} is not accessible.\n\n'
                            f'To start Neo4j:\n'
                            f'  - Using Docker Compose: cd mcp_server && docker compose -f docker/docker-compose-neo4j.yml up\n'
                            f'  - Or install Neo4j Desktop from: https://neo4j.com/download/\n'
                            f'  - Or run Neo4j manually: docker run -p 7474:7474 -p 7687:7687 neo4j:latest\n\n'
                            f'{"=" * 70}\n'
                        ) from db_error
                    else:
                        raise RuntimeError(
                            f'\n{"=" * 70}\n'
                            f'Database Connection Error: {db_provider} is not running\n'
                            f'{"=" * 70}\n\n'
                            f'{db_provider} at {db_config.get("uri", "unknown")} is not accessible.\n\n'
                            f'Please ensure {db_provider} is running and accessible.\n\n'
                            f'{"=" * 70}\n'
                        ) from db_error
                # Re-raise other errors
                raise

            # Build indices
            await self.client.build_indices_and_constraints()

            logger.info('Successfully initialized Graphiti client')

            # Log configuration details
            if llm_client:
                logger.info(
                    f'Using LLM provider: {self.config.llm.provider} / {self.config.llm.model}'
                )
            else:
                logger.info('No LLM client configured - entity extraction will be limited')

            if embedder_client:
                logger.info(f'Using Embedder provider: {self.config.embedder.provider}')
            else:
                logger.info('No Embedder client configured - search will be limited')

            if self.entity_types:
                entity_type_names = list(self.entity_types.keys())
                logger.info(f'Using custom entity types: {", ".join(entity_type_names)}')
            else:
                logger.info('Using default entity types')

            logger.info(f'Using database: {self.config.database.provider}')
            logger.info(f'Using group_id: {self.config.graphiti.group_id}')

        except Exception as e:
            logger.error(f'Failed to initialize Graphiti client: {e}')
            raise

    async def get_client(self) -> Graphiti:
        """Get the Graphiti client, initializing if necessary."""
        if self.client is None:
            await self.initialize()
        if self.client is None:
            raise RuntimeError('Failed to initialize Graphiti client')
        return self.client


@mcp.tool(task=True)
async def add_memory(
    ctx: Context,
    name: str,
    episode_body: str,
    group_id: str | None = None,
    source: str = 'text',
    source_description: str = '',
    uuid: str | None = None,
) -> SuccessResponse | ErrorResponse:
    """Add an episode to memory. This is the PRIMARY tool for storing information in the knowledge graph.

    WHEN TO USE:
    - Store successful problem-solving workflows and tool patterns
    - Save user preferences, requirements, or procedures
    - Record problems encountered and their solutions
    - Store context about projects, environments, or technology stacks
    - Capture outcomes, metrics, and effectiveness measurements
    - Remember debugging sessions, fixes, and resolutions
    - Store any information you want the agent to remember later

    WHAT IT DOES:
    - Runs as a background task (non-blocking) via FastMCP's native task system
    - Extracts entities automatically using LLM (Problems, Solutions, Workflows, ToolPatterns, etc.)
    - Creates relationships between entities in the knowledge graph
    - Sends progress notifications via MCP protocol (agent receives updates automatically)
    - Returns count of extracted entities and relationships on completion

    USE CASES:
    1. Store tool usage patterns: "Successfully fixed TypeScript error using codebase_search → read_file → search_replace"
    2. Save workflows: "Complete debugging workflow: check logs → analyze → profile → fix"
    3. Record solutions: "Fixed memory leak by adding useEffect cleanup hook"
    4. Store preferences: "User prefers TypeScript over JavaScript"
    5. Save context: "React + TypeScript + Vite project setup"
    6. Track outcomes: "Solution resulted in 95% success rate, 5 minutes saved"

    WHEN NOT TO USE:
    - To search for existing information → Use search_nodes or search_memory_facts instead
    - To retrieve episodes → Use get_episodes instead
    - To get specific relationships → Use get_entity_edge instead

    ENTITY TYPE SIGNAL WORDS (CRITICAL for proper categorization):
    Graphiti auto-extracts entity types from episode_body using LLM. To ensure correct
    categorization, ALWAYS prefix episode_body with the matching signal word:

    | Signal Word Prefix | Graphiti Entity Type | When to Use |
    |--------------------|---------------------|-------------|
    | "Preference: ..."  | Preference          | User rules, coding style, tool choices |
    | "Workflow: ..."    | Workflow            | Step-by-step processes, pipelines |
    | "ToolPattern: ..." | ToolPattern         | Reusable tool/query/code patterns |
    | "Problem: ..."     | Problem             | Known issues, risks, critical findings |
    | "Solution: ..."    | Solution            | Fixes, resolutions, workarounds |
    | "Procedure: ..."   | Procedure           | Formal processes, deployment steps |
    | (no prefix)        | Entity (generic)    | Reference data, facts, counts, schema info |

    WITHOUT the signal word prefix, most memories get categorized as generic "Entity",
    which means search_nodes(entity_types=["Workflow"]) will NOT find them.

    BEST PRACTICES:
    - ALWAYS prefix episode_body with a signal word when the type fits (see table above)
    - Keep episode_body to 2-3 lines max — Graphiti is an INDEX, not a content store
    - End with "Source: YYYY-MM-DD." so the date can be used to find full details in SpecStory
    - Use descriptive names that summarize the episode content
    - Generic Entity is fine for pure reference data (facts, counts, schema info)
    - Use source='text' for conversations and natural language
    - Use source='json' for structured data (must be properly escaped JSON string)
    - Use source='message' for chat/conversation content

    Args:
        ctx (Context): FastMCP context for progress notifications (injected automatically)
        name (str): Descriptive name summarizing the episode content (e.g., "ToolPattern: RAF pipeline interaction factors")
        episode_body (str): The content to persist. MUST start with a signal word prefix for typed entities.
                          Keep to 2-3 lines. End with "Source: YYYY-MM-DD." for traceability.
                          When source='json', must be properly escaped JSON string.
        group_id (str, optional): Namespace for the graph. Defaults to configured group_id.
        source (str, optional): Source type - 'text' (default), 'json', or 'message'
        source_description (str, optional): Additional context about the source
        uuid (str, optional): Optional UUID for the episode (auto-generated if not provided)

    Examples:
        # Store a preference (note "Preference:" prefix)
        add_memory(
            name="Preference: Cross-platform SQL",
            episode_body="Preference: Write ANSI-compatible SQL for both Snowflake AND Databricks. Avoid platform-specific syntax. Source: 2026-02-17.",
            group_id="developer_gyasisutton"
        )

        # Store a tool pattern (note "ToolPattern:" prefix)
        add_memory(
            name="ToolPattern: RAF pipeline interaction factors",
            episode_body="ToolPattern: Get_Raf_Monthly.py loads 99 interaction pairs from Tuva V28. 6 groups: Diabetes×Vascular, Vascular×Stroke, etc. Source: 2026-02-17.",
            group_id="developer_gyasisutton"
        )

        # Store a problem/risk (note "Problem:" prefix)
        add_memory(
            name="Problem: HCCpy archived Dec 2024",
            episode_body="Problem: HCCpy is no longer maintained. Successor: mimilabs/hccinfhir. Risk for 2026+ if CMS updates V28. Source: 2026-02-17.",
            group_id="developer_gyasisutton"
        )

        # Store a workflow (note "Workflow:" prefix)
        add_memory(
            name="Workflow: RAF pipeline execution",
            episode_body="Workflow: 1) Run Python Get_Raf_Monthly.py 2) CALL CREATE_MONTHLY_RAF_SNAPSHOT(year) 3) Update unified view UNION ALL. Source: 2026-02-17.",
            group_id="developer_gyasisutton"
        )

        # Store generic reference data (no prefix needed)
        add_memory(
            name="RAF pipeline Tuva table row counts",
            episode_body="Tuva V28 tables: interaction_factors=99 rows, count_factors=7 rows, hierarchy=97 rows. All version-keyed. Source: 2026-02-17.",
            group_id="developer_gyasisutton"
        )
    """
    global graphiti_service, queue_service

    if graphiti_service is None or queue_service is None:
        await ctx.error('Services not initialized')
        return ErrorResponse(error='Services not initialized')

    try:
        # Notify agent that processing has started
        await ctx.info(f"Processing episode '{name}'...")

        # Use the provided group_id or fall back to the default from config
        effective_group_id = validate_group_id(
            group_id or graphiti_service.config.graphiti.group_id, context='add_memory'
        )

        # Try to parse the source as an EpisodeType enum, with fallback to text
        episode_type = EpisodeType.text  # Default
        if source:
            try:
                episode_type = EpisodeType[source.lower()]
            except (KeyError, AttributeError):
                # If the source doesn't match any enum value, use text as default
                logger.warning(f"Unknown source type '{source}', using 'text' as default")
                episode_type = EpisodeType.text

        # Sanitize episode_body and source_description to prevent RediSearch indexing errors
        # Characters like ~, @, :, /, - can cause "Syntax error at offset X" during
        # FalkorDB's automatic fulltext indexing of the content field
        # Note: 'content', 'source', 'source_description', 'group_id' are all indexed
        # FIX: Skip sanitization for JSON source type — sanitize_for_redisearch strips
        # structural characters ({, }, [, ], :, ", ,) that JSON needs to remain valid
        if episode_type == EpisodeType.json:
            sanitized_body = episode_body
        else:
            sanitized_body = sanitize_for_redisearch(episode_body)
        sanitized_source_desc = sanitize_for_redisearch(source_description)

        # CRITICAL FIX: Use semaphore to prevent rate limit errors (429)
        # Each episode processing involves multiple LLM calls
        async with graphiti_service.semaphore:
            await ctx.info(f"Acquired semaphore, extracting entities for '{name}'...")

            # NOTE: graphiti-core's add_episode internally handles driver cloning based on group_id
            # The group_id parameter ensures the episode is stored in the correct database
            try:
                result = await asyncio.wait_for(
                    graphiti_service.client.add_episode(
                        name=name,
                        episode_body=sanitized_body,
                        source_description=sanitized_source_desc,
                        source=episode_type,
                        group_id=effective_group_id,
                        reference_time=datetime.now(timezone.utc),
                        entity_types=graphiti_service.entity_types,
                        uuid=uuid or None,
                    ),
                    timeout=ADD_EPISODE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                timeout_msg = (
                    f"add_episode timed out after {ADD_EPISODE_TIMEOUT_SECONDS}s for '{name}'. "
                    f"Entity resolution on a large graph likely caused cascading LLM calls. "
                    f"The episode was NOT persisted. Consider increasing "
                    f"ADD_EPISODE_TIMEOUT_SECONDS or reducing graph size."
                )
                logger.warning(timeout_msg)
                await ctx.error(timeout_msg)
                return ErrorResponse(error=timeout_msg)

        # Extract result info
        entity_count = len(result.nodes) if hasattr(result, 'nodes') else 0
        edge_count = len(result.edges) if hasattr(result, 'edges') else 0

        logger.info(f"Episode '{name}' processed: {entity_count} entities, {edge_count} edges")

        # Notify agent of successful completion
        await ctx.info(
            f"Episode '{name}' added successfully ({entity_count} entities, {edge_count} edges)"
        )

        return SuccessResponse(
            message=f"Episode '{name}' added to group '{effective_group_id}' ({entity_count} entities, {edge_count} edges)"
        )
    except asyncio.TimeoutError:
        # Re-raise should not happen here (caught above), but guard against
        # any timeout that escapes the inner try/except
        timeout_msg = (
            f"add_episode timed out after {ADD_EPISODE_TIMEOUT_SECONDS}s for '{name}' "
            f"(outer catch). Episode was NOT persisted."
        )
        logger.warning(timeout_msg)
        await ctx.error(timeout_msg)
        return ErrorResponse(error=timeout_msg)
    except Exception as e:
        import traceback
        error_msg = str(e)
        tb_str = traceback.format_exc()
        logger.error(f'Error processing episode: {error_msg}\nTraceback:\n{tb_str}')
        # Notify agent of failure via MCP protocol
        await ctx.error(f"Failed to process episode '{name}': {error_msg}")
        return ErrorResponse(error=f'Error processing episode: {error_msg}')


@mcp.tool()
async def search_nodes(
    query: str,
    group_ids: list[str] | None = None,
    max_nodes: int = 10,
    entity_types: list[str] | None = None,
) -> NodeSearchResponse | ErrorResponse:
    """Search for entity nodes (Problems, Solutions, Workflows, ToolPatterns, etc.) in the knowledge graph.

    WHEN TO USE:
    - Find similar problems you've solved before
    - Search for existing workflows or procedures
    - Look for solutions to specific problem types
    - Find tool patterns for similar contexts
    - Discover user preferences or requirements
    - Search for entities by type (Workflow, Problem, Solution, etc.)

    WHAT IT DOES:
    - Performs semantic search across all entity nodes in the graph
    - Uses hybrid search (semantic + keyword + graph traversal)
    - Returns entity summaries with UUIDs, labels, and attributes
    - Can filter by specific entity types (Workflow, Problem, Solution, etc.)

    USE CASES:
    1. Find workflows: "debugging workflows for memory leaks"
    2. Search problems: "TypeScript compilation errors"
    3. Find solutions: "solutions for API connection timeouts"
    4. Discover patterns: "tool patterns for code fixes"
    5. Check preferences: "user preferences for TypeScript"
    6. Find context-specific info: "React + TypeScript project solutions"

    WHEN NOT TO USE:
    - To find relationships between entities → Use search_memory_facts instead
    - To get recent episodes → Use get_episodes instead
    - To get a specific entity by UUID → Use get_entity_edge instead

    BEST PRACTICES:
    - Use specific queries: "TypeScript error solutions" not just "errors"
    - Filter by entity_types when you know what you're looking for
    - Combine with search_memory_facts for complete picture
    - Use descriptive queries that match how information was stored

    Args:
        query (str): Natural language search query describing what you're looking for
        group_ids (list[str], optional): Filter by group IDs. Defaults to configured group_id.
        max_nodes (int, optional): Maximum results to return (default: 10)
        entity_types (list[str], optional): Filter by entity types. Examples: ["Workflow", "Problem", "Solution", "ToolPattern", "Procedure", "Preference"]

    Examples:
        # Find workflows for a specific problem type
        search_nodes(
            query="debugging workflows for memory leaks",
            entity_types=["Workflow"],
            max_nodes=5
        )

        # Search for solutions to TypeScript errors
        search_nodes(
            query="TypeScript compilation error solutions",
            entity_types=["Solution", "Problem"],
            max_nodes=10
        )

        # Find tool patterns for React projects
        search_nodes(
            query="tool patterns for React TypeScript projects",
            entity_types=["ToolPattern"],
            group_ids=["developer_gyasisutton"]
        )

        # Discover user preferences
        search_nodes(
            query="user preferences for coding style",
            entity_types=["Preference"]
        )
    """
    global graphiti_service

    if graphiti_service is None:
        return ErrorResponse(error='Graphiti service not initialized')

    try:
        client = await graphiti_service.get_client()

        # Use the provided group_ids or fall back to the default from config if none provided
        effective_group_ids = validate_group_ids(
            group_ids, graphiti_service.config.graphiti.group_id, context='search_nodes'
        )

        # Create search filters
        search_filters = SearchFilters(
            node_labels=entity_types,
        )

        # Use the search_ method with node search config
        from graphiti_core.search.search_config_recipes import NODE_HYBRID_SEARCH_RRF

        # CRITICAL FIX: Clone driver with first group_id to avoid database state pollution
        # The @handle_multiple_group_ids decorator only clones when len(group_ids) > 1
        # For single group_id, we must explicitly clone to ensure correct database targeting
        # regardless of prior tool calls (e.g., add_memory mutates client.driver._database)
        cloned_driver = client.driver.clone(database=effective_group_ids[0])

        results = await client.search_(
            query=query,
            config=NODE_HYBRID_SEARCH_RRF,
            group_ids=effective_group_ids,
            search_filter=search_filters,
            driver=cloned_driver,
        )

        # Extract nodes from results
        nodes = results.nodes[:max_nodes] if results.nodes else []

        if not nodes:
            return NodeSearchResponse(message='No relevant nodes found', nodes=[])

        # Format the results
        node_results = []
        for node in nodes:
            # Get attributes and ensure no embeddings are included
            attrs = node.attributes if hasattr(node, 'attributes') else {}
            # Remove any embedding keys that might be in attributes
            attrs = {k: v for k, v in attrs.items() if 'embedding' not in k.lower()}

            node_results.append(
                NodeResult(
                    uuid=node.uuid,
                    name=node.name,
                    labels=node.labels if node.labels else [],
                    created_at=node.created_at.isoformat() if node.created_at else None,
                    summary=node.summary,
                    group_id=node.group_id,
                    attributes=attrs,
                )
            )

        return NodeSearchResponse(message='Nodes retrieved successfully', nodes=node_results)
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error searching nodes: {error_msg}')
        return ErrorResponse(error=f'Error searching nodes: {error_msg}')


@mcp.tool()
async def search_memory_facts(
    query: str,
    group_ids: list[str] | None = None,
    max_facts: int = 10,
    center_node_uuid: str | None = None,
) -> FactSearchResponse | ErrorResponse:
    """Search for relationships/facts (edges) between entities in the knowledge graph.

    WHEN TO USE:
    - Find relationships between Problems and Solutions
    - Discover which Workflows solve which Problems
    - Find ToolPatterns that work for specific contexts
    - Discover how entities relate to each other
    - Find facts like "Solution X solves Problem Y"
    - Search for patterns: "What workflows use this tool pattern?"

    WHAT IT DOES:
    - Searches for relationships (edges) between entities
    - Returns facts showing how entities connect
    - Can center search around a specific node UUID
    - Uses semantic search to find relevant relationships

    USE CASES:
    1. Find problem-solution relationships: "What solutions exist for TypeScript errors?"
    2. Discover workflow patterns: "What workflows solve memory leak problems?"
    3. Find tool pattern effectiveness: "Which tool patterns have high success rates?"
    4. Explore relationships: "How do Problems relate to Solutions and Workflows?"
    5. Context-specific patterns: "What solutions work in React + TypeScript context?"

    WHEN NOT TO USE:
    - To find entity nodes themselves → Use search_nodes instead
    - To get recent episodes → Use get_episodes instead
    - To get a specific relationship by UUID → Use get_entity_edge instead

    BEST PRACTICES:
    - Use search_nodes first to find entities, then search_memory_facts to find relationships
    - Use center_node_uuid to explore relationships around a specific entity
    - Combine both tools for comprehensive understanding
    - Use descriptive queries about relationships, not just entities

    Args:
        query (str): Natural language query about relationships/facts you're looking for
        group_ids (list[str], optional): Filter by group IDs. Defaults to configured group_id.
        max_facts (int, optional): Maximum facts to return (default: 10)
        center_node_uuid (str, optional): UUID of a node to center search around (explores relationships from that node)

    Examples:
        # Find solutions for specific problems
        search_memory_facts(
            query="solutions for TypeScript compilation errors",
            max_facts=10
        )

        # Discover workflows that solve memory leak problems
        search_memory_facts(
            query="workflows that solve memory leak problems",
            group_ids=["developer_gyasisutton"]
        )

        # Explore relationships around a specific problem (after finding its UUID with search_nodes)
        search_memory_facts(
            query="relationships and solutions",
            center_node_uuid="problem-uuid-from-search_nodes"
        )

        # Find tool patterns with high success rates
        search_memory_facts(
            query="tool patterns with success rate metrics above 90%",
            max_facts=5
        )
    """
    global graphiti_service

    if graphiti_service is None:
        return ErrorResponse(error='Graphiti service not initialized')

    try:
        # Validate max_facts parameter
        if max_facts <= 0:
            return ErrorResponse(error='max_facts must be a positive integer')

        client = await graphiti_service.get_client()

        # Use the provided group_ids or fall back to the default from config if none provided
        effective_group_ids = validate_group_ids(
            group_ids, graphiti_service.config.graphiti.group_id, context='search_memory_facts'
        )

        # CRITICAL FIX: Clone driver with first group_id to avoid database state pollution
        # The @handle_multiple_group_ids decorator only clones when len(group_ids) > 1
        # For single group_id, we must explicitly clone to ensure correct database targeting
        # regardless of prior tool calls (e.g., add_memory mutates client.driver._database)
        cloned_driver = client.driver.clone(database=effective_group_ids[0])

        relevant_edges = await client.search(
            group_ids=effective_group_ids,
            query=query,
            num_results=max_facts,
            center_node_uuid=center_node_uuid,
            driver=cloned_driver,
        )

        if not relevant_edges:
            return FactSearchResponse(message='No relevant facts found', facts=[])

        facts = [format_fact_result(edge) for edge in relevant_edges]
        return FactSearchResponse(message='Facts retrieved successfully', facts=facts)
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error searching facts: {error_msg}')
        return ErrorResponse(error=f'Error searching facts: {error_msg}')


@mcp.tool()
async def delete_entity_edge(uuid: str) -> SuccessResponse | ErrorResponse:
    """Delete a specific relationship/fact (edge) from the knowledge graph.

    WHEN TO USE:
    - Remove incorrect or outdated relationships
    - Clean up wrong fact extractions
    - Delete relationships that are no longer valid
    - Maintenance: Remove invalid entity connections

    WHAT IT DOES:
    - Permanently deletes a relationship between two entities
    - Does NOT delete the entities themselves, only the relationship
    - Use with caution - deletion cannot be undone

    USE CASES:
    1. Remove incorrect relationships: "Delete wrong fact extraction"
    2. Clean up outdated facts: "Remove relationship that's no longer valid"
    3. Maintenance: "Delete invalid entity connections"

    WHEN NOT TO USE:
    - To delete an entire episode → Use delete_episode instead
    - To clear all data → Use clear_graph instead
    - To remove entities → Relationships are removed, but entities remain

    BEST PRACTICES:
    - Verify UUID before deleting (use get_entity_edge to check)
    - Consider if the relationship is truly wrong or just needs updating
    - Use clear_graph for bulk cleanup instead

    Args:
        uuid (str): UUID of the entity edge (relationship) to delete. Get UUIDs from search_memory_facts or get_entity_edge results.

    Examples:
        # Delete a specific incorrect relationship
        delete_entity_edge(uuid="edge-uuid-to-delete")
    """
    global graphiti_service

    if graphiti_service is None:
        return ErrorResponse(error='Graphiti service not initialized')

    try:
        client = await graphiti_service.get_client()

        # Clone driver with config group_id to avoid cross-database pollution
        group_id = graphiti_service.config.graphiti.group_id
        driver = client.driver.clone(database=group_id)

        # Get the entity edge by UUID
        entity_edge = await EntityEdge.get_by_uuid(driver, uuid)
        # Delete the edge using its delete method
        await entity_edge.delete(driver)
        return SuccessResponse(message=f'Entity edge with UUID {uuid} deleted successfully')
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error deleting entity edge: {error_msg}')
        return ErrorResponse(error=f'Error deleting entity edge: {error_msg}')


@mcp.tool()
async def delete_episode(uuid: str) -> SuccessResponse | ErrorResponse:
    """Delete an episode and all its extracted entities/relationships from the knowledge graph.

    WHEN TO USE:
    - Remove incorrect or unwanted episodes
    - Delete episodes with wrong information
    - Clean up test episodes
    - Remove episodes that are no longer relevant

    WHAT IT DOES:
    - Permanently deletes an episode and all entities/relationships extracted from it
    - Use with caution - deletion cannot be undone
    - All related entities and relationships are also removed

    USE CASES:
    1. Remove incorrect episodes: "Delete episode with wrong information"
    2. Clean up test data: "Remove test episodes"
    3. Maintenance: "Delete outdated episodes"

    WHEN NOT TO USE:
    - To delete just a relationship → Use delete_entity_edge instead
    - To clear all data → Use clear_graph instead
    - To remove specific entities → Deleting episode removes all its entities

    BEST PRACTICES:
    - Verify UUID before deleting (use get_episodes to find UUIDs)
    - Consider if episode is truly wrong or just needs updating
    - Use clear_graph for bulk cleanup instead

    Args:
        uuid (str): UUID of the episode to delete. Get UUIDs from get_episodes results.

    Examples:
        # Delete a specific episode
        delete_episode(uuid="episode-uuid-from-get_episodes")
    """
    global graphiti_service

    if graphiti_service is None:
        return ErrorResponse(error='Graphiti service not initialized')

    try:
        client = await graphiti_service.get_client()

        # Clone driver with config group_id to avoid cross-database pollution
        group_id = graphiti_service.config.graphiti.group_id
        driver = client.driver.clone(database=group_id)

        # Get the episodic node by UUID
        episodic_node = await EpisodicNode.get_by_uuid(driver, uuid)
        # Delete the node using its delete method
        await episodic_node.delete(driver)
        return SuccessResponse(message=f'Episode with UUID {uuid} deleted successfully')
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error deleting episode: {error_msg}')
        return ErrorResponse(error=f'Error deleting episode: {error_msg}')


@mcp.tool()
async def get_entity_edge(uuid: str) -> dict[str, Any] | ErrorResponse:
    """Get a specific relationship/fact (edge) from the knowledge graph by its UUID.

    WHEN TO USE:
    - Retrieve a specific relationship you found in search_memory_facts results
    - Get detailed information about a specific fact/relationship
    - Access full relationship metadata (source, target, fact, timestamps)

    WHAT IT DOES:
    - Returns complete relationship information including:
      - Source and target entity UUIDs
      - The fact/relationship description
      - Creation and validity timestamps
      - Related episodes

    USE CASES:
    1. Get details of a specific relationship: "Get the relationship between Problem X and Solution Y"
    2. Access relationship metadata: "Get full details of this fact"
    3. Follow up on search results: "Get more details about this relationship"

    WHEN NOT TO USE:
    - To search for relationships → Use search_memory_facts instead
    - To find entities → Use search_nodes instead
    - To get episodes → Use get_episodes instead

    BEST PRACTICES:
    - Use UUIDs from search_memory_facts results
    - Use when you need full relationship details
    - Combine with search_nodes to get source/target entity details

    Args:
        uuid (str): UUID of the entity edge (relationship) to retrieve. Get UUIDs from search_memory_facts results.

    Examples:
        # Get a specific relationship (UUID from search_memory_facts result)
        get_entity_edge(uuid="edge-uuid-from-search-results")
    """
    global graphiti_service

    if graphiti_service is None:
        return ErrorResponse(error='Graphiti service not initialized')

    try:
        client = await graphiti_service.get_client()

        # Clone driver with config group_id to avoid cross-database pollution
        group_id = graphiti_service.config.graphiti.group_id
        driver = client.driver.clone(database=group_id)

        # Get the entity edge directly using the EntityEdge class method
        entity_edge = await EntityEdge.get_by_uuid(driver, uuid)

        # Use the format_fact_result function to serialize the edge
        # Return the Python dict directly - MCP will handle serialization
        return format_fact_result(entity_edge)
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error getting entity edge: {error_msg}')
        return ErrorResponse(error=f'Error getting entity edge: {error_msg}')


@mcp.tool()
async def get_episodes(
    group_ids: list[str] | None = None,
    max_episodes: int = 10,
    since: str | None = None,
    until: str | None = None,
) -> EpisodeSearchResponse | ErrorResponse:
    """Get recent episodes (raw stored content) from the knowledge graph.

    WHEN TO USE:
    - Retrieve workflow history chronologically
    - Get recent episodes to see what was stored
    - Review past problem-solving sessions
    - Access raw episode content (before entity extraction)
    - Get episodes in chronological order
    - Find episodes saved on a specific date or date range

    WHAT IT DOES:
    - Returns episodes sorted by creation time (most recent first)
    - Returns raw episode content, name, source, and metadata
    - Does NOT return extracted entities or relationships (use search_nodes for that)
    - Supports date range filtering via `since` and `until` parameters
    - Date filtering is done at the DATABASE level (Cypher query) — efficient for large graphs

    TIME-BASED TRIGGER MAPPING (CRITICAL for agents):
    When the user asks anything time-related, translate to since/until:

    User says "today"         → since=<today's date YYYY-MM-DD>
    User says "yesterday"     → since=<yesterday>, until=<today>
    User says "this week"     → since=<Monday of this week>
    User says "last week"     → since=<last Monday>, until=<this Monday>
    User says "this month"    → since=<YYYY-MM-01>
    User says "last month"    → since=<prev month 01>, until=<this month 01>
    User says "past N days"   → since=<today minus N days>
    User says "since Feb 10"  → since="2026-02-10"
    User says "between X and Y" → since=X, until=Y
    User says "recent"        → No date filter needed, just use max_episodes=10

    IMPORTANT: `since` is INCLUSIVE (>=), `until` is EXCLUSIVE (<).
    So since="2026-02-17", until="2026-02-17" returns NOTHING (zero-width range).
    For a single day: since="2026-02-17", until="2026-02-18".

    USE CASES:
    1. "What did I save today?" → get_episodes(since="2026-02-17")
    2. "Show last 10 episodes" → get_episodes(max_episodes=10)
    3. "What did I work on last week?" → get_episodes(since="2026-02-10", until="2026-02-17")
    4. "What did I save this month?" → get_episodes(since="2026-02-01")
    5. "Show me episodes from January" → get_episodes(since="2026-01-01", until="2026-02-01")
    6. "What problems did I log recently?" → get_episodes(max_episodes=20), then filter by name prefix

    WHEN NOT TO USE:
    - To find entities or relationships → Use search_nodes or search_memory_facts instead
    - To search by content/topic → Use search_nodes with a query instead
    - To find a specific memory by name → Use search_nodes

    BEST PRACTICES:
    - Use since/until for ANY time-based user request (pushes filter to database)
    - Combine with search_nodes when user wants both time AND topic filtering
    - Use max_episodes to limit results (default: 10)
    - Dates are YYYY-MM-DD format, always in UTC

    Args:
        group_ids (list[str], optional): Filter by group IDs. Defaults to configured group_id.
        max_episodes (int, optional): Maximum episodes to return, most recent first (default: 10)
        since (str, optional): Only return episodes created on or after this date. INCLUSIVE (>=). Format: YYYY-MM-DD.
        until (str, optional): Only return episodes created before this date. EXCLUSIVE (<). Format: YYYY-MM-DD.

    Examples:
        # Get episodes saved today
        get_episodes(since="2026-02-17")

        # Get episodes from a single specific day (note: until is next day)
        get_episodes(since="2026-02-15", until="2026-02-16")

        # Get episodes from the past week
        get_episodes(since="2026-02-10", until="2026-02-17")

        # Get episodes from January 2026
        get_episodes(since="2026-01-01", until="2026-02-01")

        # Get recent episodes (no date filter, just limit)
        get_episodes(max_episodes=5)

        # Combine date filter with limit
        get_episodes(since="2026-02-01", max_episodes=20)
    """
    global graphiti_service

    if graphiti_service is None:
        return ErrorResponse(error='Graphiti service not initialized')

    try:
        client = await graphiti_service.get_client()

        # Use the provided group_ids or fall back to the default from config if none provided
        effective_group_ids = (
            group_ids
            if group_ids is not None
            else [graphiti_service.config.graphiti.group_id]
            if graphiti_service.config.graphiti.group_id
            else []
        )

        # Get episodes from the driver directly
        from graphiti_core.nodes import EpisodicNode

        if not effective_group_ids:
            # If no group IDs, return empty list
            return EpisodeSearchResponse(message='No episodes found', episodes=[])

        # Validate date params early (before any DB queries)
        since_dt = None
        until_dt = None
        if since:
            try:
                since_dt = datetime.strptime(since, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                return ErrorResponse(error=f'Invalid since date format: {since}. Use YYYY-MM-DD.')
        if until:
            try:
                until_dt = datetime.strptime(until, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                return ErrorResponse(error=f'Invalid until date format: {until}. Use YYYY-MM-DD.')

        # Multi-group retrieval: clone driver for EACH group_id (each = separate FalkorDB database)
        all_episodes = []
        for group_id in effective_group_ids:
            # Clone driver to query the specific database for this group
            driver = client.driver.clone(database=group_id)
            try:
                if since_dt or until_dt:
                    # OPTIMIZED PATH: Push date filter into Cypher query
                    # FalkorDB stores created_at as ISO strings — lexicographic comparison works
                    since_iso = since_dt.isoformat() if since_dt else None
                    until_iso = until_dt.isoformat() if until_dt else None

                    # Build WHERE clauses dynamically
                    where_clauses = ['e.group_id IN $group_ids']
                    if since_iso:
                        where_clauses.append('e.created_at >= $since_iso')
                    if until_iso:
                        where_clauses.append('e.created_at < $until_iso')
                    where_str = ' AND '.join(where_clauses)

                    limit_clause = f'LIMIT {int(max_episodes)}' if max_episodes else ''

                    query = f"""
                        MATCH (e:Episodic)
                        WHERE {where_str}
                        RETURN DISTINCT
                            e.uuid AS uuid,
                            e.name AS name,
                            e.group_id AS group_id,
                            e.created_at AS created_at,
                            e.source AS source,
                            e.source_description AS source_description,
                            e.content AS content,
                            e.valid_at AS valid_at,
                            e.entity_edges AS entity_edges
                        ORDER BY e.created_at DESC
                        {limit_clause}
                    """

                    records, _, _ = await driver.execute_query(
                        query,
                        group_ids=[group_id],
                        since_iso=since_iso,
                        until_iso=until_iso,
                        routing_='r',
                    )
                    group_episodes = [get_episodic_node_from_record(r) for r in records]
                else:
                    # STANDARD PATH: No date filter, use library method
                    group_episodes = await EpisodicNode.get_by_group_ids(
                        driver, [group_id], limit=max_episodes
                    )
                all_episodes.extend(group_episodes)
            except Exception as e:
                logger.warning(f'Error retrieving episodes for group {group_id}: {str(e)}')
                # Continue with other groups even if one fails

        if not all_episodes:
            msg = 'No episodes found'
            if since or until:
                msg += f' for date range (since={since}, until={until})'
            return EpisodeSearchResponse(message=msg, episodes=[])

        # Sort aggregated episodes by created_at (most recent first) and apply limit
        all_episodes.sort(
            key=lambda e: e.created_at if e.created_at else datetime.min, reverse=True
        )
        episodes = all_episodes[:max_episodes]

        # Format the results
        episode_results = []
        for episode in episodes:
            episode_dict = {
                'uuid': episode.uuid,
                'name': episode.name,
                'content': episode.content,
                'created_at': episode.created_at.isoformat() if episode.created_at else None,
                'source': episode.source.value
                if hasattr(episode.source, 'value')
                else str(episode.source),
                'source_description': episode.source_description,
                'group_id': episode.group_id,
            }
            episode_results.append(episode_dict)

        return EpisodeSearchResponse(
            message='Episodes retrieved successfully', episodes=episode_results
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error getting episodes: {error_msg}')
        return ErrorResponse(error=f'Error getting episodes: {error_msg}')


@mcp.tool()
async def clear_graph(group_ids: list[str] | None = None) -> SuccessResponse | ErrorResponse:
    """Clear ALL data (episodes, entities, relationships) from the knowledge graph for specified groups.

    WHEN TO USE:
    - Start fresh with a clean knowledge graph
    - Remove all test data
    - Reset the graph for a specific group
    - Complete cleanup before re-indexing

    WHAT IT DOES:
    - Permanently deletes ALL episodes, entities, and relationships for specified group_ids
    - Rebuilds graph indices after clearing
    - Use with EXTREME CAUTION - all data is permanently lost
    - Cannot be undone

    USE CASES:
    1. Reset test environment: "Clear all test data"
    2. Start fresh: "Clear graph to rebuild from scratch"
    3. Clean up: "Remove all data for a specific group"

    WHEN NOT TO USE:
    - To delete specific episodes → Use delete_episode instead
    - To delete specific relationships → Use delete_entity_edge instead
    - For regular maintenance → Use specific delete tools instead

    BEST PRACTICES:
    - Only use when you want to completely reset the graph
    - Verify group_ids before clearing
    - Consider backing up important data first
    - Use specific delete tools for targeted cleanup

    Args:
        group_ids (list[str], optional): List of group IDs to clear. If not provided, clears the default group.
                                        WARNING: This permanently deletes ALL data for these groups.

    Examples:
        # Clear all data for a specific group
        clear_graph(group_ids=["developer_gyasisutton"])

        # Clear default group (use with caution!)
        clear_graph()
    """
    global graphiti_service

    if graphiti_service is None:
        return ErrorResponse(error='Graphiti service not initialized')

    try:
        client = await graphiti_service.get_client()

        # Use the provided group_ids or fall back to the default from config if none provided
        effective_group_ids = (
            group_ids or [graphiti_service.config.graphiti.group_id]
            if graphiti_service.config.graphiti.group_id
            else []
        )

        if not effective_group_ids:
            return ErrorResponse(error='No group IDs specified for clearing')

        # Multi-group clearing: clone driver for EACH group_id (each = separate FalkorDB database)
        cleared_groups = []
        errors = []
        for group_id in effective_group_ids:
            # Clone driver to clear the specific database for this group
            driver = client.driver.clone(database=group_id)
            try:
                # Clear data for this specific group
                await clear_data(driver, group_ids=[group_id])
                cleared_groups.append(group_id)
            except Exception as e:
                error_msg = f'{group_id}: {str(e)}'
                errors.append(error_msg)
                logger.error(f'Error clearing graph for group {group_id}: {str(e)}')

        # Report results
        if cleared_groups and not errors:
            return SuccessResponse(
                message=f'Graph data cleared successfully for group IDs: {", ".join(cleared_groups)}'
            )
        elif cleared_groups and errors:
            return SuccessResponse(
                message=f'Graph data partially cleared. Succeeded: {", ".join(cleared_groups)}. Failed: {"; ".join(errors)}'
            )
        else:
            return ErrorResponse(
                error=f'Failed to clear graph data for all groups. Errors: {"; ".join(errors)}'
            )
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error clearing graph: {error_msg}')
        return ErrorResponse(error=f'Error clearing graph: {error_msg}')


@mcp.tool()
async def get_status() -> StatusResponse:
    """Check the health and connection status of the Graphiti MCP server and database.

    WHEN TO USE:
    - Verify server is running and healthy
    - Check database connectivity
    - Diagnose connection issues
    - Health checks before operations
    - Troubleshooting: "Is the server working?"

    WHAT IT DOES:
    - Tests database connection with a simple query
    - Returns server status (ok/error)
    - Reports which database provider is connected
    - Provides error messages if connection fails

    USE CASES:
    1. Health check: "Is the server running?"
    2. Connection verification: "Can I connect to the database?"
    3. Troubleshooting: "Why can't I store memories?"
    4. Pre-operation check: "Verify server before storing data"

    WHEN NOT TO USE:
    - To search for data → Use search_nodes or search_memory_facts instead
    - To get data → Use other tools instead
    - For regular operations → Only use for health checks

    BEST PRACTICES:
    - Use when troubleshooting connection issues
    - Check status before important operations
    - Use error messages to diagnose problems

    Returns:
        StatusResponse with status ('ok' or 'error') and message describing the state

    Examples:
        # Check server status
        status = get_status()
        # Returns: {"status": "ok", "message": "Graphiti MCP server is running and connected to falkordb database"}
    """
    global graphiti_service

    if graphiti_service is None:
        return StatusResponse(status='error', message='Graphiti service not initialized')

    try:
        client = await graphiti_service.get_client()

        # Test database connection with a simple query
        # CRITICAL: Use driver.execute_query() instead of session.run()
        # - session.run() returns None on FalkorDB (violates CLAUDE.md best practice)
        # - driver.execute_query() works across all backends (FalkorDB, Neo4j, Kuzu, Neptune)
        # - driver.execute_query() returns tuple: (records, keys, metadata)
        group_id = graphiti_service.config.graphiti.group_id
        driver = client.driver.clone(database=group_id)
        result = await driver.execute_query('MATCH (n) RETURN count(n) as count')
        node_count = result[0][0]['count'] if result[0] else 0

        # Use the provider from the service's config, not the global
        provider_name = graphiti_service.config.database.provider
        return StatusResponse(
            status='ok',
            message=f'Graphiti MCP server is running and connected to {provider_name} database ({node_count} nodes)',
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f'Error checking database connection: {error_msg}')
        return StatusResponse(
            status='error',
            message=f'Graphiti MCP server is running but database connection failed: {error_msg}',
        )


@mcp.tool()
async def rate_limiter_status() -> dict[str, Any]:
    """Get rate limiter metrics and circuit breaker status.

    WHEN TO USE:
    - Check if rate limiting is active and working
    - Monitor retry counts and fallback usage
    - Diagnose API rate limit issues
    - Verify circuit breaker state

    WHAT IT DOES:
    - Returns metrics on API requests and retries
    - Shows circuit breaker state (closed/open/half-open)
    - Reports fallback usage statistics
    - Displays rate limiter configuration

    Returns:
        Dictionary with rate limiter status:
        {
            "enabled": bool,
            "metrics": {
                "request_count": int,
                "retry_count": int,
                "fallback_count": int,
                "last_request_time": str or None,
                "primary_circuit_state": str,
                "fallback_circuit_state": str or None
            },
            "config": {
                "requests_per_minute": int,
                "max_retry_wait": float,
                "circuit_threshold": int
            }
        }
    """
    rate_limiter = get_global_rate_limiter()

    if rate_limiter is None:
        return {
            'enabled': False,
            'message': 'Rate limiter not configured (using direct API calls)',
        }

    metrics = rate_limiter.get_metrics()

    return {
        'enabled': True,
        'metrics': metrics,
        'config': {
            'requests_per_minute': rate_limiter.config.requests_per_minute,
            'max_retry_wait': rate_limiter.config.max_retry_wait,
            'initial_wait': rate_limiter.config.initial_wait,
            'max_wait': rate_limiter.config.max_wait,
            'circuit_threshold': rate_limiter.config.circuit_threshold,
            'circuit_reset_timeout': rate_limiter.config.circuit_reset_timeout,
        },
    }


@mcp.tool()
async def get_async_task_status(task_id: str | None = None) -> dict[str, Any]:
    """[DEPRECATED] Get status of async fire-and-forget tasks.

    **DEPRECATION NOTICE:**
    This tool is deprecated as of FastMCP 2.x migration. The add_memory tool now uses
    FastMCP's native background task support with automatic agent notifications via
    ctx.info() and ctx.error(). Task status is managed by the MCP protocol automatically.

    This tool is retained for backward compatibility but will be removed in a future version.
    New code should NOT rely on this tool - progress updates are sent automatically via MCP.

    WHEN TO USE:
    - Legacy: Check on tasks submitted before the FastMCP 2.x migration
    - Note: New add_memory calls do NOT use this task manager

    Args:
        task_id: Specific task ID to check. If None, returns all pending/running tasks.

    Returns:
        Dictionary with task status information or deprecation warning
    """
    import warnings

    warnings.warn(
        'get_async_task_status is deprecated. add_memory now uses FastMCP native background tasks '
        'with automatic agent notifications. This tool will be removed in a future version.',
        DeprecationWarning,
        stacklevel=2,
    )
    logger.warning(
        'DEPRECATED: get_async_task_status called - this tool is deprecated with FastMCP 2.x'
    )

    task_manager = get_task_manager()

    if task_id:
        status = await task_manager.get_status(task_id)
        if status is None:
            return {'error': f'Task {task_id} not found'}
        return {
            'task_id': status.task_id,
            'name': status.name,
            'status': status.status,
            'created_at': status.created_at.isoformat(),
            'completed_at': status.completed_at.isoformat() if status.completed_at else None,
            'error': status.error,
        }

    # Return all pending/running tasks
    tasks = await task_manager.list_tasks()
    return {
        'tasks': [
            {
                'task_id': t.task_id,
                'name': t.name,
                'status': t.status,
                'created_at': t.created_at.isoformat(),
                'completed_at': t.completed_at.isoformat() if t.completed_at else None,
                'error': t.error,
            }
            for t in tasks
        ],
        'summary': {
            'total': len(tasks),
            'pending': len([t for t in tasks if t.status == 'pending']),
            'running': len([t for t in tasks if t.status == 'running']),
            'completed': len([t for t in tasks if t.status == 'completed']),
            'failed': len([t for t in tasks if t.status == 'failed']),
        },
    }


@mcp.tool()
async def graph_verifier() -> dict[str, Any] | ErrorResponse:
    """Verify graph state by showing recent additions and graph statistics.

    WHEN TO USE:
    - Verify that a memory was successfully saved
    - Check recent graph additions without searching
    - Get graph health statistics (total nodes, edges, episodes)
    - Quick sanity check: "Did my memory get stored?"
    - Avoid generic searches just to verify storage

    WHAT IT DOES:
    - Returns the 5 most recent episodes added to the graph
    - Shows total graph statistics (nodes, edges, episodes)
    - Includes entity and edge counts for recent episodes
    - Shows group distribution

    USE CASES:
    1. Post-save verification: "Did my memory X get saved?"
    2. Quick health check: "How many memories do I have?"
    3. Recent activity review: "What was recently added?"
    4. Graph statistics: "How big is my knowledge graph?"

    WHEN NOT TO USE:
    - To search for specific memories → Use search_nodes or search_memory_facts
    - To get old episodes → Use get_episodes with max_episodes
    - To search by content → Use search_nodes

    BEST PRACTICES:
    - Use after add_memory to verify storage
    - Use to check graph health periodically
    - Compare statistics before/after bulk operations

    Returns:
        Dictionary with recent additions and graph statistics:
        {
            "recent_additions": [
                {
                    "name": str,
                    "uuid": str,
                    "created_at": str,
                    "entity_count": int,
                    "edge_count": int,
                    "group_id": str
                }
            ],
            "statistics": {
                "total_nodes": int,
                "total_edges": int,
                "total_episodes": int,
                "groups": {group_id: count}
            }
        }

    Examples:
        # Verify recent memory storage
        result = graph_verifier()
        # Check if your memory appears in recent_additions

        # Check graph health
        result = graph_verifier()
        # Review total_nodes, total_edges, total_episodes
    """
    global graphiti_service

    if graphiti_service is None:
        return ErrorResponse(error='Graphiti service not initialized')

    try:
        client = await graphiti_service.get_client()

        # CRITICAL FIX: Graphiti uses group_id as the FalkorDB graph name!
        # When add_episode is called with group_id="developer_gyasisutton",
        # Graphiti clones the driver to use "developer_gyasisutton" as the database.
        # So we must query the correct graph, not the default config database.

        # Use the configured group_id to determine which graph to query
        group_id = graphiti_service.config.graphiti.group_id

        # Clone the driver to query the correct graph
        # For FalkorDB: group_id = graph name
        driver = client.driver.clone(database=group_id)

        # Query for recent episodes with entity/edge counts
        episode_query = """
        MATCH (e:Episodic)
        OPTIONAL MATCH (e)-[:MENTIONS]->(n:Entity)
        OPTIONAL MATCH (n)-[r:RELATES_TO]->()
        WITH e, count(DISTINCT n) as entity_count, count(DISTINCT r) as edge_count
        ORDER BY e.created_at DESC
        LIMIT 5
        RETURN e.uuid as uuid, e.name as name, e.created_at as created_at,
               e.group_id as group_id, entity_count, edge_count
        """
        result = await driver.execute_query(episode_query)
        logger.debug(f'graph_verifier: execute_query returned type={type(result)}, value={result}')
        episode_records = result[0] if result else []  # Extract records from tuple
        logger.debug(
            f'graph_verifier: episode_records type={type(episode_records)}, len={len(episode_records) if episode_records else 0}'
        )
        if episode_records:
            logger.debug(
                f'graph_verifier: first record type={type(episode_records[0])}, value={episode_records[0]}'
            )

        # FalkorDB returns records as DICTIONARIES (not lists!)
        # Keys: uuid, name, created_at, group_id, entity_count, edge_count
        recent_episodes = []
        for record in episode_records:
            try:
                # Handle created_at - may be string (FalkorDB) or datetime (Neo4j)
                created_at = record.get('created_at')
                if created_at:
                    if isinstance(created_at, str):
                        created_at_str = created_at
                    else:
                        created_at_str = created_at.isoformat()
                else:
                    created_at_str = None

                recent_episodes.append(
                    {
                        'uuid': record.get('uuid'),
                        'name': record.get('name', 'Unknown'),
                        'created_at': created_at_str,
                        'group_id': record.get('group_id'),
                        'entity_count': record.get('entity_count', 0) or 0,
                        'edge_count': record.get('edge_count', 0) or 0,
                    }
                )
            except Exception as record_error:
                logger.warning(f'Error processing episode record: {record_error}, record: {record}')
                continue

        # Get total statistics - use separate queries to avoid WITH clause issues in FalkorDB
        # Helper function to safely extract count from FalkorDB result
        def safe_get_count(result, key_name: str) -> int:
            """Safely extract count from FalkorDB query result.

            FalkorDB returns: (records, keys, metadata) tuple
            records is a list of DICTIONARIES, e.g., [{'total_nodes': 42}]
            """
            try:
                if not result:
                    return 0
                records = result[0] if result else []
                if not records or len(records) == 0:
                    return 0
                first_record = records[0]
                if not first_record:
                    return 0
                # FalkorDB returns dict, not list
                count_value = first_record.get(key_name)
                return int(count_value) if count_value is not None else 0
            except (TypeError, ValueError) as e:
                logger.warning(f'Error extracting count from result: {e}, result: {result}')
                return 0

        # Query 1: Count Entity nodes
        entity_count_query = """
        MATCH (n:Entity)
        RETURN count(n) as total_nodes
        """
        entity_result = await driver.execute_query(entity_count_query)
        total_nodes = safe_get_count(entity_result, 'total_nodes')

        # Query 2: Count RELATES_TO edges
        edge_count_query = """
        MATCH ()-[r:RELATES_TO]->()
        RETURN count(r) as total_edges
        """
        edge_result = await driver.execute_query(edge_count_query)
        total_edges = safe_get_count(edge_result, 'total_edges')

        # Query 3: Count Episodic nodes
        episode_count_query = """
        MATCH (e:Episodic)
        RETURN count(e) as total_episodes
        """
        episode_count_result = await driver.execute_query(episode_count_query)
        total_episodes = safe_get_count(episode_count_result, 'total_episodes')

        # Get group distribution
        group_query = """
        MATCH (e:Episodic)
        RETURN e.group_id as group_id, count(e) as count
        """
        group_result = await driver.execute_query(group_query)
        group_records = group_result[0] if group_result else []  # Extract records from tuple

        # FalkorDB returns records as DICTIONARIES (not lists!)
        # Keys: group_id, count
        group_distribution = {}
        for record in group_records:
            group_id = record.get('group_id') or 'unknown'
            group_distribution[group_id] = record.get('count', 0)

        # Build statistics dict
        statistics = {
            'total_nodes': total_nodes,
            'total_edges': total_edges,
            'total_episodes': total_episodes,
            'groups': group_distribution,
        }

        return {
            'recent_additions': recent_episodes,
            'statistics': statistics,
            'message': f'Graph verified: {statistics["total_episodes"]} episodes, {statistics["total_nodes"]} entities, {statistics["total_edges"]} relationships',
        }
    except Exception as e:
        import traceback

        error_msg = str(e)
        error_trace = traceback.format_exc()
        logger.error(f'Error verifying graph: {error_msg}')
        logger.error(f'Traceback: {error_trace}')
        return ErrorResponse(error=f'Error verifying graph: {error_msg}. See logs for details.')


@mcp.custom_route('/health', methods=['GET'])
async def health_check(request) -> JSONResponse:
    """Health check endpoint for Docker and load balancers."""
    return JSONResponse({'status': 'healthy', 'service': 'graphiti-mcp'})


async def initialize_server() -> ServerConfig:
    """Parse CLI arguments and initialize the Graphiti server configuration."""
    global config, graphiti_service, queue_service, graphiti_client, semaphore

    parser = argparse.ArgumentParser(
        description='Run the Graphiti MCP server with YAML configuration support'
    )

    # Configuration file argument
    # Default to config/config.yaml relative to the mcp_server directory
    default_config = Path(__file__).parent.parent / 'config' / 'config.yaml'
    parser.add_argument(
        '--config',
        type=Path,
        default=default_config,
        help='Path to YAML configuration file (default: config/config.yaml)',
    )

    # Transport arguments
    parser.add_argument(
        '--transport',
        choices=['sse', 'stdio', 'http'],
        help='Transport to use: http (recommended, default), stdio (standard I/O), or sse (deprecated)',
    )
    parser.add_argument(
        '--host',
        help='Host to bind the MCP server to',
    )
    parser.add_argument(
        '--port',
        type=int,
        help='Port to bind the MCP server to',
    )

    # Provider selection arguments
    parser.add_argument(
        '--llm-provider',
        choices=['openai', 'azure_openai', 'anthropic', 'gemini', 'groq'],
        help='LLM provider to use',
    )
    parser.add_argument(
        '--embedder-provider',
        choices=['openai', 'azure_openai', 'gemini', 'voyage'],
        help='Embedder provider to use',
    )
    parser.add_argument(
        '--database-provider',
        choices=['neo4j', 'falkordb'],
        help='Database provider to use',
    )

    # LLM configuration arguments
    parser.add_argument('--model', help='Model name to use with the LLM client')
    parser.add_argument('--small-model', help='Small model name to use with the LLM client')
    parser.add_argument(
        '--temperature', type=float, help='Temperature setting for the LLM (0.0-2.0)'
    )

    # Embedder configuration arguments
    parser.add_argument('--embedder-model', help='Model name to use with the embedder')

    # Graphiti-specific arguments
    parser.add_argument(
        '--group-id',
        help='Namespace for the graph. If not provided, uses config file or generates random UUID.',
    )
    parser.add_argument(
        '--user-id',
        help='User ID for tracking operations',
    )
    parser.add_argument(
        '--destroy-graph',
        action='store_true',
        help='Destroy all Graphiti graphs on startup',
    )

    args = parser.parse_args()

    # Set config path in environment for the settings to pick up
    if args.config:
        os.environ['CONFIG_PATH'] = str(args.config)

    # Load configuration with environment variables and YAML
    config = GraphitiConfig()

    # Apply CLI overrides
    config.apply_cli_overrides(args)

    # Also apply legacy CLI args for backward compatibility
    if hasattr(args, 'destroy_graph'):
        config.destroy_graph = args.destroy_graph

    # Log configuration details
    logger.info('Using configuration:')
    logger.info(f'  - LLM: {config.llm.provider} / {config.llm.model}')
    logger.info(f'  - Embedder: {config.embedder.provider} / {config.embedder.model}')
    logger.info(f'  - Database: {config.database.provider}')
    logger.info(f'  - Group ID: {config.graphiti.group_id}')
    logger.info(f'  - Transport: {config.server.transport}')

    # Log graphiti-core version
    try:
        import graphiti_core

        graphiti_version = getattr(graphiti_core, '__version__', 'unknown')
        logger.info(f'  - Graphiti Core: {graphiti_version}')
    except Exception:
        # Check for Docker-stored version file
        version_file = Path('/app/.graphiti-core-version')
        if version_file.exists():
            graphiti_version = version_file.read_text().strip()
            logger.info(f'  - Graphiti Core: {graphiti_version}')
        else:
            logger.info('  - Graphiti Core: version unavailable')

    # Handle graph destruction if requested
    if hasattr(config, 'destroy_graph') and config.destroy_graph:
        logger.warning('Destroying all Graphiti graphs as requested...')
        temp_service = GraphitiService(config, SEMAPHORE_LIMIT)
        await temp_service.initialize()
        client = await temp_service.get_client()
        await clear_data(client.driver)
        logger.info('All graphs destroyed')

    # Initialize services
    graphiti_service = GraphitiService(config, SEMAPHORE_LIMIT)
    queue_service = QueueService()
    await graphiti_service.initialize()

    # Set global client for backward compatibility
    graphiti_client = await graphiti_service.get_client()
    semaphore = graphiti_service.semaphore

    # Initialize queue service with the client
    await queue_service.initialize(graphiti_client)

    # Return MCP configuration for transport (host/port passed directly to run_async)
    return config.server


async def run_mcp_server():
    """Run the MCP server in the current event loop."""
    # Initialize the server
    mcp_config = await initialize_server()

    # Run the server with configured transport
    # FastMCP 2.x uses run_async() with transport parameter instead of separate methods
    logger.info(f'Starting MCP server with transport: {mcp_config.transport}')

    host = mcp_config.host or '127.0.0.1'
    port = mcp_config.port or 8000

    if mcp_config.transport == 'stdio':
        await mcp.run_async(transport='stdio')
    elif mcp_config.transport == 'sse':
        logger.info(f'Running MCP server with SSE transport on {host}:{port}')
        logger.info(f'Access the server at: http://{host}:{port}/sse')
        await mcp.run_async(transport='sse', host=host, port=port)
    elif mcp_config.transport == 'http':
        # Use localhost for display if binding to 0.0.0.0
        display_host = 'localhost' if host == '0.0.0.0' else host
        logger.info(f'Running MCP server with streamable HTTP transport on {host}:{port}')
        logger.info('=' * 60)
        logger.info('MCP Server Access Information:')
        logger.info(f'  Base URL: http://{display_host}:{port}/')
        logger.info(f'  MCP Endpoint: http://{display_host}:{port}/mcp/')
        logger.info('  Transport: HTTP (streamable)')

        # Show FalkorDB Browser UI access if enabled
        if os.environ.get('BROWSER', '1') == '1':
            logger.info(f'  FalkorDB Browser UI: http://{display_host}:3000/')

        logger.info('=' * 60)
        logger.info('For MCP clients, connect to the /mcp/ endpoint above')

        # Configure uvicorn logging to match our format
        configure_uvicorn_logging()

        await mcp.run_async(transport='http', host=host, port=port)
    else:
        raise ValueError(
            f'Unsupported transport: {mcp_config.transport}. Use "sse", "stdio", or "http"'
        )


def main():
    """Main function to run the Graphiti MCP server."""
    try:
        # Run everything in a single event loop
        asyncio.run(run_mcp_server())
    except KeyboardInterrupt:
        logger.info('Server shutting down...')
    except Exception as e:
        logger.error(f'Error initializing Graphiti MCP server: {str(e)}')
        raise


if __name__ == '__main__':
    main()

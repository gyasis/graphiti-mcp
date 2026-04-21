#!/usr/bin/env python3
"""
Debug script for graph_verifier tool failures.

This script directly queries FalkorDB to understand:
1. What format data is returned in
2. What the query results actually look like
3. Where the parsing might be failing

Run with: uv run python debug_graph_verifier.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
env_path = Path(__file__).parent / '.env'
logger.info(f"Loading environment from: {env_path}")
load_dotenv(dotenv_path=env_path)


async def debug_falkordb_queries():
    """Debug FalkorDB queries to understand data format."""

    try:
        # Import after loading env
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from graphiti_core.llm_client.openai_client import OpenAIClient
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.embedder.openai import OpenAIEmbedder
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        from openai import AsyncOpenAI

        logger.info("=" * 80)
        logger.info("FALKORDB DEBUG SCRIPT")
        logger.info("=" * 80)

        # Check environment
        logger.info("\n1. ENVIRONMENT CHECK")
        logger.info("-" * 80)
        required_vars = [
            'OPENAI_API_KEY',
            'FALKORDB_HOST',
            'FALKORDB_PORT',
            'FALKORDB_DATABASE'
        ]

        for var in required_vars:
            value = os.environ.get(var)
            if value:
                # Mask sensitive values
                if 'KEY' in var or 'PASSWORD' in var:
                    display_value = f"{value[:8]}..." if len(value) > 8 else "***"
                else:
                    display_value = value
                logger.info(f"  {var}: {display_value}")
            else:
                logger.warning(f"  {var}: NOT SET")

        # Get config values
        host = os.environ.get('FALKORDB_HOST', 'localhost')
        port = int(os.environ.get('FALKORDB_PORT', 6379))
        database = os.environ.get('FALKORDB_DATABASE', 'graphiti_memory')
        group_id = os.environ.get('GRAPHITI_GROUP_ID', 'developer_gyasisutton')

        logger.info(f"\n  Using FalkorDB connection:")
        logger.info(f"    Host: {host}")
        logger.info(f"    Port: {port}")
        logger.info(f"    Default Database: {database}")
        logger.info(f"    Group ID (database to query): {group_id}")

        # Create driver
        logger.info("\n2. DRIVER INITIALIZATION")
        logger.info("-" * 80)
        driver = FalkorDriver(host=host, port=port, database=database)
        logger.info(f"  Driver created: {driver}")
        logger.info(f"  Driver type: {type(driver)}")
        logger.info(f"  Driver database: {driver._database}")

        # Clone driver to use group_id as database
        logger.info(f"\n  Cloning driver with database={group_id}")
        cloned_driver = driver.clone(database=group_id)
        logger.info(f"  Cloned driver: {cloned_driver}")
        logger.info(f"  Cloned driver database: {cloned_driver._database}")

        # Test 1: Simple PING
        logger.info("\n3. SIMPLE COUNT QUERY TEST")
        logger.info("-" * 80)

        count_query = """
        MATCH (e:Episodic)
        RETURN count(e) as total_episodes
        """

        logger.info(f"  Query: {count_query.strip()}")
        result = await cloned_driver.execute_query(count_query)

        logger.info(f"\n  RAW RESULT:")
        logger.info(f"    Type: {type(result)}")
        logger.info(f"    Value: {result}")
        logger.info(f"    Length: {len(result) if result else 'N/A'}")

        if result:
            logger.info(f"\n  RESULT STRUCTURE:")
            for i, item in enumerate(result):
                logger.info(f"    result[{i}] type: {type(item)}")
                logger.info(f"    result[{i}] value: {item}")
                if isinstance(item, (list, tuple)):
                    logger.info(f"    result[{i}] length: {len(item)}")
                    if item:
                        for j, subitem in enumerate(item):
                            logger.info(f"      result[{i}][{j}] type: {type(subitem)}")
                            logger.info(f"      result[{i}][{j}] value: {subitem}")
                            if isinstance(subitem, (list, tuple)) and subitem:
                                for k, subsubitem in enumerate(subitem):
                                    logger.info(f"        result[{i}][{j}][{k}] type: {type(subsubitem)}")
                                    logger.info(f"        result[{i}][{j}][{k}] value: {subsubitem}")

        # Test 2: Episode query with aggregates
        logger.info("\n4. EPISODE QUERY WITH AGGREGATES")
        logger.info("-" * 80)

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

        logger.info(f"  Query: {episode_query.strip()}")

        try:
            result = await cloned_driver.execute_query(episode_query)

            logger.info(f"\n  RAW RESULT:")
            logger.info(f"    Type: {type(result)}")
            logger.info(f"    Value: {result}")
            logger.info(f"    Length: {len(result) if result else 'N/A'}")

            if result:
                logger.info(f"\n  RESULT STRUCTURE:")
                for i, item in enumerate(result):
                    logger.info(f"    result[{i}] type: {type(item)}")
                    logger.info(f"    result[{i}] value: {item}")
                    if isinstance(item, (list, tuple)):
                        logger.info(f"    result[{i}] length: {len(item)}")
                        if item:
                            logger.info(f"\n    FIRST RECORD:")
                            first_record = item[0]
                            logger.info(f"      Type: {type(first_record)}")
                            logger.info(f"      Value: {first_record}")
                            if isinstance(first_record, (list, tuple)):
                                logger.info(f"      Length: {len(first_record)}")
                                for j, field in enumerate(first_record):
                                    logger.info(f"        Field[{j}] type: {type(field)}")
                                    logger.info(f"        Field[{j}] value: {field}")

                # Try parsing as in graph_verifier
                logger.info(f"\n  PARSING TEST (as in graph_verifier):")
                episode_records = result[0] if result else []
                logger.info(f"    episode_records type: {type(episode_records)}")
                logger.info(f"    episode_records length: {len(episode_records) if episode_records else 'N/A'}")

                if episode_records:
                    for idx, record in enumerate(episode_records):
                        logger.info(f"\n    Record {idx}:")
                        logger.info(f"      Type: {type(record)}")
                        logger.info(f"      Length: {len(record) if hasattr(record, '__len__') else 'N/A'}")
                        logger.info(f"      Value: {record}")

                        if hasattr(record, '__len__') and len(record) >= 6:
                            logger.info(f"      Parsed fields:")
                            logger.info(f"        uuid: {record[0]}")
                            logger.info(f"        name: {record[1]}")
                            logger.info(f"        created_at: {record[2]} (type: {type(record[2])})")
                            logger.info(f"        group_id: {record[3]}")
                            logger.info(f"        entity_count: {record[4]}")
                            logger.info(f"        edge_count: {record[5]}")

        except Exception as e:
            logger.error(f"  ERROR during episode query: {e}")
            logger.error(f"  Exception type: {type(e)}")
            logger.error(f"  Exception str: {str(e)}")
            import traceback
            logger.error(f"  Traceback:\n{traceback.format_exc()}")

        # Test 3: Group distribution query
        logger.info("\n5. GROUP DISTRIBUTION QUERY")
        logger.info("-" * 80)

        group_query = """
        MATCH (e:Episodic)
        RETURN e.group_id as group_id, count(e) as count
        """

        logger.info(f"  Query: {group_query.strip()}")

        try:
            result = await cloned_driver.execute_query(group_query)

            logger.info(f"\n  RAW RESULT:")
            logger.info(f"    Type: {type(result)}")
            logger.info(f"    Value: {result}")

            if result:
                logger.info(f"\n  RESULT STRUCTURE:")
                for i, item in enumerate(result):
                    logger.info(f"    result[{i}] type: {type(item)}")
                    logger.info(f"    result[{i}] value: {item}")
                    if isinstance(item, (list, tuple)) and item:
                        logger.info(f"    result[{i}] length: {len(item)}")
                        for j, record in enumerate(item):
                            logger.info(f"      record[{j}] type: {type(record)}")
                            logger.info(f"      record[{j}] value: {record}")
                            if isinstance(record, (list, tuple)) and len(record) >= 2:
                                logger.info(f"        group_id: {record[0]}")
                                logger.info(f"        count: {record[1]}")

        except Exception as e:
            logger.error(f"  ERROR during group query: {e}")
            logger.error(f"  Exception type: {type(e)}")
            import traceback
            logger.error(f"  Traceback:\n{traceback.format_exc()}")

        # Test 4: Raw Redis commands
        logger.info("\n6. RAW REDIS/FALKORDB COMMANDS")
        logger.info("-" * 80)

        try:
            # Access underlying Redis client
            import redis.asyncio as redis_async

            redis_client = redis_async.Redis(host=host, port=port, decode_responses=True)

            # List all graphs
            logger.info("  Listing all graphs:")
            graphs = await redis_client.execute_command('GRAPH.LIST')
            logger.info(f"    Type: {type(graphs)}")
            logger.info(f"    Value: {graphs}")

            # Check if our graph exists
            if graphs and group_id in graphs:
                logger.info(f"\n  Graph '{group_id}' exists")

                # Get graph info
                logger.info(f"\n  Getting graph info for '{group_id}':")
                try:
                    # FalkorDB uses GRAPH.QUERY for Cypher queries
                    query = "MATCH (n) RETURN count(n) as total_nodes"
                    result = await redis_client.execute_command('GRAPH.QUERY', group_id, query)
                    logger.info(f"    Raw Redis result type: {type(result)}")
                    logger.info(f"    Raw Redis result value: {result}")
                except Exception as redis_error:
                    logger.error(f"    Error executing GRAPH.QUERY: {redis_error}")
            else:
                logger.warning(f"  Graph '{group_id}' NOT FOUND")
                logger.info(f"  Available graphs: {graphs}")

            await redis_client.close()

        except Exception as e:
            logger.error(f"  ERROR with raw Redis commands: {e}")
            import traceback
            logger.error(f"  Traceback:\n{traceback.format_exc()}")

        logger.info("\n" + "=" * 80)
        logger.info("DEBUG COMPLETE")
        logger.info("=" * 80)

        return True

    except Exception as e:
        logger.error(f"FATAL ERROR: {e}")
        logger.error(f"Exception type: {type(e)}")
        import traceback
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        return False


if __name__ == '__main__':
    try:
        success = asyncio.run(debug_falkordb_queries())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

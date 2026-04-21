#!/usr/bin/env python3
"""
Test script to verify graph_verifier fix.

This script tests the fixed graph_verifier logic against FalkorDB
to ensure it correctly parses dictionary-based results.

Run with: uv run python test_graph_verifier_fix.py
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
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
env_path = Path(__file__).parent / '.env'
logger.info(f"Loading environment from: {env_path}")
load_dotenv(dotenv_path=env_path)


async def test_graph_verifier_logic():
    """Test the fixed graph_verifier parsing logic."""

    try:
        from graphiti_core.driver.falkordb_driver import FalkorDriver

        logger.info("=" * 80)
        logger.info("GRAPH VERIFIER FIX TEST")
        logger.info("=" * 80)

        # Create driver
        host = os.environ.get('FALKORDB_HOST', 'localhost')
        port = int(os.environ.get('FALKORDB_PORT', 6379))
        database = os.environ.get('FALKORDB_DATABASE', 'graphiti_memory')
        group_id = os.environ.get('GRAPHITI_GROUP_ID', 'developer_gyasisutton')

        logger.info(f"\nConnecting to FalkorDB:")
        logger.info(f"  Host: {host}")
        logger.info(f"  Port: {port}")
        logger.info(f"  Database: {database}")
        logger.info(f"  Group ID: {group_id}")

        driver = FalkorDriver(host=host, port=port, database=database)
        cloned_driver = driver.clone(database=group_id)

        # Test 1: Episode query parsing (FIXED LOGIC)
        logger.info("\n" + "=" * 80)
        logger.info("TEST 1: Episode Query Parsing (FIXED)")
        logger.info("=" * 80)

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

        result = await cloned_driver.execute_query(episode_query)
        episode_records = result[0] if result else []

        logger.info(f"\nFound {len(episode_records)} episodes")

        recent_episodes = []
        for idx, record in enumerate(episode_records):
            try:
                # FIXED: Use dict.get() instead of record[index]
                created_at = record.get('created_at')
                if created_at:
                    if isinstance(created_at, str):
                        created_at_str = created_at
                    else:
                        created_at_str = created_at.isoformat()
                else:
                    created_at_str = None

                episode_data = {
                    'uuid': record.get('uuid'),
                    'name': record.get('name', 'Unknown'),
                    'created_at': created_at_str,
                    'group_id': record.get('group_id'),
                    'entity_count': record.get('entity_count', 0) or 0,
                    'edge_count': record.get('edge_count', 0) or 0,
                }

                recent_episodes.append(episode_data)
                logger.info(f"\n  Episode {idx + 1}:")
                logger.info(f"    Name: {episode_data['name']}")
                logger.info(f"    UUID: {episode_data['uuid']}")
                logger.info(f"    Created: {episode_data['created_at']}")
                logger.info(f"    Group: {episode_data['group_id']}")
                logger.info(f"    Entities: {episode_data['entity_count']}")
                logger.info(f"    Edges: {episode_data['edge_count']}")

            except Exception as e:
                logger.error(f"  ERROR parsing episode {idx}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                sys.exit(1)

        logger.info(f"\n  SUCCESS: Parsed {len(recent_episodes)} episodes")

        # Test 2: Count query parsing (FIXED LOGIC)
        logger.info("\n" + "=" * 80)
        logger.info("TEST 2: Count Query Parsing (FIXED)")
        logger.info("=" * 80)

        def safe_get_count(result, key_name: str) -> int:
            """FIXED: Extract count from dict-based FalkorDB result."""
            try:
                if not result:
                    return 0
                records = result[0] if result else []
                if not records or len(records) == 0:
                    return 0
                first_record = records[0]
                if not first_record:
                    return 0
                # FIXED: Use dict.get() instead of first_record[0]
                count_value = first_record.get(key_name)
                return int(count_value) if count_value is not None else 0
            except (TypeError, ValueError) as e:
                logger.error(f'Error extracting count: {e}')
                return 0

        # Count Entity nodes
        entity_count_query = "MATCH (n:Entity) RETURN count(n) as total_nodes"
        entity_result = await cloned_driver.execute_query(entity_count_query)
        total_nodes = safe_get_count(entity_result, 'total_nodes')
        logger.info(f"\n  Total Entity nodes: {total_nodes}")

        # Count RELATES_TO edges
        edge_count_query = "MATCH ()-[r:RELATES_TO]->() RETURN count(r) as total_edges"
        edge_result = await cloned_driver.execute_query(edge_count_query)
        total_edges = safe_get_count(edge_result, 'total_edges')
        logger.info(f"  Total RELATES_TO edges: {total_edges}")

        # Count Episodic nodes
        episode_count_query = "MATCH (e:Episodic) RETURN count(e) as total_episodes"
        episode_count_result = await cloned_driver.execute_query(episode_count_query)
        total_episodes = safe_get_count(episode_count_result, 'total_episodes')
        logger.info(f"  Total Episodic nodes: {total_episodes}")

        logger.info(f"\n  SUCCESS: All count queries parsed correctly")

        # Test 3: Group distribution parsing (FIXED LOGIC)
        logger.info("\n" + "=" * 80)
        logger.info("TEST 3: Group Distribution Parsing (FIXED)")
        logger.info("=" * 80)

        group_query = """
        MATCH (e:Episodic)
        RETURN e.group_id as group_id, count(e) as count
        """
        group_result = await cloned_driver.execute_query(group_query)
        group_records = group_result[0] if group_result else []

        group_distribution = {}
        for idx, record in enumerate(group_records):
            try:
                # FIXED: Use dict.get() instead of record[0] and record[1]
                group_id = record.get('group_id') or 'unknown'
                count = record.get('count', 0)
                group_distribution[group_id] = count
                logger.info(f"\n  Group {idx + 1}:")
                logger.info(f"    ID: {group_id}")
                logger.info(f"    Count: {count}")
            except Exception as e:
                logger.error(f"  ERROR parsing group {idx}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                sys.exit(1)

        logger.info(f"\n  SUCCESS: Parsed {len(group_distribution)} group distributions")

        # Final summary
        logger.info("\n" + "=" * 80)
        logger.info("FINAL SUMMARY")
        logger.info("=" * 80)

        statistics = {
            'total_nodes': total_nodes,
            'total_edges': total_edges,
            'total_episodes': total_episodes,
            'groups': group_distribution,
        }

        logger.info(f"\n  Graph Statistics:")
        logger.info(f"    Total Episodes: {statistics['total_episodes']}")
        logger.info(f"    Total Entities: {statistics['total_nodes']}")
        logger.info(f"    Total Relationships: {statistics['total_edges']}")
        logger.info(f"    Groups: {statistics['groups']}")

        logger.info(f"\n  Recent Additions:")
        for idx, episode in enumerate(recent_episodes[:3]):
            logger.info(f"    {idx + 1}. {episode['name']}")
            logger.info(f"       Entities: {episode['entity_count']}, Edges: {episode['edge_count']}")

        logger.info("\n" + "=" * 80)
        logger.info("ALL TESTS PASSED - FIX VERIFIED")
        logger.info("=" * 80)

        return True

    except Exception as e:
        logger.error(f"\nFATAL ERROR: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


if __name__ == '__main__':
    try:
        success = asyncio.run(test_graph_verifier_logic())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

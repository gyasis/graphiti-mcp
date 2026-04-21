#!/usr/bin/env python3
"""
Test the graph_verifier MCP tool directly.

This simulates calling the graph_verifier tool through the MCP server.

Run with: uv run python test_mcp_graph_verifier.py
"""

import asyncio
import logging
import sys
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


async def test_mcp_graph_verifier():
    """Test the graph_verifier MCP tool."""

    try:
        # Import the MCP server module
        import sys
        sys.path.insert(0, str(Path(__file__).parent / 'src'))

        from graphiti_mcp_server import graph_verifier, initialize_server

        logger.info("=" * 80)
        logger.info("MCP GRAPH VERIFIER TEST")
        logger.info("=" * 80)

        # Initialize the server
        logger.info("\nInitializing MCP server...")
        import argparse

        # Mock the command-line args
        sys.argv = [
            'test_mcp_graph_verifier.py',
            '--config', 'config/config-litellm-azure.yaml',
            '--transport', 'stdio'
        ]

        config = await initialize_server()
        logger.info(f"Server initialized with config: {config}")

        # Call the graph_verifier tool
        logger.info("\n" + "=" * 80)
        logger.info("Calling graph_verifier tool...")
        logger.info("=" * 80)

        result = await graph_verifier()

        logger.info(f"\nResult type: {type(result)}")
        logger.info(f"Result: {result}")

        # Check if result is an error
        if hasattr(result, 'error'):
            logger.error(f"\nERROR: {result.error}")
            return False

        # Validate result structure
        if isinstance(result, dict):
            logger.info("\n" + "=" * 80)
            logger.info("GRAPH VERIFIER RESULTS")
            logger.info("=" * 80)

            # Check statistics
            if 'statistics' in result:
                stats = result['statistics']
                logger.info(f"\nGraph Statistics:")
                logger.info(f"  Total Episodes: {stats.get('total_episodes', 0)}")
                logger.info(f"  Total Entities: {stats.get('total_nodes', 0)}")
                logger.info(f"  Total Relationships: {stats.get('total_edges', 0)}")
                logger.info(f"  Groups: {stats.get('groups', {})}")

            # Check recent additions
            if 'recent_additions' in result:
                recent = result['recent_additions']
                logger.info(f"\nRecent Additions ({len(recent)} episodes):")
                for idx, episode in enumerate(recent[:3]):
                    logger.info(f"  {idx + 1}. {episode.get('name', 'Unknown')}")
                    logger.info(f"     UUID: {episode.get('uuid', 'N/A')}")
                    logger.info(f"     Entities: {episode.get('entity_count', 0)}, Edges: {episode.get('edge_count', 0)}")
                    logger.info(f"     Created: {episode.get('created_at', 'N/A')}")

            # Check message
            if 'message' in result:
                logger.info(f"\nMessage: {result['message']}")

            logger.info("\n" + "=" * 80)
            logger.info("MCP GRAPH VERIFIER TEST PASSED")
            logger.info("=" * 80)
            return True

        else:
            logger.error(f"\nUnexpected result type: {type(result)}")
            logger.error(f"Result: {result}")
            return False

    except Exception as e:
        logger.error(f"\nFATAL ERROR: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


if __name__ == '__main__':
    try:
        success = asyncio.run(test_mcp_graph_verifier())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

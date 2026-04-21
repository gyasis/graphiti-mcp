#!/usr/bin/env python3
"""
Test to understand how Graphiti maps group_id to FalkorDB graphs.
This will help diagnose the data persistence / graph selection issue.
"""
import asyncio
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# Import required modules
from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.llm_client.azure_openai_client import AzureOpenAILLMClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.azure_openai import AzureOpenAIEmbedderClient
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.nodes import EpisodeType
from openai import AsyncOpenAI
import redis
from falkordb import FalkorDB

async def main():
    print("=" * 80)
    print("GRAPHITI GROUP_ID TO FALKORDB GRAPH MAPPING TEST")
    print("=" * 80)

    # Setup Graphiti client
    azure_endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
    azure_api_key = os.environ.get('AZURE_OPENAI_API_KEY')
    azure_deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT')
    azure_embedding = os.environ.get('AZURE_OPENAI_EMBEDDING_DEPLOYMENT')

    # Create FalkorDB driver with EXPLICIT database name
    database_name = "test_mapping_database"
    print(f"\n1. Creating FalkorDriver with database='{database_name}'")
    driver = FalkorDriver(
        host='localhost',
        port=6379,
        database=database_name  # This should determine which graph to use
    )

    # Create LLM client
    azure_client = AsyncOpenAI(
        base_url=f'{azure_endpoint}/openai/v1/',
        api_key=azure_api_key
    )
    llm_config = LLMConfig(model=azure_deployment, small_model=azure_deployment)
    llm_client = AzureOpenAILLMClient(azure_client=azure_client, config=llm_config)
    embedder = AzureOpenAIEmbedderClient(azure_client=azure_client, model=azure_embedding)
    cross_encoder = OpenAIRerankerClient(client=llm_client, config=llm_config)

    # Create Graphiti instance
    graphiti = Graphiti(
        graph_driver=driver,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=cross_encoder
    )

    # Test 1: Add episode with group_id="test_group_a"
    print(f"\n2. Adding episode with group_id='test_group_a'")
    print(f"   Expected graph: '{database_name}'")
    result_a = await graphiti.add_episode(
        name="Test Episode A",
        episode_body="This is test episode A to verify graph mapping.",
        source_description="Mapping test A",
        reference_time=datetime.now(timezone.utc),
        source=EpisodeType.text,
        group_id="test_group_a"
    )
    print(f"   Result: {len(result_a.nodes)} nodes, {len(result_a.edges)} edges created")

    # Test 2: Add episode with group_id="test_group_b"
    print(f"\n3. Adding episode with group_id='test_group_b'")
    print(f"   Expected graph: '{database_name}'")
    result_b = await graphiti.add_episode(
        name="Test Episode B",
        episode_body="This is test episode B to verify graph mapping.",
        source_description="Mapping test B",
        reference_time=datetime.now(timezone.utc),
        source=EpisodeType.text,
        group_id="test_group_b"
    )
    print(f"   Result: {len(result_b.nodes)} nodes, {len(result_b.edges)} edges created")

    # Now check which graphs actually have the data
    print(f"\n4. Checking FalkorDB graphs for the data...")
    db = FalkorDB(host='localhost', port=6379)
    r = redis.Redis(host='localhost', port=6379, decode_responses=True)
    graphs = r.execute_command('GRAPH.LIST')

    print(f"\n   All graphs in FalkorDB: {graphs}")

    # Check each graph for our test episodes
    for graph_name in graphs:
        graph = db.select_graph(graph_name)
        try:
            result = graph.query("MATCH (e:Episodic) WHERE e.name STARTS WITH 'Test Episode' RETURN e.name, e.group_id")
            if result.result_set:
                print(f"\n   [{graph_name}] Found test episodes:")
                for row in result.result_set:
                    print(f"      - {row[0]} (group_id: {row[1]})")
            else:
                print(f"\n   [{graph_name}] No test episodes")
        except Exception as e:
            print(f"\n   [{graph_name}] Error: {e}")

    print("\n" + "=" * 80)
    print("CONCLUSION:")
    print("=" * 80)
    print(f"Driver configured with database='{database_name}'")
    print("Check above to see which graph(s) actually received the data.")
    print("If data went to 'test_group_a' or 'test_group_b' graphs instead of")
    print(f"'{database_name}', then Graphiti is using group_id to select graphs!")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())

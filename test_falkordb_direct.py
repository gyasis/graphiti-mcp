#!/usr/bin/env python3
"""
Direct FalkorDB test script to diagnose data persistence issues.
Tests connection and queries the database directly without using Graphiti.
"""
import redis
from falkordb import FalkorDB

def test_redis_connection():
    """Test basic Redis connection"""
    print("=" * 60)
    print("Testing Redis Connection")
    print("=" * 60)
    try:
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        ping = r.ping()
        print(f"✅ Redis PING: {ping}")

        # Get all keys
        keys = r.keys('*')
        print(f"\n📊 Total keys in Redis: {len(keys)}")
        if keys:
            print(f"Keys: {keys[:10]}")  # Show first 10 keys
        return True
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        return False

def test_falkordb_graphs():
    """Test FalkorDB graphs"""
    print("\n" + "=" * 60)
    print("Testing FalkorDB Graphs")
    print("=" * 60)
    try:
        db = FalkorDB(host='localhost', port=6379)

        # List all graphs
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        graphs = r.execute_command('GRAPH.LIST')
        print(f"\n📊 Total graphs: {len(graphs) if graphs else 0}")

        if graphs:
            print(f"Graphs: {graphs}")

            # Check each graph
            for graph_name in graphs:
                print(f"\n--- Graph: {graph_name} ---")
                graph = db.select_graph(graph_name)

                # Count nodes
                try:
                    node_result = graph.query("MATCH (n) RETURN count(n) as count")
                    node_count = node_result.result_set[0][0] if node_result.result_set else 0
                    print(f"  Nodes: {node_count}")
                except Exception as e:
                    print(f"  Error counting nodes: {e}")

                # Count relationships
                try:
                    rel_result = graph.query("MATCH ()-[r]->() RETURN count(r) as count")
                    rel_count = rel_result.result_set[0][0] if rel_result.result_set else 0
                    print(f"  Relationships: {rel_count}")
                except Exception as e:
                    print(f"  Error counting relationships: {e}")

                # Get sample of Episodic nodes
                try:
                    episode_result = graph.query("MATCH (e:Episodic) RETURN e.name, e.uuid LIMIT 5")
                    if episode_result.result_set:
                        print(f"  Sample Episodes:")
                        for row in episode_result.result_set:
                            print(f"    - {row[0]} ({row[1]})")
                    else:
                        print(f"  No Episodic nodes found")
                except Exception as e:
                    print(f"  Error querying episodes: {e}")

                # Get sample of Entity nodes
                try:
                    entity_result = graph.query("MATCH (n:Entity) RETURN labels(n), n.name LIMIT 5")
                    if entity_result.result_set:
                        print(f"  Sample Entities:")
                        for row in entity_result.result_set:
                            print(f"    - {row[1]} ({row[0]})")
                    else:
                        print(f"  No Entity nodes found")
                except Exception as e:
                    print(f"  Error querying entities: {e}")
        else:
            print("❌ No graphs found in FalkorDB")

        return True
    except Exception as e:
        print(f"❌ FalkorDB test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("FalkorDB Direct Connection Test")
    print("=" * 60)

    redis_ok = test_redis_connection()
    if redis_ok:
        test_falkordb_graphs()

    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)

if __name__ == "__main__":
    main()

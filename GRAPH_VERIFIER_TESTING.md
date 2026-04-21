# Graph Verifier Testing Guide

## Quick Test (Automated)

Run the comprehensive test script:

```bash
cd /home/gyasisutton/dev/tools/graphiti-mcp
uv run python test_graph_verifier_fix.py
```

Expected output:
```
🔧 Initializing Graphiti with FalkorDB...
✅ Graphiti initialized

📊 Test 1: Empty graph statistics
--------------------------------------------------
Cleared test graph

🔍 Testing empty graph queries...
  Entity count: 0
  Edge count: 0
  Episode count: 0
✅ Empty graph queries succeeded!

📊 Test 2: Graph with one episode
--------------------------------------------------
Added episode
  Entity count: 2
  Edge count: 1
  Episode count: 1
✅ Graph with data queries succeeded!

📊 Test 3: Recent episodes query
--------------------------------------------------
  Found 1 recent episodes
    Episode 1:
      Name: Test Episode 1
      Entities: 2
      Edges: 1
✅ Recent episodes query succeeded!

🧹 Cleaning up test graph...
✅ Cleanup complete!

==================================================
🎉 All tests passed! graph_verifier fix is working correctly.
==================================================
```

## Manual Testing with MCP Client

### Setup

1. Ensure FalkorDB is running:
```bash
docker compose up -d
docker exec falkordb-graphiti redis-cli PING
# Should return: PONG
```

2. Restart MCP server (if using Claude Code):
- Use Command Palette (Ctrl+Shift+P)
- Select "MCP: Restart Servers"
- Or restart Claude Code

### Test 1: Empty Graph

1. Clear your graph:
```
Use the clear_graph tool to reset the test graph
```

2. Run verifier:
```
Use the graph_verifier tool
```

Expected result:
```json
{
  "recent_additions": [],
  "statistics": {
    "total_nodes": 0,
    "total_edges": 0,
    "total_episodes": 0,
    "groups": {}
  },
  "message": "Graph verified: 0 episodes, 0 entities, 0 relationships"
}
```

### Test 2: Single Episode

1. Add a memory:
```
Use add_memory to store a test episode:
- name: "Test Episode"
- episode_body: "Testing the graph verifier with Python and FalkorDB"
```

2. Run verifier:
```
Use the graph_verifier tool
```

Expected result:
```json
{
  "recent_additions": [
    {
      "uuid": "<uuid>",
      "name": "Test Episode",
      "created_at": "2025-12-26T...",
      "group_id": "developer_gyasisutton",
      "entity_count": 2,
      "edge_count": 0
    }
  ],
  "statistics": {
    "total_nodes": 2,
    "total_edges": 0,
    "total_episodes": 1,
    "groups": {
      "developer_gyasisutton": 1
    }
  },
  "message": "Graph verified: 1 episodes, 2 entities, 0 relationships"
}
```

### Test 3: Multiple Episodes

1. Add more memories (3-5 episodes)

2. Run verifier:
```
Use the graph_verifier tool
```

Expected result:
- `recent_additions` should show last 5 episodes (or fewer if < 5 total)
- `statistics.total_episodes` should match total count
- `statistics.total_nodes` should be > 0
- `statistics.total_edges` should be > 0 (if entities are related)

### Test 4: Verify After Search

1. Use `search_nodes` to find something

2. Run verifier:
```
Use the graph_verifier tool
```

Expected result:
- Tool should still work (not affected by previous operations)
- Statistics should be consistent

## Regression Testing

Test the fix doesn't break existing functionality:

### Test Case 1: Recent Additions Ordering

1. Add 3 episodes in sequence:
   - Episode A (wait 1 second)
   - Episode B (wait 1 second)
   - Episode C

2. Run verifier

3. Verify:
   - `recent_additions[0]` is Episode C (most recent)
   - `recent_additions[1]` is Episode B
   - `recent_additions[2]` is Episode A

### Test Case 2: Group Distribution

1. Clear graph

2. Add episodes with different group_ids (if supported):
   - 2 episodes with `group_id="test1"`
   - 3 episodes with `group_id="test2"`

3. Run verifier

4. Verify:
   - `statistics.groups["test1"]` = 2
   - `statistics.groups["test2"]` = 3

### Test Case 3: Entity/Edge Counts

1. Add episode with multiple entities:
```
episode_body: "Python is a programming language. FalkorDB is a graph database. Python can connect to FalkorDB."
```

2. Run verifier

3. Verify:
   - `entity_count` > 0 in recent_additions
   - `edge_count` > 0 if relationships were extracted
   - Counts match expected extraction

## Error Scenario Testing

### Test 1: Database Connection Loss

1. Stop FalkorDB:
```bash
docker compose down
```

2. Run verifier

Expected:
- Clear error message (not "2")
- Error mentions connection issue
- Tool doesn't crash MCP server

3. Restart FalkorDB:
```bash
docker compose up -d
```

### Test 2: Invalid Group ID

1. Try to verify with non-existent group_id (if tool supports it)

Expected:
- Empty results (0 counts)
- No crashes
- Clear message

### Test 3: Corrupted Data

1. Manually create incomplete episode (via direct Cypher):
```cypher
CREATE (:Episodic {uuid: "test-incomplete"})
```

2. Run verifier

Expected:
- Tool completes successfully
- Warning logged for incomplete record
- Other valid episodes still shown

## Performance Testing

### Test 1: Empty Graph Performance

1. Clear graph
2. Time the verifier execution
3. Expected: < 100ms

### Test 2: Small Graph Performance (10 episodes)

1. Add 10 episodes
2. Time the verifier execution
3. Expected: < 200ms

### Test 3: Large Graph Performance (100 episodes)

1. Add 100 episodes
2. Time the verifier execution
3. Expected: < 500ms

Note: Only recent 5 episodes are fetched, so performance should be relatively constant.

## Debugging Failed Tests

### If verifier returns empty results:

1. Check FalkorDB is running:
```bash
docker ps | grep falkordb
```

2. Check graph has data:
```bash
docker exec falkordb-graphiti redis-cli GRAPH.QUERY developer_gyasisutton "MATCH (n) RETURN count(n)"
```

3. Check logs for errors:
```bash
# Check MCP server logs (Claude Code or Cursor)
# Look for "Error verifying graph:" messages
```

### If verifier returns error:

1. Check error message for clues

2. Look at full traceback in logs:
```bash
# MCP server logs will have full stack trace
# Search for "Traceback:" after "Error verifying graph:"
```

3. Verify Cypher queries work directly:
```bash
docker exec falkordb-graphiti redis-cli GRAPH.QUERY developer_gyasisutton "MATCH (e:Episodic) RETURN count(e)"
```

### If counts seem wrong:

1. Manually verify with Cypher:
```bash
# Count entities
docker exec falkordb-graphiti redis-cli GRAPH.QUERY developer_gyasisutton "MATCH (n:Entity) RETURN count(n)"

# Count edges
docker exec falkordb-graphiti redis-cli GRAPH.QUERY developer_gyasisutton "MATCH ()-[r:RELATES_TO]->() RETURN count(r)"

# Count episodes
docker exec falkordb-graphiti redis-cli GRAPH.QUERY developer_gyasisutton "MATCH (e:Episodic) RETURN count(e)"
```

2. Compare with verifier results

## Success Criteria

The fix is successful if:

- ✅ Verifier works on empty graph (returns 0 counts)
- ✅ Verifier works on sparse graph (episodes but few entities)
- ✅ Verifier works on full graph (many episodes, entities, relationships)
- ✅ Error messages are clear and helpful (not "2")
- ✅ Recent additions are sorted correctly (newest first)
- ✅ Statistics are accurate
- ✅ Performance is acceptable (< 500ms)
- ✅ No crashes or IndexError exceptions
- ✅ Logs provide useful debugging info

## Reporting Issues

If you encounter issues:

1. Note the exact error message
2. Check MCP server logs for full traceback
3. Note the graph state (empty, sparse, full)
4. Try to reproduce with test script
5. Report with:
   - Error message
   - Traceback from logs
   - Graph state
   - Steps to reproduce

## Next Steps After Testing

Once all tests pass:

1. Update CHANGELOG.md with fix details
2. Consider adding automated regression tests
3. Monitor production usage for edge cases
4. Document any additional findings

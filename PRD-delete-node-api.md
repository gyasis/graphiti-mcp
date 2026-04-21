# PRD: `delete_node` API for Graphiti

**Author:** Gyasi Sutton
**Date:** 2026-02-25
**Status:** Draft
**Priority:** High — blocks memory hygiene operations

## Problem Statement

Graphiti's current deletion API has a critical gap: **there is no way to delete an individual entity node**.

### Current API Surface

| Tool | What It Deletes | Limitation |
|------|----------------|------------|
| `delete_episode` | Episode + edges created by it + nodes mentioned ONLY by it | Nodes mentioned by 2+ episodes survive |
| `delete_entity_edge` | A single edge between two nodes | Leaves both nodes intact |
| `clear_graph` | Everything for a group | Nuclear option — destroys all data |

### The Orphaned Node Problem

When `delete_episode` runs, it checks each mentioned node:

```python
# graphiti_core/graphiti.py:1252-1258
for node in nodes:
    query = 'MATCH (e:Episodic)-[:MENTIONS]->(n:Entity {uuid: $uuid}) RETURN count(*) AS episode_count'
    records, _, _ = await self.driver.execute_query(query, uuid=node.uuid, routing_='r')
    for record in records:
        if record['episode_count'] == 1:
            nodes_to_delete.append(node)
```

Nodes with `episode_count > 1` are **preserved**, even if all their source episodes are later deleted one-by-one. This creates **orphaned nodes** that:
- Still appear in `search_nodes` results (semantic search hits their summary)
- Have stale/incorrect summaries that mislead agents
- Cannot be removed without `clear_graph` (nuclear option)
- Accumulate over time, degrading search quality

### Real-World Impact (2026-02-25)

During a memory system overhaul, 5 stale workflow nodes needed deletion:
- "At session end or before compact run get_async_task_status" (old polling pattern — replaced by MCP auto-notifications)
- "Context compression prevention strategy" (replaced by automated hooks)
- "FILE BASED MEMORY" (merged into unified memory system)
- "TODO md Externalize task list" (replaced by session.json)
- "memory search" (old auto-activation rules — replaced by orchestrator hook)

**Result:** 3 source episodes deleted, but 8+ orphaned nodes persisted because they were referenced by multiple episodes. The stale nodes continue to appear in `search_nodes` results, potentially misleading future agent sessions.

## Proposed Solution

### New API: `delete_node`

Add a `remove_node` method to `Graphiti` class and expose it as `delete_node` MCP tool.

### Core Library Change (`graphiti_core/graphiti.py`)

```python
async def remove_node(self, entity_node_uuid: str, cascade: bool = False):
    """Delete an entity node and clean up its relationships.

    Args:
        entity_node_uuid: UUID of the EntityNode to delete.
        cascade: If True, also delete edges where this node is source or target.
                 If False (default), only delete the node if it has no remaining edges.

    Raises:
        NodeHasEdgesError: If cascade=False and node still has connected edges.
        NodeNotFoundError: If node UUID doesn't exist.
    """
    # 1. Find the entity node
    node = await EntityNode.get_by_uuid(self.driver, entity_node_uuid)

    # 2. Find all edges connected to this node (as source or target)
    query: LiteralString = """
        MATCH (n:Entity {uuid: $uuid})-[r]-(e:Entity)
        WHERE r.uuid IS NOT NULL
        RETURN r.uuid AS edge_uuid
    """
    records, _, _ = await self.driver.execute_query(
        query, uuid=entity_node_uuid, routing_='r'
    )
    connected_edge_uuids = [r['edge_uuid'] for r in records]

    # 3. Handle edges based on cascade flag
    if connected_edge_uuids and not cascade:
        raise NodeHasEdgesError(
            f"Node {entity_node_uuid} has {len(connected_edge_uuids)} connected edges. "
            f"Use cascade=True to delete them, or delete edges first."
        )

    if connected_edge_uuids and cascade:
        await Edge.delete_by_uuids(self.driver, connected_edge_uuids)

    # 4. Remove MENTIONS relationships from episodic nodes
    query: LiteralString = """
        MATCH (e:Episodic)-[m:MENTIONS]->(n:Entity {uuid: $uuid})
        DELETE m
    """
    await self.driver.execute_query(query, uuid=entity_node_uuid)

    # 5. Remove from community memberships
    query: LiteralString = """
        MATCH (n:Entity {uuid: $uuid})-[r:BELONGS_TO]->(c:Community)
        DELETE r
    """
    await self.driver.execute_query(query, uuid=entity_node_uuid)

    # 6. Delete the node itself
    await Node.delete_by_uuids(self.driver, [node])
```

### MCP Server Change (`src/graphiti_mcp_server.py`)

```python
@mcp.tool()
async def delete_node(uuid: str, cascade: bool = False) -> SuccessResponse | ErrorResponse:
    """Delete a specific entity node from the knowledge graph.

    Args:
        uuid: UUID of the entity node to delete. Get UUIDs from search_nodes results.
        cascade: If True, also deletes all edges connected to this node.
                 If False (default), fails if the node still has edges.
    """
    try:
        client = await get_graphiti()
        await client.remove_node(uuid, cascade=cascade)
        return SuccessResponse(message=f"Node {uuid} deleted successfully")
    except NodeHasEdgesError as e:
        return ErrorResponse(error=str(e))
    except Exception as e:
        return ErrorResponse(error=f"Error deleting node: {str(e)}")
```

## API Design Decisions

### Why `cascade` defaults to `False`

Safety first. Deleting a node that's connected to other important nodes via edges could silently destroy valuable relationship data. The default behavior forces the caller to either:
1. Delete edges manually first (targeted cleanup)
2. Explicitly opt into cascade (bulk cleanup)

### Why not modify `delete_episode` instead?

`delete_episode`'s behavior of preserving multi-referenced nodes is **correct by design** — if two episodes mention the same entity, deleting one shouldn't destroy shared knowledge. The problem is that there's no cleanup path for when ALL episodes referencing a node are gone.

### Alternative: `delete_orphaned_nodes` (maintenance tool)

A complementary tool that finds and deletes nodes with zero episode MENTIONS:

```python
@mcp.tool()
async def delete_orphaned_nodes(
    group_id: str | None = None,
    dry_run: bool = True
) -> SuccessResponse | ErrorResponse:
    """Find and delete entity nodes with no episode references.

    Args:
        group_id: Scope cleanup to a specific group.
        dry_run: If True (default), only report what would be deleted.
    """
    query = """
        MATCH (n:Entity)
        WHERE NOT EXISTS { MATCH (e:Episodic)-[:MENTIONS]->(n) }
        AND ($group_id IS NULL OR n.group_id = $group_id)
        RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary
    """
    # ... execute and optionally delete
```

## Implementation Plan

### Phase 1: Core `delete_node` (Minimum Viable)
1. Add `remove_node()` to `graphiti_core/graphiti.py`
2. Add `NodeHasEdgesError` exception class
3. Add `delete_node` MCP tool to server
4. Add unit tests for: basic delete, cascade delete, not-found error, has-edges error

### Phase 2: Maintenance Tools
1. Add `delete_orphaned_nodes` (dry_run + execute modes)
2. Add `get_node_references` tool (show which episodes/edges reference a node)
3. Add orphan detection to `graph_verifier` tool

### Phase 3: Upstream Contribution
Consider contributing `remove_node` back to the `graphiti-core` package (currently v0.24.3) via PR to the Zep AI repository.

## Testing Strategy

```python
async def test_delete_node_basic():
    """Delete a node with no edges."""
    # Add episode → extract node → delete episode → node orphaned
    # delete_node(uuid) → verify node gone from search

async def test_delete_node_cascade():
    """Delete a node with cascade=True removes edges too."""
    # Add episode with 2 entities + 1 edge
    # delete_node(entity_a_uuid, cascade=True)
    # Verify: entity_a gone, edge gone, entity_b still exists

async def test_delete_node_no_cascade_with_edges():
    """Fail if node has edges and cascade=False."""
    # Add episode with connected entities
    # delete_node(uuid, cascade=False) → raises NodeHasEdgesError

async def test_delete_node_not_found():
    """Error when UUID doesn't exist."""
    # delete_node("nonexistent-uuid") → raises NodeNotFoundError
```

## Files to Modify

| File | Change |
|------|--------|
| `graphiti_core/graphiti.py` | Add `remove_node()` method |
| `graphiti_core/nodes.py` | Add `NodeHasEdgesError` exception (or in `exceptions.py`) |
| `src/graphiti_mcp_server.py` | Add `delete_node` MCP tool |
| `tests/` | Add unit tests for all scenarios |

## Success Criteria

1. `search_nodes` → find stale node UUID → `delete_node(uuid, cascade=True)` → node no longer appears in searches
2. Orphaned nodes from multi-episode deletions can be cleaned up without `clear_graph`
3. No data loss for nodes that are still actively referenced by valid episodes

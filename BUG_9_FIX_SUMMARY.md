# BUG 9 (MEDIUM) - Multi-Group Methods Fix

## Summary

Fixed multi-group operations in `get_episodes` and `clear_graph` methods to properly query and clear data across multiple FalkorDB databases.

## Problem

Both methods were incorrectly querying only ONE database when multiple group_ids were provided:

1. **get_episodes** (lines 948-997): Called `EpisodicNode.get_by_group_ids()` with all group_ids on a single driver, which only queries the default database.

2. **clear_graph** (lines 1048-1068): Called `clear_data()` with all group_ids on a single driver, which only clears the default database.

**Root Cause**: Graphiti's multi-tenancy uses `group_id` as FalkorDB database name. Each group_id = separate graph/database. Must clone driver for EACH group to access the correct database.

## Solution

Applied per-group driver cloning pattern for both methods:

### get_episodes Fix

**Before** (broken - queries only one database):
```python
episodes = await EpisodicNode.get_by_group_ids(
    client.driver, effective_group_ids, limit=max_episodes
)
```

**After** (fixed - queries each database):
```python
# Multi-group retrieval: clone driver for EACH group_id
all_episodes = []
for group_id in effective_group_ids:
    # Clone driver to query the specific database for this group
    driver = client.driver.clone(database=group_id)
    try:
        # Query episodes for this specific group
        group_episodes = await EpisodicNode.get_by_group_ids(
            driver, [group_id], limit=max_episodes
        )
        all_episodes.extend(group_episodes)
    except Exception as e:
        logger.warning(f'Error retrieving episodes for group {group_id}: {str(e)}')
        # Continue with other groups even if one fails

# Sort aggregated episodes by created_at (most recent first) and apply limit
all_episodes.sort(key=lambda e: e.created_at if e.created_at else datetime.min, reverse=True)
episodes = all_episodes[:max_episodes]
```

### clear_graph Fix

**Before** (broken - clears only one database):
```python
await clear_data(client.driver, group_ids=effective_group_ids)
```

**After** (fixed - clears each database):
```python
# Multi-group clearing: clone driver for EACH group_id
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

# Report results with partial success handling
if cleared_groups and not errors:
    return SuccessResponse(...)
elif cleared_groups and errors:
    return SuccessResponse(message='Graph data partially cleared. ...')
else:
    return ErrorResponse(...)
```

## Key Changes

1. **Per-Group Driver Cloning**: Both methods now clone the driver for EACH group_id
2. **Error Resilience**: Operations continue even if one group fails (partial success handling)
3. **Result Aggregation**: get_episodes aggregates and sorts results across all groups
4. **Better Reporting**: clear_graph reports partial successes and individual group errors

## Files Modified

- `/home/gyasisutton/dev/tools/graphiti-mcp/src/graphiti_mcp_server.py`
  - Lines 975-999: get_episodes method (multi-group retrieval with aggregation)
  - Lines 1085-1112: clear_graph method (per-group clearing with error handling)

## Testing

Created comprehensive test suite: `/home/gyasisutton/dev/tools/graphiti-mcp/test_multigroup_fixes.py`

**Test Results**:
```
✅ get_episodes multi-group: PASSED
  - Verified driver cloning for each group
  - Verified episode aggregation across groups
  - Verified chronological sorting

✅ clear_graph multi-group: PASSED
  - Verified driver cloning for each group
  - Verified independent clearing per database
  - Verified partial success handling
```

## Impact

### Before Fix
- **get_episodes([group-1, group-2])**: Only returned episodes from default database
- **clear_graph([group-1, group-2])**: Only cleared default database
- Multi-group operations were effectively broken

### After Fix
- **get_episodes([group-1, group-2])**: Returns aggregated episodes from BOTH databases, sorted by created_at
- **clear_graph([group-1, group-2])**: Clears BOTH databases independently, with partial success reporting
- Multi-group operations work correctly across all specified databases

## Related Bugs

This fix partially addresses **BUG 3 (HIGH)** - Multi-group operations across Graphiti methods. Additional methods requiring similar fixes:
- `search_nodes`
- `search_memory_facts`
- `delete_episode`
- `delete_entity_edge`
- `get_entity_edge`

## Verification

To verify the fix in production:

```python
# Test get_episodes with multiple groups
result = await get_episodes(group_ids=["group-1", "group-2"], max_episodes=10)
# Should return episodes from BOTH groups, sorted by created_at

# Test clear_graph with multiple groups
result = await clear_graph(group_ids=["test-group-1", "test-group-2"])
# Should clear BOTH databases independently
```

## Notes

- Uses `datetime.min` as fallback for sorting episodes without created_at timestamps
- Error handling ensures operations continue even if individual groups fail
- clear_graph now provides detailed partial success reporting
- Follows the database-agnostic pattern established in graph_verifier tool

## References

- **Pattern Source**: Based on `graph_verifier` tool implementation (lines 1227-1260)
- **FalkorDB Multi-Tenancy**: Each group_id maps to separate FalkorDB database
- **Driver Cloning**: Required for accessing different databases within same connection

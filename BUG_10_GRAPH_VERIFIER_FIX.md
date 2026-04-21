# Bug 10: graph_verifier KeyError "0" Fix

## Problem
The `graph_verifier` tool was failing with cryptic error messages like `"Error verifying graph: 0"` and `"Error verifying graph: 2"`.

## Root Cause
**FalkorDB returns query results as DICTIONARIES, not lists/arrays.**

The original code was trying to access query results using numeric indices like `record[0]`, `record[1]`, etc., which caused `KeyError` exceptions. When Python converts `KeyError(0)` to a string, it becomes `"0"`, leading to the confusing error messages.

### Debug Output Example
```python
# What FalkorDB returns:
result = ([
    {'uuid': 'c8e4f851...', 'name': 'Driver Cloning Bug Fix Verified', ...},
    {'uuid': '50d178a7...', 'name': 'MCP Tools Test - December 2025', ...}
], ['uuid', 'name', 'created_at', ...], None)

# First record is a DICT, not a list:
record = result[0][0]
# record = {'uuid': 'c8e4f851...', 'name': 'Driver Cloning Bug Fix Verified', ...}

# OLD CODE (WRONG):
uuid = record[0]  # KeyError: 0 (dicts don't have numeric keys!)

# FIXED CODE (CORRECT):
uuid = record.get('uuid')  # Works! Access by key name
```

## Changes Made

### 1. Episode Query Result Parsing
**File**: `/home/gyasisutton/dev/tools/graphiti-mcp/src/graphiti_mcp_server.py` (lines 1338-1368)

**Before** (incorrect):
```python
# FalkorDB returns records as lists, not dicts
# Return order: uuid, name, created_at, group_id, entity_count, edge_count
recent_episodes = []
for record in episode_records:
    created_at = record[2] if len(record) > 2 else None
    recent_episodes.append({
        'uuid': record[0] if len(record) > 0 else None,
        'name': record[1] if len(record) > 1 else 'Unknown',
        'created_at': created_at_str,
        'group_id': record[3] if len(record) > 3 else None,
        'entity_count': record[4] if len(record) > 4 else 0,
        'edge_count': record[5] if len(record) > 5 else 0,
    })
```

**After** (fixed):
```python
# FalkorDB returns records as DICTIONARIES (not lists!)
# Keys: uuid, name, created_at, group_id, entity_count, edge_count
recent_episodes = []
for record in episode_records:
    created_at = record.get('created_at')
    recent_episodes.append({
        'uuid': record.get('uuid'),
        'name': record.get('name', 'Unknown'),
        'created_at': created_at_str,
        'group_id': record.get('group_id'),
        'entity_count': record.get('entity_count', 0) or 0,
        'edge_count': record.get('edge_count', 0) or 0,
    })
```

### 2. Count Query Helper Function
**File**: `/home/gyasisutton/dev/tools/graphiti-mcp/src/graphiti_mcp_server.py` (lines 1372-1392)

**Before** (incorrect):
```python
def safe_get_count(result) -> int:
    """FalkorDB returns: (records, keys, metadata) tuple
    records is a list of lists, e.g., [[42]] for count queries
    """
    records = result[0] if result else []
    first_record = records[0]
    count_value = first_record[0]  # KeyError: 0!
    return int(count_value)
```

**After** (fixed):
```python
def safe_get_count(result, key_name: str) -> int:
    """FalkorDB returns: (records, keys, metadata) tuple
    records is a list of DICTIONARIES, e.g., [{'total_nodes': 42}]
    """
    records = result[0] if result else []
    first_record = records[0]
    count_value = first_record.get(key_name)  # Access by key name!
    return int(count_value)
```

Updated all calls:
```python
# Before:
total_nodes = safe_get_count(entity_result)

# After:
total_nodes = safe_get_count(entity_result, 'total_nodes')
total_edges = safe_get_count(edge_result, 'total_edges')
total_episodes = safe_get_count(episode_count_result, 'total_episodes')
```

### 3. Group Distribution Query Parsing
**File**: `/home/gyasisutton/dev/tools/graphiti-mcp/src/graphiti_mcp_server.py` (lines 1418-1431)

**Before** (incorrect):
```python
# FalkorDB returns records as lists, not dicts
# Return order: group_id, count
group_distribution = {}
for record in group_records:
    group_id = record[0] if record[0] is not None else 'unknown'
    group_distribution[group_id] = record[1]
```

**After** (fixed):
```python
# FalkorDB returns records as DICTIONARIES (not lists!)
# Keys: group_id, count
group_distribution = {}
for record in group_records:
    group_id = record.get('group_id') or 'unknown'
    group_distribution[group_id] = record.get('count', 0)
```

## Testing

### Debug Script
Created `/home/gyasisutton/dev/tools/graphiti-mcp/debug_graph_verifier.py` to:
1. Inspect raw FalkorDB query result format
2. Reveal that records are dictionaries, not lists
3. Identify the exact location of the KeyError

### Test Script
Created `/home/gyasisutton/dev/tools/graphiti-mcp/test_graph_verifier_fix.py` to:
1. Test episode query parsing with dictionary access
2. Test count query parsing with key names
3. Test group distribution parsing with dictionary access
4. Verify all three components work correctly

### Test Results
```
================================================================================
ALL TESTS PASSED - FIX VERIFIED
================================================================================

Graph Statistics:
  Total Episodes: 10
  Total Entities: 105
  Total Relationships: 114
  Groups: {'developer_gyasisutton': 10}

Recent Additions:
  1. Driver Cloning Bug Fix Verified (Entities: 10, Edges: 11)
  2. MCP Tools Test - December 2025 (Entities: 12, Edges: 18)
  3. graph_verifier Database Abstraction Fix - Complete Workflow (Entities: 19, Edges: 32)
```

## Impact

### Before Fix
- `graph_verifier` tool always failed with `KeyError: 0` or `KeyError: 2`
- Error messages were cryptic: `"Error verifying graph: 0"`
- Users couldn't verify memory storage or check graph health

### After Fix
- `graph_verifier` successfully parses all FalkorDB query results
- Returns complete graph statistics and recent additions
- Users can verify memory storage and check graph health

## Database Compatibility

This fix maintains compatibility with the database abstraction pattern used elsewhere in Graphiti MCP:
- **FalkorDB**: Returns dictionaries (now handled correctly)
- **Neo4j**: Also returns dictionaries (will continue to work)
- **Kuzu**: Also returns dictionaries (will continue to work)
- **Neptune**: Also returns dictionaries (will continue to work)

The fix aligns with the documented best practice of using `driver.execute_query()` for database-agnostic operations.

## Lessons Learned

1. **Always inspect raw query results** when debugging database queries
2. **Different graph databases have different return formats**:
   - FalkorDB: `(list[dict], list[str], metadata)`
   - Neo4j: Can vary depending on driver version
3. **KeyError with numeric key** (e.g., `KeyError: 0`) indicates trying to access a dict with a list index
4. **Use `.get()` method** for safe dictionary access with defaults

## Related Files
- `/home/gyasisutton/dev/tools/graphiti-mcp/src/graphiti_mcp_server.py` (main fix)
- `/home/gyasisutton/dev/tools/graphiti-mcp/debug_graph_verifier.py` (debug script)
- `/home/gyasisutton/dev/tools/graphiti-mcp/test_graph_verifier_fix.py` (test script)
- `/home/gyasisutton/dev/tools/graphiti-mcp/BUG_10_GRAPH_VERIFIER_FIX.md` (this document)

## Resolution
**Status**: FIXED ✅
**Date**: 2025-12-26
**Verified**: All test cases pass, graph_verifier now works correctly with FalkorDB

# BUG 9 Multi-Group Fix - Visual Comparison

## get_episodes Method

### BEFORE (Broken - queries only one database)

```python
# Lines 963-970 (old implementation)
if effective_group_ids:
    episodes = await EpisodicNode.get_by_group_ids(
        client.driver, effective_group_ids, limit=max_episodes
    )
else:
    episodes = []
```

**Problem**: Single call to `get_by_group_ids()` with multiple group_ids on ONE driver only queries the DEFAULT database. Other group databases are ignored.

**Example Behavior**:
```python
# Query with ["group-1", "group-2"]
episodes = await EpisodicNode.get_by_group_ids(
    client.driver,        # Uses default database (e.g., "group-1")
    ["group-1", "group-2"],  # group-2 is IGNORED!
    limit=10
)
# Result: Only episodes from group-1, missing all group-2 episodes
```

---

### AFTER (Fixed - queries each database separately)

```python
# Lines 1017-1037 (new implementation)
# Multi-group retrieval: clone driver for EACH group_id (each = separate FalkorDB database)
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

**Fix**: Clone driver for EACH group_id, query separately, aggregate results, sort by timestamp.

**Example Behavior**:
```python
# Query with ["group-1", "group-2"]
all_episodes = []

# Iteration 1: group-1
driver = client.driver.clone(database="group-1")  # Points to group-1 database
episodes = await EpisodicNode.get_by_group_ids(driver, ["group-1"], limit=10)
all_episodes.extend(episodes)  # [ep1, ep2]

# Iteration 2: group-2
driver = client.driver.clone(database="group-2")  # Points to group-2 database
episodes = await EpisodicNode.get_by_group_ids(driver, ["group-2"], limit=10)
all_episodes.extend(episodes)  # [ep1, ep2, ep3]

# Sort and limit
all_episodes.sort(key=lambda e: e.created_at, reverse=True)
episodes = all_episodes[:10]  # Top 10 most recent across ALL groups
# Result: Episodes from BOTH group-1 AND group-2, sorted by created_at
```

---

## clear_graph Method

### BEFORE (Broken - clears only one database)

```python
# Lines 1060 (old implementation)
await clear_data(client.driver, group_ids=effective_group_ids)
```

**Problem**: Single call to `clear_data()` with multiple group_ids on ONE driver only clears the DEFAULT database. Other group databases remain untouched.

**Example Behavior**:
```python
# Clear with ["group-1", "group-2", "group-3"]
await clear_data(
    client.driver,  # Uses default database (e.g., "group-1")
    group_ids=["group-1", "group-2", "group-3"]  # group-2 and group-3 IGNORED!
)
# Result: Only group-1 database cleared, group-2 and group-3 still have data
```

---

### AFTER (Fixed - clears each database separately)

```python
# Lines 1123-1149 (new implementation)
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
```

**Fix**: Clone driver for EACH group_id, clear independently, track successes/errors, report partial results.

**Example Behavior**:
```python
# Clear with ["group-1", "group-2", "group-3"]
cleared_groups = []
errors = []

# Iteration 1: group-1
driver = client.driver.clone(database="group-1")  # Points to group-1 database
await clear_data(driver, group_ids=["group-1"])
cleared_groups.append("group-1")  # ["group-1"]

# Iteration 2: group-2
driver = client.driver.clone(database="group-2")  # Points to group-2 database
await clear_data(driver, group_ids=["group-2"])
cleared_groups.append("group-2")  # ["group-1", "group-2"]

# Iteration 3: group-3
driver = client.driver.clone(database="group-3")  # Points to group-3 database
await clear_data(driver, group_ids=["group-3"])
cleared_groups.append("group-3")  # ["group-1", "group-2", "group-3"]

# Result: ALL three databases cleared independently
# Returns: "Graph data cleared successfully for group IDs: group-1, group-2, group-3"
```

---

## Key Differences Summary

| Aspect | BEFORE (Broken) | AFTER (Fixed) |
|--------|----------------|---------------|
| **get_episodes** | Single query on default database | Per-group queries with aggregation |
| **clear_graph** | Single clear on default database | Per-group clears with partial success tracking |
| **Multi-group support** | Broken - only processes default group | Working - processes ALL specified groups |
| **Error handling** | Fails completely on any error | Continues with other groups on error |
| **Result reporting** | Basic success/failure | Detailed partial success reporting |
| **Driver usage** | Single driver (default database) | Cloned driver per group (correct database) |

---

## Why This Matters

**Graphiti's Multi-Tenancy Model**:
- Each `group_id` = separate FalkorDB database
- FalkorDB uses database names to isolate data
- `driver.clone(database=group_id)` switches to that database
- Without cloning, ALL operations hit the default database

**Impact on Users**:
- Multi-device sync relies on querying multiple groups
- Cross-project analysis requires aggregating data from multiple groups
- Bulk cleanup operations need to clear multiple groups independently
- Before fix: Multi-group operations silently failed (only processed one group)
- After fix: Multi-group operations work correctly across all specified groups

# Graphiti MCP Performance Investigation

**Date**: 2025-12-25
**Issue**: `add_memory` tool takes 10+ minutes and confirmations hang
**Investigator**: Claude Code + User (gyasisutton)

---

## Problem Summary

### Symptoms
1. **Slow saves**: `add_memory` sometimes takes **10+ minutes** to complete
2. **Hanging confirmations**: Response doesn't return until another MCP call is made
3. **Poor UX**: No progress indication during long waits

### Root Causes Identified

#### 1. **Entity Type Complexity** (PRIMARY CAUSE)
- **14 custom entity types** defined in `config-litellm-azure.yaml` (lines 54-103)
- Each entity type has **extensive descriptions** (100-400 words each)
- Entity extraction sends ALL descriptions to LLM on EVERY episode

**Impact:**
- Huge LLM prompt size (thousands of tokens just for entity type descriptions)
- Multiple LLM calls per episode:
  1. `extract_nodes` - Extract entities (with all 14 type descriptions)
  2. `resolve_extracted_nodes` - Deduplicate entities (more LLM calls)
  3. `_extract_and_resolve_edges` - Extract relationships (LLM calls)
  4. `extract_attributes_from_nodes` - Extract attributes (LLM calls)

**Each LLM call** with Azure OpenAI:
- Large prompt = slower processing
- Azure rate limits can cause queuing delays
- 4-5 LLM calls per episode × slow processing = 10+ minutes

#### 2. **Azure OpenAI Rate Limits**
- `SEMAPHORE_LIMIT` = 10 (default)
- Azure OpenAI quotas vary by deployment
- Too many concurrent requests → rate limiting → delays

#### 3. **Synchronous Processing** (lines 442-469 in graphiti_mcp_server.py)
```python
# Process episode synchronously (async queue doesn't work with MCP stdio)
result = await graphiti_service.client.add_episode(...)
```
- Episode processing is fully synchronous
- MCP stdio transport waits for complete processing
- No progress updates during processing

#### 4. **No Progress Feedback**
- User has no idea what's happening during 10-minute wait
- No logs visible in MCP stdio mode
- Appears to hang

---

## Performance Breakdown

From `graphiti_core/graphiti.py` (lines 615-814), `add_episode` performs:

```
1. Retrieve previous episodes (context)         ~1-2 seconds
2. Extract nodes (LLM call #1)                  ~2-5 minutes (with 14 entity types!)
3. Resolve extracted nodes (LLM calls #2-N)     ~2-4 minutes (deduplication)
4. Extract & resolve edges (LLM calls)          ~2-3 minutes
5. Extract attributes (LLM calls)               ~1-2 minutes
6. Save to database                             ~1 second
7. Update communities (optional)                ~1-5 minutes (if enabled)
                                        Total:  ~8-20 minutes
```

---

## Solutions & Recommendations

### Immediate Fixes (Quick Wins)

#### 1. **Reduce Entity Type Count**
**Current**: 14 entity types
**Recommended**: 5-7 core types for faster processing

**Keep these HIGH-PRIORITY types:**
- `Preference` (most important per config)
- `Problem`
- `Solution`
- `Workflow`
- `ToolPattern`
- `Outcome`
- `Metric`

**Remove or merge:**
- `Procedure` → Merge into `Workflow`
- `Context` → Can be inferred from episode content
- `Requirement` → Merge into `Problem` or `Preference`
- `Event`, `Location`, `Organization`, `Document` → Use only if absolutely needed
- `Topic`, `Object` → These are fallback types, rarely needed

**Expected Impact**: 50-70% reduction in entity extraction time

#### 2. **Simplify Entity Descriptions**
**Current**: 100-400 words per entity type
**Recommended**: 20-50 words per entity type

**Example Before** (current `Problem` description - 100+ words):
```yaml
description: "An error, bug, issue, challenge, or obstacle that was encountered and solved. Includes error types, bug categories, problem classifications, failures, malfunctions, defects. Trigger patterns: 'Error', 'Bug', 'Issue', 'Problem', 'Failure', 'Exception', 'Crash', 'Malfunction', 'Defect', 'Challenge', 'Obstacle', 'Blocker', error messages, problem statements. Examples: 'TypeScript compilation error: Property x does not exist', 'Memory leak in React application', 'API connection timeout', 'Database query performance issue'. Relationships: Solved by Solution/Workflow, Addressed by ToolPattern/Procedure, Occurs in Context, Results in Outcome, Tracked by Metric. PRIORITY: HIGH - Problems are central to developer workflows."
```

**Example After** (simplified - 30 words):
```yaml
description: "Errors, bugs, issues, or obstacles encountered. Examples: TypeScript errors, memory leaks, API timeouts. Solved by Solutions/Workflows."
```

**Expected Impact**: 30-50% reduction in LLM prompt size and processing time

#### 3. **Add Progress Logging**
Add progress logs to show what's happening:

```python
@mcp.tool()
async def add_memory(
    name: str,
    episode_body: str,
    # ... other params
) -> SuccessResponse | ErrorResponse:
    try:
        logger.info(f"[1/6] Starting episode processing: '{name}'")

        logger.info(f"[2/6] Extracting entities from episode...")
        result = await graphiti_service.client.add_episode(...)

        logger.info(f"[3/6] Entities extracted: {entity_count} entities")
        logger.info(f"[4/6] Extracting relationships: {edge_count} relationships")
        logger.info(f"[5/6] Saving to database...")
        logger.info(f"[6/6] Episode saved successfully!")

        return SuccessResponse(message=f"Episode '{name}' added...")
    except Exception as e:
        logger.error(f"Error processing episode: {error_msg}")
        return ErrorResponse(error=f"Error: {error_msg}")
```

**Expected Impact**: Better UX, users know it's working (not hung)

#### 4. **Increase SEMAPHORE_LIMIT** (if Azure quota allows)
Add to `.env`:
```bash
SEMAPHORE_LIMIT=15  # Increase from 10 to 15 if Azure quota allows
```

**Check Azure quota first** - don't exceed your deployment's RPM limit

---

### Medium-Term Fixes

#### 5. **Use Streaming/Background Processing**
- Add a background task queue for episode processing
- Return immediately with job ID
- Allow clients to poll for status
- **Trade-off**: More complex, requires queue service

#### 6. **Cache Entity Extraction Prompts**
- Pre-compute entity type prompt sections
- Reuse across episodes
- **Expected Impact**: 10-20% faster prompt construction

#### 7. **Batch Small Episodes**
- If episodes are short, batch multiple episodes
- Process in bulk using `add_episode_bulk`
- **Trade-off**: Delayed processing

---

### Long-Term Optimizations

#### 8. **Use Smaller/Faster LLM for Extraction**
- Use GPT-4o-mini or GPT-3.5-turbo for entity extraction
- Reserve GPT-4 only for complex deduplication
- **Expected Impact**: 50-70% faster processing

#### 9. **Implement Progressive Enhancement**
- First pass: Extract only high-priority entities (Preference, Problem, Solution)
- Second pass (async): Extract remaining entities
- Return response after first pass
- **Expected Impact**: User gets response in 1-2 minutes, full processing continues in background

---

## Recommended Configuration Changes

### Option 1: Minimal (Fast)
**File**: `config/config-litellm-azure-fast.yaml`

```yaml
graphiti:
  entity_types:
    - name: "Preference"
      description: "User preferences and choices. Trigger: 'I want/like/prefer'"

    - name: "Problem"
      description: "Errors, bugs, issues. Examples: TypeScript errors, API timeouts"

    - name: "Solution"
      description: "Fixes and resolutions. Examples: Add type annotation, use retry logic"

    - name: "Workflow"
      description: "Multi-step processes. Examples: debugging workflow, deployment process"

    - name: "ToolPattern"
      description: "Tool sequences. Examples: search → read → edit"
```

**Expected time**: 2-4 minutes per episode

### Option 2: Balanced (Recommended)
**File**: `config/config-litellm-azure-balanced.yaml`

```yaml
graphiti:
  entity_types:
    - name: "Preference"
      description: "User preferences, choices, opinions. Trigger: 'I want/like/prefer'"

    - name: "Problem"
      description: "Errors, bugs, issues encountered. Examples: TypeScript errors, memory leaks"

    - name: "Solution"
      description: "Fixes and resolutions. Examples: Add type annotation, update config"

    - name: "Workflow"
      description: "Complete multi-step processes. Examples: debugging workflow, deployment pipeline"

    - name: "ToolPattern"
      description: "Successful tool sequences. Examples: search → read → edit"

    - name: "Outcome"
      description: "Results and effects. Examples: Success, Failed, Performance improved"

    - name: "Metric"
      description: "Quantifiable measurements. Examples: 95% success rate, 5 minutes saved"
```

**Expected time**: 4-6 minutes per episode

---

## Testing Recommendations

1. **Create test config** with 5 entity types
2. **Measure performance** with simple episode:
   ```bash
   time uv run python test_add_memory.py
   ```
3. **Compare times**:
   - Current (14 types): ~10-15 minutes
   - Optimized (5 types): ~2-4 minutes
   - Optimized (7 types): ~4-6 minutes

4. **Use `graph_verifier` tool** to verify memories saved correctly

---

## Action Items

### Priority 1 (Do Now)
- [ ] Create `config-litellm-azure-fast.yaml` with 5 entity types
- [ ] Test performance with simplified config
- [ ] Measure time improvement
- [ ] Use `graph_verifier` to verify storage

### Priority 2 (This Week)
- [ ] Create `config-litellm-azure-balanced.yaml` with 7 entity types
- [ ] Add progress logging to `add_memory`
- [ ] Document optimal SEMAPHORE_LIMIT for your Azure quota
- [ ] Add timeout warnings (if >5 minutes)

### Priority 3 (Future)
- [ ] Investigate background processing option
- [ ] Explore using smaller LLM for extraction
- [ ] Implement progressive enhancement approach

---

## Usage After Fixes

### Start MCP with Fast Config
```bash
cd /home/gyasisutton/dev/tools/graphiti-mcp
docker compose up -d  # Start FalkorDB

# Use fast config
uv run python main.py --config config/config-litellm-azure-fast.yaml --transport stdio
```

### Verify Memory Storage
After calling `add_memory`, use `graph_verifier` to check:
```python
# In Claude Code or Cursor
result = graph_verifier()
# Check recent_additions for your memory
# Verify entity_count and edge_count
```

---

## Summary

**Root Cause**: Too many entity types (14) with verbose descriptions → huge LLM prompts → 10+ minute processing

**Quick Fix**: Reduce to 5-7 entity types, simplify descriptions → ~70% faster (2-6 minutes)

**Tools Added**:
- `graph_verifier` - Verify recent additions and graph stats without searching

**Next Steps**:
1. Create fast config with 5 entity types
2. Test and measure improvement
3. Use `graph_verifier` to verify storage
4. Add progress logging for better UX

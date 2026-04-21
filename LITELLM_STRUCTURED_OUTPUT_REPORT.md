# LiteLLM + Azure OpenAI Structured Output Test Report

**Test Date:** 2024-12-22
**Azure Endpoint:** `https://admin-m9ihapvr-eastus2.services.ai.azure.com`
**Azure Deployment:** `model-router`

## Executive Summary

LiteLLM successfully handles structured outputs with Azure OpenAI. The key finding is that **API version matters critically** for certain features.

## Test Results

### Structured Output Methods Tested

| Method | Works? | Notes |
|--------|--------|-------|
| `response_format={"type": "json_object"}` | **YES** | Works with all API versions |
| `response_format={"type": "json_schema", ...}` (LiteLLM) | **YES** | Works with all API versions |
| `response_format` with Pydantic schema conversion | **YES** | Works, requires manual validation |
| Function/Tool calling | **YES** | Works with all API versions |
| Async `acompletion` with json_object | **YES** | Works |
| Async `acompletion` with json_schema | **YES** | Works |
| OpenAI `beta.chat.completions.parse()` | **CONDITIONAL** | Requires API version >= 2024-08-01-preview |

### API Version Compatibility

| API Version | LiteLLM json_schema | OpenAI beta.parse() |
|-------------|---------------------|---------------------|
| 2024-05-01-preview | PASS | **FAIL** |
| 2024-08-01-preview | PASS | PASS |
| 2024-10-01-preview | PASS | PASS |
| 2024-12-01-preview | PASS | PASS |

## Root Cause Analysis

### Why OpenAI `beta.chat.completions.parse()` Failed

The error was:
```
Error code: 400 - {'error': {'code': 'BadRequest', 'message': 'response_format value as json_schema is enabled only for api versions 2024-08-01-preview and later'}}
```

**The `.env` file had `AZURE_OPENAI_API_VERSION=2024-05-01-preview`** which is too old for the `json_schema` response format required by `beta.chat.completions.parse()`.

### Why LiteLLM Worked Even with Old API Version

LiteLLM internally handles structured outputs differently:
1. It uses Azure's model inference endpoint which may have different routing
2. It may be converting structured output requests to equivalent JSON mode
3. The Azure model-router deployment may have special handling

## Recommended Fix

**Update the API version in `.env`:**

```bash
# OLD (doesn't support json_schema)
AZURE_OPENAI_API_VERSION=2024-05-01-preview

# NEW (supports json_schema and beta.parse)
AZURE_OPENAI_API_VERSION=2024-08-01-preview
```

Also update `config/config-litellm-azure.yaml`:

```yaml
providers:
  litellm:
    api_version: ${AZURE_OPENAI_API_VERSION:2024-08-01-preview}  # Changed default
```

## What Graphiti Needs

Graphiti-core uses structured outputs in these ways:

1. **Entity extraction** - Uses Pydantic models with `response_format`
2. **Relationship extraction** - Uses structured JSON schemas
3. **Episode processing** - May use `beta.chat.completions.parse()`

### Compatibility Matrix for Graphiti

| Graphiti Feature | LiteLLM Support | Notes |
|------------------|-----------------|-------|
| Basic LLM calls | Full | Works |
| JSON mode | Full | Works |
| Structured outputs (json_schema) | Full | Works |
| Pydantic models | Full* | Requires manual validation or schema conversion |
| Async operations | Full | Works |
| Function calling | Full | Works |

*LiteLLM doesn't directly support `response_format=PydanticModel`, but you can:
1. Convert Pydantic schema with `Model.model_json_schema()`
2. Use the schema in `response_format={"type": "json_schema", "json_schema": {...}}`
3. Validate response with `Model.model_validate_json(response)`

## Conclusion

**LiteLLM + Azure OpenAI CAN handle structured outputs that Graphiti needs**, provided:

1. **API version is 2024-08-01-preview or later** (for full compatibility)
2. Pydantic models are converted to JSON schemas (LiteLLM handles this)
3. Either use LiteLLM's native structured output support OR the OpenAI SDK with correct API version

## Files Modified

To fix the issue, update:
- `/home/gyasisutton/dev/tools/graphiti-mcp/.env` - Change API version
- `/home/gyasisutton/dev/tools/graphiti-mcp/config/config-litellm-azure.yaml` - Update default version

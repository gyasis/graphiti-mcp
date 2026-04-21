# Graphiti MCP Multi-Tenancy Guide

This guide explains how Graphiti's multi-database capabilities work and provides practical use cases and user stories for different scenarios.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [User Stories](#user-stories)
- [Use Cases](#use-cases)
- [Best Practices](#best-practices)
- [Migration Scenarios](#migration-scenarios)
- [Troubleshooting](#troubleshooting)

## Architecture Overview

### How Multi-Tenancy Works

Graphiti MCP uses `group_id` as the FalkorDB database/graph name for multi-tenancy isolation:

```
┌─────────────────────────────────────────────────────────────┐
│                    FalkorDB Instance                         │
│                    (localhost:6379)                          │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Database   │  │   Database   │  │   Database   │     │
│  │  "team-dev"  │  │ "team-staging"│  │ "team-prod"  │     │
│  │              │  │              │  │              │     │
│  │  Episodes    │  │  Episodes    │  │  Episodes    │     │
│  │  Entities    │  │  Entities    │  │  Entities    │     │
│  │  Facts       │  │  Facts       │  │  Facts       │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                              │
│  Each group_id = separate graph with complete isolation     │
└─────────────────────────────────────────────────────────────┘
```

### Key Concepts

1. **group_id = Database Name**: Each `group_id` maps to a separate FalkorDB database/graph
2. **Complete Isolation**: Data in one group cannot be accessed from another group
3. **Driver Cloning**: Tools clone the driver with the appropriate `group_id` to switch database context
4. **Multi-Group Operations**: Some tools can query across multiple groups and aggregate results

### Driver Cloning Pattern

All MCP tools use this pattern internally:

```python
# Clone driver to specific database for isolation
client = await graphiti_service.get_client()
group_id = "team-dev"  # The group/database to use
driver = client.driver.clone(database=group_id)

# All operations use the cloned driver
result = await SomeNode.get_by_uuid(driver, uuid)
```

**Why cloning is necessary**: When `add_memory(group_id="X")` is called, Graphiti mutates the global `client.driver` to point to database "X". Subsequent operations using `client.driver` would query the wrong database. Cloning ensures each operation uses the correct database.

## User Stories

### Story 1: Multi-Environment AI Development

**As a** developer building an AI agent with Graphiti memory
**I want** separate knowledge graphs for development, staging, and production
**So that** I can safely test changes without affecting production data

**Implementation**:
```python
# Development environment
add_memory(
    name="Feature Test",
    episode_body="Testing new entity extraction...",
    group_id="myapp-dev"
)

# Staging environment
add_memory(
    name="Integration Test",
    episode_body="Verifying end-to-end workflow...",
    group_id="myapp-staging"
)

# Production environment (separate deployment)
add_memory(
    name="User Session",
    episode_body="Production user interaction...",
    group_id="myapp-prod"
)
```

**Benefits**:
- Independent testing without production impact
- Easy rollback if staging reveals issues
- Clear separation of concerns

---

### Story 2: Multi-Tenant SaaS Application

**As a** SaaS product owner
**I want** each customer to have their own isolated knowledge graph
**So that** customer data is completely separated for security and privacy

**Implementation**:
```python
# Customer A's data
add_memory(
    name="Customer A Preferences",
    episode_body="Customer A prefers dark mode and compact layouts...",
    group_id="customer-acme-corp"
)

# Customer B's data
add_memory(
    name="Customer B Preferences",
    episode_body="Customer B requires HIPAA compliance...",
    group_id="customer-beta-systems"
)

# Query only Customer A's data
results = search_nodes(
    query="user interface preferences",
    group_ids=["customer-acme-corp"]
)
```

**Benefits**:
- Complete data isolation per customer
- GDPR/privacy compliance (data residency per tenant)
- Easy per-customer backups and analytics

---

### Story 3: Team Collaboration with Shared Knowledge

**As a** team lead managing multiple projects
**I want** separate knowledge graphs per project with cross-project search capability
**So that** teams can work independently but still discover related work

**Implementation**:
```python
# Project Alpha team
add_memory(
    name="Alpha Design Decision",
    episode_body="Using React for frontend, TypeScript for type safety...",
    group_id="project-alpha"
)

# Project Beta team
add_memory(
    name="Beta Architecture",
    episode_body="Microservices with Node.js backend, React frontend...",
    group_id="project-beta"
)

# Search across both projects (cross-project discovery)
results = search_nodes(
    query="React frontend architecture patterns",
    group_ids=["project-alpha", "project-beta"]
)

# Returns aggregated results from both projects
# Each result includes which project it came from
```

**Benefits**:
- Project isolation for focused work
- Cross-pollination of knowledge across teams
- Discover similar solutions across projects

---

### Story 4: Personal vs. Organizational Knowledge

**As a** developer using an AI coding assistant
**I want** separate graphs for my personal learning and company projects
**So that** I can keep personal notes private while contributing to team knowledge

**Implementation**:
```python
# Personal learning notes
add_memory(
    name="Personal: Rust Learning",
    episode_body="Learned about ownership and borrowing in Rust...",
    group_id="developer-john-personal"
)

# Company project knowledge
add_memory(
    name="Company: API Design",
    episode_body="REST API best practices for customer service...",
    group_id="company-customer-api"
)

# Query only company knowledge (for team sharing)
company_results = search_nodes(
    query="API design patterns",
    group_ids=["company-customer-api"]
)

# Query only personal notes (private)
personal_results = search_nodes(
    query="Rust memory management",
    group_ids=["developer-john-personal"]
)
```

**Benefits**:
- Privacy for personal notes
- Separation of personal and professional knowledge
- Easy context switching between personal/work

---

### Story 5: Distributed Development Team

**As a** distributed team working across time zones
**I want** each developer to have their own sandbox with ability to sync
**So that** we can work asynchronously without conflicts

**Implementation**:
```python
# Developer 1 (US) sandbox
add_memory(
    name="Feature Implementation",
    episode_body="Implemented authentication flow using OAuth2...",
    group_id="developer-alice-sandbox"
)

# Developer 2 (EU) sandbox
add_memory(
    name="Database Design",
    episode_body="Normalized user table schema...",
    group_id="developer-bob-sandbox"
)

# Team shared knowledge (manual promotion after review)
add_memory(
    name="Approved Pattern",
    episode_body="OAuth2 authentication flow approved for production...",
    group_id="team-shared-knowledge"
)

# Search across all sandboxes for code review
review_results = search_nodes(
    query="authentication implementation",
    group_ids=["developer-alice-sandbox", "developer-bob-sandbox"]
)
```

**Benefits**:
- Conflict-free parallel development
- Review before promoting to shared knowledge
- Historical record of individual contributions

---

### Story 6: Experimentation and A/B Testing

**As a** data scientist experimenting with AI agent prompts
**I want** separate graphs for each experiment variant
**So that** I can compare results without contamination

**Implementation**:
```python
# Experiment A: Concise prompts
add_memory(
    name="Concise Prompt Test",
    episode_body="Tested short, direct prompts. Result: 85% accuracy...",
    group_id="experiment-concise-prompts"
)

# Experiment B: Detailed prompts
add_memory(
    name="Detailed Prompt Test",
    episode_body="Tested verbose, explanatory prompts. Result: 92% accuracy...",
    group_id="experiment-detailed-prompts"
)

# Compare results across experiments
comparison = {
    "concise": search_nodes(
        query="accuracy metrics",
        group_ids=["experiment-concise-prompts"]
    ),
    "detailed": search_nodes(
        query="accuracy metrics",
        group_ids=["experiment-detailed-prompts"]
    )
}
```

**Benefits**:
- Clean A/B testing without cross-contamination
- Easy comparison of experiment results
- Preserve all experiment data for analysis

## Use Cases

### Use Case 1: SaaS Customer Onboarding

**Scenario**: New customer signs up for your AI-powered analytics SaaS.

**Implementation Flow**:

```python
# Step 1: Create customer-specific graph on signup
customer_id = "customer-startup-xyz"
add_memory(
    name="Customer Onboarding",
    episode_body=f"Customer {customer_id} signed up for Pro plan...",
    group_id=customer_id
)

# Step 2: Import customer data
add_memory(
    name="Initial Data Import",
    episode_body="{\"users\": 50, \"data_sources\": 3, \"integrations\": [\"Salesforce\", \"HubSpot\"]}",
    source="json",
    group_id=customer_id
)

# Step 3: Build customer-specific knowledge
add_memory(
    name="Customer Preferences",
    episode_body="Customer prefers weekly reports, dark mode UI...",
    group_id=customer_id
)

# Step 4: Query customer-specific insights
insights = search_memory_facts(
    query="customer usage patterns and preferences",
    group_ids=[customer_id]
)
```

**Benefits**:
- Each customer gets isolated, secure knowledge graph
- Easy customer offboarding (just `clear_graph(group_ids=[customer_id])`)
- Per-customer analytics and insights

---

### Use Case 2: CI/CD Pipeline Testing

**Scenario**: Testing knowledge graph changes before deployment.

**Implementation Flow**:

```python
# Step 1: Development - test new entity types
add_memory(
    name="Test New Entity Type",
    episode_body="Testing extraction of 'Workflow' entities...",
    group_id="ci-build-12345"
)

# Step 2: Automated tests verify extraction
test_results = search_nodes(
    query="workflow entities",
    group_ids=["ci-build-12345"],
    entity_types=["Workflow"]
)

# Step 3: If tests pass, promote to staging
add_memory(
    name="Promoted from CI",
    episode_body=test_results,  # Copy successful results
    group_id="staging"
)

# Step 4: Staging validation
staging_validation = search_nodes(
    query="workflow entities",
    group_ids=["staging"]
)

# Step 5: Production deployment (manual approval)
add_memory(
    name="Production Release",
    episode_body=staging_validation,
    group_id="production"
)

# Step 6: Cleanup CI build graph
clear_graph(group_ids=["ci-build-12345"])
```

**Benefits**:
- Safe testing without production impact
- Automated validation in CI/CD
- Easy cleanup of temporary test graphs

---

### Use Case 3: Multi-Region Deployment

**Scenario**: Global SaaS with data residency requirements.

**Implementation Flow**:

```python
# US customers (data stays in US region)
add_memory(
    name="US Customer Data",
    episode_body="US-based customer preferences...",
    group_id="region-us-customer-123"
)

# EU customers (GDPR compliance - data stays in EU)
add_memory(
    name="EU Customer Data",
    episode_body="EU-based customer preferences...",
    group_id="region-eu-customer-456"
)

# Region-specific queries
us_insights = search_nodes(
    query="customer behavior patterns",
    group_ids=["region-us-customer-123"]
)

# Cross-region analytics (aggregate only, not raw data)
all_regions = get_episodes(
    group_ids=["region-us-customer-123", "region-eu-customer-456"],
    max_episodes=100
)
# Returns aggregated metadata, not sensitive data
```

**Benefits**:
- Data residency compliance (GDPR, CCPA)
- Regional performance optimization
- Legal compliance for cross-border data

---

### Use Case 4: Feature Flags and Gradual Rollouts

**Scenario**: Testing new AI features with beta users.

**Implementation Flow**:

```python
# Beta users get new feature
beta_users = ["user-alice", "user-bob"]
for user_id in beta_users:
    add_memory(
        name="Beta Feature Access",
        episode_body="User has access to new entity extraction...",
        group_id=f"beta-{user_id}"
    )

# Production users stay on stable version
prod_users = ["user-charlie", "user-diana"]
for user_id in prod_users:
    add_memory(
        name="Stable Version",
        episode_body="User on production-stable version...",
        group_id=f"prod-{user_id}"
    )

# Measure beta performance
beta_metrics = search_nodes(
    query="feature usage and accuracy metrics",
    group_ids=[f"beta-{u}" for u in beta_users]
)

# Compare with production baseline
prod_metrics = search_nodes(
    query="feature usage and accuracy metrics",
    group_ids=[f"prod-{u}" for u in prod_users]
)

# If beta succeeds, promote all users to beta version
if beta_metrics["accuracy"] > prod_metrics["accuracy"]:
    # Gradual rollout: 10% -> 50% -> 100%
    pass
```

**Benefits**:
- Safe feature testing with small user group
- Easy rollback if beta fails
- Data-driven rollout decisions

---

### Use Case 5: Compliance and Audit Trails

**Scenario**: Healthcare application with HIPAA audit requirements.

**Implementation Flow**:

```python
# Patient A's medical history (isolated graph)
add_memory(
    name="Patient Medical Record",
    episode_body="Patient A diagnosed with hypertension...",
    group_id="patient-a-medical-record"
)

# Audit trail in separate graph
add_memory(
    name="Access Log",
    episode_body="{\"accessed_by\": \"Dr. Smith\", \"timestamp\": \"2025-12-25T10:00:00Z\", \"action\": \"viewed medical record\"}",
    source="json",
    group_id="audit-trail-patient-a"
)

# Compliance query: Who accessed patient data?
audit_results = search_memory_facts(
    query="access logs for patient A",
    group_ids=["audit-trail-patient-a"]
)

# Patient data export (HIPAA right to access)
patient_data = get_episodes(
    group_ids=["patient-a-medical-record"],
    max_episodes=1000
)

# Patient data deletion (HIPAA right to be forgotten)
clear_graph(group_ids=["patient-a-medical-record", "audit-trail-patient-a"])
```

**Benefits**:
- HIPAA compliance (patient data isolation)
- Audit trails for regulatory compliance
- Easy data export and deletion

## Best Practices

### 1. Group ID Naming Conventions

Use descriptive, hierarchical naming:

```python
# Good naming patterns
"company-customer-api"           # Scope: company, Project: customer-api
"team-alpha"                     # Scope: team, Name: alpha
"developer-john-sandbox"         # Scope: developer, Name: john, Type: sandbox
"customer-acme-corp"             # Scope: customer, Name: acme-corp
"experiment-prompt-variant-a"    # Scope: experiment, Name: prompt-variant-a
"region-eu-customer-123"         # Scope: region, Location: eu, ID: customer-123

# Avoid
"graph1"                         # Not descriptive
"my_graph"                       # Ambiguous scope
"test"                           # Too generic
```

### 2. When to Use Single vs. Multiple Groups

**Use Single Group When**:
- All data should be accessible together
- No isolation requirements
- Simple use case (personal project, single tenant)

**Use Multiple Groups When**:
- Data must be isolated (multi-tenant, multi-environment)
- Compliance requirements (GDPR, HIPAA)
- Different access controls needed
- A/B testing or experimentation
- Distributed teams with sandboxes

### 3. Multi-Group Operations

Some tools support querying across multiple groups:

```python
# Search across multiple groups (returns aggregated results)
search_nodes(
    query="authentication patterns",
    group_ids=["team-alpha", "team-beta", "team-gamma"]
)

# Get episodes from multiple groups (sorted by timestamp)
get_episodes(
    group_ids=["dev", "staging"],
    max_episodes=50
)

# Clear multiple groups (useful for cleanup)
clear_graph(group_ids=["experiment-a", "experiment-b", "experiment-c"])
```

### 4. Configuration Best Practices

Set default `group_id` in config for convenience:

```yaml
# config.yaml
graphiti:
  group_id: "developer-john-primary"  # Default for all operations
```

Then override when needed:

```python
# Uses default group_id from config
add_memory(name="Default Graph", episode_body="...")

# Override for specific group
add_memory(
    name="Customer Graph",
    episode_body="...",
    group_id="customer-acme"  # Overrides default
)
```

### 5. UUID Lookup Limitations

**Important**: UUID-based operations use the **config's default group_id**:

```python
# Episode created in "customer-acme" group
result = add_memory(
    name="Customer Data",
    episode_body="...",
    group_id="customer-acme"
)
# Returns: {"episode_uuid": "abc-123", ...}

# UUID lookup (uses config's default group_id, NOT "customer-acme")
episode = get_entity_edge(uuid="abc-123")
# ⚠️ May fail if config group_id != "customer-acme"
```

**Workaround**: Ensure your config `group_id` matches the group where UUIDs were created, or store the `group_id` along with UUIDs for later reference.

### 6. Data Migration Between Groups

To move data from one group to another:

```python
# Step 1: Export episodes from source group
source_episodes = get_episodes(
    group_ids=["dev"],
    max_episodes=1000
)

# Step 2: Re-ingest to target group
for episode in source_episodes:
    add_memory(
        name=episode.name,
        episode_body=episode.content,
        source=episode.source,
        group_id="staging"  # Target group
    )

# Step 3: Verify migration
target_count = len(get_episodes(group_ids=["staging"]))
print(f"Migrated {target_count} episodes to staging")

# Step 4: Optional - cleanup source
# clear_graph(group_ids=["dev"])
```

## Migration Scenarios

### Scenario 1: Migrating from Single-Tenant to Multi-Tenant

**Before**: All customers share one graph (`group_id="main"`)
**After**: Each customer gets isolated graph

**Migration Steps**:

```python
# Step 1: Export all episodes from shared graph
all_episodes = get_episodes(group_ids=["main"], max_episodes=10000)

# Step 2: Identify customer IDs (from episode metadata)
customer_mapping = {}
for episode in all_episodes:
    # Assume episodes have customer info in content
    customer_id = extract_customer_id(episode.content)
    if customer_id not in customer_mapping:
        customer_mapping[customer_id] = []
    customer_mapping[customer_id].append(episode)

# Step 3: Create customer-specific graphs
for customer_id, episodes in customer_mapping.items():
    group_id = f"customer-{customer_id}"
    for episode in episodes:
        add_memory(
            name=episode.name,
            episode_body=episode.content,
            group_id=group_id
        )

# Step 4: Verify migration
for customer_id in customer_mapping.keys():
    count = len(get_episodes(group_ids=[f"customer-{customer_id}"]))
    print(f"Customer {customer_id}: {count} episodes migrated")

# Step 5: Backup and clear old shared graph
# backup_graph(group_id="main")  # Your backup mechanism
# clear_graph(group_ids=["main"])
```

---

### Scenario 2: Environment Promotion (Dev → Staging → Prod)

**Goal**: Promote tested knowledge from dev to staging to production.

**Implementation**:

```python
# Step 1: Development - create and test knowledge
add_memory(
    name="New Feature Knowledge",
    episode_body="Feature implementation details...",
    group_id="dev"
)

# Step 2: Verify in dev
dev_results = search_nodes(
    query="new feature implementation",
    group_ids=["dev"]
)

# Step 3: Promote to staging (selective copy)
if dev_results["count"] > 0:
    for result in dev_results["results"]:
        add_memory(
            name=f"[From Dev] {result.name}",
            episode_body=result.summary,
            group_id="staging"
        )

# Step 4: Staging validation
staging_results = search_nodes(
    query="new feature implementation",
    group_ids=["staging"]
)

# Step 5: Promote to production (after approval)
def promote_to_prod(approved=False):
    if not approved:
        raise Exception("Production promotion requires approval")

    for result in staging_results["results"]:
        add_memory(
            name=f"[Prod Release] {result.name}",
            episode_body=result.summary,
            group_id="production"
        )

# Manual approval required
# promote_to_prod(approved=True)
```

---

### Scenario 3: Customer Offboarding

**Goal**: Safely remove customer data when they cancel subscription.

**Implementation**:

```python
customer_id = "customer-cancelled-corp"

# Step 1: Final backup (regulatory requirement)
backup_episodes = get_episodes(
    group_ids=[customer_id],
    max_episodes=10000
)
# Save backup to cold storage (S3, etc.)
save_backup(f"backups/{customer_id}-final.json", backup_episodes)

# Step 2: Verify backup integrity
backup_count = len(backup_episodes)
print(f"Backed up {backup_count} episodes for {customer_id}")

# Step 3: Clear customer graph (GDPR right to be forgotten)
clear_graph(group_ids=[customer_id])

# Step 4: Verify deletion
remaining = get_episodes(group_ids=[customer_id])
assert len(remaining) == 0, "Failed to delete customer data"

# Step 5: Audit log
add_memory(
    name="Customer Offboarding",
    episode_body=f"Customer {customer_id} data deleted per GDPR request",
    group_id="audit-log"
)
```

## Troubleshooting

### Issue 1: UUID Not Found

**Symptom**: `get_entity_edge(uuid="abc-123")` returns "UUID not found"

**Cause**: UUID was created in a different `group_id` than the config default

**Solution**:
```python
# Option 1: Update config to match the group where UUID exists
# config.yaml:
# graphiti:
#   group_id: "correct-group-id"

# Option 2: Store group_id with UUIDs for later reference
result = add_memory(
    name="My Episode",
    episode_body="...",
    group_id="customer-abc"
)
# Save: uuid=result["episode_uuid"], group_id="customer-abc"
```

---

### Issue 2: Cross-Group Queries Return No Results

**Symptom**: `search_nodes(group_ids=["A", "B"])` returns empty results

**Cause**: Data exists, but query doesn't match content in those specific groups

**Solution**:
```python
# Debug: Check what's actually in each group
group_a_episodes = get_episodes(group_ids=["A"], max_episodes=10)
group_b_episodes = get_episodes(group_ids=["B"], max_episodes=10)

# Verify data exists
print(f"Group A: {len(group_a_episodes)} episodes")
print(f"Group B: {len(group_b_episodes)} episodes")

# Try broader query
results = search_nodes(
    query="",  # Empty = match all
    group_ids=["A", "B"]
)
```

---

### Issue 3: Performance Degradation with Many Groups

**Symptom**: Slow queries when querying across 10+ groups

**Cause**: Multi-group queries aggregate results from each database sequentially

**Solution**:
```python
# Option 1: Query fewer groups at once
results_batch_1 = search_nodes(query="...", group_ids=["A", "B", "C"])
results_batch_2 = search_nodes(query="...", group_ids=["D", "E", "F"])

# Option 2: Use specific group_id when possible
results = search_nodes(query="...", group_ids=["specific-group"])

# Option 3: Consider consolidating related groups
# Merge "team-alpha-dev" and "team-alpha-staging" if isolation not needed
```

---

### Issue 4: Accidental Data Leakage Between Groups

**Symptom**: Customer A sees Customer B's data

**Cause**: Wrong `group_id` parameter in query

**Solution**:
```python
# BAD: Using wrong customer ID
customer_id = get_current_customer()  # Returns "customer-a"
results = search_nodes(
    query="preferences",
    group_ids=["customer-b"]  # BUG: Querying wrong customer!
)

# GOOD: Always use authenticated customer ID
customer_id = get_authenticated_customer_id()  # Verified customer
results = search_nodes(
    query="preferences",
    group_ids=[customer_id]  # Correct: authenticated customer only
)

# BEST: Add server-side validation
def validate_customer_access(requested_group_id, authenticated_customer_id):
    if not requested_group_id.startswith(f"customer-{authenticated_customer_id}"):
        raise PermissionError("Cannot access other customer's data")
    return requested_group_id

safe_group = validate_customer_access("customer-a", auth_customer="a")
```

---

## Summary

Graphiti's multi-tenancy capabilities provide powerful isolation and organization features through `group_id`. Key takeaways:

1. **Complete Isolation**: Each `group_id` is a separate FalkorDB database
2. **Flexible Querying**: Single-group or multi-group queries
3. **Production Ready**: Supports multi-tenant SaaS, multi-environment, and compliance use cases
4. **Simple Migration**: Easy data movement between groups
5. **Best Practices**: Use descriptive naming, validate access, handle UUIDs carefully

For more details, see:
- [README.md](README.md) - Installation and configuration
- [CLAUDE.md](CLAUDE.md) - Technical architecture and driver cloning pattern
- [Graphiti Core Documentation](https://github.com/getzep/graphiti) - Upstream project

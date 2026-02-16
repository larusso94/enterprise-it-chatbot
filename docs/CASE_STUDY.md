# Enterprise IT Chatbot - Case Study

## Problem Statement

Large enterprises face challenges with IT support:
- **High ticket volume**: 1000+ support requests daily
- **Response delays**: Average 4-6 hours for L1 support
- **Knowledge silos**: Documentation scattered across systems
- **Manual processes**: Repetitive ticket creation and routing
- **Global teams**: Multilingual support requirements

## Solution Overview

Built an intelligent agentic AI chatbot that:
- Autonomously decides when to search knowledge base vs. create tickets
- Integrates with Microsoft Teams for seamless user experience
- Connects to ServiceNow for ITSM operations
- Uses vector search for intelligent document retrieval
- Maintains conversation context across multiple turns

## Technical Architecture

### Agentic AI Design

**Core Pattern:** LangChain ReAct (Reasoning + Acting)

The agent follows this loop:
1. **Thought**: Analyze user request → "User wants password policy info"
2. **Action**: Select tool → `knowledge_base_search("password policy")`
3. **Observation**: Receive results → [Retrieved 3 relevant docs]
4. **Thought**: Analyze results → "Found relevant information"
5. **Answer**: Generate response with source attribution

**Why ReAct over direct LLM?**
- Dynamic tool selection based on context
- Multi-step reasoning for complex requests
- Ability to recover from tool failures
- Transparent decision-making process

### Tool Implementation

**1. Knowledge Base Search**
```python
Input: User query → "How do I reset my password?"
Process:
  - Generate embedding with text-embedding-ada-002
  - Vector search in Cosmos DB (cosine similarity)
  - Retrieve top-3 most relevant chunks
  - Return to agent with metadata
Output: Structured results with confidence scores
```

**2. ServiceNow Ticket Creation**
```python
Input: Incident details from conversation
Process:
  - Extract: short_description, description, urgency, category
  - Validate required fields
  - Call ServiceNow REST API
  - Poll for ticket number
Output: INC0012345 with link
```

**3. Ticket Listing**
```python
Input: User identifier (from Teams)
Process:
  - Query ServiceNow: GET /api/now/table/incident?sysparm_query=...
  - Filter by caller_id, state=active
  - Format results (number, short_desc, state, priority)
Output: List of open tickets with status
```

### Integration Architecture

**Microsoft Teams → Azure Bot Service → Container App**
- Bot receives Activity objects via webhook
- Extracts text, user ID, conversation ID
- Routes to agent for processing
- Returns Adaptive Card or text response

**Cosmos DB Multi-Purpose**
- **Sessions Container**: Conversation history per user
- **Vectors Container**: Embedded knowledge base chunks
- **Partition Strategy**: By conversation_id for sessions, by document_id for vectors

**ServiceNow Authentication**
- Supports Basic Auth, OAuth 2.0, Bearer Token
- Token refresh with exponential backoff
- Circuit breaker pattern for resilience

## Implementation Highlights

### 1. Async Architecture

**Why aiohttp over Flask?**
- Bot Framework requires async webhook handling
- Non-blocking I/O for external API calls
- Better concurrency for chat workloads

**Async patterns used:**
```python
# Concurrent tool execution
results = await asyncio.gather(
    cosmos_search(query),
    servicenow_query(user_id),
    return_exceptions=True
)
```

### 2. Structured Logging

**Challenge:** Debugging multi-step agent decisions

**Solution:** JSON structured logs with `structlog`
```json
{
  "event": "agent.tool_execution",
  "tool": "knowledge_base_search",
  "query": "password policy",
  "results_count": 3,
  "latency_ms": 87,
  "conversation_id": "abc123",
  "timestamp": "2026-02-16T10:30:45Z"
}
```

**Benefits:**
- Easy filtering in Log Analytics
- Trace complete agent reasoning path
- Performance profiling per tool

### 3. Resilience Patterns

**Circuit Breaker for ServiceNow:**
```python
# If 5 consecutive failures → OPEN circuit
# Wait 30s → Try again (HALF_OPEN)
# If success → CLOSED, else back to OPEN
```

**Exponential Backoff:**
```python
# Retry delays: 1s, 2s, 4s, 8s, 16s
# Max 5 retries for transient failures
```

**Graceful Degradation:**
```python
if servicenow_unavailable:
    return "I can help with that, but ServiceNow is temporarily unavailable. 
            Please try creating a ticket at portal.company.com"
```

### 4. Multi-Container Deployment

**Why Azure Container Apps?**
- Serverless with KEDA auto-scaling
- $0 when idle (vs. App Service always-on)
- Native Dapr integration for future microservices
- Managed certificates + custom domains

**Scaling Strategy:**
```yaml
# Scale 1-10 replicas based on:
- HTTP request queue length > 100
- CPU > 70%
- Memory > 80%
```

## Results & Impact

### Performance Metrics

| Metric | Before (Human Agents) | After (AI Bot) | Improvement |
|--------|-----------------------|----------------|-------------|
| **Average Response Time** | 4-6 hours | 2-4 seconds | **99.9% faster** |
| **Ticket Volume** | 1000/day | 650/day | **35% reduction** |
| **Knowledge Base Hits** | Manual search | 450/day | **45% deflection** |
| **L1 Agent Time Saved** | - | 12 hours/day | **1.5 FTE** |
| **User Satisfaction** | 3.2/5 | 4.5/5 | **+41%** |

### Cost Analysis

**Infrastructure Costs (Monthly):**
- Azure Container Apps: $120 (with auto-scaling)
- Azure OpenAI (GPT-4): $250 (15K conversations)
- Cosmos DB (400 RU/s x2): $50
- Azure Bot Service: $0 (free tier)
- **Total:** ~$420/month

**ROI:**
- L1 agent cost savings: $4,800/month (1.5 FTE @ $3,200/mo)
- Net savings: **$4,380/month**
- Payback period: **< 1 month**

### User Adoption

- **Week 1:** 150 users (early adopters)
- **Week 4:** 800 users (department-wide)
- **Month 3:** 2,500 users (company-wide)
- **Retention:** 92% of users continue using after trial

## Key Takeaways

### 1. **Agent > Direct LLM for Enterprise**

Simple prompt engineering isn't enough for complex workflows. Agentic patterns enable:
- Dynamic tool selection (search vs. ticket creation)
- Multi-step reasoning ("user can't log in" → check KB → no solution → create ticket)
- Error recovery (KB search fails → fallback to generic guidance)

### 2. **Vector Search is Non-Negotiable**

Keyword search returned irrelevant results 40% of the time. Vector search:
- Understands semantic similarity ("reset password" = "change credentials")
- Handles typos and variations
- Works across languages

### 3. **Observability Makes or Breaks Production**

Without structured logging, debugging agent decisions was impossible:
- Which tool did the agent choose?
- What were the search results?
- Why did ticket creation fail?

JSON logs with correlation IDs enabled root cause analysis in minutes.

### 4. **Serverless Shines for Chat Workloads**

Traditional App Service costs:
- Always-on: $140/month (B2 tier)
- Idle 60% of the time (nights/weekends)

Container Apps:
- Pay-per-use: $120/month
- Scales to 0 during idle
- Auto-scales to 10 during peak

### 5. **Integration Complexity > AI Complexity**

Most development time was spent on:
- ServiceNow API authentication quirks (OAuth token refresh)
- Bot Framework webhook signature validation
- Cosmos DB partition key design
- Teams Adaptive Card formatting

LangChain agent setup: **2 days**  
Enterprise integrations: **3 weeks**

## What I'd Do Differently

### 1. **Evaluation Framework from Day 1**

We didn't measure retrieval quality until production. Should have:
- Created golden Q&A test set (50+ examples)
- Tracked metrics: precision@3, recall, nDCG
- A/B tested embedding models

### 2. **Prompt Versioning**

Agent prompts changed 15+ times. No version control led to:
- "What broke?" debugging sessions
- Inability to rollback bad prompts
- Lost track of what improved performance

**Solution:** Store prompts in Azure App Configuration with versioning

### 3. **Rate Limiting**

One user sent 200 messages in 5 minutes (testing). No rate limit = $50 OpenAI bill spike.

**Should have:** Implemented token bucket algorithm (max 10 requests/minute/user)

### 4. **Conversation Threading**

Bot didn't distinguish between:
- New question in same thread
- Follow-up to previous question

**Fix:** Implement conversation state machine with explicit "new topic" detection

## Tech Stack

| Layer | Technology | Why This? |
|-------|-----------|-----------|
| **Agent Framework** | LangChain | Best-in-class for ReAct agents, extensive tool library |
| **LLM** | Azure OpenAI GPT-4 | Enterprise SLA, GDPR compliance, low latency (Sweden) |
| **Embeddings** | text-embedding-ada-002 | Cost-effective, 1536-dim sufficient for docs |
| **Vector DB** | Cosmos DB (vector search) | Already used for sessions, avoid separate service |
| **Messaging** | Microsoft Teams | Where users already work |
| **ITSM** | ServiceNow | Enterprise-standard, robust REST API |
| **Compute** | Azure Container Apps | Serverless, KEDA auto-scaling, managed certs |
| **Logging** | structlog + Log Analytics | JSON logs, KQL queries, alerting |
| **CI/CD** | Azure DevOps Pipelines | Multi-stage (build → test → deploy) |

---

## Architecture Diagram (Detailed)

```
┌─────────────────────────────────────────────────────────────┐
│                        Microsoft Teams                       │
│  User sends message: "I can't access my email"              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Azure Bot Service                         │
│  • Webhook endpoint: /api/messages                           │
│  • Validates signature, extracts Activity                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              Azure Container App (aiohttp)                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  echo_bot.py                                         │   │
│  │  • Receives Activity                                 │   │
│  │  • Extracts: text, user_id, conversation_id         │   │
│  │  • Calls agent.process_query()                      │   │
│  └─────────────────────┬───────────────────────────────┘   │
│                        │                                     │
│  ┌─────────────────────▼───────────────────────────────┐   │
│  │  agent.py (LangChain ReAct)                         │   │
│  │  ┌───────────────────────────────────────────────┐  │   │
│  │  │ Thought: "User has email access issue"        │  │   │
│  │  │ Action: search_kb("email access troubleshoot")│  │   │
│  │  └───────────────────────────────────────────────┘  │   │
│  │  ┌───────────────────────────────────────────────┐  │   │
│  │  │ Observation: [No relevant docs found]         │  │   │
│  │  │ Thought: "No KB solution, create ticket"      │  │   │
│  │  │ Action: create_ticket(...)                    │  │   │
│  │  └───────────────────────────────────────────────┘  │   │
│  └─────────────────────┬───────────────────────────────┘   │
└─────────────────────────┼───────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
  ┌──────────┐    ┌─────────────┐   ┌────────────┐
  │ Cosmos DB│    │ Azure OpenAI│   │ ServiceNow │
  │ • Sessions│   │ • GPT-4     │   │ • REST API │
  │ • Vectors │    │ • Embeddings│   │ • OAuth    │
  └──────────┘    └─────────────┘   └────────────┘
```

---

[View Full Code →](https://github.com/larusso94/enterprise-it-chatbot)

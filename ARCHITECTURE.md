# ARIA — Autonomous Repository Intelligence Agent

## Goal
A multiagent AI system that attaches to any GitHub repository,
understands it deeply, and acts on it autonomously — reviewing PRs,
catching regressions, onboarding developers, triaging bugs, and
getting smarter over time through fine-tuning on accepted feedback.

---

## What ARIA is NOT
Not a documentation generator (like Google Code Wiki).
Not a chat wrapper (like GitHub Copilot).
ARIA reads the repo and WORKS in it — files issues, reviews PRs,
runs tests, remembers decisions.

---

## Tech Stack

| Component         | Technology              | Purpose                              |
|-------------------|-------------------------|--------------------------------------|
| LLM               | Anthropic Claude        | Agent reasoning                      |
| Embeddings        | Voyage AI voyage-code-3 | Code semantic search (1024 dims)     |
| Vector store      | Qdrant (local Docker)   | Semantic retrieval                   |
| Graph store       | Neo4j (local Docker)    | Call graph, relationships            |
| Episodic memory   | Postgres                | Agent action history                 |
| Orchestration     | LangGraph               | Supervisor + worker agent workflows  |
| Tool protocol     | MCP                     | Standardized tool use                |
| Agent protocol    | A2A                     | Agent-to-agent communication         |
| Queue             | Redis                   | Async task processing                |
| Tracing           | Langfuse                | LLMOps observability                 |
| API server        | FastAPI                 | GitHub webhook receiver              |
| Deployment        | Docker + K8s            | Production infrastructure            |

---

## Project Structure
```
aria/
├── aria/
│   ├── agents/        # supervisor + worker agents
│   ├── memory/        # vector store, graph RAG, episodic memory
│   ├── tools/         # MCP servers, GitHub API, code executor
│   ├── evals/         # eval harness, LLM-as-judge
│   └── infra/         # queues, checkpointing, tracing
├── tests/
├── docker-compose.yml
├── ARCHITECTURE.md    # this file
└── CONTEXT.md         # teacher-student context for AI sessions
```

---

## Critical Design Decisions

### 1. Deterministic chunk IDs
IDs are generated using uuid5 (deterministic) not uuid4 (random).
Formula: uuid5(NAMESPACE_URL, f"{repo_url}::{file_path}::{qualified_name}")
Why: Allows safe re-ingestion. Same chunk = same ID = upsert updates
in place. No duplicates on re-run.

### 2. Qualified function names
Functions inside classes get qualified names: Flask.__init__ not __init__
Why: Prevents ID collision between same-named methods in different classes.
Implemented via recursive AST walker (not ast.walk which is flat).

### 3. Deduplication of property getter/setter
seen_names set per file prevents duplicate extraction of @property
getter + setter pairs which share the same function name.
First definition wins (getter before setter).

### 4. Chunking strategy
One function = one chunk. One class = one chunk.
NOT character-based chunking (loses semantic boundaries).
Nested functions inside functions are NOT extracted separately
(they belong to their parent's context).

### 5. Embedding model
voyage-code-3 (1024 dims) over sentence-transformers (384 dims).
Reason: trained specifically on code, understands that
authenticate() and verify_token() are semantically close.

---

## Database Schemas

### Qdrant Collection: aria_code_chunks
```
{
  id:      uuid5(repo_url::file_path::qualified_name),
  vector:  float[1024],  # voyage-code-3 output
  payload: {
    name:                string,  # qualified: Flask.__init__
    content:             string,  # full source code of chunk
    file_path:           string,  # relative path in repo
    chunk_type:          string,  # "function" or "class"
    start_line:          int,     # display only, not used in ID
    language:            string,  # "python"
    repo_url:            string,  # which repo
    last_commit_message: string,
    last_commit_author:  string,
  }
}
```

### Neo4j Graph (in progress)
Nodes:
  (:Function {name, file_path, repo_url})
  (:Class    {name, file_path, repo_url})
  (:Module   {name, file_path, repo_url})

Relationships:
  (:Function)-[:CALLS]->(:Function)
  (:Module)-[:IMPORTS]->(:Module)
  (:Class)-[:INHERITS]->(:Class)

### Postgres — Episodic Store (planned)
  agent_actions(id, agent_type, action, target, outcome, timestamp)
  feedback(id, action_id, accepted, reason, timestamp)

---

## Current Status

### Phase 1 — Repo Brain  (COMPLETE)
- [x] Step 1 — RepoReader: clones repo, walks git tree, extracts chunks
- [x] Step 2 — Embedder: embeds chunks with voyage-code-3, stores in Qdrant
- [x] Step 3 — Retriever: semantic search working, tested on Flask repo
- [x] Step 4 — Graph RAG: call graph builder → Neo4j (NEXT)
- [x] Step 5 — GraphRetriever: query relationships from Neo4j

### Phase 1 Results (Flask repo)
- 898 chunks extracted and embedded in Qdrant
- 1,975 nodes in Neo4j (1,677 functions, 83 classes, 215 modules)
- 3,302 relationships (2,782 CALLS, 477 IMPORTS, 43 INHERITS)
- Semantic search tested — accurate results on 3 queries
- Graph queries tested — blast radius, callers, dependencies working

### Phase 2 — Agent Team (NEXT)
- [ ] Supervisor agent (LangGraph)
- [ ] Code review agent
- [ ] Regression agent
- [ ] Bug triage agent
- [ ] Onboarding agent

### Phase 3 — MCP Tool Layer (planned)
- [ ] GitHub MCP server
- [ ] Code execution MCP server
- [ ] Notification MCP server

### Phase 4 — Evals and Guardrails (planned)
- [ ] LLM-as-judge eval harness
- [ ] Prompt injection guardrails
- [ ] RAGAS retrieval evaluation

### Phase 5 — LLMOps + Production (planned)
- [ ] Langfuse tracing
- [ ] Redis async queue
- [ ] Checkpointing
- [ ] Docker + K8s deployment

### Phase 6 — Fine-tuning (planned)
- [ ] Collect accepted/rejected feedback from episodic store
- [ ] LoRA fine-tune on repo-specific patterns
- [ ] DPO alignment on team preferences

---

## What We Tested
- Flask repo (github.com/pallets/flask)
- 898 chunks extracted (after deduplication)
- 898 vectors stored in Qdrant
- Retrieval tested on 3 queries — semantically accurate results

## Known Issues / Decisions Pending
- docs/ and tests/ folders are included in ingestion
  (todo: add optional filter for source-only ingestion)
- Only Python supported currently
  (JavaScript, Go support planned for Phase 3)
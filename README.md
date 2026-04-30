# ARIA — Autonomous Repository Intelligence Agent

ARIA is a GitHub App that reads your codebase, understands it deeply, and automatically acts on it — so your team spends less time on mechanical work and more time on real engineering.

---

## The problem it solves

When a developer opens a pull request, someone has to figure out what the change actually affects, check if anything downstream will break, and post a review with specific feedback. When a new developer joins the team, someone has to spend days explaining how everything works. When a bug is reported, someone has to figure out which part of the codebase is responsible.

All of this takes time. It gets missed when teams are busy. The knowledge about why things were done a certain way lives in people's heads, not in the codebase.

ARIA handles all of this automatically.

---

## How it works

When you install ARIA on a GitHub repository, it first reads and understands your entire codebase — then it watches for events and acts on them.

**Reading the codebase**

ARIA parses every file and builds two kinds of memory:

- A **semantic memory** (Qdrant) — so it can find code by meaning. Ask "how does authentication work?" and it finds the right code even if the word "authentication" isn't in the function name.
- A **relationship map** (Neo4j) — so it knows which functions call which other functions. When one thing changes, ARIA knows exactly what else might break.

**Watching and acting**

```
Something happens on GitHub
        ↓
ARIA receives it instantly
        ↓
The right agent handles it automatically
```

---

## The agents

### Retrieval Agent
The foundation everything else is built on. An autonomous agent that navigates the codebase using three tools — semantic search, graph traversal, and raw file reading. Other agents call this one to gather context before acting. It runs as a ReAct loop, chaining tool calls until it has enough information to answer.

### Onboarding Agent
When a new developer joins or asks a question about the codebase, the Onboarding Agent answers it. It takes a vague or technical question, dispatches the Retrieval Agent to find the relevant code, and converts the raw findings into a clear, educational explanation.

```
New developer asks: "How does the parallel tool execution work?"
        ↓
Onboarding Agent reads the question
        ↓
Dispatches Retrieval Scout to find relevant code
        ↓
Returns a structured guide explaining how it works,
where the code lives, and how the pieces connect
```

It uses claude-haiku for speed and cost efficiency — the Retrieval Agent does the heavy lifting, the Onboarding Agent just explains what was found.

### ARIA Verify *(in progress)*
A deterministic PR review agent. Runs mechanical checks on every pull request — graph integrity, security patterns, documentation sync, test coverage, pattern compliance, and PR scope — and posts verifiable facts to GitHub within 30 seconds. No LLM in the critical path.

---

## The MCP server

ARIA exposes its codebase intelligence as an MCP (Model Context Protocol) server running on port 8001. Any AI assistant — Claude, Cursor, or your own agent — can connect to it and query ARIA's memory directly.

Three tools:
- `search_code` — find relevant code by natural language query
- `get_dependencies` — find what calls a function and what it calls
- `read_file` — fetch the full source of any file

This turns ARIA into a shared brain for any AI tool in your workflow.

---

## Tech stack

| Component | What it does |
|---|---|
| FastAPI | Receives GitHub webhook events |
| Qdrant | Stores code as searchable vectors |
| Neo4j | Stores the function call graph |
| Voyage AI voyage-code-3 | Converts code into semantic vectors |
| Anthropic Claude | Powers all agents |
| Tree-sitter + Python AST | Parses code across languages |
| FastMCP | Exposes ARIA's memory as MCP tools |
| GitHub App (JWT auth) | Secure integration with GitHub |

---

## Project status

- ✅ Codebase ingestion pipeline
- ✅ Semantic search (Qdrant)
- ✅ Call graph (Neo4j)
- ✅ GitHub App server (webhooks, push, PR, installation)
- ✅ MCP server (3 tools)
- ✅ Retrieval Agent (autonomous codebase navigator)
- ✅ Onboarding Agent (answers developer questions)
- 🔧 ARIA Verify (deterministic PR checks) — in progress

---

*Built by Nikhil Yadav — github.com/NIKHIL-evan/ARIA*

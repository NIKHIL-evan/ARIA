import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from aria.memory.retriever import Retriever
from aria.memory.graph_retriever import GraphRetriever
from aria.agents.prompts import BUG_TRIAGE_SYSTEM, BUG_TRIAGE_USER

load_dotenv()


class ARIATriage(BaseModel):
    label: list[Literal["bug", "enhancement", "question", "high-priority", "low-priority"]] = Field(
        description="GitHub labels to apply to the issue."
    )
    assigned_to: str = Field(
        description="GitHub username of the person who owns the most relevant code. "
                    "Infer from last_commit_author in the retrieved chunks. "
                    "Use 'unknown' if no clear owner found."
    )
    related_code: list[str] = Field(
        description="List of qualified function or class names most likely responsible."
    )
    similar_past_issue: str = Field(
        description="Description of a similar past issue if found in context, "
                    "otherwise 'None found'."
    )
    investigation_path: str = Field(
        description="Step-by-step markdown investigation path for the assignee. "
                    "Be specific — name exact functions and files to look at first."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence in the triage. High if related code is clearly identified."
    )


retriever       = Retriever()
graph_retriever = GraphRetriever()

llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    max_tokens=1024,
    temperature=0.0,
).with_structured_output(ARIATriage)


def build_chunks_context(chunks: list) -> str:
    if not chunks:
        return "No related code found."
    sections = []
    for i, chunk in enumerate(chunks, 1):
        sections.append(
            f"[Chunk {i}] {chunk.name} — {chunk.file_path} "
            f"(last modified by {chunk.last_commit_author})\n"
            f"```python\n{chunk.content[:500]}\n```"
        )
    return "\n\n".join(sections)


def build_dependencies_context(modules: list) -> str:
    if not modules:
        return "No module dependencies found."
    return "\n".join(f"  - {m.file_path}" for m in modules)


def apply_triage_to_github(repo_full_name: str, issue_number: int, triage: ARIATriage) -> None:
    print("\n[GitHub STUB] Would apply triage:")
    print(f"  Repo        : {repo_full_name}")
    print(f"  Issue       : #{issue_number}")
    print(f"  Labels      : {triage.label}")
    print(f"  Assigned to : @{triage.assigned_to}")
    print(f"  Related code: {triage.related_code}")
    print(f"  Confidence  : {triage.confidence}")
    print(f"  Investigation path preview:\n{triage.investigation_path[:300]}...")


def bug_triage_agent(state: dict) -> dict:
    payload      = state["event_payload"]
    issue        = payload.get("issue", {})
    repo_name    = payload.get("repository", {}).get("full_name", "unknown/repo")
    issue_number = issue.get("number", 0)
    issue_title  = issue.get("title", "Unknown")
    issue_body   = issue.get("body", "")
    reporter     = issue.get("user", {}).get("login", "unknown")

    print(f"\n[BugTriageAgent] Triaging issue #{issue_number}: '{issue_title}' by {reporter}")

    search_query = f"{issue_title} {issue_body[:300]}"
    chunks = retriever.search(query=search_query, limit=7)
    print(f"  Retrieved {len(chunks)} related chunks from Qdrant")

    dependencies = []
    if chunks:
        top_chunk_name = chunks[0].name.split(".")[0]
        dependencies = graph_retriever.get_dependencies(top_chunk_name, limit=10)
        print(f"  Retrieved {len(dependencies)} module dependencies from Neo4j")

    past_feedback = "No past feedback available yet (episodic store not built)."

    user_message = BUG_TRIAGE_USER.format(
        repo         = repo_name,
        issue_number = issue_number,
        issue_title  = issue_title,
        issue_body   = issue_body or "No description provided.",
        reporter     = reporter,
        related_code = build_chunks_context(chunks),
        dependencies = build_dependencies_context(dependencies),
        past_feedback= past_feedback,
    )

    print("  Calling Claude for triage...")
    triage: ARIATriage = llm.invoke([
        SystemMessage(content=BUG_TRIAGE_SYSTEM),
        HumanMessage(content=user_message),
    ])

    apply_triage_to_github(repo_name, issue_number, triage)

    return {
        "retrieved_chunks": chunks,
        "graph_context"   : dependencies,
        "agent_output"    : triage.model_dump_json(indent=2),
    }
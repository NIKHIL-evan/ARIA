import os
import re
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from unidiff import PatchSet
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from aria.memory.retriever import Retriever
from aria.memory.graph_retriever import GraphRetriever
from aria.agents.prompts import CODE_REVIEW_SYSTEM, CODE_REVIEW_USER

load_dotenv()


class ARIAReview(BaseModel):
    review_status: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"] = Field(
        description="The official GitHub review state. "
                    "APPROVE if no issues found. "
                    "REQUEST_CHANGES if issues must be fixed before merge. "
                    "COMMENT if observations are worth noting but not blocking."
    )
    referenced_files: list[str] = Field(
        description="List of file paths you are referencing in your review. "
                    "Only include files that actually appear in the context given."
    )
    comment: str = Field(
        description="The full review as a markdown-formatted string. "
                    "Be specific. Reference exact file paths and function names. "
                    "Never be generic."
    )


retriever       = Retriever()
graph_retriever = GraphRetriever()

llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    max_tokens=1024,
    temperature=0.0,
).with_structured_output(ARIAReview)


def extract_changed_function_names(diff_str: str) -> list[str]:
    if not diff_str:
        return []

    patch = PatchSet.from_string(diff_str)
    changed_functions = set()
    func_pattern = re.compile(r'def\s+(\w+)\s*\(')

    for patched_file in patch:
        if patched_file.is_removed_file:
            continue
        try:
            for hunk in patched_file:
                header_match = func_pattern.search(hunk.section_header)
                if header_match:
                    changed_functions.add(header_match.group(1))
                for line in hunk:
                    if line.is_added:
                        line_match = func_pattern.search(line.value)
                        if line_match:
                            changed_functions.add(line_match.group(1))
        except Exception:
            continue

    return list(changed_functions)


def extract_diff_from_payload(payload: dict) -> str:
    return payload.get("diff") or payload.get("patch") or ""


def build_chunks_context(chunks: list) -> str:
    if not chunks:
        return "No semantically similar code found."
    sections = []
    for i, chunk in enumerate(chunks, 1):
        sections.append(
            f"[Chunk {i}] {chunk.name} — {chunk.file_path}\n"
            f"```python\n{chunk.content[:600]}\n```"
        )
    return "\n\n".join(sections)


def build_blast_radius_context(nodes: list) -> str:
    if not nodes:
        return "No downstream callers found."
    lines = []
    for node in sorted(nodes, key=lambda n: n.hops):
        lines.append(f"  - {node.qualified_name} ({node.file_path}) — {node.hops} hop(s) away")
    return "\n".join(lines)


def fetch_past_feedback(changed_functions: list[str]) -> str:
    return "No past feedback available yet (episodic store not built)."


def post_review_to_github(repo_full_name: str, pr_number: int, review: ARIAReview) -> None:
    print("\n[GitHub STUB] Would post the following review:")
    print(f"  Repo      : {repo_full_name}")
    print(f"  PR number : #{pr_number}")
    print(f"  Status    : {review.review_status}")
    print(f"  Files     : {review.referenced_files}")
    print(f"  Comment preview:\n{review.comment[:300]}...")


def code_review_agent(state: dict) -> dict:
    payload   = state["event_payload"]
    pr        = payload.get("pull_request", {})
    pr_title  = pr.get("title", "Unknown")
    pr_author = pr.get("user", {}).get("login", "Unknown")
    pr_branch = pr.get("head", {}).get("ref", "Unknown")
    pr_base   = pr.get("base", {}).get("ref", "main")
    pr_number = pr.get("number", 0)
    repo_name = payload.get("repository", {}).get("full_name", "unknown/repo")

    print(f"\n[CodeReviewAgent] Reviewing PR #{pr_number}: '{pr_title}' by {pr_author}")

    diff = extract_diff_from_payload(payload)
    changed_functions = extract_changed_function_names(diff)
    print(f"  Changed functions detected: {changed_functions or ['none detected']}")

    search_query = diff[:500] if diff else pr_title
    chunks = retriever.search(query=search_query, limit=5)
    print(f"  Retrieved {len(chunks)} chunks from Qdrant")

    blast_radius_nodes = []
    for fn_name in changed_functions:
        nodes = graph_retriever.get_blast_radius(fn_name, max_hops=3, limit=10)
        blast_radius_nodes.extend(nodes)

    seen: dict[str, int] = {}
    unique_blast = []
    for node in blast_radius_nodes:
        if node.qualified_name not in seen:
            seen[node.qualified_name] = node.hops
            unique_blast.append(node)
        elif node.hops < seen[node.qualified_name]:
            seen[node.qualified_name] = node.hops
            unique_blast = [
                n if n.qualified_name != node.qualified_name else node
                for n in unique_blast
            ]

    print(f"  Blast radius: {len(unique_blast)} affected functions")

    past_feedback = fetch_past_feedback(changed_functions)

    user_message = CODE_REVIEW_USER.format(
        pr_title         = pr_title,
        pr_author        = pr_author,
        pr_branch        = pr_branch,
        pr_base          = pr_base,
        diff             = diff or "Diff not available.",
        retrieved_chunks = build_chunks_context(chunks),
        blast_radius     = build_blast_radius_context(unique_blast),
        past_feedback    = past_feedback,
    )

    print("  Calling Claude for review...")
    review: ARIAReview = llm.invoke([
        SystemMessage(content=CODE_REVIEW_SYSTEM),
        HumanMessage(content=user_message),
    ])

    post_review_to_github(repo_name, pr_number, review)

    return {
        "retrieved_chunks": chunks,
        "graph_context"   : unique_blast,
        "agent_output"    : review.model_dump_json(indent=2),
    }
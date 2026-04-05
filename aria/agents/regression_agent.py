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
from aria.agents.prompts import REGRESSION_SYSTEM, REGRESSION_USER

load_dotenv()


# ---------------------------------------------------------------------------
# OUTPUT SHAPES
# ---------------------------------------------------------------------------

class SandboxResult(BaseModel):
    passed: bool
    failed_tests: list[str]
    stack_trace: str
    duration_seconds: float


class ARIAIssue(BaseModel):
    regression_status: Literal["REGRESSION_FOUND", "ALL_TESTS_PASSED"] = Field(
        description="REGRESSION_FOUND if any test failed. ALL_TESTS_PASSED if clean."
    )
    issue_title: str = Field(
        description="Specific GitHub issue title. Must include the commit sha, "
                    "the changed function name, and the failing test name. "
                    "Example: 'Regression in commit abc123 — register_blueprint breaks test_app_setup'"
    )
    issue_body: str = Field(
        description="Full markdown root cause analysis for the GitHub issue. "
                    "Must include: what changed, what broke, the call path from "
                    "changed function to failing test, and a suggested fix."
    )
    affected_tests: list[str] = Field(
        description="List of test function names that failed."
    )


# ---------------------------------------------------------------------------
# MODULE-LEVEL SINGLETONS
# ---------------------------------------------------------------------------

retriever       = Retriever()
graph_retriever = GraphRetriever()

llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    max_tokens=2048,
    temperature=0.0,
).with_structured_output(ARIAIssue)


# ---------------------------------------------------------------------------
# BRANCH FILTER
# ---------------------------------------------------------------------------

def is_main_branch(payload: dict) -> bool:
    return payload.get("ref") == "refs/heads/main"


# ---------------------------------------------------------------------------
# DIFF EXTRACTION
# ---------------------------------------------------------------------------

def extract_diff_from_push_payload(payload: dict) -> str:
    # Phase 2 stub: diff injected manually in test payload.
    # Phase 3: fetch from GitHub API using before/after SHA:
    #   GET /repos/{owner}/{repo}/compare/{before}...{after}
    return payload.get("diff") or ""


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


# ---------------------------------------------------------------------------
# BLAST RADIUS
# ---------------------------------------------------------------------------
MAX_REGRESSION_HOPS = int(os.getenv("ARIA_MAX_REGRESSION_HOPS", "4"))

def get_blast_radius(changed_functions: list[str]) -> list:
    blast_radius_nodes = []
    for fn_name in changed_functions:
        nodes = graph_retriever.get_blast_radius(fn_name, max_hops=MAX_REGRESSION_HOPS, limit=25)
        blast_radius_nodes.extend(nodes)

    # Deduplicate keeping lowest hop count
    seen: dict[str, int] = {}
    unique = []
    for node in blast_radius_nodes:
        if node.qualified_name not in seen:
            seen[node.qualified_name] = node.hops
            unique.append(node)
        elif node.hops < seen[node.qualified_name]:
            seen[node.qualified_name] = node.hops
            unique = [
                n if n.qualified_name != node.qualified_name else node
                for n in unique
            ]

    return unique


# ---------------------------------------------------------------------------
# TEST DISCOVERY
# ---------------------------------------------------------------------------

def discover_affected_tests(blast_radius_nodes: list, changed_functions: list[str]) -> list:
    """
    Step 1 — Neo4j: find functions in the blast radius that live in tests/ folder.
    These are direct test callers captured in the call graph.

    Step 2 — Qdrant fallback: if Neo4j finds nothing, search semantically
    using changed function names as queries, filtering for test files.
    """
    # Step 1: Neo4j — filter blast radius nodes that are test files
    test_nodes = [
        node for node in blast_radius_nodes
        if node.file_path and "test" in node.file_path.lower()
    ]

    if test_nodes:
        print(f"  Test discovery: {len(test_nodes)} tests found via Neo4j call graph")
        return test_nodes

    # Step 2: Qdrant fallback — semantic search restricted to test files
    print("  Test discovery: Neo4j found nothing, falling back to Qdrant semantic search")
    test_chunks = []
    for fn_name in changed_functions:
        chunks = retriever.search(query=f"test {fn_name}", limit=5)
        # Filter for chunks that live in test files
        test_chunks.extend([
            c for c in chunks
            if "test" in c.file_path.lower()
        ])

    # Deduplicate by file_path
    seen_paths = set()
    unique_chunks = []
    for chunk in test_chunks:
        if chunk.file_path not in seen_paths:
            seen_paths.add(chunk.file_path)
            unique_chunks.append(chunk)

    print(f"  Test discovery: {len(unique_chunks)} tests found via Qdrant fallback")
    return unique_chunks


# ---------------------------------------------------------------------------
# SANDBOX (STUB)
# ---------------------------------------------------------------------------

def run_tests_in_sandbox(test_nodes: list) -> SandboxResult:
    """
    STUB — Phase 3 will implement real execution via Docker or MCP.

    Always returns a deterministic failure so we can:
    - Test Claude's root cause analysis schema every run
    - Keep the test pipeline non-flaky
    - Exercise the hardest execution path consistently
    """
    test_names = [
        getattr(node, "qualified_name", None) or getattr(node, "name", "unknown_test")
        for node in test_nodes[:3]  # cap at 3 for the stub
    ]

    fake_stack_trace = ""
    if test_names:
        fake_stack_trace = (
            f"FAILED {test_names[0]}\n"
            f"  AssertionError: Expected return value to be a gateway result object,\n"
            f"  got dict instead.\n\n"
            f"  Full traceback:\n"
            f"    File 'tests/test_app.py', line 47, in {test_names[0]}\n"
            f"      assert isinstance(result, GatewayResult)\n"
            f"  AssertionError: isinstance check failed — got <class 'dict'>"
        )

    return SandboxResult(
        passed=False,
        failed_tests=test_names,
        stack_trace=fake_stack_trace,
        duration_seconds=3.42,
    )


# ---------------------------------------------------------------------------
# GITHUB ISSUE (STUB)
# ---------------------------------------------------------------------------

def file_github_issue(repo_full_name: str, issue: ARIAIssue) -> None:
    """
    STUB — Phase 3 will implement via GitHub API.

    Real implementation:
        POST /repos/{owner}/{repo}/issues
        Body: { "title": issue.issue_title, "body": issue.issue_body,
                "labels": ["bug", "regression", "high-priority"] }
    Requires GITHUB_TOKEN in .env.
    """
    print("\n[GitHub STUB] Would file the following issue:")
    print(f"  Repo   : {repo_full_name}")
    print(f"  Title  : {issue.issue_title}")
    print(f"  Tests  : {issue.affected_tests}")
    print(f"  Body preview:\n{issue.issue_body[:400]}...")


# ---------------------------------------------------------------------------
# CONTEXT BUILDERS
# ---------------------------------------------------------------------------

def build_blast_radius_context(nodes: list) -> str:
    if not nodes:
        return "No downstream callers found."
    lines = []
    for node in sorted(nodes, key=lambda n: n.hops):
        lines.append(
            f"  - {node.qualified_name} ({node.file_path}) — {node.hops} hop(s) away"
        )
    return "\n".join(lines)


def build_test_context(test_nodes: list) -> str:
    if not test_nodes:
        return "No affected tests found."
    lines = []
    for node in test_nodes:
        name = getattr(node, "qualified_name", None) or getattr(node, "name", "unknown")
        path = getattr(node, "file_path", "unknown")
        lines.append(f"  - {name} ({path})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# THE AGENT NODE
# ---------------------------------------------------------------------------

def regression_agent(state: dict) -> dict:
    payload   = state["event_payload"]
    repo_name = payload.get("repository", {}).get("full_name", "unknown/repo")
    pusher    = payload.get("pusher", {}).get("name", "unknown")
    after_sha = payload.get("after", "unknown")[:7]  # short sha for display
    ref       = payload.get("ref", "")

    print(f"\n[RegressionAgent] Push event on {ref} by {pusher} (commit {after_sha})")

    # --- Branch filter ---
    # Only act on pushes to main. Ignore all feature branches.
    if not is_main_branch(payload):
        print(f"  Ignoring push to {ref} — not main branch.")
        return {"agent_output": f"Ignored — push was to {ref}, not main."}

    print("  Push is to main — running regression analysis...")

    # --- Diff + changed functions ---
    diff = extract_diff_from_push_payload(payload)
    changed_functions = extract_changed_function_names(diff)
    print(f"  Changed functions: {changed_functions or ['none detected']}")

    if not changed_functions and not diff:
        return {"agent_output": "No diff available. Skipping regression analysis."}

    # --- Blast radius via Neo4j ---
    blast_radius = get_blast_radius(changed_functions)
    print(f"  Blast radius: {len(blast_radius)} affected functions")

    # --- Test discovery: Neo4j first, Qdrant fallback ---
    test_nodes = discover_affected_tests(blast_radius, changed_functions)

    if not test_nodes:
        print("  No affected tests found. Nothing to run.")
        return {"agent_output": "No affected tests found. Skipping sandbox execution."}

    # --- Run tests in sandbox ---
    print(f"  Running {len(test_nodes)} test(s) in sandbox...")
    sandbox_result = run_tests_in_sandbox(test_nodes)

    # --- Silent success: all tests passed ---
    if sandbox_result.passed:
        print("  All tests passed. No regression detected.")
        return {"agent_output": "All regression tests passed. No anomalies detected."}

    print(f"  Regression detected — {len(sandbox_result.failed_tests)} test(s) failed.")

    # --- Build prompt for Claude ---
    user_message = REGRESSION_USER.format(
        repo        = repo_name,
        pusher      = pusher,
        commit_sha  = after_sha,
        diff        = diff or "Diff not available.",
        blast_radius= build_blast_radius_context(blast_radius),
        test_context= build_test_context(test_nodes),
        failed_tests= "\n".join(sandbox_result.failed_tests),
        stack_trace = sandbox_result.stack_trace,
    )

    # --- Call Claude for root cause analysis ---
    print("  Calling Claude for root cause analysis...")
    issue: ARIAIssue = llm.invoke([
        SystemMessage(content=REGRESSION_SYSTEM),
        HumanMessage(content=user_message),
    ])

    # --- File GitHub issue (stub) ---
    file_github_issue(repo_name, issue)

    return {
        "retrieved_chunks": [],
        "graph_context"   : blast_radius,
        "agent_output"    : issue.model_dump_json(indent=2),
    }
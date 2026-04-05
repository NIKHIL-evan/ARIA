import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from aria.memory.retriever import Retriever
from aria.memory.graph_retriever import GraphRetriever
from aria.agents.prompts import ONBOARDING_SYSTEM, ONBOARDING_USER

load_dotenv()


class ReadingStep(BaseModel):
    order: int = Field(description="Reading order number starting from 1.")
    file_path: str = Field(description="File path to read.")
    reason: str = Field(description="One sentence explaining why this file matters.")


class ARIAOnboarding(BaseModel):
    welcome_summary: str = Field(
        description="2-3 sentence overview of the codebase for a new developer. "
                    "Mention the core purpose and key architectural patterns."
    )
    reading_order: list[ReadingStep] = Field(
        description="Ordered list of files to read first. Max 7 files. "
                    "Start with the entry point, then core modules, then examples."
    )
    key_concepts: list[str] = Field(
        description="List of 3-5 concepts the developer must understand before "
                    "touching any code. Be specific to this codebase."
    )
    first_task_suggestion: str = Field(
        description="A concrete, small, safe first task the new developer can do "
                    "to get familiar with the codebase without breaking anything."
    )


retriever       = Retriever()
graph_retriever = GraphRetriever()

llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    max_tokens=1024,
    temperature=0.0,
).with_structured_output(ARIAOnboarding)


def get_most_connected_modules(limit: int = 10) -> list:
    with graph_retriever.driver.session() as session:
        result = session.run("""
            MATCH (m:Module)-[:IMPORTS]->(dep:Module)
            WITH m, count(dep) AS import_count
            ORDER BY import_count DESC
            LIMIT $limit
            RETURN m.file_path AS file_path, m.repo_url AS repo_url, import_count
        """, limit=limit)
        return [record for record in result]


def build_architecture_context(modules: list) -> str:
    if not modules:
        return "No module data available."
    lines = []
    for record in modules:
        lines.append(
            f"  - {record['file_path']} ({record['import_count']} imports)"
        )
    return "\n".join(lines)


def build_chunks_context(chunks: list) -> str:
    if not chunks:
        return "No code samples found."
    sections = []
    for i, chunk in enumerate(chunks, 1):
        sections.append(
            f"[{i}] {chunk.name} — {chunk.file_path}\n"
            f"```python\n{chunk.content[:400]}\n```"
        )
    return "\n\n".join(sections)


def send_onboarding_to_github(repo_full_name: str, member_login: str, onboarding: ARIAOnboarding) -> None:
    print("\n[GitHub STUB] Would post onboarding comment:")
    print(f"  Repo    : {repo_full_name}")
    print(f"  Member  : @{member_login}")
    print(f"  Summary : {onboarding.welcome_summary[:200]}")
    print(f"  Reading order: {len(onboarding.reading_order)} files")
    print(f"  Key concepts : {onboarding.key_concepts}")


def onboarding_agent(state: dict) -> dict:
    payload      = state["event_payload"]
    repo_name    = payload.get("repository", {}).get("full_name", "unknown/repo")
    member_login = payload.get("member", {}).get("login", "new_developer")

    print(f"\n[OnboardingAgent] Onboarding @{member_login} to {repo_name}")

    core_chunks = retriever.search(query="application entry point core module setup", limit=5)
    print(f"  Retrieved {len(core_chunks)} core module chunks from Qdrant")

    connected_modules = get_most_connected_modules(limit=8)
    print(f"  Retrieved {len(connected_modules)} most connected modules from Neo4j")

    user_message = ONBOARDING_USER.format(
        repo             = repo_name,
        member           = member_login,
        core_chunks      = build_chunks_context(core_chunks),
        architecture     = build_architecture_context(connected_modules),
    )

    print("  Calling Claude for onboarding guide...")
    onboarding: ARIAOnboarding = llm.invoke([
        SystemMessage(content=ONBOARDING_SYSTEM),
        HumanMessage(content=user_message),
    ])

    send_onboarding_to_github(repo_name, member_login, onboarding)

    return {
        "retrieved_chunks": core_chunks,
        "graph_context"   : [],
        "agent_output"    : onboarding.model_dump_json(indent=2),
    }
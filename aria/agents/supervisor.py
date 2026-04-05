"""
aria/agents/supervisor.py

The entry point for all GitHub webhook events.
Reads the event type and routes to the correct worker agent.

LangGraph flow:
    START → route_event → {code_review | regression | bug_triage | onboarding} → END
"""

from typing import Any
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END

from aria.memory.retriever import Retriever
from aria.memory.graph_retriever import GraphRetriever
from aria.agents.regression_agent import regression_agent
from aria.agents.code_review_agent import code_review_agent
from aria.agents.onboarding_agent import onboarding_agent
from aria.agents.bug_triage_agent import bug_triage_agent

class ARIAState(TypedDict):
    # Raw GitHub webhook payload — set once at entry, never mutated
    event_type: str                  # "pull_request", "push", "issues", "member"
    event_payload: dict[str, Any]    # full JSON from GitHub

    # Populated by worker agents as they run
    retrieved_chunks: list[Any]      # semantic search results from Qdrant
    graph_context: list[Any]         # relationship results from Neo4j
    agent_output: str                # final text output from the worker



def route_event(state: ARIAState) -> str:
    """
    Reads event_type from state and returns the name of the next node.
    LangGraph uses this return value to pick the edge to follow.
    """
    event = state["event_type"]

    if event == "pull_request":
        return "code_review_agent"
    elif event == "push":
        return "regression_agent"
    elif event == "issues":
        return "bug_triage_agent"
    elif event == "member":
        return "onboarding_agent"
    else:
        # Unknown event — log and stop gracefully
        print(f"[Supervisor] Unknown event type: '{event}'. Routing to END.")
        return END

# --------------------------------------------------------------------------
# 4. BUILD THE GRAPH
# --------------------------------------------------------------------------

def build_supervisor() -> StateGraph:
    """
    Assembles the LangGraph StateGraph.
    Returns a compiled graph ready to invoke with an ARIAState dict.
    """
    graph = StateGraph(ARIAState)

    # --- Register nodes ---
    # Each string name here must match what route_event() returns.
    graph.add_node("code_review_agent", code_review_agent)
    graph.add_node("regression_agent",  regression_agent)
    graph.add_node("bug_triage_agent",  bug_triage_agent)
    graph.add_node("onboarding_agent",  onboarding_agent)

    # --- Conditional entry edge ---
    # START fires route_event(). Its return value picks the destination.
    graph.add_conditional_edges(
        START,
        route_event,
        {
            # return value of route_event → node name to go to
            "code_review_agent" : "code_review_agent",
            "regression_agent"  : "regression_agent",
            "bug_triage_agent"  : "bug_triage_agent",
            "onboarding_agent"  : "onboarding_agent",
        }
    )

    # --- Each worker goes straight to END ---
    graph.add_edge("code_review_agent", END)
    graph.add_edge("regression_agent",  END)
    graph.add_edge("bug_triage_agent",  END)
    graph.add_edge("onboarding_agent",  END)

    return graph.compile()


# ---------------------------------------------------------------------------
# 5. MODULE-LEVEL SINGLETON
# ---------------------------------------------------------------------------
# Build once when the module is imported.
# FastAPI webhook handler will import and call: supervisor.invoke(state)

supervisor = build_supervisor()

from aria.agents.supervisor import supervisor, ARIAState

VALID_DIFF = (
    "--- a/src/payments.py\n"
    "+++ b/src/payments.py\n"
    "@@ -5,5 +5,8 @@ def process_payment(self, amount, user_id):\n"
    "     def process_payment(self, amount, user_id):\n"
    "         result = self.gateway.charge(amount)\n"
    "-        return result\n"
    "+        return {\n"
    "+            \"status\": result.status,\n"
    "+            \"transaction_id\": result.id,\n"
    "+        }\n"
    " \n"
    "     def refund(self, transaction_id):\n"
)

FAKE_PR_PAYLOAD = {
    "action": "opened",
    "number": 42,
    "pull_request": {
        "number" : 42,
        "title"  : "Refactor process_payment to return structured response",
        "user"   : {"login": "arjun"},
        "head"   : {"ref": "feature/payment-refactor", "sha": "abc123"},
        "base"   : {"ref": "main"},
    },
    "repository": {
        "full_name": "arjun/myproject",
        "clone_url": "https://github.com/arjun/myproject.git",
    },
    "diff": VALID_DIFF,
}


def main():
    state: ARIAState = {
        "event_type"      : "pull_request",
        "event_payload"   : FAKE_PR_PAYLOAD,
        "retrieved_chunks": [],
        "graph_context"   : [],
        "agent_output"    : "",
    }

    print("=" * 60)
    print("Invoking ARIA supervisor with fake PR payload...")
    print("=" * 60)

    result = supervisor.invoke(state)

    print("\n" + "=" * 60)
    print("FINAL agent_output:")
    print("=" * 60)
    print(result["agent_output"])

    print("\n" + "=" * 60)
    print(f"Chunks retrieved : {len(result['retrieved_chunks'])}")
    print(f"Blast radius     : {len(result['graph_context'])} functions")
    print("=" * 60)


if __name__ == "__main__":
    main()
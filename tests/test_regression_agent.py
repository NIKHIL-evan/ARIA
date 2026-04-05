from aria.agents.supervisor import supervisor, ARIAState

VALID_DIFF = (
    "--- a/src/flask/app.py\n"
    "+++ b/src/flask/app.py\n"
    "@@ -1,6 +1,6 @@     def register_blueprint(self, blueprint, **options):\n"
    " class App:\n"
    "     def register_blueprint(self, blueprint, **options):\n"
    "-        self._blueprints[blueprint.name] = blueprint\n"
    "+        self._blueprints[blueprint.name] = (blueprint, options)\n"
    "         blueprint.register(self, options)\n"
    " \n"
    "     def add_url_rule(self, rule, endpoint=None):\n"
)

FAKE_PUSH_PAYLOAD = {
    "ref"   : "refs/heads/main",
    "before": "aaaaaa",
    "after" : "bbbbbb",
    "pusher": {"name": "arjun"},
    "repository": {
        "full_name": "pallets/flask",
        "clone_url": "https://github.com/pallets/flask.git",
    },
    "commits": [
        {
            "id"      : "bbbbbb",
            "message" : "Change register_blueprint to store options alongside blueprint",
            "modified": ["src/flask/app.py"],
        }
    ],
    "diff": VALID_DIFF,
}


def main():
    state: ARIAState = {
        "event_type"      : "push",
        "event_payload"   : FAKE_PUSH_PAYLOAD,
        "retrieved_chunks": [],
        "graph_context"   : [],
        "agent_output"    : "",
    }

    print("=" * 60)
    print("Invoking ARIA supervisor with fake push payload...")
    print("=" * 60)

    result = supervisor.invoke(state)

    print("\n" + "=" * 60)
    print("FINAL agent_output:")
    print("=" * 60)
    print(result["agent_output"])
    print("\n" + "=" * 60)
    print(f"Blast radius : {len(result['graph_context'])} functions")
    print("=" * 60)


if __name__ == "__main__":
    main()
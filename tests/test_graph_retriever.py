import sys
sys.path.append(".")

from aria.memory.graph_retriever import GraphRetriever

gr = GraphRetriever()

# Test 1 — direct callers
print("=" * 60)
print("TEST 1: Who directly calls register_blueprint?")
print("=" * 60)
callers = gr.get_callers("register_blueprint")
for c in callers:
    print(f"  {c.qualified_name}  →  {c.file_path}")

# Test 2 — blast radius
print("\n" + "=" * 60)
print("TEST 2: Full blast radius (3 hops)")
print("=" * 60)
blast = gr.get_blast_radius("register_blueprint", max_hops=3)
for b in blast:
    print(f"  hop {b.hops}  {b.qualified_name}  →  {b.file_path}")

# Test 3 — dependencies
print("\n" + "=" * 60)
print("TEST 3: What does app.py import?")
print("=" * 60)
deps = gr.get_dependencies("app.py")
for d in deps:
    print(f"  {d.file_path}")

# Test 4 — inheritance
print("\n" + "=" * 60)
print("TEST 4: What inherits from Scaffold?")
print("=" * 60)
children = gr.get_inheritance_chain("Scaffold")
for c in children:
    print(f"  {c.qualified_name}  →  {c.file_path}")

gr.close()
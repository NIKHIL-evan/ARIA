import sys
sys.path.append(".")

from aria.memory.graph_builder import GraphBuilder
from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

load_dotenv()

# Step 1 — build the graph
print("Building call graph for Flask...")
builder = GraphBuilder("https://github.com/pallets/flask")
builder.build()
builder.close()
print("Graph built.\n")

# Step 2 — query it to verify
driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD"))
)

with driver.session() as session:

    # Count nodes
    result = session.run("MATCH (f:Function) RETURN count(f) AS n")
    print(f"Function nodes:  {result.single()['n']}")

    result = session.run("MATCH (c:Class) RETURN count(c) AS n")
    print(f"Class nodes:     {result.single()['n']}")

    result = session.run("MATCH (m:Module) RETURN count(m) AS n")
    print(f"Module nodes:    {result.single()['n']}")

    # Count relationships
    result = session.run("MATCH ()-[r:CALLS]->() RETURN count(r) AS n")
    print(f"CALLS edges:     {result.single()['n']}")

    result = session.run("MATCH ()-[r:IMPORTS]->() RETURN count(r) AS n")
    print(f"IMPORTS edges:   {result.single()['n']}")

    result = session.run("MATCH ()-[r:INHERITS]->() RETURN count(r) AS n")
    print(f"INHERITS edges:  {result.single()['n']}")

    # The real test — find everything that calls register_blueprint
    print("\nWhat calls register_blueprint?")
    result = session.run("""
        MATCH (caller:Function)-[:CALLS]->(f:Function)
        WHERE f.qualified_name CONTAINS 'register_blueprint'
        RETURN caller.qualified_name AS caller,
               caller.file_path     AS file
        LIMIT 10
    """)
    for record in result:
        print(f"  {record['caller']}  →  {record['file']}")

driver.close()
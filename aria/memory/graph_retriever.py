import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from pydantic import BaseModel

load_dotenv()


# --- Data shapes for graph query results ---
# Same philosophy as CodeChunk — typed outputs, not raw dicts.
# Every result is a Pydantic model so agents get structured data.

class FunctionNode(BaseModel):
    qualified_name: str
    file_path: str
    repo_url: str
    hops: int = 0        # how many CALLS edges away from the target


class ModuleNode(BaseModel):
    file_path: str
    repo_url: str


class GraphRetriever:
    """
    Query layer on top of Neo4j.
    Answers relationship questions the vector store cannot.
    
    Three question types:
    1. Who calls this function?        → get_callers()
    2. What is the blast radius?       → get_blast_radius()
    3. What does this module import?   → get_dependencies()
    """

    def __init__(self):
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(
                os.getenv("NEO4J_USER"),
                os.getenv("NEO4J_PASSWORD")
            )
        )

    def get_callers(
        self,
        function_name: str,
        limit: int = 10
    ) -> list[FunctionNode]:
        """
        Returns all functions that directly call the given function.
        
        Used by: code review agent when a PR changes a function.
        Question answered: "what breaks if I change this?"
        
        Example:
            get_callers("register_blueprint")
            → [create_app, test_setup, init_app, ...]
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (caller:Function)-[:CALLS]->(f:Function)
                WHERE f.qualified_name CONTAINS $name
                  AND caller.repo_url = $repo_url
                RETURN caller.qualified_name AS qualified_name,
                       caller.file_path      AS file_path,
                       caller.repo_url       AS repo_url
                LIMIT $limit
            """,
                name=function_name,
                repo_url=os.getenv("ARIA_REPO_URL", ""),
                limit=limit
            )

            # Convert raw Neo4j records into typed FunctionNode objects
            callers = []
            for record in result:
                # record is like a dict — access fields by key
                callers.append(FunctionNode(
                    qualified_name=record["qualified_name"],
                    file_path=record["file_path"],
                    repo_url=record["repo_url"],
                    hops=1   # direct callers are always 1 hop away
                ))
            return callers

    def get_blast_radius(
        self,
        function_name: str,
        max_hops: int = 3,
        limit: int = 25
    ) -> list[FunctionNode]:
        """
        Returns everything that depends on the given function,
        up to max_hops levels deep.
        
        *1..3 in Cypher means "follow CALLS edges 1 to 3 times"
        This gives us direct callers AND callers of callers.
        
        Used by: regression agent, code review agent.
        Question answered: "what is the full impact of this change?"
        
        Example:
            get_blast_radius("register_blueprint", max_hops=3)
            → [create_app (1 hop), test_setup (2 hops), run_app (3 hops)]
        """
        with self.driver.session() as session:
            # We build the *1..N part dynamically based on max_hops.
            # Cypher doesn't support parameterized hop counts so we
            # use string formatting here — safe because max_hops is
            # an integer we control, not user input.
            query = f"""
                MATCH path = (caller:Function)-[:CALLS*1..{max_hops}]->(f:Function)
                WHERE f.qualified_name CONTAINS $name
                RETURN DISTINCT
                       caller.qualified_name AS qualified_name,
                       caller.file_path      AS file_path,
                       caller.repo_url       AS repo_url,
                       min(length(path))     AS hops
                ORDER BY hops
                LIMIT $limit
            """
            result = session.run(query, name=function_name, limit=limit)

            nodes = []
            for record in result:
                nodes.append(FunctionNode(
                    qualified_name=record["qualified_name"],
                    file_path=record["file_path"],
                    repo_url=record["repo_url"],
                    hops=record["hops"]
                ))
            return nodes

    def get_dependencies(
        self,
        module_name: str,
        limit: int = 20
    ) -> list[ModuleNode]:
        """
        Returns all modules that the given module imports from.
        
        Used by: onboarding agent to explain architecture,
                 refactor agent to find circular dependencies.
        Question answered: "what does this module depend on?"
        
        Example:
            get_dependencies("app.py")
            → [blueprints.py, sessions.py, ctx.py, ...]
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (m:Module)-[:IMPORTS]->(dep:Module)
                WHERE m.file_path CONTAINS $module
                RETURN dep.file_path AS file_path,
                       dep.repo_url  AS repo_url
                LIMIT $limit
            """,
                module=module_name,
                limit=limit
            )

            return [
                ModuleNode(
                    file_path=record["file_path"],
                    repo_url=record["repo_url"]
                )
                for record in result
            ]

    def get_inheritance_chain(
        self,
        class_name: str
    ) -> list[FunctionNode]:
        """
        Returns all classes that inherit from the given class.
        
        Used by: code review agent when a base class changes.
        Question answered: "what child classes are affected?"
        
        Example:
            get_inheritance_chain("Scaffold")
            → [Flask, Blueprint]
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (child:Class)-[:INHERITS]->(parent:Class)
                WHERE parent.qualified_name CONTAINS $name
                RETURN child.qualified_name AS qualified_name,
                       child.file_path      AS file_path,
                       child.repo_url       AS repo_url
            """, name=class_name)

            return [
                FunctionNode(
                    qualified_name=record["qualified_name"],
                    file_path=record["file_path"],
                    repo_url=record["repo_url"],
                    hops=1
                )
                for record in result
            ]

    def close(self):
        self.driver.close()
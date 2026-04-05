import ast
import os
import tempfile
import shutil
import stat
from pathlib import Path
from dotenv import load_dotenv
from git import Repo
from neo4j import GraphDatabase

load_dotenv()


class GraphBuilder:
    """
    Reads a git repository and builds a call graph in Neo4j.
    
    Extracts three relationship types:
    - CALLS:    function A calls function B
    - IMPORTS:  module A imports from module B  
    - INHERITS: class A inherits from class B
    
    Uses MERGE so it is safe to re-run — existing nodes and
    relationships are updated, not duplicated.
    """

    def __init__(self, repo_url: str):
        self.repo_url = repo_url

        # Connect to Neo4j using credentials from .env
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(
                os.getenv("NEO4J_USER"),
                os.getenv("NEO4J_PASSWORD")
            )
        )

    def build(self):
        """
        Main entry point. Clones the repo, walks every Python file,
        extracts relationships, stores everything in Neo4j.
        """
        def force_delete(action, name, exc):
            # Same Windows fix we used in repo_reader
            os.chmod(name, stat.S_IWRITE)
            os.remove(name)

        tmp_dir = tempfile.mkdtemp()

        try:
            print(f"Cloning {self.repo_url} for graph analysis...")
            repo = Repo.clone_from(self.repo_url, tmp_dir)
            print("Clone complete. Building graph...")

            self._create_indexes()

            base_path = Path(tmp_dir)
            files_processed = 0

            for py_file in base_path.rglob("*.py"):
                # Skip non-source folders
                if any(part in py_file.parts for part in [
                    ".venv", "__pycache__", "node_modules", ".git"
                ]):
                    continue

                relative_path = str(
                    py_file.relative_to(base_path)
                )

                try:
                    source = py_file.read_text(encoding="utf-8")
                except Exception:
                    continue

                # Process this file — extract all nodes + relationships
                self._process_file(source, relative_path)
                files_processed += 1

            repo.close()
            print(f"Graph built from {files_processed} files.")

        finally:
            shutil.rmtree(tmp_dir, onexc=force_delete)

    def _create_indexes(self):
        """
        Creates Neo4j indexes on the properties we query most often.
        An index makes MATCH queries fast — without it Neo4j scans
        every single node to find a match (slow at scale).
        Think of it like an index in a book — jump straight to the
        page instead of reading every page.
        """
        with self.driver.session() as session:
            # Index on Function.qualified_name — we look this up constantly
            session.run("""
                CREATE INDEX function_name IF NOT EXISTS
                FOR (f:Function) ON (f.qualified_name)
            """)
            # Index on Class.qualified_name
            session.run("""
                CREATE INDEX class_name IF NOT EXISTS
                FOR (c:Class) ON (c.qualified_name)
            """)
            # Index on Module.file_path
            session.run("""
                CREATE INDEX module_path IF NOT EXISTS
                FOR (m:Module) ON (m.file_path)
            """)

    def _process_file(self, source: str, file_path: str):
        """
        Parses one Python file and extracts:
        1. Module node for the file itself
        2. Function and Class nodes defined in the file
        3. CALLS relationships (what each function calls)
        4. IMPORTS relationships (what this module imports)
        5. INHERITS relationships (what each class extends)
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return

        # --- 1. Create the Module node for this file ---
        self._merge_module(file_path)

        # --- 2. Extract IMPORTS from top-level import statements ---
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # "from flask import Blueprint" 
                # node.module = "flask"
                if node.module:
                    self._merge_import(file_path, node.module)

            elif isinstance(node, ast.Import):
                # "import os"
                # node.names = [alias(name='os')]
                for alias in node.names:
                    self._merge_import(file_path, alias.name)

        # --- 3. Extract functions, classes, CALLS, INHERITS ---
        # We reuse the same recursive pattern from repo_reader
        # to get qualified names consistently
        self._walk_nodes(tree, file_path, parent_path=None)

    def _walk_nodes(
        self,
        tree,
        file_path: str,
        parent_path: str | None
    ):
        """
        Recursively walks the AST to extract Function and Class nodes
        along with their CALLS and INHERITS relationships.
        Uses the same parent_path pattern as repo_reader for
        consistent qualified names.
        """
        # Determine which nodes to walk:
        # At module level → iter_child_nodes (top level only)
        # Inside a class  → node.body (already a list)
        if parent_path is None:
            nodes_to_visit = list(ast.iter_child_nodes(tree))
        else:
            nodes_to_visit = list(tree.body) if hasattr(tree, 'body') else []

        for node in nodes_to_visit:
            if isinstance(node, ast.ClassDef):
                qualified_name = (
                    f"{parent_path}.{node.name}"
                    if parent_path else node.name
                )

                # Create Class node in Neo4j
                self._merge_class(qualified_name, file_path)

                # Extract INHERITS relationships
                # node.bases is a list of what this class extends
                for base in node.bases:
                    base_name = self._get_call_name(base)
                    if base_name:
                        self._merge_inherits(
                            qualified_name, base_name, file_path
                        )

                # Recurse into class body to find methods
                self._walk_nodes(node, file_path, qualified_name)

            elif isinstance(node, (
                ast.FunctionDef, ast.AsyncFunctionDef
            )):
                qualified_name = (
                    f"{parent_path}.{node.name}"
                    if parent_path else node.name
                )

                # Create Function node in Neo4j
                self._merge_function(qualified_name, file_path)

                # Extract CALLS — walk the function body looking
                # for ast.Call nodes (any function call expression)
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        callee_name = self._get_call_name(child.func)
                        if callee_name:
                            self._merge_calls(
                                qualified_name,
                                callee_name,
                                file_path
                            )

    def _get_call_name(self, node) -> str | None:
        """
        Extracts the name from a call expression AST node.
        
        Handles three patterns:
        
        Pattern 1 — simple name:
          register_blueprint()
          node = ast.Name(id='register_blueprint')
          returns: "register_blueprint"
        
        Pattern 2 — attribute call:
          app.register_blueprint()
          node = ast.Attribute(attr='register_blueprint', 
                               value=ast.Name(id='app'))
          returns: "app.register_blueprint"
        
        Pattern 3 — chained:
          self.app.register_blueprint()
          returns: "app.register_blueprint" (drops 'self')
        """
        if isinstance(node, ast.Name):
            return node.id

        elif isinstance(node, ast.Attribute):
            # Get the object being called on
            value = node.value
            if isinstance(value, ast.Name):
                # Skip 'self' — self.foo() should just be "foo"
                # because we don't know the concrete class of self
                # at static analysis time
                if value.id == "self":
                    return node.attr
                return f"{value.id}.{node.attr}"
            elif isinstance(value, ast.Attribute):
                # Chained: a.b.c() → just take the last two parts
                inner = self._get_call_name(value)
                if inner:
                    return f"{inner}.{node.attr}"

        return None  # too complex to resolve statically

    # --- Neo4j write methods ---
    # Each one opens a session and runs a Cypher MERGE statement.
    # MERGE = create if not exists, match if exists.
    # This makes all writes idempotent — safe to re-run.

    def _merge_module(self, file_path: str):
        with self.driver.session() as session:
            session.run("""
                MERGE (m:Module {
                    file_path: $file_path,
                    repo_url:  $repo_url
                })
            """, file_path=file_path, repo_url=self.repo_url)

    def _merge_function(self, qualified_name: str, file_path: str):
        with self.driver.session() as session:
            session.run("""
                MERGE (f:Function {
                    qualified_name: $qualified_name,
                    repo_url:       $repo_url
                })
                SET f.file_path = $file_path
            """, qualified_name=qualified_name,
                file_path=file_path,
                repo_url=self.repo_url)

    def _merge_class(self, qualified_name: str, file_path: str):
        with self.driver.session() as session:
            session.run("""
                MERGE (c:Class {
                    qualified_name: $qualified_name,
                    repo_url:       $repo_url
                })
                SET c.file_path = $file_path
            """, qualified_name=qualified_name,
                file_path=file_path,
                repo_url=self.repo_url)

    def _merge_import(self, from_file: str, to_module: str):
        with self.driver.session() as session:
            session.run("""
                MERGE (a:Module {
                    file_path: $from_file,
                    repo_url:  $repo_url
                })
                MERGE (b:Module {
                    file_path: $to_module,
                    repo_url:  $repo_url
                })
                MERGE (a)-[:IMPORTS]->(b)
            """, from_file=from_file,
                to_module=to_module,
                repo_url=self.repo_url)

    def _merge_calls(
        self,
        caller: str,
        callee: str,
        file_path: str
    ):
        with self.driver.session() as session:
            session.run("""
                MERGE (a:Function {
                    qualified_name: $caller,
                    repo_url:       $repo_url
                })
                MERGE (b:Function {
                    qualified_name: $callee,
                    repo_url:       $repo_url
                })
                MERGE (a)-[:CALLS]->(b)
            """, caller=caller,
                callee=callee,
                repo_url=self.repo_url)

    def _merge_inherits(
        self,
        child: str,
        parent: str,
        file_path: str
    ):
        with self.driver.session() as session:
            session.run("""
                MERGE (a:Class {
                    qualified_name: $child,
                    repo_url:       $repo_url
                })
                MERGE (b:Class {
                    qualified_name: $parent,
                    repo_url:       $repo_url
                })
                MERGE (a)-[:INHERITS]->(b)
            """, child=child,
                parent=parent,
                repo_url=self.repo_url)

    def close(self):
        """Always call this when done — closes the Neo4j connection."""
        self.driver.close()
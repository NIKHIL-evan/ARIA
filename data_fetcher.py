import os
from urllib.parse import urlparse
from neo4j import GraphDatabase
from dotenv import load_dotenv

from aria.memory.qdrant_store import QdrantStore
from aria.infra.github_client import GitHubClient

load_dotenv()


class DataFetcher:
    """
    Lightweight data access layer for the MCP server.
    No LLM calls. No API credits. Just raw data retrieval.
    """

    VALID_DIRECTIONS = {"callers", "dependencies", "both"}

    def __init__(self):
        uri      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user     = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")

        self.driver       = GraphDatabase.driver(uri, auth=(user, password))
        self.qdrant_store = QdrantStore()
        self.github_client = GitHubClient(
            app_id   = os.getenv("GITHUB_APP_ID"),
            pem_path = os.getenv("GITHUB_PEM_PATH"),
        )

    def search_semantic_code(self, query: str, repo_url: str, limit: int = 5) -> str:
        try:
            results = self.qdrant_store.search(query=query, repo_url=repo_url, limit=limit)

            if not results:
                return f"No semantic matches found for '{query}'."

            out = [f"--- Semantic Search: '{query}' ---"]
            for i, chunk in enumerate(results, 1):
                out.append(
                    f"\n[{i}]"
                    f"\nFile     : {chunk.file_path}"
                    f"\nType     : {chunk.node_type} | Name: {chunk.name}"
                    f"\nSignature: {chunk.signature}"
                    f"\nDocstring: {chunk.docstring}"
                    f"\nCalls    : {chunk.calls}"
                    f"\nImports  : {chunk.imports}"
                    f"\nLines    : {chunk.line_range}"
                    f"\nCommit   : {chunk.last_commit_message} ({chunk.last_commit_author})"
                    f"\nCode:\n{chunk.content}"
                    f"\n{'-' * 40}"
                )
            return "\n".join(out)

        except Exception as e:
            return f"Error querying Qdrant: {str(e)}"

    def get_structural_dependencies(
        self, node_name: str, repo_url: str, direction: str = "both"
    ) -> str:
        if direction not in self.VALID_DIRECTIONS:
            return f"Invalid direction '{direction}'. Must be one of {self.VALID_DIRECTIONS}."

        query_out = """
            MATCH (source:CodeNode {name: $node_name, repo_url: $repo_url})-[r]->(target:CodeNode)
            RETURN type(r) AS relationship, target.name AS node_name,
                   target.type AS node_type, target.file_path AS file_path
        """
        query_in = """
            MATCH (target:CodeNode {name: $node_name, repo_url: $repo_url})<-[r]-(source:CodeNode)
            RETURN type(r) AS relationship, source.name AS node_name,
                   source.type AS node_type, source.file_path AS file_path
        """

        results = []
        try:
            with self.driver.session() as session:
                if direction in ("dependencies", "both"):
                    rows = session.run(query_out, node_name=node_name, repo_url=repo_url).data()
                    if rows:
                        results.append(f"--- What `{node_name}` uses (Outbound) ---")
                        for r in rows:
                            results.append(
                                f"  [{r['relationship']}] -> {r['node_type']} "
                                f"`{r['node_name']}` in {r['file_path']}"
                            )

                if direction in ("callers", "both"):
                    rows = session.run(query_in, node_name=node_name, repo_url=repo_url).data()
                    if rows:
                        results.append(f"--- What calls `{node_name}` (Inbound) ---")
                        for r in rows:
                            results.append(
                                f"  {r['node_type']} `{r['node_name']}` "
                                f"in {r['file_path']} <- [{r['relationship']}]"
                            )

            if not results:
                return f"No structural dependencies found for '{node_name}'."

            return "\n".join(results)

        except Exception as e:
            return f"Error querying Neo4j: {str(e)}"

    def read_full_file(self, file_path: str, repo_url: str) -> str:
        try:
            parts = urlparse(repo_url).path.strip("/").split("/")
            if len(parts) < 2:
                return f"Malformed repo URL: '{repo_url}'"

            owner, repo_name = parts[0], parts[1]
            content = self.github_client.get_file_content(owner, repo_name, file_path)

            if not content:
                return f"File '{file_path}' is empty or not found."

            return f"--- {file_path} ---\n{content}\n--- end ---"

        except Exception as e:
            return f"Error reading file from GitHub: {str(e)}"
import os
from neo4j import GraphDatabase

class Neo4jManager:
    def __init__(self):
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self._setup_schema()

    def _setup_schema(self):
        """Ensures the database infrastructure exists before accepting data."""
        with self.driver.session() as session:
            # 1. The Constraint (Ensures O(1) lookups for target_id)
            session.run("""
                CREATE CONSTRAINT codenode_id IF NOT EXISTS 
                FOR (n:CodeNode) REQUIRE n.id IS UNIQUE
            """)
            # 2. The Index (Ensures O(log N) lookups for target_name)
            session.run("""
                CREATE INDEX codenode_name IF NOT EXISTS 
                FOR (n:CodeNode) ON (n.name)
            """)
            
    def close(self):
        self.driver.close()

    def sync_graph(self, nodes: list, edges: list, delete_ids: list[str]):
        """
        Executes the graph synchronization in a single transaction.
        """
        # 1. Convert Pydantic models to dicts
        nodes_data = [n.model_dump() for n in nodes]
        edges_data = [e.model_dump() for e in edges]

        with self.driver.session() as session:
            session.execute_write(self._execute_sync, nodes_data, edges_data, delete_ids)

    @staticmethod
    def _execute_sync(tx, nodes_data, edges_data, delete_ids):
        # 1. Clear deleted nodes (DETACH DELETE automatically destroys connected edges)
        if delete_ids:
            tx.run("""
                UNWIND $delete_ids AS chunk_id
                MATCH (n:CodeNode {id: chunk_id})
                DETACH DELETE n
            """, delete_ids=delete_ids)

        # 2. Insert/Update Nodes
        if nodes_data:
            tx.run("""
                UNWIND $nodes AS node
                MERGE (n:CodeNode {id: node.id})
                SET n.name = node.name,
                    n.type = node.node_type,
                    n.file_path = node.file_path,
                    n.repo_url = node.repo_url
            """, nodes=nodes_data)

        # 3. Draw Edges where TARGET ID is known (DEFINES)
        if edges_data:
            tx.run("""
                UNWIND $edges AS edge
                WITH edge WHERE edge.target_id IS NOT NULL
                MATCH (source:CodeNode {id: edge.source_id})
                MATCH (target:CodeNode {id: edge.target_id})
                MERGE (source)-[rel:$(edge.relation_type)]->(target)
            """, edges=edges_data)

            # 4. Draw Edges where TARGET NAME is known (CALLS, IMPORTS, INHERITS)
            tx.run("""
                UNWIND $edges AS edge
                WITH edge WHERE edge.target_name IS NOT NULL
                MATCH (source:CodeNode {id: edge.source_id})
                MATCH (target:CodeNode {name: edge.target_name})
                MERGE (source)-[rel:$(edge.relation_type)]->(target)
            """, edges=edges_data)

    def purge_repository(self, repo_url: str):
        """Deletes all nodes and edges associated with a specific repository."""
        with self.driver.session() as session:
            session.run("""
                MATCH (n:CodeNode {repo_url: $repo_url})
                DETACH DELETE n
            """, repo_url=repo_url)
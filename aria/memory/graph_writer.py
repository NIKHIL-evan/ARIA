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
            session.run("""
                CREATE INDEX codenode_valid_to_sha IF NOT EXISTS
                FOR (n:CodeNode) on (n.valid_to_sha)""")
            session.run("""
                CREATE INDEX edge_valid_from_sha IF NOT EXISTS
                FOR ()-[r:CALLS]-() ON (r.valid_from_sha)""")
            session.run("""
                CREATE INDEX edge_valid_from_time IF NOT EXISTS
                FOR ()-[r:CALLS]-() ON (r.valid_from_time)""")
            session.run("""
                CREATE INDEX edge_valid_to_sha IF NOT EXISTS
                FOR ()-[r:CALLS]-() ON (r.valid_to_sha)""")
            session.run("""
                CREATE INDEX edge_valid_to_time IF NOT EXISTS
                FOR ()-[r:CALLS]-() ON (r.valid_to_time)""")
            
    def close(self):
        self.driver.close()

    def sync_graph(self, nodes_to_add: list, edges_to_add: list, nodes_to_update: list, edges_to_update: list, ids_to_delete: list, commit_sha: str, commit_time: str):
        """
        Executes the graph synchronization in a single transaction.
        """
        # 1. Convert Pydantic models to dicts
        add_nodes_data = [n.model_dump() for n in nodes_to_add]
        add_edges_data = [e.model_dump() for e in edges_to_add]
        update_nodes_data = [n.model_dump() for n in nodes_to_update]
        update_edges_data = [e.model_dump() for e in edges_to_update]

        with self.driver.session() as session:
            session.execute_write(self._execute_sync, add_nodes_data, add_edges_data,
            update_nodes_data, update_edges_data,
            ids_to_delete,
            commit_sha, commit_time )

    @staticmethod
    def _execute_sync(tx, add_nodes_data, add_edges_data, update_nodes_data, update_edges_data, ids_to_delete, commit_sha, commit_time ):
    # 1. Expire old nodes and edges
        if ids_to_delete:
            tx.run("""
                UNWIND $ids_to_expire AS node_id
                MATCH (n:CodeNode {id: node_id})
                WHERE n.valid_to_sha IS NULL 
                SET n.valid_to_sha = $commit_sha,
                    n.valid_to_time = datetime($commit_time)""", ids_to_expire=ids_to_delete, commit_sha=commit_sha, commit_time=commit_time)
            
            tx.run("""
                UNWIND $ids_to_expire AS node_id
                MATCH (n:CodeNode {id: node_id})-[r:CALLS]->()
                WHERE r.valid_to_sha IS NULL 
                SET r.valid_to_sha = $commit_sha,
                    r.valid_to_time = datetime($commit_time)""", ids_to_expire=ids_to_delete, commit_sha=commit_sha, commit_time=commit_time)
            
            tx.run("""
                UNWIND $ids_to_expire AS node_id
                MATCH ()-[r:CALLS]->(n:CodeNode {id: node_id})
                WHERE r.valid_to_sha IS NULL
                SET r.valid_to_sha = $commit_sha,
                    r.valid_to_time = datetime($commit_time)""", ids_to_expire=ids_to_delete, commit_sha=commit_sha, commit_time=commit_time)
            
            tx.run("""
                UNWIND $ids_to_expire AS node_id
                MATCH (n:CodeNode {id: node_id})-[r]->()
                WHERE type(r) IN ['IMPORTS', 'INHERITS']
                DELETE r""", ids_to_expire=ids_to_delete)

    # 2. Insert Nodes and edges
        if add_nodes_data:
            tx.run("""
                UNWIND $nodes AS node
                CREATE (n:CodeNode {id: node.id})
                SET n.name = node.name,
                    n.type = node.node_type,
                    n.file_path = node.file_path,
                    n.repo_url = node.repo_url,
                    n.valid_from_sha = $commit_sha,
                    n.valid_from_time = datetime($commit_time),
                    n.valid_to_sha = null,
                    n.valid_to_time = null
            """, nodes=add_nodes_data, 
                commit_sha=commit_sha, commit_time=commit_time)
            
        add_call_edges = [e for e in add_edges_data if e["relation_type"] == "CALLS"]
        add_other_edges = [e for e in add_edges_data if e["relation_type"] != "CALLS"]

        if add_call_edges:
            tx.run("""
                UNWIND $edges AS edge
                WITH edge WHERE edge.target_id is NOT NULL
                MATCH (source:CodeNode {id: edge.source_id})
                MATCH (target:CodeNode {id: edge.target_id})
                CREATE (source)-[r:CALLS]->(target)
                SET r.valid_from_sha = $commit_sha,
                    r.valid_from_time = datetime($commit_time),
                    r.valid_to_sha = null,
                    r.valid_to_time = null""", edges=add_call_edges,commit_sha=commit_sha, commit_time=commit_time)
            
            tx.run("""
                UNWIND $edges AS edge
                WITH edge WHERE edge.target_name IS NOT NULL
                MATCH (source:CodeNode {id: edge.source_id})
                MATCH (target:CodeNode {name: edge.target_name})
                CREATE (source)-[r:CALLS]->(target)
                SET r.valid_from_sha = $commit_sha,
                    r.valid_from_time = datetime($commit_time),
                    r.valid_to_sha = null,
                    r.valid_to_time = null""", edges=add_call_edges, commit_sha=commit_sha, commit_time=commit_time)
            
        if add_other_edges:
            tx.run("""
                UNWIND $edges AS edge
                WITH edge WHERE edge.target_id IS NOT NULL
                MATCH (source:CodeNode {id: edge.source_id})
                MATCH (target:CodeNode {id: edge.target_id})
                MERGE (source)-[rel:$(edge.relation_type)]->(target)
            """, edges=add_other_edges)

            tx.run("""
                UNWIND $edges AS edge
                WITH edge WHERE edge.target_name IS NOT NULL
                MATCH (source:CodeNode {id: edge.source_id})
                MATCH (target:CodeNode {name: edge.target_name})
                MERGE (source)-[rel:$(edge.relation_type)]->(target)
            """, edges=add_other_edges)
        
    # Update Nodes and edges
        if update_nodes_data:
            # Expire all active CALLS edges (temporal — keep history)
            tx.run("""
                UNWIND $nodes AS node
                MATCH (source:CodeNode {id: node.id})-[r:CALLS]->()
                WHERE r.valid_to_sha IS NULL
                SET r.valid_to_sha = $commit_sha
                    r.valid_to_time = datetime($commit_time)""", nodes=update_nodes_data, commit_sha=commit_sha, commit_time=commit_time)
            
            # Delete all IMPORTS/INHERITS edges (non-temporal — no history needed)
            tx.run("""
                UNWIND $nodes AS node
                MATCH (n:CodeNode {id: node.id})-[r]->()
                WHERE type(r) IN ['IMPORTS', 'INHERITS']
                DELETE r""", nodes=update_nodes_data)
            
            # Update existing node properties (MERGE, not CREATE)
            tx.run("""
                UNWIND $nodes AS node
                MERGE (n:CodeNode {id: node.id})
                SET n.name = node.name,
                    n.type = node.node_type,
                    n.file_path = node.file_path,
                    n.repo_url = node.repo_url""", nodes=update_nodes_data)
                    
            update_call_edges = [e for e in update_edges_data if e["relation_type"] == "CALLS"]
            update_other_edges = [e for e in update_edges_data if e["relation_type"] != "CALLS"]

            if update_call_edges:
                tx.run("""
                    UNWIND $edges AS edge
                    WITH edge WHERE edge.target_id is NOT NULL
                    MATCH (source:CodeNode {id: edge.source_id})
                    MATCH (target:CodeNode {id: edge.target_id})
                    CREATE (source)-[r:CALLS]->(target)
                    SET r.valid_from_sha = $commit_sha,
                        r.valid_from_time = datetime($commit_time),
                        r.valid_to_sha = null,
                        r.valid_to_time = null""", edges=update_call_edges,commit_sha=commit_sha, commit_time=commit_time)
                
                tx.run("""
                    UNWIND $edges AS edge
                    WITH edge WHERE edge.target_name IS NOT NULL
                    MATCH (source:CodeNode {id: edge.source_id})
                    MATCH (target:CodeNode {name: edge.target_name})
                    CREATE (source)-[r:CALLS]->(target)
                    SET r.valid_from_sha = $commit_sha,
                        r.valid_from_time = datetime($commit_time),
                        r.valid_to_sha = null,
                        r.valid_to_time = null""", edges=update_call_edges, commit_sha=commit_sha, commit_time=commit_time)
                
            if update_other_edges:
                tx.run("""
                    UNWIND $edges AS edge
                    WITH edge WHERE edge.target_id IS NOT NULL
                    MATCH (source:CodeNode {id: edge.source_id})
                    MATCH (target:CodeNode {id: edge.target_id})
                    MERGE (source)-[rel:$(edge.relation_type)]->(target)
                """, edges=update_other_edges)

                tx.run("""
                    UNWIND $edges AS edge
                    WITH edge WHERE edge.target_name IS NOT NULL
                    MATCH (source:CodeNode {id: edge.source_id})
                    MATCH (target:CodeNode {name: edge.target_name})
                    MERGE (source)-[rel:$(edge.relation_type)]->(target)
                """, edges=update_other_edges)
                
    def purge_repository(self, repo_url: str):
        """Deletes all nodes and edges associated with a specific repository."""
        with self.driver.session() as session:
            session.run("""
                MATCH (n:CodeNode {repo_url: $repo_url})
                DETACH DELETE n
            """, repo_url=repo_url)
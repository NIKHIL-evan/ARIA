from typing import Dict, List
from qdrant_client import QdrantClient, models
from qdrant_client.http import models
import voyageai
from aria.memory.repo_reader import CodeChunk
import os
from dotenv import load_dotenv

load_dotenv()

class QdrantStore:
    def __init__(self, host: str = "localhost", port: int = 6333):
        # Connect to your local Docker container
        self.client = QdrantClient(host=host, port=port) 
        self.collection_name = "aria_codebase"
        self._ensure_collection()
        self.voyage = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))

    def _ensure_collection(self):
        """Creates the collection if it does not exist. Voyage-code-3 uses 1024 dimensions."""
        if not self.client.collection_exists(self.collection_name):
            print(f"Creating new Qdrant collection: {self.collection_name}")
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=1024, # Voyage-code-3 dimension size
                    distance=models.Distance.COSINE
                )
            )

    def get_file_state(self, repo_url: str, file_path: str) -> Dict[str, str]:
        """
        Retrieves the {chunk_id: content_hash} mapping for a specific file.
        This provides the 'existing_state' required by the SyncManager.
        """
        # We use Qdrant's scroll API to find all chunks matching this specific file
        records, _ = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="file_path",
                        match=models.MatchValue(value=file_path)
                    ),
                    models.FieldCondition(
                        key="repo_url",
                        match=models.MatchValue(value=repo_url)
                    )
                ]
            ),
            # We only need the payload (metadata), not the heavy vector arrays
            with_payload=True,
            with_vectors=False,
            limit=10000 # Assume no single file has more than 10k chunks
        )

        existing_state = {}
        for record in records:
            # record.id is the UUID string
            # record.payload contains our JSON metadata
            content_hash = record.payload.get("content_hash")
            if content_hash:
                existing_state[str(record.id)] = content_hash
                
        return existing_state
    
    def search(self, query: str, repo_url: str, limit: int = 3) -> List[CodeChunk]:
        """Searches Qdrant for the closest semantic matches to the query."""
        
        query_vector = self.voyage.embed(
            query,
            model="voyage-code-3",
            input_type="query"
        ).embeddings[0]
        
        repo_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="repo_url",
                    match=models.MatchValue(value=repo_url)
                )
            ]
        )

        hits = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=repo_filter,
            limit=limit
        ).points

        result = []
        for hit in hits:
            chunk_data = hit.payload
            chunk_object = CodeChunk(**chunk_data)
            result.append(chunk_object)

        return result

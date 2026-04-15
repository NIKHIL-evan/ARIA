import os
import voyageai
from typing import List
from qdrant_client.models import PointStruct
from aria.memory.repo_reader import CodeChunk
from aria.memory.qdrant_store import QdrantStore
from qdrant_client.http import models
from dotenv import load_dotenv

BATCH_SIZE = 64
load_dotenv()
class Embedder:
    """
    Takes CodeChunks, converts them to vectors using voyage-code-3, 
    and synchronizes them into Qdrant based on SyncManager deltas.
    """
    def __init__(self, qdrant_store: QdrantStore):
        # We inject the store dependency rather than creating a new connection
        self.qdrant_store = qdrant_store
        self.voyage = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))

    def sync_deltas(self, to_add: List[CodeChunk], to_update: List[CodeChunk], to_delete_ids: List[str]):
        """
        Executes the physical database changes dictated by the SyncManager.
        """
        # 1. Purge Orphans
        if to_delete_ids:
            print(f"Purging {len(to_delete_ids)} orphaned chunks from database...")
            self.qdrant_store.client.delete(
                collection_name=self.qdrant_store.collection_name,
                points_selector=to_delete_ids
            )

        # 2. Embed and Upsert
        # In a vector database, an Add and an Update are exactly the same operation (Upsert).
        # It overwrites the ID if it exists, or creates it if it doesn't.
        upserts = to_add + to_update
        
        if upserts:
            print(f"Embedding and upserting {len(upserts)} chunks...")
            self._embed_and_upsert(upserts)
        else:
            print("No new embeddings required.")

    def _embed_and_upsert(self, chunks: List[CodeChunk]):
        """Your original batching and embedding logic, preserved."""
        for batch_start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[batch_start: batch_start + BATCH_SIZE]

            print(f"Embedding batch {batch_start // BATCH_SIZE + 1}/{(len(chunks) - 1) // BATCH_SIZE + 1}...")

            texts_to_embed = [
                f"{chunk.node_type} {chunk.name} in {chunk.file_path}\n\n{chunk.content}"
                for chunk in batch
            ]

            result = self.voyage.embed(
                texts_to_embed,
                model="voyage-code-3",
                input_type="document",
            )

            vectors = result.embeddings
            points = []
            
            for i, chunk in enumerate(batch):
                payload = chunk.model_dump()
                points.append(
                    PointStruct(
                        id=chunk.id, 
                        vector=vectors[i],
                        payload=payload
                    )
                )

            self.qdrant_store.client.upsert(
                collection_name=self.qdrant_store.collection_name,
                points=points,
            )

    def purge_repository(self, repo_url: str):
        """Deletes all chunks associated with a specific repository."""
        self.qdrant_store.client.delete(
            collection_name=self.qdrant_store.collection_name,
            points_selector=models.Filter(
                must=[models.FieldCondition(key="repo_url", match=models.MatchValue(value=repo_url))]
            )
        )
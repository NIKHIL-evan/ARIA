import os
import uuid
import voyageai
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
)
from aria.memory.repo_reader import CodeChunk

load_dotenv()

# How many chunks we send to Voyage in one API call.
# Voyage's hard limit is 128 — we use 64 to stay safe
# and avoid hitting rate limits on large repos.
BATCH_SIZE = 64

# Must match voyage-code-3's output exactly.
# If you change embedding models later, change this too.
EMBEDDING_DIMENSIONS = 1024

# The name of our collection in Qdrant.
# Think of this like a table name in a normal database.
COLLECTION_NAME = "aria_code_chunks"


class Embedder:
    """
    Takes CodeChunk objects, converts them to vectors using
    voyage-code-3, and stores them in Qdrant.
    """

    def __init__(self):
        # Voyage client — handles the embedding API calls
        self.voyage = voyageai.Client(
            api_key=os.getenv("VOYAGE_API_KEY")
        )

        # Qdrant client — handles vector storage
        self.qdrant = QdrantClient(
            url=os.getenv("QDRANT_URL")
        )

        # Make sure the collection exists before we try to store anything
        self._init_collection()

    def _init_collection(self):
        """
        Creates the Qdrant collection if it doesn't exist yet.
        Safe to call multiple times — won't overwrite existing data.
        """
        # Get list of existing collections
        existing = [
            c.name for c in
            self.qdrant.get_collections().collections
        ]

        if COLLECTION_NAME not in existing:
            self.qdrant.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(
                    size=EMBEDDING_DIMENSIONS,  # must match model output
                    distance=Distance.COSINE,   # similarity metric
                ),
            )
            print(f"Created Qdrant collection: {COLLECTION_NAME}")
        else:
            print(f"Collection already exists: {COLLECTION_NAME}")

    def embed_chunks(self, chunks: list[CodeChunk]) -> int:
        """
        Main entry point. Takes a list of CodeChunks,
        embeds them in batches, stores in Qdrant.
        Returns the total number of vectors stored.
        """
        total_stored = 0

        # Split chunks into batches of BATCH_SIZE
        # range(0, 1627, 64) gives us [0, 64, 128, ..., 1600]
        # so we process chunks[0:64], chunks[64:128], etc.
        for batch_start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[batch_start: batch_start + BATCH_SIZE]

            print(
                f"Embedding batch "
                f"{batch_start // BATCH_SIZE + 1}/"
                f"{(len(chunks) - 1) // BATCH_SIZE + 1} "
                f"({len(batch)} chunks)..."
            )

            # Step 1 — build the text we'll embed for each chunk.
            # We prepend the function name and file path because
            # voyage-code-3 uses this context to produce better vectors.
            # "function authenticate_user in auth/utils.py" gives the
            # model more signal than the raw code alone.
            texts_to_embed = [
                f"{chunk.chunk_type} {chunk.name} "
                f"in {chunk.file_path}\n\n{chunk.content}"
                for chunk in batch
            ]

            # Step 2 — call Voyage API to get vectors.
            # input_type="document" tells Voyage these are documents
            # being stored (not a search query — that uses "query").
            # This distinction matters: Voyage uses different
            # internal processing for documents vs queries.
            result = self.voyage.embed(
                texts_to_embed,
                model="voyage-code-3",
                input_type="document",
            )

            # result.embeddings is a list of lists —
            # one list of 1024 floats per chunk
            vectors = result.embeddings

            # Step 3 — build Qdrant PointStructs.
            # Each point needs: id, vector, payload.
            # uuid.uuid4() generates a unique ID for each point.
            # int(uuid.uuid4()) converts it to an integer because
            # Qdrant expects integer or string IDs, not UUID objects.
            points = []
            for i, chunk in enumerate(batch):
                unique_string = f"{chunk.repo_url}::{chunk.file_path}::{chunk.name}"
                deterministic_id = str(uuid.uuid5(uuid.NAMESPACE_URL, unique_string))

                points.append(
                    PointStruct(
                        id=deterministic_id,  # Now strictly deterministic!
                        vector=vectors[i],
                        payload={
                            "name": chunk.name,
                            "content": chunk.content,
                            "file_path": chunk.file_path,
                            "chunk_type": chunk.chunk_type,
                            "start_line": chunk.start_line,
                            "language": chunk.language,
                            "repo_url": chunk.repo_url,
                            "last_commit_message": chunk.last_commit_message,
                            "last_commit_author": chunk.last_commit_author,
                        }
                    )
                )

            # Step 4 — upsert into Qdrant.
            # Upsert = insert if new, update if ID already exists.
            # Safe to run multiple times on the same repo.
            self.qdrant.upsert(
                collection_name=COLLECTION_NAME,
                points=points,
            )

            total_stored += len(batch)

        return total_stored
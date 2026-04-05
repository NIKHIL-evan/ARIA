import os
import voyageai
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from aria.memory.repo_reader import CodeChunk

load_dotenv()

COLLECTION_NAME = "aria_code_chunks"

class Retriever:
    """
    Translates natural language questions into vectors and 
    searches the Qdrant database for the most relevant CodeChunks.
    """
    def __init__(self):
        # We need the exact same tools we used to write the data
        self.voyage = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))
        self.qdrant = QdrantClient(url=os.getenv("QDRANT_URL"))

    def search(self, query: str, limit: int = 5) -> list[CodeChunk]:
        """
        Embeds the query and returns the top matching code chunks.
        """
        print(f"Embedding query: '{query}'...")
        
        # 1. Embed the Question
        # TEACHER'S NOTE: Notice input_type="query". 
        # When we saved the code, we used "document". 
        # Voyage uses different math for questions vs. stored data.
        result = self.voyage.embed(
            [query],
            model="voyage-code-3",
            input_type="query", 
        )
        query_vector = result.embeddings[0]

        # 2. Search Qdrant using Cosine Distance
        print("Searching database...")
        search_results = self.qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,       # CHANGED: This must be 'query', not 'query_vector'
            limit=limit,
            with_payload=True         # REQUIRED: Tells Qdrant to send the code text back
        ).points
        # 3. Reconstruct the Data
        # Qdrant returns raw JSON payloads. We strictly format them back 
        # into our Pydantic CodeChunk objects so the rest of our app can use them safely.
        chunks = []
        for hit in search_results:
            payload = hit.payload
            chunk = CodeChunk(
                name=payload["name"],
                content=payload["content"],
                file_path=payload["file_path"],
                chunk_type=payload["chunk_type"],
                start_line=payload["start_line"],
                language=payload["language"],
                repo_url=payload["repo_url"],
                last_commit_message=payload["last_commit_message"],
                last_commit_author=payload["last_commit_author"]
            )
            chunks.append(chunk)

        return chunks
import sys
sys.path.append(".")

from aria.memory.repo_reader import RepoReader
from aria.memory.embedder import Embedder

# Step 1 — read the repo (you already know this works)
print("Reading repo...")
reader = RepoReader("https://github.com/pallets/flask")
chunks = reader.read()
print(f"Got {len(chunks)} chunks\n")

# Step 2 — embed and store
print("Embedding and storing in Qdrant...")
embedder = Embedder()
total = embedder.embed_chunks(chunks)
print(f"\nStored {total} vectors in Qdrant")
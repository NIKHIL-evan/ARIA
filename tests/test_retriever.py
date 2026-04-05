import sys
sys.path.append(".")

from aria.memory.retriever import Retriever

retriever = Retriever()

queries = [
    "how does flask handle authentication",
    "where is request context managed",
    "how are blueprints registered",
]

for query in queries:
    print(f"\n{'='*60}")
    print(f"QUERY: {query}")
    print('='*60)
    
    chunks = retriever.search(query, limit=3)
    
    for i, chunk in enumerate(chunks, 1):
        print(f"\nResult {i}:")
        print(f"  Name:    {chunk.name}")
        print(f"  File:    {chunk.file_path}")
        print(f"  Author:  {chunk.last_commit_author}")
        print(f"  Preview: {chunk.content[:150].strip()}...")
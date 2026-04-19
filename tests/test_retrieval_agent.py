import asyncio
from aria.agents.Retrieval_agent import RetrievalAgent

async def main():
    agent = RetrievalAgent()
    repo_url = "https://github.com/NIKHIL-evan/ARIA" # Put your exact repo URL here
    
    # Pure Retrieval Query: No explanations, just data mapping.
    query = (
        "Locate the exact file paths and code chunks for the `RetrievalAgent` class "
        "and the `QdrantStore` class. Once located, retrieve the outbound structural "
        "dependencies for both classes to map what other components they call."
    )
    
    print(f"Starting Retrieval Agent for: {repo_url}...\n")
    final_report = await agent.run(repo_url=repo_url, query=query, max_step=8)
    
    print("\n" + "="*50)
    print("FINAL DOWNSTREAM REPORT")
    print("="*50)
    print(final_report)

if __name__ == "__main__":
    asyncio.run(main())
import asyncio
from aria.agents.Retrieval_agent import RetrievalAgent
from aria.agents.onboarding_agent import OnboardingAgent

async def main():
    # 1. Boot up the Scout
    print("Booting Retrieval Agent...")
    scout = RetrievalAgent()
    
    # 2. Boot up the Mentor and Inject the Scout
    print("Booting Onboarding Agent...")
    mentor = OnboardingAgent(retriever=scout)
    
    repo_url = "https://github.com/NIKHIL-evan/ARIA"
    
    # 3. Ask a Junior Developer question!
    junior_question = "Hey, I'm new here. Can you explain how the RetrievalAgent's parallel tool execution works? Where is that code located?"
    
    print("\nStarting Onboarding Flow...\n")
    final_guide = await mentor.run(query=junior_question, repo_url=repo_url)
    
    print("\n" + "="*60)
    print("FINAL ONBOARDING GUIDE")
    print("="*60)
    print(final_guide)

if __name__ == "__main__":
    asyncio.run(main())
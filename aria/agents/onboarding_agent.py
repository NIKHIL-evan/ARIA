from aria.agents.Retrieval_agent import RetrievalAgent
import anthropic 
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

r = RetrievalAgent()

class OnboardingAgent:
    def __init__(self, retriever):
        self.retriever = retriever
        self.client = anthropic.AsyncAnthropic(api_key= os.environ.get("ANTHROPIC_API_KEY"))

    async def run(self, query: str, repo_url: str) -> str:
        """Runs a React agent loop with Retrieval tool.
        First agent must understand the user query which are mostly vauge or too descriptive,
        then construct a database search query for required infomation about the codebase,
        then analyze all the info from retrieval tool and convert it educational or helpful format or what the user asked"""

        tools = [
            {
                "name": "dispatch_retrieval_scout",
                "description": "Dispatches the ARIA Retrieval Scout to automatically search the codebase, read files, and map graph dependencies. You MUST use this tool before attempting to answer any codebase questions."
                " Pass a highly targeted, technical search phrase (e.g., 'Stripe webhook listener implementation') into the tool, not a conversational question. The Scout will return a dense Markdown report containing absolute reality.",
                "input_schema": {
                    "type": "object",
                    "properties":{
                        "technical_search_phrase": {"type": "string", "description": "Highly targeted phrase derived from original user query which Retrieval agent will use for semantic search " }
                    },
                    "required": ["technical_search_phrase"]
                }
            }
        ]
        system_prompt = """"""

        messages = [{"role": "user", "content": query}]

        step = 0
        max_step = 5
        while step < max_step:
            step += 1
            print(f"\n--- [Step {step}] Claude is thinking... ---")

            response = await self.client.messages.create(
                model="claude-haiku-4-5",
                system=system_prompt,
                max_tokens=8192,
                messages=messages,
                tools=tools
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "tool_use":
                tool_calls = [
                    b for b in response.content 
                    if b.type == "tool_use"
                ]

                async def process_single_call(call):
                    print(f"   -> Dispatching Scout with phrase: '{call.input['technical_search_phrase']}'")
                    try:
                        search_phrase = call.input["technical_search_phrase"]
                        result = await self.retriever.run(repo_url=repo_url, query=search_phrase)
                    except Exception as e:
                        print(f"Error executing {call.name}: {str(e)}")

                    return {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": result
                    }
                
                tool_results_content = await asyncio.gather(
                    *(process_single_call(call) for call in tool_calls))
                
                messages.append({
                    "role": "user",
                    "content": list(tool_results_content)
                })

            elif response.stop_reason == "end_turn":
                text_blocks = [
                    b.text for b in response.content 
                    if b.type == "text"
                ]
                return "\n".join(text_blocks)
            
            elif response.stop_reason == "max_tokens":
                text_blocks = [b.text for b in response.content if b.type == "text"]
                partial_report = "\n".join(text_blocks)
                return f"WARNING: Report was cut off due to token limits. Partial output below:\n\n{partial_report}"

            else:
                return f"Error: Unexpected stop_reason '{response.stop_reason}'. Aborting."
            
        return "Error: Reached maximum steps without finding a final answer."
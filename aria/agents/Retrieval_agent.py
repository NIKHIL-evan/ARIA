from aria.memory.qdrant_store import QdrantStore
import os
from neo4j import GraphDatabase
from github_client import GitHubClient
import anthropic
from urllib.parse import urlparse
from dotenv import load_dotenv
import asyncio

load_dotenv()

class RetrievalAgent:
    """
    The interface layer between the LLM brain and the ARIA memory system.
    Provides strictly typed tools for semantic search, graph traversal, and raw file reading.
    """
    def __init__(self):
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.qdrant_store = QdrantStore()
        self.github_client = GitHubClient()
        self.client = anthropic.AsyncAnthropic(api_key = os.environ.get("ANTHROPIC_API_KEY"))

    def search_semantic_code(self, query: str, repo_url: str, limit: int = 3) -> str:
        """
        Searches the Qdrant vector database to find the most semantically relevant code snippets.
        
        Args:
            query: The natural language search term.
            repo_url: The full URL of the repository.
            limit: Maximum chunks to return.
        """
        try:
            results = self.qdrant_store.search(query=query, repo_url=repo_url, limit=limit)
            
            if not results:
                return f"Observation: No semantic matches found for '{query}' in Qdrant."
            
            formatted_out = [f"--- Semantic Search Results for '{query}' ---"]
            for i, chunk in enumerate(results, 1):
                # We expose the metadata (calls) so the LLM gets immediate context
                formatted_out.append(
                    f"\n[Result {i}]"
                    f"\nFile: {chunk.file_path}"
                    f"\nType: {chunk.node_type} | Name: {chunk.name}"
                    f"\nCalls (Metadata): {chunk.calls}"
                    f"\nCode Snippet:\n{chunk.content}"
                    f"\n{'-'*40}"
                )
            return "\n".join(formatted_out)

        except Exception as e:
            return f"Observation Error: Failed to query Qdrant - {str(e)}"
    
    VALID_DIRECTIONS = {"callers", "dependencies", "both"}
    def get_structural_dependencies(self, node_name: str, repo_url: str, direction: str = "both") -> str:
        """
        Queries the Neo4j graph database to find how a specific function, class, or module interacts.
        
        Args:
            node_name: The exact name of the node (e.g., 'ClassName.method' or 'module_name').
            repo_url: The full URL of the repository.
            direction: 'callers' (inbound), 'dependencies' (outbound), or 'both'.
        """
        query_out = """
            MATCH (source:CodeNode {name: $node_name, repo_url: $repo_url})-[r]->(target:CodeNode)
            RETURN type(r) AS relationship, target.name AS node_name, target.type AS node_type, target.file_path AS file_path
        """
        
        query_in = """
            MATCH (target:CodeNode {name: $node_name, repo_url: $repo_url})<-[r]-(source:CodeNode)
            RETURN type(r) AS relationship, source.name AS node_name, source.type AS node_type, source.file_path AS file_path
        """
        if direction not in self.VALID_DIRECTIONS:
            return f"Observation Error: Invalid direction '{direction}'. Must be one of {self.VALID_DIRECTIONS}."
        
        results = []
        
        try:
            with self.driver.session() as session:
                # 1. Outbound (What this node uses)
                if direction in ["dependencies", "both"]:
                    outbound = session.run(query_out, node_name=node_name, repo_url=repo_url).data()
                    if outbound:
                        results.append(f"--- What `{node_name}` uses (Outbound) ---")
                        for record in outbound:
                            results.append(f"- [{record['relationship']}] -> {record['node_type']} `{record['node_name']}` (in {record['file_path']})")

                # 2. Inbound (What uses this node)
                if direction in ["callers", "both"]:
                    inbound = session.run(query_in, node_name=node_name, repo_url=repo_url).data()
                    if inbound:
                        results.append(f"--- What uses `{node_name}` (Inbound) ---")
                        for record in inbound:
                            results.append(f"- {record['node_type']} `{record['node_name']}` (in {record['file_path']}) <- [{record['relationship']}] -")

            if not results:
                return f"Observation: No structural dependencies found for '{node_name}' in Neo4j."
            
            return "\n".join(results)

        except Exception as e:
            return f"Observation Error: Failed to query Neo4j - {str(e)}"


    def read_full_file(self, file_path: str, repo_url: str) -> str:
        try:
            path_parts = urlparse(repo_url).path.strip("/").split("/")

            if len(path_parts) < 2:
                return f"Observation Error: Malformed repository URL '{repo_url}'."

            owner, repo_name = path_parts[0], path_parts[1]
            content = self.github_client.get_file_content(owner, repo_name, file_path)

            if not content:
                return f"Observation: File '{file_path}' is empty or could not be located."

            return f"--- Raw Content of {file_path} ---\n{content}\n--- End of {file_path} ---"

        except Exception as e:
            return f"Observation Error: Failed to fetch file from GitHub - {str(e)}"
        
    retrieval_tools = [
    {
        "name": "search_semantic_code",
        "description": "Searches the Qdrant vector database to find the most semantically relevant code snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The natural language search term."},
                "repo_url": {"type": "string", "description": "The exact repository URL."},
                "limit": {"type": "integer", "description": "Maximum chunks to return. Default 3."}
            },
            "required": ["query", "repo_url"]
        }
    },
    {
        "name": "get_structural_dependencies",
        "description": " Queries the Neo4j graph database to find how a specific function, class, or module interacts.ONLY use this tool after you have discovered a valid node_name using search_semantic_code ",
        "input_schema": {
            "type": "object",
            "properties": {
            "node_name": {"type": "string", "description": "The exact name of the node."},
            "repo_url": {"type": "string", "description": "The exact repository URL."},
            "direction": {"type": "string", "description": "Must be exactly one of: 'callers', 'dependencies', or 'both'.", "enum": ["callers", "dependencies", "both"]}
        },
            "required": ["node_name", "repo_url", "direction"]
        }, 
    },
    {
        "name": "read_full_file",
        "description": "Fetches the complete, raw source code of a specific file from the repository.ONLY use this tool after you have discovered a valid file_path using search_semantic_code or get_structural_dependencies.",
        "input_schema": {
            "type": "object",
            "properties": {
            "file_path": {"type": "string", "description": "The exact path to the file."},
            "repo_url": {"type": "string", "description": "The exact repository URL."}
        },
            "required": ["file_path", "repo_url"]
        }
    }
    ]

    async def execute_tool(self, name, args):
        if name == "search_semantic_code":
            # Offloads the blocking search to a background thread
            return await asyncio.to_thread(self.search_semantic_code, **args)

        elif name == "get_structural_dependencies":
            return await asyncio.to_thread(self.get_structural_dependencies, **args)

        elif name == "read_full_file":
            return await asyncio.to_thread(self.read_full_file, **args)

        else:
            raise ValueError(f"Unknown tool: {name}")

    async def run(self, repo_url: str, query: str, max_step: int = 5) -> str:
        # 1. Give Claude the context immediately in a system prompt
        system_prompt = f"""You are the ARIA Retrieval Agent, a specialized data-extraction microservice. 
            Your target repository is: {repo_url}

            # PRIME DIRECTIVE
            Your sole purpose is to navigate the codebase using your tools, gather precise technical context, and compile a dense, factual report for downstream agents (e.g., QA, Onboarding, or Bug Triage agents). You do not write new code, fix bugs, or offer opinions. You only retrieve and map reality.

            # TOOL CHAINING STRATEGY
            You have access to a semantic vector index (Qdrant), a structural graph (Neo4j), and raw file reading capabilities. You must execute your investigation sequentially:
            1. THE MAP: Always start with `search_semantic_code` to find exact file paths and `chunk_id`s based on the semantic meaning of the query. NEVER hallucinate or guess a file path.
            2. THE ROADS: Once you have a node or file name, use `get_structural_dependencies` to calculate the blast radius (who calls it, and who it calls).
            3. THE TERRITORY: If a chunk's logic is ambiguous or you need to see the exact syntax/comments, use `read_full_file` on the exact path you discovered in steps 1 or 2.

            # EXECUTION RULES
            - Iterate autonomously. If a search returns no results, rethink your query and search again.
            - Do not make redundant calls. If you already pulled the blast radius for `main.py`, do not pull it again.
            - Stop exploring the moment you have enough context to definitively answer the downstream agent's query.

            # FINAL OUTPUT REQUIREMENTS
            When you have finished gathering data, you must output a structured markdown report. 
            - DO NOT use conversational filler (e.g., "Here is what I found", "Let me know if you need anything else").
            - MUST include exact file paths, class/function names, and strict structural relationships.
            - Format the output logically with clear headings (e.g., ### Relevant Files, ### Blast Radius, ### Core Logic) so the downstream agent can instantly parse the context."""
        
        messages = [{"role": "user", "content": query}]
        
        step = 0
        while step < max_step:
            step += 1
            print(f"\n--- [Step {step}] Claude is thinking... ---")

            # 2. Make the API Call
            response = await self.client.messages.create(
                model="claude-sonnet-4-5",
                system=system_prompt,
                max_tokens=2048,
                messages=messages,
                tools=self.retrieval_tools
            )

            # Append the assistant's entire response (which includes the tool_use blocks)
            messages.append({
                "role": "assistant",
                "content": response.content
            })

            # 3. Route: End of Turn
            if response.stop_reason == "end_turn":
                text_blocks = [
                    b.text for b in response.content 
                    if b.type == "text"
                ]
                return "\n".join(text_blocks)

            # 4. Route: Tool Execution
            elif response.stop_reason == "tool_use":
                tool_calls = [
                    b for b in response.content 
                    if b.type == "tool_use"
                ]

                async def process_single_call(call):
                    print(f"Executing {call.name}")
                    args = dict(call.input)
                    args.setdefault("repo_url", repo_url)
                    try:
                        result = await self.execute_tool(call.name, args)
                    except Exception as e:
                        print(f"Error executing {call.name}: {str(e)}")

                    return {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": str(result)
                    }
                
                tool_results_content = await asyncio.gather(
                    *(process_single_call(call) for call in tool_calls)
                )
                messages.append({
                    "role": "user",
                    "content": list(tool_results_content)
                })

            else:
                return f"Error: Unexpected stop_reason '{response.stop_reason}'. Aborting."
            
        return "Error: Reached maximum steps without finding a final answer."
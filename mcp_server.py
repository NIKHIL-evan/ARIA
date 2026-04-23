from fastmcp import FastMCP
from data_fetcher import DataFetcher

mcp     = FastMCP("aria-context-server")
fetcher = DataFetcher()  # one instance, connections opened once


@mcp.tool()
def search_code(query: str, repo_url: str, limit: int = 5) -> str:
    """
    Semantically searches the codebase for relevant code chunks stored in Qdrant.

    Always call this first. It returns file paths, function/class names,
    signatures, docstrings, call graphs, imports, and code content.
    Use the results to discover exact node_names for get_dependencies,
    or exact file_paths for read_file.

    Args:
        query:    Natural language description of what you need.
        repo_url: Full GitHub URL e.g. https://github.com/owner/repo
        limit:    How many chunks to return. Default 5.
    """
    return fetcher.search_semantic_code(query=query, repo_url=repo_url, limit=limit)


@mcp.tool()
def get_dependencies(node_name: str, repo_url: str, direction: str = "both") -> str:
    """
    Queries the Neo4j graph to find what a function or class calls,
    and what calls it. Use this to understand blast radius and relationships.

    Only call this AFTER getting a valid node_name from search_code.
    Never guess or hallucinate a node_name.

    Args:
        node_name: Exact name from search_code e.g. 'MyClass.my_method'
        repo_url:  Full GitHub URL e.g. https://github.com/owner/repo
        direction: 'callers', 'dependencies', or 'both'
    """
    return fetcher.get_structural_dependencies(
        node_name=node_name, repo_url=repo_url, direction=direction
    )


@mcp.tool()
def read_file(file_path: str, repo_url: str) -> str:
    """
    Fetches the complete raw source of a file directly from GitHub.

    Only call this AFTER getting a valid file_path from search_code
    or get_dependencies. Use when a code chunk is insufficient and
    you need the full file context.

    Args:
        file_path: Exact path e.g. 'aria/memory/qdrant_store.py'
        repo_url:  Full GitHub URL e.g. https://github.com/owner/repo
    """
    return fetcher.read_full_file(file_path=file_path, repo_url=repo_url)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8001)
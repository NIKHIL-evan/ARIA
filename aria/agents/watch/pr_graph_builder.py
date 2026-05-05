import asyncio
from aria.infra.github_client import GitHubClient
from aria.memory.repo_reader import parse_code_string
from aria.memory.graph_writer import Neo4jManager
from aria.memory.syncmanager import SyncManager
from aria.memory.qdrant_store import QdrantStore

async def build_pr_graph(
        owner: str, repo_name: str, repo_url: str, pr_number: str, head_sha: str,
        github_client: GitHubClient,
        neo4j_manager: Neo4jManager,
        sync_manager: SyncManager,
        qdrant_store: QdrantStore
) -> list[str]:
    """Builds a temporary graph in Neo4j for the PR branch version of changed files."""
    from datetime import datetime, timezone
    commit_time = datetime.now(timezone.utc).isoformat()

    pr_files = await asyncio.to_thread(github_client.get_pr_files, owner, repo_name, pr_number)

    processed_files = []

    for file_info in pr_files:
        path = file_info["filename"]
        status = file_info["status"]

        if path.endswith(".py"):
            continue

        if status == "removed":
            existing_state = await asyncio.to_thread(
                qdrant_store.get_file_state, repo_url, path
            )
            delete_ids = list(existing_state.keys())
            if delete_ids:
                await asyncio.to_thread(
                    neo4j_manager.sync_graph,
                    [], [],     # no nodes/edges to add
                    [], [],     # no nodes/edges to update
                    delete_ids, # IDs to expire
                    head_sha, commit_time,
                )
            processed_files.append(path)
            continue

        try:
            # Fetch the file content at the PR branch version
            raw_text = await asyncio.to_thread(
                github_client.get_file_content,
                owner, repo_name, path, ref=head_sha
            )
 
            # Parse the PR version of the file
            incoming_chunks, incoming_nodes, incoming_edges = parse_code_string(
                source_code=raw_text,
                file_path=path,
                repo_url=repo_url,
                commit_message=f"PR #{pr_number} preview",
                commit_author="ARIA Watch",
            )
 
            # Get existing state from Qdrant to determine adds vs updates
            existing_state = await asyncio.to_thread(
                qdrant_store.get_file_state, repo_url, path
            )
 
            # Compute deltas
            to_add, to_update, to_delete_ids = sync_manager.compute_deltas(
                existing_state, incoming_chunks
            )
 
            # Separate nodes and edges by add/update
            added_ids = {chunk.id for chunk in to_add}
            updated_ids = {chunk.id for chunk in to_update}
 
            nodes_to_add = [n for n in incoming_nodes if n.id in added_ids]
            nodes_to_update = [n for n in incoming_nodes if n.id in updated_ids]
            edges_for_new = [e for e in incoming_edges if e.source_id in added_ids]
            edges_for_updated = [e for e in incoming_edges if e.source_id in updated_ids]
 
            # Write to Neo4j with head_sha as the commit identifier
            if nodes_to_add or nodes_to_update or edges_for_new or edges_for_updated or to_delete_ids:
                await asyncio.to_thread(
                    neo4j_manager.sync_graph,
                    nodes_to_add, edges_for_new,
                    nodes_to_update, edges_for_updated,
                    to_delete_ids,
                    head_sha, commit_time,
                )
 
            processed_files.append(path)
 
        except Exception as e:
            print(f"[ARIA Watch] Failed to process {path}: {e}")
            continue
 
    return processed_files
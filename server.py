import hmac
import os
from dotenv import load_dotenv
import hashlib
import asyncio
from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from aria.infra.github_client import GitHubClient
from aria.memory.repo_reader import parse_code_string
from aria.memory.qdrant_store import QdrantStore
from aria.memory.syncmanager import SyncManager
from aria.memory.embedder import Embedder
from aria.memory.graph_writer import Neo4jManager 
from aria.agents.watch.pr_graph_builder import build_pr_graph

load_dotenv()

app = FastAPI()

diplomat = GitHubClient(app_id=os.getenv("GITHUB_APP_ID"), pem_path=os.getenv("GITHUB_PEM_PATH"))
store = QdrantStore()
manager = SyncManager()
embedder = Embedder(qdrant_store=store)
neo4j_manager = Neo4jManager()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

def verify_signature(payload_body: bytes, signature_header: str):
    """Cryptographically verifies the payload came from GitHub."""
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing signature")
        
    hash_object = hmac.new(WEBHOOK_SECRET.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()
    
    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")

# BACKGROUND WORKER FUNCTIONS

async def process_installation(payload: dict):
    """Handles deep database syncs for new or deleted repos."""
    action = payload.get("action")
    owner = payload["installation"]["account"]["login"]

    if action in ["deleted", "removed"]:
        repos_to_purge = payload.get("repositories") or payload.get("repositories_removed", [])
        for repo in repos_to_purge:
            repo_url = f"https://github.com/{owner}/{repo['name']}"
            print(f"\n[PURGE] Destroying all data for: {repo_url}")
            await asyncio.to_thread(embedder.purge_repository, repo_url)
            await asyncio.to_thread(neo4j_manager.purge_repository, repo_url)

    elif action in ["created", "added"]:
        repos = payload["repositories"]
        for repo in repos:
            repo_name = repo["name"]
            repo_url = f"https://github.com/{owner}/{repo_name}"
            commit_message = "Initial App Installation"
            commit_author = "System" 
            repo_to_add = []
            repo_nodes, repo_edges = [], []
            print(f"\n--- Ingesting Repository: {repo_name} ---")

            files = await asyncio.to_thread(diplomat.get_repo_content, owner, repo_name)
            for path, source_code in files.items():
                incoming_chunks, incoming_nodes, incoming_edges = parse_code_string(source_code, path, repo_url, commit_message=commit_message, commit_author=commit_author)
                    
                repo_to_add.extend(incoming_chunks)
                repo_nodes.extend(incoming_nodes)
                repo_edges.extend(incoming_edges)

            # Batch Deploy
            if repo_to_add:
                print(f"Deploying {len(repo_to_add)} additions to Qdrant...")
                # We pass empty lists for updates and deletes
                await asyncio.to_thread(embedder.sync_deltas, repo_to_add, [], []) 
                    
            if repo_nodes or repo_edges:
                print(f"Deploying {len(repo_nodes)} nodes and {len(repo_edges)} edges to Neo4j...")
                # We pass an empty list for deleted_ids
                from datetime import datetime, timezone
                commit_sha = "initial_ingestion"
                commit_time = datetime.now(timezone.utc).isoformat()
                await asyncio.to_thread(neo4j_manager.sync_graph,
                    repo_nodes, repo_edges,   
                    [], [],                     
                    [],                         
                    commit_sha, commit_time)

            print(f"\n--- Finished Ingesting Repository: {repo_name} ---")


# ---Helper Functions---
async def add_mod_single_file(owner, repo_name, repo_url, commit_message, commit_author, file_path, commit_sha, commit_time):
    try:
        #Step 1 Download and Step 2 Fetch from DB concurrently
        raw_text, existing_state = await asyncio.gather(
            asyncio.to_thread(diplomat.get_file_content, owner, repo_name, file_path),  
            asyncio.to_thread(store.get_file_state, repo_url, file_path)    
        )
        # Step 2: Parse
        incoming_chunks, incoming_nodes, incoming_edges = parse_code_string(
            source_code=raw_text,
            file_path=file_path,
            repo_url=repo_url,
            commit_message=commit_message,
            commit_author=commit_author
        )
        
        # Step 3: Math
        to_add, to_update, to_delete_ids = manager.compute_deltas(existing_state, incoming_chunks)
        
        # Step 4: Database Execution
        await asyncio.to_thread(embedder.sync_deltas, to_add, to_update, to_delete_ids)

        added_ids = {chunk.id for chunk in to_add}
        updated_ids = {chunk.id for chunk in to_update}

        nodes_to_add = [n for n in incoming_nodes if n.id in added_ids]
        nodes_to_update = [n for n in incoming_nodes if n.id in updated_ids]

        edges_for_new = [e for e in incoming_edges if e.source_id in added_ids]
        edges_for_updated = [e for e in incoming_edges if e.source_id in updated_ids]
        
        if nodes_to_add or nodes_to_update or edges_for_new or edges_for_updated or to_delete_ids:
            await asyncio.to_thread(neo4j_manager.sync_graph,
                nodes_to_add, edges_for_new,
                nodes_to_update, edges_for_updated,
                to_delete_ids,
                commit_sha, commit_time)
            
    except Exception as e:
        raise Exception(f"Failed processing {file_path}: {e}")

async def remove_single_file(repo_url, file_path, commit_sha, commit_time):
    try:
        existing_state = await asyncio.to_thread(store.get_file_state, repo_url, file_path)
        to_delete = list(existing_state.keys())
        
        if to_delete:
            print(f"Purging {len(to_delete)} orphaned items from {file_path}...")
            # Purge from Qdrant
            await asyncio.to_thread(embedder.qdrant_store.client.delete, 
            store.collection_name, to_delete)
            # Purge from Neo4j (passing empty lists for nodes/edges)
            await asyncio.to_thread(neo4j_manager.sync_graph, 
                nodes_to_add=[], 
                edges_to_add=[],
                nodes_to_update=[], edges_to_update=[],
                ids_to_delete=to_delete,
                commit_sha=commit_sha, commit_time=commit_time)
    
    except Exception as e:
        raise Exception(f"Failed processing {file_path}: {e}")

async def process_push(payload: dict):
    """Handles embedding updates for pushed commits."""
    repo_name = payload["repository"]["name"]
    owner = payload["repository"]["owner"]["login"]
    repo_url = payload["repository"]["html_url"]
    commits = payload.get("commits", [])
    for commit in commits:
        # Extract author and message specific to THIS commit
        commit_message = commit.get("message", "No commit message")
        commit_author = commit.get("author", {}).get("name", "Unknown Author")
        commit_sha = commit.get("id")          
        commit_time = commit.get("timestamp")
        # Extract the lists
        mod_add = commit.get("modified",[]) + commit.get("added",[])
        removed = commit.get("removed",[])
        results = await asyncio.gather(
            asyncio.gather(*(add_mod_single_file(owner, repo_name, repo_url, commit_message, commit_author, file_path, commit_sha, commit_time) for file_path in mod_add if file_path.endswith(".py")), return_exceptions= True),
            asyncio.gather(*(remove_single_file(repo_url, file_path, commit_sha, commit_time) for file_path in removed), return_exceptions=True),
            return_exceptions=True
        )
        for process in results:
            for result in process:
                if isinstance(result, Exception):
                    print(f"[ERROR] {result}")

    print(f"\n--- Finished Processing Push for {repo_name} ---")


async def process_pull_request(payload: dict):
    pr_number = payload["pull_request"]["number"]
    base_sha = payload["pull_request"]["base"]["sha"]
    head_sha = payload["pull_request"]["head"]["sha"]
    owner = payload["repository"]["owner"]["login"]
    repo_name = payload["repository"]["name"]
    repo_url = payload["repository"]["html_url"]
    
    # Step 1: Build the PR graph in Neo4j
    await build_pr_graph(owner, repo_name, repo_url, pr_number, head_sha)
    
    # Step 2: Run analysis
    drift_report = diff_engine.compute_drift(
        repo_url, base_sha, head_sha)
    
    # Step 3: Investigate critical findings (agents)
    verified = await orchestrator.investigate(drift_report)
    
    # Step 4: Post comment
    comment = reporter.format_report(drift_report, verified)
    publisher.post_comment(owner, repo_name, pr_number, comment)
    
    # Step 5: Clean up PR edges
    neo4j_manager.rollback_commit(repo_url, head_sha)

# THE FASTAPI ROUTER

@app.post("/webhook")
async def webhook_listener(
    request: Request,
    background_tasks: BackgroundTasks, # <-- Inject the BackgroundTasks object
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None)
):
    # 1. Extract and Verify
    raw_payload = await request.body()
    verify_signature(raw_payload, x_hub_signature_256)
    payload = await request.json()
    
    # 2. Route the event to the Background Worker
    if x_github_event == "installation":
        background_tasks.add_task(process_installation, payload)
        
    elif x_github_event == "push":
        background_tasks.add_task(process_push, payload)
        
    elif x_github_event == "pull_request":
        background_tasks.add_task(process_pull_request, payload)
        
    else:
        print(f"\n[ROUTER] Ignoring event type: {x_github_event}")

    # 3. Return 200 OK IMMEDIATELY
    return {"status": "accepted", "message": "Task moved to background queue."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
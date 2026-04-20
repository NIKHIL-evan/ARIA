import hmac
import os
from dotenv import load_dotenv
import hashlib
from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from aria.infra.github_client import GitHubClient
from aria.memory.repo_reader import parse_code_string
from aria.memory.qdrant_store import QdrantStore
from aria.memory.syncmanager import SyncManager
from aria.memory.embedder import Embedder
from aria.memory.graph_writer import Neo4jManager

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
            embedder.purge_repository(repo_url)
            neo4j_manager.purge_repository(repo_url)

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

            files = diplomat.get_repo_content(owner, repo_name)
            for path, source_code in files.items():
                incoming_chunks, incoming_nodes, incoming_edges = parse_code_string(source_code, path, repo_url, commit_message=commit_message, commit_author=commit_author)
                    
                repo_to_add.extend(incoming_chunks)
                repo_nodes.extend(incoming_nodes)
                repo_edges.extend(incoming_edges)

            # Batch Deploy
            if repo_to_add:
                print(f"Deploying {len(repo_to_add)} additions to Qdrant...")
                # We pass empty lists for updates and deletes
                embedder.sync_deltas(repo_to_add, [], []) 
                    
            if repo_nodes or repo_edges:
                print(f"Deploying {len(repo_nodes)} nodes and {len(repo_edges)} edges to Neo4j...")
                # We pass an empty list for deleted_ids
                neo4j_manager.sync_graph(repo_nodes, repo_edges, [])

            print(f"\n--- Finished Ingesting Repository: {repo_name} ---")


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
        # Extract the lists
        mod_add = commit.get("modified",[]) + commit.get("added",[])
        removed = commit.get("removed",[])

        # Loop through only the modified and added files in THIS commit
        for file_path in mod_add:
            if not file_path.endswith(".py"):
                continue
                
            print(f"\n--- Processing {file_path} ---")
            
            # Step 1: Download
            raw_text = diplomat.get_file_content(owner, repo_name, file_path)
            
            # Step 2: Parse
            incoming_chunks, incoming_nodes, incoming_edges = parse_code_string(
                source_code=raw_text,
                file_path=file_path,
                repo_url=repo_url,
                commit_message=commit_message,
                commit_author=commit_author
            )
            
            # Step 3: Fetch DB State
            existing_state = store.get_file_state(repo_url, file_path)
            
            # Step 4: Math
            to_add, to_update, to_delete_ids = manager.compute_deltas(existing_state, incoming_chunks)
            
            # Step 5: Database Execution
            embedder.sync_deltas(to_add, to_update, to_delete_ids)

            changed_ids = {chunk.id for chunk in (to_add + to_update)}
            nodes_to_sync = [n for n in incoming_nodes if n.id in changed_ids]
            edges_to_sync = incoming_edges if (nodes_to_sync or to_delete_ids) else []
            
            if nodes_to_sync or edges_to_sync or to_delete_ids:
                neo4j_manager.sync_graph(nodes_to_sync, edges_to_sync, to_delete_ids)
        
        for file_path in removed:
            existing_state = store.get_file_state(repo_url, file_path)
            to_delete = list(existing_state.keys())
            
            if to_delete:
                print(f"Purging {len(to_delete)} orphaned items from {file_path}...")
                # Purge from Qdrant
                embedder.qdrant_store.client.delete(
                    collection_name=store.collection_name, points_selector=to_delete
                )
                # Purge from Neo4j (passing empty lists for nodes/edges)
                neo4j_manager.sync_graph(nodes=[], edges=[], delete_ids=to_delete)

    print(f"\n--- Finished Processing Push for {repo_name} ---")


async def process_pull_request(payload: dict):
    """Handles the Code Review Agent logic (Phase 2)."""
    action = payload["action"]
    pull_number = payload["pull_request"]["number"]
    owner = payload["repository"]["owner"]["login"]
    repo_name = payload["repository"]["name"]
    
    if action in ["opened", "synchronize"]:
        files = diplomat.get_pr_files(owner, repo_name, pull_number)
        for file in files:
            path = file["filename"]
            status = file["status"]
            patch = file.get("patch", "")
            
            if status == "removed" or not path.endswith(".py"):
                continue
                
            print(f"\n[PR #{pull_number}] File: {path}")
            print(f"Patch data:\n{patch}")

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
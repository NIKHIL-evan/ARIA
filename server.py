import hmac
import hashlib
from fastapi import FastAPI, Request, Header, HTTPException
from github_client import GitHubClient
from aria.memory.repo_reader import parse_code_string
from aria.memory.qdrant_store import QdrantStore
from aria.memory.syncmanager import SyncManager
from aria.memory.embedder import Embedder

app = FastAPI()

APP_ID = "3338188"
PEM_FILE = "aria-dev-ynikh.2026-04-10.private-key.pem"

diplomat = GitHubClient(app_id=APP_ID, pem_path=PEM_FILE)
store = QdrantStore()
manager = SyncManager()
embedder = Embedder(qdrant_store=store)

# SECURITY: The shared secret between ARIA and GitHub.
WEBHOOK_SECRET = "aria_test_secret_1234" 

def verify_signature(payload_body: bytes, signature_header: str):
    """Cryptographically verifies the payload came from GitHub."""
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing signature")
        
    hash_object = hmac.new(WEBHOOK_SECRET.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()
    
    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")

@app.post("/webhook")
async def webhook_listener(
    request: Request,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None)
):
    # 1. Extract raw bytes for security verification
    raw_payload = await request.body()
    
    # 2. Authenticate the sender
    verify_signature(raw_payload, x_hub_signature_256)
    
    # 3. Parse data safely
    payload = await request.json()
    
    # 4. Route traffic based on Event Header
    if x_github_event == "installation":
        owner = payload["installation"]["account"]["login"]
        repos = payload["repositories"]
        for repo in repos:
            repo_name = repo["name"]
            repo_url = f"https://github.com/{owner}/{repo_name}"
            commit_message = "Initial App Installation"
            commit_author = "System" 
            global_to_add = []

            files = diplomat.get_repo_content(owner, repo_name)
            for path, source_code in files.items():
                incoming_chunks = parse_code_string(source_code, path, repo_url, commit_message=commit_message, commit_author=commit_author)
                existing_chunks = store.get_file_state(repo_url, path)
                to_add, to_update, to_delete_ids = manager.compute_deltas(existing_chunks, incoming_chunks)
                global_to_add.extend(to_add)
            sync = embedder.sync_deltas(global_to_add, to_update, to_delete_ids)


    elif x_github_event == "push":
        repo_name = payload["repository"]["name"]
        owner = payload["repository"]["owner"]["login"]
        repo_url = payload["repository"]["html_url"]
        
        commits = payload.get("commits", [])
        
        # Loop through each commit in the push
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
                incoming_chunks = parse_code_string(
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
            
            for file_path in removed:
                existing_state = store.get_file_state(repo_url, file_path)
                print(f"Purging {len(existing_state)} orphaned files from database...")
                to_delete = [ids for ids in existing_state.keys()]
                embedder.qdrant_store.client.delete(
                    collection_name = store.collection_name,
                    points_selector=to_delete
                )

    else:
        print(f"\n[ROUTER] Ignoring event type: {x_github_event}")

    # 5. Return success to GitHub immediately
    return {"status": "accepted"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
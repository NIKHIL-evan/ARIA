import hmac
import hashlib
from fastapi import FastAPI, Request, Header, HTTPException

app = FastAPI()

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
        action = payload.get("action")
        print(f"\n[ROUTER] Installation Event Received. Action: {action}")
        
    elif x_github_event == "push":
        commits = payload.get("commits", [])
        print(f"\n[ROUTER] Push Event Received. Commits: {len(commits)}")
        # We will extract added/modified/removed files here later
        
    else:
        print(f"\n[ROUTER] Unhandled event type: {x_github_event}")

    # 5. Return success to GitHub immediately
    return {"status": "accepted"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
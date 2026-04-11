import jwt
import time
import requests
import base64

class GitHubClient:
    def __init__(self, app_id: str, pem_path: str):
        self.app_id = app_id
        self.pem_path = pem_path

        with open(self.pem_path, "r") as pem_file:
            self.signing_key = pem_file.read()

    def _generate_jwt(self) -> str:
        """Generates the Master Key (JWT) valid for 10 minutes."""
        payload = {
            'iat': int(time.time()),
            'exp': int(time.time()) + (10 * 60),
            'iss': self.app_id
        }
        return jwt.encode(payload, self.signing_key, algorithm='RS256')        
    
    def get_installation_token(self, owner: str, repo: str) -> str:
        """Trades the Master Key for a Room Key for a specific repository."""
        jwt_token = self._generate_jwt()
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # 1. Get the Installation ID
        install_url = f"https://api.github.com/repos/{owner}/{repo}/installation"
        install_response = requests.get(install_url, headers=headers)
        
        if not install_response.ok:
            raise Exception(f"Failed to find installation: {install_response.text}")
            
        installation_id = install_response.json()['id']
        
        # 2. Trade for the Access Token
        token_url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        token_response = requests.post(token_url, headers=headers)
        
        # THIS IS THE BLOCK YOU DELETED. DO NOT DELETE IT.
        if not token_response.ok:
            raise Exception(f"CRITICAL API ERROR: GitHub rejected the token request. Details: {token_response.text}")
            
        return token_response.json()['token']
    
    def get_file_content(self, owner: str, repo: str, file_path: str) -> str:
        """Downloads the raw text of a specific file."""
        token = self.get_installation_token(owner, repo)
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            # GitHub sends files encoded in Base64 so special characters don't break the JSON. We decode it to a string.
            return base64.b64decode(data['content']).decode('utf-8')
        else:
            raise Exception(f"Failed to fetch {file_path}: {response.text}")
        
if __name__ == "__main__":
    # 1. Put your actual App ID here (From the top of your GitHub App settings page)
    APP_ID = "3338188" 
    
    # 2. Put the exact name of the .pem file you downloaded here
    PEM_FILE = "aria-dev-ynikh.2026-04-10.private-key.pem" 
    
    # 3. Put your GitHub Username and the repository name
    OWNER = "NIKHIL-evan"
    REPO = "ARIA"
    
    # 4. We will try to download the server.py file we just wrote
    TARGET_FILE = "server.py" 

    print("Initializing GitHub Client...")
    client = GitHubClient(app_id=APP_ID, pem_path=PEM_FILE)
    
    print(f"Requesting file: {TARGET_FILE}...")
    content = client.get_file_content(owner=OWNER, repo=REPO, file_path=TARGET_FILE)
    
    print("\n--- FILE DOWNLOAD SUCCESSFUL ---")
    print(content[:200] + "\n...[TRUNCATED]")
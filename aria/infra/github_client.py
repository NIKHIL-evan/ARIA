import jwt
import time
import requests
import base64
import io
import tarfile

class GitHubClient:
    def __init__(self, app_id: str, pem_path: str):
        self.app_id = app_id
        self.pem_path = pem_path

        with open(self.pem_path, "r") as pem_file:
            self.signing_key = pem_file.read()

    def _generate_jwt(self) -> str:
        """Generates the Master Key (JWT) valid for 10 minutes."""
        now = int(time.time())
        payload = {
            "iat": now - 60, 
            "exp": now + 540, 
            "iss": self.app_id
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
        
        
        if not token_response.ok:
            raise Exception(f"CRITICAL API ERROR: GitHub rejected the token request. Details: {token_response.text}")
            
        return token_response.json()['token']
    
    def get_file_content(self, owner: str, repo: str, file_path: str, ref=None) -> str:
        """Downloads the raw text of a specific file."""
        token = self.get_installation_token(owner, repo)
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
        if ref:
            url += f"?ref={ref}"
            
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            # GitHub sends files encoded in Base64 so special characters don't break the JSON. We decode it to a string.
            return base64.b64decode(data['content']).decode('utf-8')
        else:
            raise Exception(f"Failed to fetch {file_path}: {response.text}")
        
    def get_repo_content(self, owner:str, repo: str) -> str:
        """downloads the whole repo archive(tar)"""
        token = self.get_installation_token(owner, repo)
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json"
        }

        url = f"https://api.github.com/repos/{owner}/{repo}/tarball/main"
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            virtual_file = io.BytesIO(response.content)
                
                # 2. Open the archive
            repo_files = {} # This is the dictionary we will return
                
            with tarfile.open(fileobj=virtual_file, mode="r:gz") as tar:
                for member in tar.getmembers():
                        
                    # Only process Python files
                    if member.name.endswith(".py"):
                            
                        parts = member.name.split('/', 1)
                        clean_path = parts[1]
                            
                        file_object = tar.extractfile(member)
                        if file_object is not None:
                            code_string = file_object.read().decode('utf-8')
                            
                        repo_files[clean_path] = code_string

            return repo_files
        
        else:
            raise Exception(f"Failed to fetch repo: {response.text}")
        
    def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list:
        """Returns list of changed files with their patches for a PR."""
        token = self.get_installation_token(owner, repo)
        header = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
        response = requests.get(url=url, headers=header)

        if not response.ok:
            raise Exception(f"Failed to fetch PR files: {response.text}")

        return response.json()
            

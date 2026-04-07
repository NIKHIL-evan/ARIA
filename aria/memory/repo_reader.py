import ast
import os
import uuid
import tempfile
from pathlib import Path
from typing import Optional
from git import Repo
from pydantic import BaseModel, Field
from typing import Optional, List, Tuple

class CodeChunk(BaseModel):
    # --- Identifiers ---
    # Generated using uuid.uuid5(namespace, f"{repo_url}::{file_path}::{node_type}::{name}")
    id: str 
    
    # --- Structural Metadata ---
    name: str                          # Fully qualified (e.g., "AuthService.login")
    node_type: str                     # "module", "class", "function", or "async_function"
    parent_id: Optional[str] = None    # The ID of the parent Class or Module
    file_path: str                     # Relative path (e.g., "src/auth.py")
    repo_url: str                      # Source repository
    
    # --- The "Meat" (Logic & Intent) ---
    # For Classes: Contains only the signature/header. 
    # For Functions: Contains the full body.
    content: str                       
    signature: Optional[str] = None    # Just the 'def name(args):' or 'class Name(bases):'
    docstring: Optional[str] = None    # Extracted via ast.get_docstring()
    
    # --- Dependency Mapping ---
    # List of resolved absolute paths or module names this chunk depends on
    imports: List[str] = Field(default_factory=list)
    
    # --- Technical Details ---
    line_range: Tuple[int, int]        # (start_line, end_line)
    language: str = "python"
    
    # --- Git Metadata ---
    last_commit_hash: Optional[str] = None
    last_commit_message: Optional[str] = None
    last_commit_author: Optional[str] = None


# --- The reader class ---

class RepoReader:
    """
    Clones a git repository and extracts all functions and classes
    as CodeChunk objects, ready to be embedded into Qdrant.
    """
    def __init__(self, repo_url: str):
        self.repo_url = repo_url.strip()
        self.chunks: list[CodeChunk] = []   # all extracted chunks live here

    def read(self) -> list[CodeChunk]:
        """
        Main entry point. Call this and you get back a list of CodeChunks.
        Windows-safe version: manually closes git repo and force-deletes
        the temp folder so git file locks don't block cleanup.
        """

        import shutil
        import stat

        def force_delete(action, name, exc):
            """
            Windows keeps git files as read-only.
            This callback changes permissions to writable
            before deleting — fixes the WinError 32 / 5 errors.
            """
            os.chmod(name, stat.S_IWRITE)
            os.remove(name)

        tmp_dir = tempfile.mkdtemp()  # create temp folder manually

        try:
            print(f"Cloning {self.repo_url} ...")
            repo = Repo.clone_from(self.repo_url, tmp_dir)
            print("Clone complete. Reading files...")

            commit_map = self._build_commit_map(repo)
            self._walk_tree(repo, tmp_dir, commit_map)

            # Explicitly close the repo before deleting the folder
            # This releases git's file handles on Windows
            repo.close()

        finally:
            # Force delete even if read-only files exist
            shutil.rmtree(tmp_dir, onexc=force_delete)

        print(f"Extracted {len(self.chunks)} chunks from {self.repo_url}")
        return self.chunks

    def _build_commit_map(self, repo: Repo) -> dict:
        """
        Returns a dict like:
        { "aria/agents/supervisor.py": (commit_message, author_name) }

        We iterate over ALL commits and track the latest one per file.
        iter_commits() walks the chain we saw in the git diagram —
        from newest commit back to the very first one.
        """
        commit_map = {}
        for commit in repo.iter_commits():
            for file_path in commit.stats.files:
                # stats.files contains every file changed in that commit
                # Since we walk newest-first, the first time we see a
                # file is the most recent commit that touched it
                if file_path not in commit_map:
                    commit_map[file_path] = (
                        commit.message.strip(),
                        commit.author.name
                    )
        return commit_map

    def _walk_tree(self, repo: Repo, base_dir: str, commit_map: dict):
        """
        Walks every file in the repo. For Python files, extracts
        functions and classes using AST parsing.
        """
        base_path = Path(base_dir)

        MAX_FILE_SIZE = 512*1024
        
        # rglob("*.py") recursively finds every .py file in every folder
        for py_file in base_path.rglob("*.py"):

            if any(part in py_file.parts for part in [
                ".venv", "__pycache__", "node_modules", ".git"
            ]):
                continue

            if py_file.stat().st_size > MAX_FILE_SIZE:
                print(f"Skipping {py_file.name}: File too large ({py_file.stat().st_size // 1024} KB)")
                continue

            # relative_to() gives us the path INSIDE the repo,
            # not the full temp directory path
            relative_path = py_file.relative_to(base_path).as_posix()

            try:
                source_code = py_file.read_text(encoding="utf-8")
            except Exception:
                # Some files have weird encodings — skip them gracefully
                continue

            # Get the commit info for this file from our map
            commit_info = commit_map.get(relative_path, (None, None))

            # Parse and extract chunks from this file
            chunks = self._extract_chunks(
                source_code=source_code,
                file_path=relative_path,
                commit_message=commit_info[0],
                commit_author=commit_info[1],
            )
            self.chunks.extend(chunks)

    def _extract_chunks(
        self,
        source_code: str,
        file_path: str,
        commit_message: str | None,
        commit_author: str | None,
    ) -> list[CodeChunk]:
        chunks = []
        source_lines = source_code.splitlines()
        
        try:
            tree = ast.parse(source_code)
            is_parsable = True
        except SyntaxError:
            tree = None
            is_parsable = False

        # --Helper URL Normalization function
        def _normalize_url( url: str) -> str:
            """Ensures https://github.com/user/repo.git becomes github.com/user/repo"""
            url = url.lower().strip()
            if url.endswith(".git"):
                url = url[:-4]
            if url.endswith("/"):
                url = url[:-1]
            # Remove protocol for the ID string to keep it clean
            return url.replace("https://", "").replace("http://", "")
                
        # --- Helper: Deterministic ID Generator ---
        def _generate_node_id(repo_url: str, f_path: str, n_type: str, n_name: str) -> str:
            normalized_url = _normalize_url(repo_url)
            unique_string = f"{normalized_url.lower()}::/{f_path}::{n_type}::{n_name}"
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_string))

        # --- Helper: Import Extraction ---
        def _extract_imports(tree_node):
            imports = []
            for node in ast.walk(tree_node):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module if node.module else ""
                    imports.append(module)
            return imports

        # 1. MODULE LEVEL CHUNK (The Anchor)
        module_name = Path(file_path).stem
        module_id = _generate_node_id(self.repo_url, file_path, "module", module_name)
        
        chunks.append(CodeChunk(
            id=module_id,
            name=module_name,
            node_type="module",
            parent_id=None,
            file_path=file_path,
            repo_url=self.repo_url,
            content="",  # Modules are structural; intent is in the docstring
            docstring=ast.get_docstring(tree) if is_parsable else source_code[:500],
            imports=_extract_imports(tree) if is_parsable else None,
            line_range=(1, len(source_lines)),
            last_commit_message=commit_message,
            last_commit_author=commit_author
        ))

        if not is_parsable:
            return chunks
        
        # 2. TRAVERSE BODY
        for node in tree.body:
            
            # --- CASE 1: CLASS DEFINITIONS ---
            if isinstance(node, ast.ClassDef):
                class_id = _generate_node_id(self.repo_url, file_path, "class", node.name)
                
                # Extract Skeleton (Decorators + Signature + Docstring)
                # Find where the docstring ends or where the first statement begins
                start_index = node.lineno - 1
                # We slice only the 'header' of the class to avoid context bloat
                # Default to capturing the first 5 lines of the class if we can't find a clean break
                end_index = node.body[0].lineno - 1 if node.body else node.end_lineno
                class_skeleton = "\n".join(source_lines[start_index:end_index])
                
                chunks.append(CodeChunk(
                    id=class_id,
                    name=node.name,
                    node_type="class",
                    parent_id=module_id,
                    file_path=file_path,
                    repo_url=self.repo_url,
                    content=class_skeleton.strip(),
                    signature=f"class {node.name}:",
                    docstring=ast.get_docstring(node),
                    line_range=(node.lineno, node.end_lineno),
                    last_commit_message=commit_message,
                    last_commit_author=commit_author
                ))

                # --- Extract Methods inside the Class ---
                for sub_node in node.body:
                    if isinstance(sub_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_qualified_name = f"{node.name}.{sub_node.name}"
                        method_type = "async_function" if isinstance(sub_node, ast.AsyncFunctionDef) else "function"
                        
                        method_content = "\n".join(source_lines[sub_node.lineno - 1 : sub_node.end_lineno])
                        
                        chunks.append(CodeChunk(
                            id=_generate_node_id(self.repo_url, file_path, method_type, method_qualified_name),
                            name=method_qualified_name,
                            node_type=method_type,
                            parent_id=class_id, # Links back to the class skeleton
                            file_path=file_path,
                            repo_url=self.repo_url,
                            content=method_content,
                            signature=ast.unparse(sub_node).split('\n')[0], # Captures def func(args):
                            docstring=ast.get_docstring(sub_node),
                            line_range=(sub_node.lineno, sub_node.end_lineno),
                            last_commit_message=commit_message,
                            last_commit_author=commit_author
                        ))

            # --- CASE 2: TOP-LEVEL FUNCTIONS ---
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_type = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                func_content = "\n".join(source_lines[node.lineno - 1 : node.end_lineno])
                
                chunks.append(CodeChunk(
                    id=_generate_node_id(self.repo_url, file_path, func_type, node.name),
                    name=node.name,
                    node_type=func_type,
                    parent_id=module_id, # Top-level functions point to the Module
                    file_path=file_path,
                    repo_url=self.repo_url,
                    content=func_content,
                    signature=ast.unparse(node).split('\n')[0],
                    docstring=ast.get_docstring(node),
                    line_range=(node.lineno, node.end_lineno),
                    last_commit_message=commit_message,
                    last_commit_author=commit_author
                ))

        return chunks
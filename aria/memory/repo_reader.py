import ast
import os
import tempfile
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from git import Repo


# --- The data shape for one chunk of code ---
# Pydantic BaseModel means every field is type-checked automatically.
# If you try to create a CodeChunk without a 'content' field, it crashes
# immediately with a clear error — not silently somewhere else.

class CodeChunk(BaseModel):
    name: str                          # function or class name
    content: str                       # the actual source code
    file_path: str                     # relative path inside the repo
    chunk_type: str                    # "function" or "class"
    start_line: int                    # line number where it starts
    language: str                      # "python" (we support more later)
    repo_url: str                      # which repo this came from
    last_commit_message: Optional[str] = None   # why was this last changed?
    last_commit_author: Optional[str] = None    # who last touched this?


# --- The reader class ---

class RepoReader:
    """
    Clones a git repository and extracts all functions and classes
    as CodeChunk objects, ready to be embedded into Qdrant.
    """

    def __init__(self, repo_url: str):
        self.repo_url = repo_url
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

        # rglob("*.py") recursively finds every .py file in every folder
        for py_file in base_path.rglob("*.py"):

            # Skip virtual environment folders and test caches —
            # we don't want to embed installed libraries, only our code
            if any(part in py_file.parts for part in [
                ".venv", "__pycache__", "node_modules", ".git"
            ]):
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
        # Tracks qualified names already extracted from THIS file.
        # If the same qualified name appears twice (property getter+setter,
        # overloaded function), we keep the first and skip the rest.
        seen_names = set()

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return chunks

        source_lines = source_code.splitlines()

        def extract_node(node, parent_path: str | None = None):

            qualified_name = (
                f"{parent_path}.{node.name}" if parent_path else node.name
            )

            chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
            start = node.lineno - 1
            end = node.end_lineno
            chunk_content = "\n".join(source_lines[start:end])

            # Only add if we haven't seen this qualified name in this file yet
            if qualified_name not in seen_names and len(chunk_content.strip()) >= 20:
                seen_names.add(qualified_name)
                chunks.append(CodeChunk(
                    name=qualified_name,
                    content=chunk_content,
                    file_path=file_path,
                    chunk_type=chunk_type,
                    start_line=node.lineno,
                    language="python",
                    repo_url=self.repo_url,
                    last_commit_message=commit_message,
                    last_commit_author=commit_author,
                ))

            # Recurse into classes only
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                        ast.ClassDef
                    )):
                        extract_node(child, parent_path=qualified_name)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef
            )):
                extract_node(node, parent_path=None)

        return chunks
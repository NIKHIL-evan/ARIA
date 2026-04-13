import ast
import uuid
from typing import Optional, List, Tuple
from pydantic import BaseModel, Field

class CodeChunk(BaseModel):
    # --- Identifiers ---
    id: str 
    content_hash: str
    
    # --- Structural Metadata ---
    name: str                          
    node_type: str                     
    parent_id: Optional[str] = None    
    file_path: str                     
    repo_url: str                      
    
    # --- The "Meat" (Logic & Intent) ---
    content: str                       
    signature: Optional[str] = None    
    docstring: Optional[str] = None    
    
    # --- Dependency Mapping ---
    imports: List[str] = Field(default_factory=list)
    
    # --- Technical Details ---
    line_range: Tuple[int, int]        
    language: str = "python"
    
    # --- Git Metadata ---
    last_commit_hash: Optional[str] = None
    last_commit_message: Optional[str] = None
    last_commit_author: Optional[str] = None


def parse_code_string(
    source_code: str,
    file_path: str,
    repo_url: str,
    commit_message: Optional[str] = None,
    commit_author: Optional[str] = None,
) -> List[CodeChunk]:
    """
    Takes a raw string of Python code from memory and parses it into CodeChunks.
    No disk access required.
    """
    chunks = []
    source_lines = source_code.splitlines()
    
    try:
        tree = ast.parse(source_code)
        is_parsable = True
    except SyntaxError:
        tree = None
        is_parsable = False

    # --Helper URL Normalization function
    def _normalize_url(url: str) -> str:
        url = url.lower().strip()
        if url.endswith(".git"):
            url = url[:-4]
        if url.endswith("/"):
            url = url[:-1]
        return url.replace("https://", "").replace("http://", "")
            
    # --- Helper: Deterministic ID Generator ---
    def _generate_node_id(r_url: str, f_path: str, n_type: str, n_name: str) -> str:
        normalized_url = _normalize_url(r_url)
        unique_string = f"{normalized_url.lower()}::/{f_path}::{n_type}::{n_name}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_string))
    
    # ---Helper: Content Hash Generator ---
    def _generate_content_hash(text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

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

    # 1. MODULE LEVEL CHUNK
    # Since we don't have Pathlib anymore, we extract the stem using standard string methods
    module_name = file_path.split('/')[-1].replace('.py', '')
    module_id = _generate_node_id(repo_url, file_path, "module", module_name)
    mod_doc = ast.get_docstring(tree) if is_parsable else source_code[:500]
    mod_imports = _extract_imports(tree) if is_parsable else None
    
    chunks.append(CodeChunk(
        id=module_id,
        content_hash=_generate_content_hash(str(mod_doc) + str(mod_imports)),
        name=module_name,
        node_type="module",
        parent_id=None,
        file_path=file_path,
        repo_url=repo_url,
        content= f"Docstring: {mod_doc}\nImports: {mod_imports}",
        docstring=mod_doc,
        imports=mod_imports,
        line_range=(1, len(source_lines)),
        last_commit_message=commit_message,
        last_commit_author=commit_author
    ))

    if not is_parsable:
        return chunks

    # --- Helper: Signature Extraction ---
    def _extract_exact_signature(node: ast.AST, source_lines: list[str]) -> str:
        if not hasattr(node, 'lineno') or not getattr(node, 'body', None):
            return ""

        # Check if decorators exist to find the true physical start line
        if getattr(node, 'decorator_list', None):
            start_idx = node.decorator_list[0].lineno - 1
        else:
            start_idx = node.lineno - 1

        # Body[0].lineno is the line where the first statement starts
        end_idx = node.body[0].lineno - 1

        if start_idx == end_idx:
            raw_text = source_lines[start_idx]
        else:
            raw_text = "\n".join(source_lines[start_idx:end_idx])
        
        return _find_terminating_colon(raw_text)

    def _find_terminating_colon(text: str) -> str:
        depth = 0
        last_valid_colon = -1
        for i, char in enumerate(text):
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == ":" and depth == 0:
                last_valid_colon = i
        if last_valid_colon != -1:
            return text[:last_valid_colon + 1].strip()
        return text.strip()


    # 2. TRAVERSE BODY
    for node in tree.body:
        
        # --- CASE 1: CLASS DEFINITIONS ---
        if isinstance(node, ast.ClassDef):
            class_id = _generate_node_id(repo_url, file_path, "class", node.name)
            
            start_index = node.lineno - 1
            end_index = node.body[0].lineno - 1 if node.body else node.end_lineno
            class_skeleton = "\n".join(source_lines[start_index:end_index])
            
            chunks.append(CodeChunk(
                id=class_id,
                content_hash=_generate_content_hash(class_skeleton.strip()),
                name=node.name,
                node_type="class",
                parent_id=module_id,
                file_path=file_path,
                repo_url=repo_url,
                content=class_skeleton.strip(),
                signature=_extract_exact_signature(node, source_lines),
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
                        id=_generate_node_id(repo_url, file_path, method_type, method_qualified_name),
                        content_hash=_generate_content_hash(method_content),
                        name=method_qualified_name,
                        node_type=method_type,
                        parent_id=class_id, 
                        file_path=file_path,
                        repo_url=repo_url,
                        content=method_content,
                        signature=_extract_exact_signature(sub_node, source_lines),
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
                id=_generate_node_id(repo_url, file_path, func_type, node.name),
                content_hash=_generate_content_hash(func_content),
                name=node.name,
                node_type=func_type,
                parent_id=module_id,
                file_path=file_path,
                repo_url=repo_url,
                content=func_content,
                signature=_extract_exact_signature(node, source_lines),
                docstring=ast.get_docstring(node),
                line_range=(node.lineno, node.end_lineno),
                last_commit_message=commit_message,
                last_commit_author=commit_author
            ))

    return chunks

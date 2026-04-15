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

    # --- Git Metadata ---
    last_commit_hash: Optional[str] = None
    last_commit_message: Optional[str] = None
    last_commit_author: Optional[str] = None

class GraphNode(BaseModel):
    id: str
    name: str
    node_type: str  # "module", "class", or "function"
    file_path: str
    repo_url: str

class GraphEdge(BaseModel):
    source_id: str
    target_id: Optional[str] = None   # Used when we know the exact ID (e.g., DEFINES)
    target_name: Optional[str] = None # Used when we only know the string name (e.g., CALLS, IMPORTS)
    relation_type: str                # "DEFINES", "CALLS", "IMPORTS", "INHERITS"


def _resolve_call_name(node: ast.AST) -> str | None:
    """Recursively unwraps AST nodes to get the full string name of a call."""
    if isinstance(node, ast.Name):
        return node.id
        
    elif isinstance(node, ast.Attribute):
        base_name = _resolve_call_name(node.value)
        if base_name:
            if base_name == "self":
                return node.attr  # Convert 'self.sync_graph' to just 'sync_graph'
            return f"{base_name}.{node.attr}"
            
    return None 

def _extract_dependencies(node: ast.AST) -> tuple[list[str], list[str]]:
    """Sweeps an AST node body to extract all function calls and imports."""
    calls = []
    imports = []
    
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            call_name = _resolve_call_name(child.func)
            if call_name:
                calls.append(call_name)
                
        elif isinstance(child, ast.ImportFrom):
            if child.module:
                imports.append(child.module)
                
        elif isinstance(child, ast.Import):
            for alias in child.names:
                imports.append(alias.name)
                
    return list(set(calls)), list(set(imports))

def parse_code_string(
    source_code: str,
    file_path: str,
    repo_url: str,
    commit_message: Optional[str] = None,
    commit_author: Optional[str] = None,
) -> tuple[list[CodeChunk], list[GraphNode], list[GraphEdge]]:
    """
    Takes a raw string of Python code from memory and parses it into CodeChunks.
    """
    chunks = []
    graph_nodes = []
    graph_edges = []

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
    module_name = file_path.split('/')[-1].replace('.py', '')
    module_id = _generate_node_id(repo_url, file_path, "module", module_name)
    mod_doc = ast.get_docstring(tree) if is_parsable else source_code[:500]
    mod_imports = _extract_imports(tree) if is_parsable else None
    global_assignments = []
    if is_parsable:
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                segment = ast.get_source_segment(source_code, node)
                if segment:
                    global_assignments.append(segment)
                    
    global_text = "\n".join(global_assignments)
    
    chunks.append(CodeChunk(
        id=module_id,
        content_hash=_generate_content_hash(str(mod_doc) + str(mod_imports)),
        name=module_name,
        node_type="module",
        parent_id=None,
        file_path=file_path,
        repo_url=repo_url,
        content= f"Docstring: {mod_doc}\nImports: {mod_imports}\nGlobal Variables:\n{global_text}",
        docstring=mod_doc,
        imports=mod_imports,
        line_range=(1, len(source_lines)),
        last_commit_message=commit_message,
        last_commit_author=commit_author
    ))
    graph_nodes.append(GraphNode(id=module_id, name=module_name, node_type="module", file_path=file_path, repo_url=repo_url))
    mod_calls, mod_imports = _extract_dependencies(tree)
    for imp in mod_imports:
        graph_edges.append(GraphEdge(
            source_id=module_id, target_name=imp, relation_type="IMPORTS"
        ))
    # Create the CALLS Edges (Global instantiations)
    for call in mod_calls:
        graph_edges.append(GraphEdge(
            source_id=module_id, target_name=call, relation_type="CALLS"
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
            graph_nodes.append(GraphNode(
                id=class_id, name=node.name, node_type="class", file_path=file_path, repo_url=repo_url))
            
            graph_edges.append(GraphEdge(
                source_id=module_id, target_id=class_id, relation_type="DEFINES"))
            
            bases = [_resolve_call_name(b) for b in node.bases if _resolve_call_name(b)]
            for base in bases:
                graph_edges.append(GraphEdge(
                    source_id=class_id, target_name=base, relation_type="INHERITS"
                ))

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
                    method_id = _generate_node_id(repo_url, file_path, method_type, method_qualified_name)
                    method_content = "\n".join(source_lines[sub_node.lineno - 1 : sub_node.end_lineno])
                    graph_nodes.append(GraphNode(id=method_id, name=method_qualified_name, node_type=method_type, file_path=file_path, repo_url=repo_url))

                    graph_edges.append(GraphEdge(source_id=class_id, target_id=method_id, relation_type="DEFINES"))

                    func_calls, _ = _extract_dependencies(sub_node)
                    for calls in func_calls:
                        graph_edges.append(GraphEdge(source_id=method_id, target_name=calls, relation_type="CALLS"))

                    chunks.append(CodeChunk(
                        id=method_id,
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
            func_id = _generate_node_id(repo_url, file_path, "function", node.name)
            func_type = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            func_content = "\n".join(source_lines[node.lineno - 1 : node.end_lineno])
            func_calls, _ = _extract_dependencies(node)
            graph_nodes.append(GraphNode(
                id=func_id, name=node.name, node_type="function", file_path=file_path, repo_url=repo_url))
            
            graph_edges.append(GraphEdge(
                source_id=module_id, target_id=func_id, relation_type="DEFINES"))
            
            for call in func_calls:
                graph_edges.append(GraphEdge(
                    source_id=func_id, target_name=call, relation_type="CALLS"))

            chunks.append(CodeChunk(
                id=func_id,
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

    return chunks, graph_nodes, graph_edges

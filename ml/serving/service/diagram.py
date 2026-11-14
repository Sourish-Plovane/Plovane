"""B10, project ingestion -> §5.4 diagram.

The handbook (§9) names B10 the "diagram-generation model" but §10 defines
only the result format, not the method. For languages where exact parsing is
cheap (Python today), we use ``ast`` to produce real function nodes and
``calls`` edges. For everything else we drop back to a file-level node, the
same conservative shape ``extract.py`` already uses for non-Python harvesting.

Relationship vocabulary: ``contains`` for folder/file/function nesting and
``calls`` for function-to-function references. The handbook keeps the full
enumeration open (OD-2); this picks the two values the client needs today and
documents them explicitly so OD-2 can confirm them.
"""

from __future__ import annotations

import ast
import os
from typing import Any


NODE_TYPES = ("function", "file", "folder")
RELATIONSHIPS = ("contains", "calls")


# Input: path (str)
# Output: str
# Processing: Normalise a project-relative path to use forward slashes.
# Changed by: Sourish
# Date: 2026-10-31
def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


# Input: path (str)
# Output: list[str]
# Processing: Each ancestor folder of ``path``, from the project root inward.
# Changed by: Sourish
# Date: 2026-10-31
def _folders_above(path: str) -> list[str]:
    parts = _norm(path).split("/")
    acc: list[str] = []
    for index in range(1, len(parts)):
        acc.append("/".join(parts[:index]))
    return acc


# Input: node (ast.AST)
# Output: list[ast.FunctionDef | ast.AsyncFunctionDef]
# Processing: Every function definition anywhere in a Python module.
# Changed by: Sourish
# Date: 2026-10-31
def _python_functions(node: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(child)
    return found


# Input: call (ast.Call)
# Output: str | None
# Processing: The callee's base name for a Call expression, or None.
# Changed by: Sourish
# Date: 2026-10-31
def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


# Input: body (dict), source_path (str)
# Output: list[dict]
# Processing: Diagram nodes for one Python file, including its functions.
# Changed by: Sourish
# Date: 2026-10-31
def _python_file_nodes(code: str, source_path: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return (
            [{"node_id": _file_node_id(source_path),
              "label": os.path.basename(source_path),
              "node_type": "file",
              "source_path": source_path}],
            {},
        )
    file_node_id = _file_node_id(source_path)
    nodes: list[dict[str, Any]] = [{
        "node_id": file_node_id,
        "label": os.path.basename(source_path),
        "node_type": "file",
        "source_path": source_path,
    }]
    name_to_id: dict[str, str] = {}
    for func in _python_functions(tree):
        node_id = _function_node_id(source_path, func.name, func.lineno)
        nodes.append({
            "node_id": node_id,
            "label": func.name,
            "node_type": "function",
            "source_path": source_path,
        })
        name_to_id.setdefault(func.name, node_id)
    return nodes, name_to_id


# Input: source_path (str)
# Output: str
# Processing: Stable opaque id for a file-level node.
# Changed by: Sourish
# Date: 2026-10-31
def _file_node_id(source_path: str) -> str:
    return "file:" + _norm(source_path)


# Input: source_path (str), name (str), line (int)
# Output: str
# Processing: Stable opaque id for a function node (name + line disambiguates overloads).
# Changed by: Sourish
# Date: 2026-10-31
def _function_node_id(source_path: str, name: str, line: int) -> str:
    return f"func:{_norm(source_path)}:{name}:{line}"


# Input: folder (str)
# Output: str
# Processing: Stable opaque id for a folder node.
# Changed by: Sourish
# Date: 2026-10-31
def _folder_node_id(folder: str) -> str:
    return "folder:" + _norm(folder)


# Input: payload (Any)
# Output: dict
# Processing: Validate §9.1 and return a cleaned copy.
# Changed by: Sourish
# Date: 2026-10-31
def validate_ingestion_input(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("the body must be a JSON object")
    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project_id is required and must be a non-empty string")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("files is required and must be a non-empty array")
    cleaned: list[dict[str, str]] = []
    for index, file in enumerate(files):
        if not isinstance(file, dict):
            raise ValueError(f"files[{index}] must be an object")
        path = file.get("path")
        language = file.get("language")
        content = file.get("content")
        if not isinstance(path, str) or not path:
            raise ValueError(f"files[{index}].path must be a non-empty string")
        if not isinstance(language, str) or not language:
            raise ValueError(f"files[{index}].language must be a non-empty string")
        if not isinstance(content, str):
            raise ValueError(f"files[{index}].content must be a string")
        cleaned.append({"path": path, "language": language, "content": content})
    return {"project_id": project_id, "files": cleaned}


# Input: files (list[dict])
# Output: tuple[list[dict], dict[str, dict]]
# Processing: Build file + function nodes for every file; return nodes and per-file callee maps.
# Changed by: Sourish
# Date: 2026-10-31
def _file_level_nodes(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    nodes: list[dict[str, Any]] = []
    per_file_names: dict[str, dict[str, str]] = {}
    for file in files:
        path = _norm(file["path"])
        language = file["language"].lower()
        code = file["content"]
        if language == "python":
            file_nodes, name_map = _python_file_nodes(code, path)
            nodes.extend(file_nodes)
            per_file_names[path] = name_map
        else:
            nodes.append({
                "node_id": _file_node_id(path),
                "label": os.path.basename(path) or path,
                "node_type": "file",
                "source_path": path,
            })
            per_file_names[path] = {}
    return nodes, per_file_names


# Input: files (list[dict])
# Output: list[dict]
# Processing: Build folder nodes for every ancestor directory present.
# Changed by: Sourish
# Date: 2026-10-31
def _folder_nodes(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    folders: set[str] = set()
    for file in files:
        folders.update(_folders_above(file["path"]))
    return [
        {"node_id": _folder_node_id(folder),
         "label": folder.rsplit("/", 1)[-1] or folder,
         "node_type": "folder",
         "source_path": folder}
        for folder in sorted(folders)
    ]


# Input: nodes (list[dict])
# Output: list[dict]
# Processing: ``contains`` edges that mirror folder/file/function nesting.
# Changed by: Sourish
# Date: 2026-10-31
def _contains_edges(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {n["node_id"]: n for n in nodes}
    edges: list[dict[str, Any]] = []
    for node in nodes:
        if node["node_type"] == "folder":
            parent = node["source_path"].rsplit("/", 1)[0]
            if parent and parent != node["source_path"]:
                parent_id = _folder_node_id(parent)
                if parent_id in by_id:
                    edges.append({"source_node_id": parent_id,
                                  "target_node_id": node["node_id"],
                                  "relationship": "contains"})
        elif node["node_type"] == "file":
            folder = node["source_path"].rsplit("/", 1)[0] if "/" in node["source_path"] else ""
            folder_id = _folder_node_id(folder) if folder else None
            if folder_id and folder_id in by_id:
                edges.append({"source_node_id": folder_id,
                              "target_node_id": node["node_id"],
                              "relationship": "contains"})
        elif node["node_type"] == "function":
            file_id = _file_node_id(node["source_path"])
            if file_id in by_id:
                edges.append({"source_node_id": file_id,
                              "target_node_id": node["node_id"],
                              "relationship": "contains"})
    return edges


# Input: files (list[dict]), per_file_names (dict)
# Output: list[dict]
# Processing: ``calls`` edges for Python; cross-file calls resolve by function name.
# Changed by: Sourish
# Date: 2026-10-31
def _call_edges(files: list[dict[str, Any]],
                per_file_names: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    global_names: dict[str, str] = {}
    for name_map in per_file_names.values():
        for name, node_id in name_map.items():
            global_names.setdefault(name, node_id)

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for file in files:
        if file["language"].lower() != "python":
            continue
        path = _norm(file["path"])
        try:
            tree = ast.parse(file["content"])
        except SyntaxError:
            continue
        for func in _python_functions(tree):
            caller_id = _function_node_id(path, func.name, func.lineno)
            local_names = per_file_names.get(path, {})
            for child in ast.walk(func):
                if isinstance(child, ast.Call):
                    callee = _callee_name(child)
                    if not callee:
                        continue
                    target_id = local_names.get(callee) or global_names.get(callee)
                    if not target_id or target_id == caller_id:
                        continue
                    key = (caller_id, target_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append({"source_node_id": caller_id,
                                  "target_node_id": target_id,
                                  "relationship": "calls"})
    return edges


# Input: payload (dict)
# Output: dict
# Processing: §9.1 in, §5.4 out. Entry point from the HTTP handler.
# Changed by: Sourish
# Date: 2026-10-31
def generate(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = validate_ingestion_input(payload)
    folders = _folder_nodes(cleaned["files"])
    files_and_funcs, per_file_names = _file_level_nodes(cleaned["files"])
    nodes = folders + files_and_funcs
    edges = _contains_edges(nodes) + _call_edges(cleaned["files"], per_file_names)
    return {"project_id": cleaned["project_id"], "diagram": {"nodes": nodes, "edges": edges}}

"""API endpoints: ports on service blocks, backed by real route handlers.

An endpoint is drawn as a port on the edge of its service block. Adding one
writes a Flask (Python) or Express (JavaScript) handler; linking a caller to
it writes the client call in the caller's file; and an API arrow always ends
at an endpoint, so ``/endpoint check`` can compare what the caller sends with
what the endpoint expects.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from typing import Any

from whiteboard import codegen
from whiteboard.engine import Context
from whiteboard.model import Block, Endpoint
from whiteboard.ops.arrows import file_of, new_arrow
from whiteboard.registry import OpError, operation

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
ROUTE_DECORATOR = re.compile(r'@bp\.route\("[^"]*", methods=\["[A-Z]+"\]\)')
EXPRESS_ROUTE = re.compile(r"router\.(get|post|put|patch|delete)\('[^']*'")

TEST_SCRIPT = r"""
import importlib.util, json, sys
root, handler, method, route, body = json.loads(sys.argv[1])
sys.path.insert(0, root)
spec = importlib.util.spec_from_file_location("plovane_routes", root + "/" + handler)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from flask import Flask
app = Flask("plovane-endpoint-test")
app.register_blueprint(module.bp)
reply = app.test_client().open(route, method=method, json=None if method in ("GET", "DELETE") else body)
data = reply.get_json(silent=True)
print(json.dumps({"status": reply.status_code,
                  "body": data if data is not None else reply.get_data(as_text=True)[:2000]}))
"""


# Input: value (Any)
# Output: Any
# Processing: A schema from a name, a JSON field map, or a dict.
# Changed by: Claude Code
# Date: 2026-10-22
def parse_schema(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    text = str(value).strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpError("INVALID_ARGS", f"schema is not valid JSON: {exc}") from None
    return text


# Input: sent (Any), expected (Any)
# Output: bool
# Processing: Whether a caller's request schema matches an endpoint's.
# Changed by: Claude Code
# Date: 2026-10-22
def schemas_match(sent: Any, expected: Any) -> bool:
    """Unknown on either side counts as a match: nothing says otherwise."""
    if sent is None or expected is None:
        return True
    if isinstance(sent, dict) and isinstance(expected, dict):
        return set(sent) == set(expected)
    return sent == expected


# Input: ctx (Context), ref (str)
# Output: Endpoint
# Processing: An endpoint from "service:METHOD /path".
# Changed by: Claude Code
# Date: 2026-10-22
def resolve(ctx: Context, ref: str) -> Endpoint:
    key = endpoint_key(ctx, ref)
    if key not in ctx.board.endpoints:
        raise OpError("NOT_FOUND", f"no endpoint {ref!r}; /endpoint list SERVICE shows them")
    return ctx.board.endpoints[key]


# Input: ctx (Context), ref (str)
# Output: str
# Processing: The canonical endpoint id for a reference, resolving the service.
# Changed by: Claude Code
# Date: 2026-10-22
def endpoint_key(ctx: Context, ref: str) -> str:
    service, method, route = split_ref(ref)
    return f"{ctx.board.block(service).id}:{method} {route}"


# Input: ref (str)
# Output: tuple[str, str, str]
# Processing: Split "service:METHOD /path" into its parts.
# Changed by: Claude Code
# Date: 2026-10-22
def split_ref(ref: str) -> tuple[str, str, str]:
    match = re.match(r"^\s*([^:]+):\s*([A-Za-z]+)\s+(/\S*)\s*$", ref or "")
    if not match:
        raise OpError("INVALID_ARGS", f"an endpoint looks like payments:POST /charge, got {ref!r}")
    method = match.group(2).upper()
    if method not in METHODS:
        raise OpError("INVALID_ARGS", f"method must be one of {list(METHODS)}")
    return match.group(1).strip(), method, match.group(3)


# Input: ctx (Context), service (Block)
# Output: str
# Processing: The language a service's code is written in.
# Changed by: Claude Code
# Date: 2026-10-22
def service_language(ctx: Context, service: Block) -> str:
    if service.meta.get("language") in ("python", "javascript", "typescript"):
        return "javascript" if service.meta["language"] == "typescript" else service.meta["language"]
    if service.path and ctx.fs.is_dir(service.path):
        languages = [codegen.language_of(f) for f in ctx.fs.files_under(service.path)]
        if languages.count("javascript") + languages.count("typescript") > languages.count("python"):
            return "javascript"
    return "python"


# Input: ctx (Context), service (Block)
# Output: str
# Processing: The service's routes file, giving the service a folder if needed.
# Changed by: Claude Code
# Date: 2026-10-22
def routes_file(ctx: Context, service: Block) -> str:
    if service.kind not in ("service", "server"):
        raise OpError("INVALID_ARGS", f"{service.id} is a {service.kind}; endpoints belong to a service or server")
    if not service.path:
        service.path = f"services/{service.id}"
        ctx.notes.append(f"gave {service.id} the folder {service.path}")
    ctx.require(service.path)
    language = service_language(ctx, service)
    service.meta["language"] = language
    path = f"{service.path.strip('/')}/routes.{'py' if language == 'python' else 'js'}"
    if not ctx.fs.exists(path):
        ctx.fs.write(path, codegen.routes_header(language, service.name))
    return path


# Input: ctx (Context), endpoint (Endpoint)
# Output: dict[str, Any]
# Processing: An endpoint as JSON with its callers.
# Changed by: Claude Code
# Date: 2026-10-22
def endpoint_record(ctx: Context, endpoint: Endpoint) -> dict[str, Any]:
    from whiteboard.model import METHOD_COLOR

    return {
        "id": endpoint.id, "service": endpoint.service, "method": endpoint.method, "path": endpoint.path,
        "request": endpoint.request, "response": endpoint.response, "handler": endpoint.handler,
        "status": endpoint.status, "color": METHOD_COLOR[endpoint.method], "mock": endpoint.mock,
        "last_test": endpoint.last_test,
        "callers": [a.source for a in ctx.board.arrows.values() if a.endpoint == endpoint.id],
    }


# Input: ctx (Context), service (str), method (str), path (str), request (Any), response (Any)
# Output: dict[str, Any]
# Processing: Add an endpoint port and write its route handler.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.add", command="/endpoint add", tool="plovane_endpoint_add", group="plovane_endpoint")
def add(ctx: Context, service: str, method: str, path: str, request: Any = None,
        response: Any = None) -> dict[str, Any]:
    """Adds an endpoint block to a service and creates the real route handler."""
    svc = ctx.board.block(service)
    ctx.require(svc)
    _, verb, route = split_ref(f"{svc.id}:{method} {path}")
    key = f"{svc.id}:{verb} {route}"
    existing = ctx.board.endpoints.get(key)
    if existing and existing.handler:
        raise OpError("CONFLICT", f"{key} already exists")
    file = routes_file(ctx, svc)
    request, response = parse_schema(request), parse_schema(response)
    text = ctx.fs.read(file)
    if codegen.find_marked(text, f"endpoint {key}"):
        raise OpError("CONFLICT", f"{file} already has a handler for {key}")
    language = service_language(ctx, svc)
    ctx.fs.write(file, codegen.append_block(text, codegen.handler_code(language, key, verb, route, request, response)))
    endpoint = existing or Endpoint(id=key, service=svc.id, method=verb, path=route)
    endpoint.request = request if request is not None else endpoint.request
    endpoint.response = response if response is not None else endpoint.response
    endpoint.handler, endpoint.status = file, "built"
    ctx.board.endpoints[key] = endpoint
    for arrow in ctx.board.arrows.values():
        if arrow.endpoint == key and arrow.status == "planned":
            arrow.status = "built"
    return endpoint_record(ctx, endpoint)


# Input: ctx (Context), source (str), endpoint (str), request (Any)
# Output: dict[str, Any]
# Processing: Connect a block to an endpoint and write the client call.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.link", command="/endpoint link", tool="plovane_endpoint_link", group="plovane_endpoint")
def link(ctx: Context, source: str, endpoint: str, request: Any = None) -> dict[str, Any]:
    """Connects a block to an endpoint with an API arrow and generates the client call code."""
    caller = ctx.board.block(source)
    ctx.require(caller)
    service_ref, verb, route = split_ref(endpoint)
    svc = ctx.board.block(service_ref)
    key = f"{svc.id}:{verb} {route}"
    target = ctx.board.endpoints.get(key)
    if target is None:
        # Linking before the endpoint exists is allowed: it shows as planned.
        target = Endpoint(id=key, service=svc.id, method=verb, path=route, status="planned")
        ctx.board.endpoints[key] = target
    if any(a.source == caller.id and a.endpoint == key for a in ctx.board.arrows.values()):
        raise OpError("CONFLICT", f"{caller.id} already calls {key}")
    sent = parse_schema(request) if request is not None else target.request
    file = file_of(caller)
    client = None
    language = codegen.language_of(file) if file else None
    if file and language in ("python", "javascript", "typescript"):
        text = ctx.fs.read(file)
        if not codegen.find_marked(text, f"call {key}"):
            code = codegen.client_code(language, key, svc.name, verb, route, sent, target.response)
            ctx.fs.write(file, codegen.append_block(text, code))
        client = {"file": file, "key": f"call {key}"}
    else:
        ctx.notes.append(f"{caller.id} has no Python or JavaScript file, so no client code was written")
    status = "built" if target.status in ("built", "mocked") else "planned"
    arrow = new_arrow(ctx, caller, svc, "api", endpoint=key, status=status)
    arrow.meta = {"request": sent, "method": verb, "route": route, "client": client}
    return {"arrow": arrow.id, "endpoint": key, "status": status, "client_code": client}


# Input: ctx (Context), source (str), endpoint (str)
# Output: dict[str, Any]
# Processing: Remove an API connection and its client call code.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.unlink", command="/endpoint unlink", tool="plovane_endpoint_unlink",
           group="plovane_endpoint", confirm=True)
def unlink(ctx: Context, source: str, endpoint: str) -> dict[str, Any]:
    """Removes an API connection and its client call code."""
    caller = ctx.board.block(source)
    ctx.require(caller)
    key = endpoint_key(ctx, endpoint)
    arrows = [a for a in ctx.board.arrows.values() if a.source == caller.id and a.endpoint == key]
    if not arrows:
        raise OpError("NOT_FOUND", f"{caller.id} does not call {key}")
    removed_code = []
    for arrow in arrows:
        client = arrow.meta.get("client")
        if client and ctx.fs.exists(client["file"]):
            text = ctx.fs.read(client["file"])
            ctx.fs.write(client["file"], codegen.replace_marked(text, client["key"], None))
            removed_code.append(client["file"])
        del ctx.board.arrows[arrow.id]
    return {"unlinked": caller.id, "endpoint": key, "code_removed_from": removed_code}


# Input: ctx (Context), service (str)
# Output: dict[str, Any]
# Processing: A service's endpoints and who calls each.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.list", command="/endpoint list", tool="plovane_endpoint_list", group="plovane_endpoint",
           mutates=False)
def list_endpoints(ctx: Context, service: str) -> dict[str, Any]:
    """Lists a service's endpoints and who calls each one."""
    svc = ctx.board.block(service)
    ctx.require(svc, "read")
    return {"service": svc.id, "endpoints": [endpoint_record(ctx, e) for e in ctx.board.endpoints.values()
                                             if e.service == svc.id]}


# Input: ctx (Context), endpoint (str)
# Output: dict[str, Any]
# Processing: An endpoint's method, path, schemas, handler code and callers.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.info", command="/endpoint info", tool="plovane_endpoint_info", group="plovane_endpoint",
           mutates=False)
def info(ctx: Context, endpoint: str) -> dict[str, Any]:
    """Shows an endpoint's method, path, schemas, handler code, and callers."""
    target = resolve(ctx, endpoint)
    ctx.require(ctx.board.blocks[target.service], "read")
    code = None
    if target.handler and ctx.fs.exists(target.handler):
        text = ctx.fs.read(target.handler)
        span = codegen.find_marked(text, f"endpoint {target.id}")
        if span:
            code = "\n".join(text.splitlines()[span[0] + 1:span[1]])
    return endpoint_record(ctx, target) | {"handler_code": code}


# Input: ctx (Context), endpoint (str), path (str | None), method (str | None), request (Any), response (Any)
# Output: dict[str, Any]
# Processing: Change an endpoint and flag the callers that break.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.edit", command="/endpoint edit", tool="plovane_endpoint_edit", group="plovane_endpoint",
           confirm=True)
def edit(ctx: Context, endpoint: str, path: str | None = None, method: str | None = None, request: Any = None,
         response: Any = None) -> dict[str, Any]:
    """Changes an endpoint's path, method, or schemas and flags callers that break."""
    target = resolve(ctx, endpoint)
    svc = ctx.board.blocks[target.service]
    ctx.require(svc)
    old_key = target.id
    _, verb, route = split_ref(f"{svc.id}:{method or target.method} {path or target.path}")
    new_key = f"{svc.id}:{verb} {route}"
    if new_key != old_key and new_key in ctx.board.endpoints:
        raise OpError("CONFLICT", f"{new_key} already exists")
    if target.handler and ctx.fs.exists(target.handler):
        text = ctx.fs.read(target.handler)
        span = codegen.find_marked(text, f"endpoint {old_key}")
        if span:
            lines = text.splitlines(keepends=True)
            block = "".join(lines[span[0]:span[1] + 1])
            block = block.replace(f"endpoint {old_key}", f"endpoint {new_key}")
            block = block.replace(f"Handle {target.method} {target.path}.", f"Handle {verb} {route}.")
            block = ROUTE_DECORATOR.sub(f'@bp.route("{route}", methods=["{verb}"])', block)
            block = EXPRESS_ROUTE.sub(f"router.{verb.lower()}('{route}'", block)
            ctx.fs.write(target.handler, "".join(lines[:span[0]]) + block + "".join(lines[span[1] + 1:]))
    target.id, target.method, target.path = new_key, verb, route
    if request is not None:
        target.request = parse_schema(request)
    if response is not None:
        target.response = parse_schema(response)
    del ctx.board.endpoints[old_key]
    ctx.board.endpoints[new_key] = target
    broken = []
    for arrow in ctx.board.arrows.values():
        if arrow.endpoint == old_key:
            arrow.endpoint = new_key
            if arrow.meta.get("route") != route or arrow.meta.get("method") != verb or \
                    not schemas_match(arrow.meta.get("request"), target.request):
                arrow.status = "failing"
                broken.append(arrow.source)
    if broken:
        ctx.notes.append(f"callers still send the old request: {broken}. Re-link them with /endpoint link.")
    return endpoint_record(ctx, target) | {"was": old_key, "broken_callers": broken}


# Input: ctx (Context), endpoint (str)
# Output: dict[str, Any]
# Processing: Delete an endpoint and its handler after listing its callers.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.delete", command="/endpoint delete", tool="plovane_endpoint_delete",
           group="plovane_endpoint", confirm=True)
def delete(ctx: Context, endpoint: str) -> dict[str, Any]:
    """Deletes an endpoint after showing every block that calls it."""
    target = resolve(ctx, endpoint)
    ctx.require(ctx.board.blocks[target.service])
    callers = [a.source for a in ctx.board.arrows.values() if a.endpoint == target.id]
    if target.handler and ctx.fs.exists(target.handler):
        text = ctx.fs.read(target.handler)
        ctx.fs.write(target.handler, codegen.replace_marked(text, f"endpoint {target.id}", None))
    for arrow_id in [a.id for a in ctx.board.arrows.values() if a.endpoint == target.id]:
        del ctx.board.arrows[arrow_id]
    del ctx.board.endpoints[target.id]
    if callers:
        ctx.notes.append(f"{callers} still contain client code for {target.id}; it will now fail")
    return {"deleted": target.id, "callers": callers}


# Input: ctx (Context), endpoint (str), body (Any)
# Output: dict[str, Any]
# Processing: Send a test request to the handler (or its mock) and record the reply.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.test", command="/endpoint test", tool="plovane_endpoint_test", group="plovane_endpoint",
           mutates=False)
def test(ctx: Context, endpoint: str, body: Any = None) -> dict[str, Any]:
    """Sends a test request in the sandbox and shows the response on the arrow."""
    target = resolve(ctx, endpoint)
    ctx.require(ctx.board.blocks[target.service], "read")
    if body in (None, "sample"):
        payload = codegen.sample_for(target.request)
    else:
        payload = parse_schema(body) if not isinstance(body, dict) else body
    if target.status == "mocked":
        reply = {"status": 200, "body": target.mock, "mocked": True}
    elif not target.handler or not ctx.fs.exists(target.handler):
        raise OpError("CONFLICT", f"{target.id} has no handler yet; /endpoint add it or /endpoint mock it")
    elif not target.handler.endswith(".py"):
        raise OpError("UNSUPPORTED", "test requests run Python (Flask) handlers; mock JavaScript ones")
    else:
        done = ctx.run([sys.executable, "-c", TEST_SCRIPT,
                        json.dumps([str(ctx.root), target.handler, target.method, target.path, payload])], timeout=60)
        if done.returncode != 0:
            raise OpError("TEST_FAILED", (done.stderr or done.stdout).strip()[-1500:])
        reply = json.loads(done.stdout.strip().splitlines()[-1])
    reply["sent"] = payload
    target.last_test = reply
    for arrow in ctx.board.arrows.values():
        if arrow.endpoint == target.id:
            arrow.meta["last_test"] = {"status": reply["status"]}
    return {"endpoint": target.id, **reply}


# Input: ctx (Context), endpoint (str), response (Any)
# Output: dict[str, Any]
# Processing: Give an endpoint a mock response so callers can be built first.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.mock", command="/endpoint mock", tool="plovane_endpoint_mock", group="plovane_endpoint")
def mock(ctx: Context, endpoint: str, response: Any = "sample") -> dict[str, Any]:
    """Creates a mock response so callers can be built before the endpoint exists."""
    try:
        target = resolve(ctx, endpoint)
    except OpError:
        service_ref, verb, route = split_ref(endpoint)
        svc = ctx.board.block(service_ref)
        target = Endpoint(id=f"{svc.id}:{verb} {route}", service=svc.id, method=verb, path=route)
        ctx.board.endpoints[target.id] = target
    ctx.require(ctx.board.blocks[target.service])
    target.mock = codegen.sample_for(target.response) if response in (None, "sample") else (
        response if isinstance(response, (dict, list)) else parse_schema(response))
    target.status = "mocked"
    return endpoint_record(ctx, target)


# Input: ctx (Context), target (str)
# Output: dict[str, Any]
# Processing: Check every API arrow against its endpoint; mismatches turn red.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.check", command="/endpoint check", tool="plovane_endpoint_check", group="plovane_endpoint")
def check(ctx: Context, target: str = "all") -> dict[str, Any]:
    """Checks every API arrow: caller request matches endpoint schema; mismatches turn red."""
    scope = None if target == "all" else ctx.board.block(target).id
    problems = []
    for endpoint in ctx.board.endpoints.values():
        if scope and endpoint.service != scope:
            continue
        if endpoint.status == "built":
            text = ctx.fs.read(endpoint.handler) if endpoint.handler and ctx.fs.exists(endpoint.handler) else ""
            if not codegen.find_marked(text, f"endpoint {endpoint.id}"):
                endpoint.status = "failing"
                problems.append({"endpoint": endpoint.id, "problem": "handler code is missing"})
    for arrow in ctx.board.arrows.values():
        if arrow.type != "api" or (scope and ctx.board.endpoints.get(arrow.endpoint or "") and
                                   ctx.board.endpoints[arrow.endpoint].service != scope):
            continue
        endpoint = ctx.board.endpoints.get(arrow.endpoint or "")
        issue = None
        if arrow.endpoint is None:
            issue = "API arrow ends at a service, not an endpoint"
        elif endpoint is None:
            issue = f"endpoint {arrow.endpoint} no longer exists"
        elif not schemas_match(arrow.meta.get("request"), endpoint.request):
            issue = f"caller sends {arrow.meta.get('request')}, endpoint expects {endpoint.request}"
        elif arrow.meta.get("route") not in (None, endpoint.path) or arrow.meta.get("method") not in (None, endpoint.method):
            issue = f"caller calls {arrow.meta.get('method')} {arrow.meta.get('route')}"
        else:
            client = arrow.meta.get("client")
            if client:
                text = ctx.fs.read(client["file"]) if ctx.fs.exists(client["file"]) else ""
                if not codegen.find_marked(text, client["key"]):
                    issue = f"client code is missing from {client['file']}"
        if issue:
            arrow.status = "failing"
            problems.append({"arrow": arrow.id, "caller": arrow.source, "problem": issue})
        elif endpoint is not None:
            arrow.status = "built" if endpoint.status in ("built", "mocked", "external") else "planned"
    return {"checked": target, "ok": not problems, "problems": problems}


# Input: ctx (Context), spec (str)
# Output: dict[str, Any]
# Processing: Read an OpenAPI document from a project file or URL.
# Changed by: Claude Code
# Date: 2026-10-22
def read_spec(ctx: Context, spec: str) -> dict[str, Any]:
    if spec.startswith(("http://", "https://")):
        with urllib.request.urlopen(spec, timeout=30) as reply:
            text = reply.read().decode("utf-8", errors="replace")
    else:
        text = ctx.fs.read(spec)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError:
            raise OpError("UNAVAILABLE", "YAML specs need PyYAML (pip install pyyaml); JSON works without it") from None
        return yaml.safe_load(text)


# Input: schema (dict | None)
# Output: Any
# Processing: A schema name from a $ref, or a field map from inline properties.
# Changed by: Claude Code
# Date: 2026-10-22
def schema_from_openapi(schema: dict | None) -> Any:
    if not schema:
        return None
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "properties" in schema:
        return {k: v.get("type", "object") for k, v in schema["properties"].items()}
    return schema.get("type")


# Input: operation_ (dict)
# Output: tuple[Any, Any]
# Processing: Request and success-response schemas of one OpenAPI operation.
# Changed by: Claude Code
# Date: 2026-10-22
def operation_schemas(operation_: dict) -> tuple[Any, Any]:
    body = (operation_.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
    response = None
    for code in ("200", "201", "202", 200, 201, 202):
        content = (operation_.get("responses", {}).get(code) or {}).get("content", {})
        if content:
            response = content.get("application/json", {}).get("schema")
            break
    return schema_from_openapi(body), schema_from_openapi(response)


# Input: ctx (Context), document (dict), service (Block), status (str)
# Output: list[str]
# Processing: Create endpoint records for every operation in an OpenAPI document.
# Changed by: Claude Code
# Date: 2026-10-22
def endpoints_from_spec(ctx: Context, document: dict, service: Block, status: str) -> list[str]:
    created = []
    for route, item in (document.get("paths") or {}).items():
        for verb, operation_ in item.items():
            if verb.upper() not in METHODS:
                continue
            key = f"{service.id}:{verb.upper()} {route}"
            request, response = operation_schemas(operation_)
            if key not in ctx.board.endpoints:
                ctx.board.endpoints[key] = Endpoint(id=key, service=service.id, method=verb.upper(), path=route,
                                                    request=request, response=response, status=status)
                created.append(key)
    return created


# Input: ctx (Context), spec (str), service (str | None)
# Output: dict[str, Any]
# Processing: Create endpoints from an OpenAPI spec.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.import", command="/endpoint import", tool="plovane_endpoint_import",
           group="plovane_endpoint")
def import_spec(ctx: Context, spec: str, service: str | None = None) -> dict[str, Any]:
    """Creates endpoint blocks from an OpenAPI spec."""
    document = read_spec(ctx, spec)
    title = (document.get("info") or {}).get("title") or os.path.splitext(os.path.basename(spec))[0]
    try:
        svc = ctx.board.block(service or title)
    except OpError:
        svc = ctx.new_block("service", service or title)
    ctx.require(svc)
    created = endpoints_from_spec(ctx, document, svc, "planned")
    ctx.notes.append("imported endpoints are planned; /endpoint add each one to write its handler")
    return {"service": svc.id, "endpoints": created}


# Input: ctx (Context), service (str), out (str | None)
# Output: dict[str, Any]
# Processing: A service's endpoints as an OpenAPI 3 document, optionally written to a file.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("endpoint.export", command="/endpoint export", tool="plovane_endpoint_export",
           group="plovane_endpoint")
def export(ctx: Context, service: str, out: str | None = None) -> dict[str, Any]:
    """Exports a service's endpoints as an OpenAPI spec."""
    svc = ctx.board.block(service)
    ctx.require(svc, "read")
    components: dict[str, Any] = {}

    # Input: schema (Any)
    # Output: dict | None
    # Processing: An OpenAPI schema object for a stored schema.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def to_schema(schema: Any) -> dict | None:
        if schema is None:
            return None
        if isinstance(schema, dict):
            return {"type": "object", "properties": {k: {"type": str(v)} for k, v in schema.items()}}
        components.setdefault(str(schema), {"type": "object"})
        return {"$ref": f"#/components/schemas/{schema}"}

    paths: dict[str, Any] = {}
    for endpoint in ctx.board.endpoints.values():
        if endpoint.service != svc.id:
            continue
        operation_: dict[str, Any] = {"operationId": codegen.handler_name(endpoint.method, endpoint.path),
                                      "responses": {"200": {"description": "OK"}}}
        if (request := to_schema(endpoint.request)) is not None:
            operation_["requestBody"] = {"content": {"application/json": {"schema": request}}}
        if (response := to_schema(endpoint.response)) is not None:
            operation_["responses"]["200"]["content"] = {"application/json": {"schema": response}}
        paths.setdefault(endpoint.path, {})[endpoint.method.lower()] = operation_
    document = {"openapi": "3.0.3", "info": {"title": svc.name, "version": "1.0.0"}, "paths": paths}
    if components:
        document["components"] = {"schemas": components}
    if out:
        ctx.require(out)
        ctx.fs.write(out, json.dumps(document, indent=2) + "\n")
    return {"service": svc.id, "openapi": document, "written_to": out}


# Input: ctx (Context), name (str), spec (str | None), base_url (str | None)
# Output: dict[str, Any]
# Processing: Add a third-party API as an external block with its endpoints.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("external.add", command="/api external add", tool="plovane_external_api_add", group="plovane_endpoint")
def add_external(ctx: Context, name: str, spec: str | None = None, base_url: str | None = None) -> dict[str, Any]:
    """Adds a third-party API (such as a payment provider) as an external block with its endpoints."""
    ctx.require(None)
    block = ctx.new_block("external", name, meta={"base_url": base_url})
    created: list[str] = []
    if spec:
        document = read_spec(ctx, spec)
        servers = document.get("servers") or []
        if not base_url and servers:
            block.meta["base_url"] = servers[0].get("url")
        created = endpoints_from_spec(ctx, document, block, "external")
    return {"block": block.id, "base_url": block.meta.get("base_url"), "endpoints": created}

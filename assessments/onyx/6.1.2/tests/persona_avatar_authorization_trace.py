"""Static authorization trace for Onyx's persona-avatar endpoint."""

import ast
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

source_root = Path("/workspaces/onyx-runtime-v5.1.3")
evidence = Path(
    "assessments/onyx/6.1.2/evidence/"
    "onyx-persona-avatar-authorization-trace.json"
)

paths = {
    "api": "backend/onyx/server/features/persona/api.py",
    "persona": "backend/onyx/db/persona.py",
    "file_record": "backend/onyx/db/file_record.py",
    "file_store": "backend/onyx/file_store/file_store.py",
    "main": "backend/onyx/main.py",
}

def git(*args):
    return subprocess.check_output(
        ["git", "-C", str(source_root), *args],
        text=True,
    ).strip()

def read_source(name):
    return (source_root / paths[name]).read_text()

def function(name, function_name):
    tree = ast.parse(read_source(name))
    return next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )

api_source = read_source("api")
handler = function("api", "get_persona_avatar")
handler_source = ast.get_source_segment(api_source, handler)
persona_source = read_source("persona")
persona_fn = function("persona", "get_persona_by_id")
persona_fn_source = ast.get_source_segment(
    persona_source, persona_fn
)
main_source = read_source("main")

def call_line(node, method):
    matches = [
        n.lineno for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == method)
            or (
                isinstance(n.func, ast.Attribute)
                and n.func.attr == method
            )
        )
    ]
    return min(matches) if matches else None

persona_line = call_line(handler, "get_persona_by_id")
record_line = call_line(
    handler, "get_filerecord_by_file_id_optional"
)
read_line = call_line(handler, "read_file")

checks = {
    "avatar_route_declared": any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "get"
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "basic_router"
        and len(decorator.args) >= 1
        and isinstance(decorator.args[0], ast.Constant)
        and decorator.args[0].value == "/{persona_id}/avatar"
        for decorator in handler.decorator_list
    ),

    "persona_router_prefix":
        'basic_router = APIRouter(prefix="/persona")'
        in api_source,

    "persona_router_registered":
        "include_router_with_global_prefix_prepended(application, persona_router)"
        in main_source,

    "basic_access_permission":
        "require_permission(Permission.BASIC_ACCESS)"
        in handler_source,

    "requesting_user_passed_to_persona_lookup":
        "user=user" in handler_source,

    "persona_read_mode":
        "is_for_edit=False" in handler_source,

    "persona_checked_before_file_read":
        persona_line is not None
        and record_line is not None
        and read_line is not None
        and persona_line < record_line < read_line,

    "file_origin_checked":
        "FileOrigin.CHAT_UPLOAD" in handler_source,

    "missing_avatar_returns_not_found":
        "OnyxErrorCode.NOT_FOUND" in handler_source,

    "global_permission_paths_present":
        "Permission.MANAGE_AGENTS" in persona_fn_source
        and "Permission.READ_AGENTS" in persona_fn_source,

    "ownership_and_sharing_paths_present":
        "Persona.user_id == user.id" in persona_fn_source
        and "Persona.is_public" in persona_fn_source
        and "get_user_group_ids_for_user" in persona_fn_source,
}

report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "source_commit": git("rev-parse", "HEAD"),
    "source_clean": not bool(
        git("status", "--porcelain", "--untracked-files=no")
    ),
    "method": "Static AST and source-text verification",
    "target_route": "/persona/{persona_id}/avatar",
    "functions": {
        "get_persona_avatar": {
            "file": paths["api"],
            "start_line": handler.lineno,
            "end_line": handler.end_lineno,
        },
        "get_persona_by_id": {
            "file": paths["persona"],
            "start_line": persona_fn.lineno,
            "end_line": persona_fn.end_lineno,
        },
    },
    "execution_order_lines": {
        "persona_lookup": persona_line,
        "file_record_lookup": record_line,
        "file_read": read_line,
    },
    "source_hashes": {
        path: hashlib.sha256(
            (source_root / path).read_bytes()
        ).hexdigest()
        for path in paths.values()
    },
    "checks": checks,
    "overall": (
        "PASS"
        if all(checks.values())
        else "REVIEW_REQUIRED"
    ),
    "limitations": [
        "Static source relationships, not runtime enforcement evidence",
        "Does not test authorized or unauthorized avatar retrieval",
        "Does not test tenant or user isolation",
        "Does not prove deployed image provenance",
    ],
}

assert report["source_clean"], "Source checkout modified"

evidence.parent.mkdir(parents=True, exist_ok=True)
evidence.write_text(json.dumps(report, indent=2) + "\n")

print("=== PERSONA AVATAR AUTHORIZATION TRACE ===")
print("Source:", report["source_commit"])
print("Route:", report["target_route"])

for name, passed in checks.items():
    print(name, "PASS" if passed else "REVIEW")

print("Overall:", report["overall"])
print("Evidence:", evidence)

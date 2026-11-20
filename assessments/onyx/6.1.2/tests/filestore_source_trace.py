"""Read-only AST trace of Onyx v5.1.3 file-store boundaries."""

import ast
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

source_root = Path("/workspaces/onyx-runtime-v5.1.3")
output = Path(
    "assessments/onyx/6.1.2/evidence/"
    "onyx-filestore-source-trace.json"
)

targets = {
    "backend/onyx/file_store/file_store.py": [
        "_build_default_file_store",
        "get_s3_file_store",
        "S3BackedFileStore._get_s3_key",
        "S3BackedFileStore.save_file",
        "S3BackedFileStore.read_file",
        "S3BackedFileStore.delete_file",
    ],
    "backend/onyx/file_store/s3_key_utils.py": [
        "generate_s3_key",
    ],
    "backend/onyx/db/file_record.py": [
        "get_filerecord_by_file_id",
    ],
    "backend/shared_configs/contextvars.py": [
        "get_current_tenant_id",
    ],
}

def git(*args):
    return subprocess.check_output(
        ["git", "-C", str(source_root), *args],
        text=True,
    ).strip()

def find_function(tree, qualified_name):
    parts = qualified_name.split(".")
    current = tree

    for part in parts:
        current = next(
            (
                node
                for node in current.body
                if isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                )
                and node.name == part
            ),
            None,
        )

        if current is None:
            raise ValueError(
                f"Function missing: {qualified_name}"
            )

    if not isinstance(
        current, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        raise ValueError("Expected function")

    return current

def calls_in(node):
    calls = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)
    return sorted(calls)

head = git("rev-parse", "HEAD")
dirty = git(
    "status",
    "--porcelain",
    "--untracked-files=no",
) != ""

if dirty:
    raise SystemExit(
        "BLOCKED: Local Onyx source has tracked modifications. "
        "Review source provenance before assessment."
    )

findings = {}
file_hashes = {}

for relative, names in targets.items():
    path = source_root / relative
    contents = path.read_bytes()
    file_hashes[relative] = hashlib.sha256(
        contents
    ).hexdigest()

    tree = ast.parse(contents.decode("utf-8"))

    for name in names:
        node = find_function(tree, name)
        findings[name] = {
            "file": relative,
            "start_line": node.lineno,
            "end_line": node.end_lineno,
            "calls": calls_in(node),
        }

required_calls = {
    "_build_default_file_store": {"get_s3_file_store"},
    "S3BackedFileStore._get_s3_key": {
        "get_current_tenant_id",
        "generate_s3_key",
    },
    "S3BackedFileStore.save_file": {
        "put_object",
        "upsert_filerecord",
        "commit",
    },
    "S3BackedFileStore.read_file": {
        "get_filerecord_by_file_id",
        "get_object",
    },
    "S3BackedFileStore.delete_file": {
        "get_filerecord_by_file_id_for_update",
        "delete_object",
        "commit",
    },
}

checks = {}

for name, required in required_calls.items():
    observed = set(findings[name]["calls"])
    checks[name] = required.issubset(observed)

report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "source_commit": head,
    "tracked_source_clean": not dirty,
    "method": "Static Python AST analysis",
    "source_file_sha256": file_hashes,
    "functions": findings,
    "checks": checks,
    "overall": (
        "PASS" if all(checks.values())
        else "REVIEW_REQUIRED"
    ),
    "limitations": [
        "Static source analysis, not a runtime call trace",
        "Function calls alone do not establish authorization",
        "No user-level authorization path independently verified",
        "No cross-tenant access attempt performed",
        "Source checkout does not prove deployed image provenance",
    ],
}

output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2) + "\n")

print("=== ONYX FILE-STORE SOURCE TRACE ===")
print("Source commit:", head)
print("Functions traced:", len(findings))

for name, result in findings.items():
    print(
        f"{name}: lines "
        f"{result['start_line']}-{result['end_line']}"
    )

print("\n=== CALL GRAPH CHECKS ===")
for name, passed in checks.items():
    print(name, "PASS" if passed else "REVIEW")

print("\nOverall:", report["overall"])
print("Evidence:", output)

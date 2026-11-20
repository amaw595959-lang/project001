"""Isolated Onyx private-persona visibility/sharing test.

Uses only existing synthetic admin/member accounts.
Does not upload files or alter account permissions.
"""

import datetime
import hashlib
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8080"
RECOVERY = Path(
    "/var/log/onyx/.onyx-612-private-persona-recovery.json"
)

accounts = json.load(sys.stdin)
checks = {}
statuses = {}
error_type = None
persona_id = None
creation_attempted = False
cleanup_verified = False
admin = None
member = None

if RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists():
    raise SystemExit(
        "BLOCKED: An unresolved persona recovery record exists"
    )

def check(name, actual, accepted):
    statuses[name] = actual
    checks[name] = actual in accepted
    print(
        name, actual,
        "PASS" if checks[name] else "REVIEW",
        file=sys.stderr,
    )
    if not checks[name]:
        raise RuntimeError("Unexpected result: " + name)

def new_client():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(
            http.cookiejar.CookieJar()
        )
    )

def request(client, path, method="GET", payload=None):
    headers = {}
    body = None

    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif method == "POST":
        body = b""

    req = urllib.request.Request(
        BASE + path,
        data=body,
        method=method,
        headers=headers,
    )

    try:
        with client.open(req, timeout=20) as response:
            status = response.status
            raw = response.read(2_000_000)
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read(2_000_000)
        error.close()

    result = None
    if raw:
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            result = None

    return status, result

def login(client, role):
    account = accounts[role]

    form = urllib.parse.urlencode({
        "username": account["email"],
        "password": account["password"],
    }).encode()

    req = urllib.request.Request(
        BASE + "/auth/login",
        data=form,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
    )

    try:
        with client.open(req, timeout=20) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()

    check(role + "_login", status, {204})

def persist_recovery(data, initial=False):
    if initial:
        fd = os.open(
            str(RECOVERY),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        return

    temporary = Path(str(RECOVERY) + ".new")
    fd = os.open(
        str(temporary),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )

    with os.fdopen(fd, "w") as f:
        json.dump(data, f)

    os.replace(temporary, RECOVERY)

try:
    admin = new_client()
    member = new_client()

    login(admin, "admin")
    login(member, "member")

    admin_me_status, admin_me = request(admin, "/me")
    member_me_status, member_me = request(member, "/me")

    check("admin_authenticated", admin_me_status, {200})
    check("member_authenticated", member_me_status, {200})

    admin_id = uuid.UUID(str(admin_me["id"]))
    member_id = uuid.UUID(str(member_me["id"]))

    if admin_id == member_id:
        raise RuntimeError("Distinct synthetic users required")

    a_status, a_perms = request(admin, "/me/permissions")
    m_status, m_perms = request(member, "/me/permissions")

    check("admin_permissions_endpoint", a_status, {200})
    check("member_permissions_endpoint", m_status, {200})

    ap = set(a_perms["permissions"])
    mp = set(m_perms["permissions"])

    if not {
        "add:agents", "manage:agents", "read:agents"
    }.issubset(ap):
        raise RuntimeError("Admin lacks expected permissions")

    if mp.intersection({
        "add:agents", "manage:agents", "read:agents"
    }):
        raise RuntimeError("Member has unexpected agent privileges")

    name = "wp612-private-isolation-" + uuid.uuid4().hex

    journal = {
        "synthetic_persona_name": name,
        "persona_id": None,
        "purpose": "WP-6.1.2 controlled cleanup",
    }

    # Journal exists before the first resource write.
    persist_recovery(journal, initial=True)

    persona = {
        "name": name,
        "description": "Synthetic security assessment fixture",
        "document_set_ids": [],
        "tool_ids": [],
        "system_prompt": "Synthetic security testing only.",
        "task_prompt": "",
        "datetime_aware": False,
        "is_public": False,
        "users": [],
        "groups": [],
    }

    creation_attempted = True

    created_status, created = request(
        admin, "/persona", "POST", persona
    )

    check("private_persona_create_http", created_status, {200, 201})

    if not isinstance(created, dict):
        raise RuntimeError("Creation response missing JSON object")

    persona_id = created.get("id")

    if not isinstance(persona_id, int) or persona_id <= 0:
        raise RuntimeError("Creation response missing valid persona ID")

    journal["persona_id"] = persona_id
    persist_recovery(journal)

    path = f"/persona/{persona_id}"

    owner_status, owner_data = request(admin, path)
    check("admin_can_read_private_persona", owner_status, {200})

    if not isinstance(owner_data, dict):
        raise RuntimeError("Owner response missing JSON object")

    if owner_data.get("id") != persona_id:
        raise RuntimeError("Owner response mismatched persona ID")

    if owner_data.get("is_public") is not False:
        raise RuntimeError("Synthetic persona is not private")

    denied_status, _ = request(member, path)
    check("member_private_persona_denied", denied_status, {404})

    share_status, _ = request(
        admin,
        path + "/share",
        "PATCH",
        {
            "is_public": False,
            "user_shares": [
                {
                    "user_id": str(member_id),
                    "permission": "VIEWER",
                }
            ],
        },
    )
    check("admin_shares_with_member", share_status, {200, 204})

    shared_status, shared_data = request(member, path)
    check("member_shared_persona_allowed", shared_status, {200})

    if not isinstance(shared_data, dict):
        raise RuntimeError("Shared response missing JSON object")

    if shared_data.get("id") != persona_id:
        raise RuntimeError("Shared response mismatched persona ID")

    revoke_status, _ = request(
        admin,
        path + "/share",
        "PATCH",
        {
            "is_public": False,
            "user_shares": [],
        },
    )
    check("admin_revokes_member_share", revoke_status, {200, 204})

    revoked_status, _ = request(member, path)
    check("member_access_revoked", revoked_status, {404})

except Exception as error:
    error_type = type(error).__name__
    print("ASSESSMENT STOPPED:", error_type, file=sys.stderr)

finally:
    # Delete only the synthetic persona identified in this run.
    if persona_id is not None and admin is not None:
        try:
            path = f"/persona/{persona_id}"

            delete_status, _ = request(
                admin, path, "DELETE"
            )
            check("admin_soft_delete", delete_status, {200, 204})

            deleted_status, _ = request(admin, path)
            check(
                "soft_deleted_persona_not_accessible",
                deleted_status,
                {404},
            )

            cleanup_verified = True
            RECOVERY.unlink()

        except Exception as error:
            error_type = type(error).__name__
            print(
                "CRITICAL: Persona cleanup not verified:",
                error_type,
                file=sys.stderr,
            )

    for role, client in (("member", member), ("admin", admin)):
        if client is None:
            continue

        try:
            status, _ = request(
                client,
                "/auth/logout",
                "POST",
            )
            check(role + "_logout", status, {204})
        except Exception:
            error_type = "LogoutVerificationError"

passed = (
    creation_attempted
    and cleanup_verified
    and error_type is None
    and len(checks) == 17
    and all(checks.values())
)

report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "target": "Onyx v5.1.3 isolated laboratory",
    "test_type": "Private persona visibility and sharing lifecycle",
    "persona_id_sha256": (
        hashlib.sha256(str(persona_id).encode()).hexdigest()
        if persona_id is not None else None
    ),
    "checks": checks,
    "http_statuses": statuses,
    "soft_delete_verified": cleanup_verified,
    "error_type": error_type,
    "overall": "PASS" if passed else "REVIEW_REQUIRED",
    "limitations": [
        "Persona created by privileged synthetic admin",
        "No non-administrator owner tested",
        "No avatar uploaded or retrieved",
        "No actual S3 file access tested",
        "No cross-tenant test performed",
        "Persona deletion is soft deletion, not physical DB erasure",
        "No credentials or session tokens recorded",
    ],
}

print(json.dumps(report, indent=2))

if RECOVERY.exists():
    print(
        "WARNING: Unresolved recovery record; do not rerun.",
        file=sys.stderr,
    )

sys.exit(0 if passed else 1)

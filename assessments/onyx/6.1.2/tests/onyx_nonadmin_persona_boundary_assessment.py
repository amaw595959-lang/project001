"""WP-6.1.2 Section 22: isolated Onyx v5.1.3 non-admin persona boundary test.

Uses existing synthetic admin/member A and previously registered synthetic member B.
Creates one private synthetic persona and soft-deletes only that exact fixture.
Does NOT delete synthetic member B; separately verify/clean up that account.
Credentials come from JSON on stdin; never print or commit them.
"""

import json
from datetime import datetime, timezone
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path

from onyx.db.engine.sql_engine import SqlEngine, get_session_with_current_tenant
from onyx.db.models import Persona

BASE = "http://127.0.0.1:8080"
RECOVERY = Path("/var/log/onyx/.onyx-612-nonadmin-persona-recovery.json")

REQUIRED = (
    "admin_login", "a_login", "b_login",
    "admin_me", "a_me", "b_me", "identities_distinct",
    "admin_permissions", "a_permissions", "b_permissions", "roles_safe",
    "fixture_name_unused", "persona_created", "initial_owner_admin",
    "a_initial_private_denied", "b_initial_private_denied",
    "transfer_admin_to_a", "owner_record_a", "a_owner_read_allowed",
    "b_private_denied", "b_private_share_denied", "b_still_denied",
    "private_remains_private_after_b_attempt",
    "a_shares_with_b", "b_shared_read_allowed",
    "b_viewer_cannot_reshare", "owner_still_a_after_viewer_attempt",
    "private_remains_private_after_viewer_attempt", "b_viewer_transfer_denied",
    "a_revokes_b", "b_revoked_access_denied", "a_owner_read_retained",
    "b_ownership_transfer_denied", "owner_still_a_after_transfer_attempt",
    "a_transfers_back_to_admin", "owner_restored_admin",
    "admin_soft_deletes", "database_tombstone_verified",
    "b_deleted_persona_denied", "a_deleted_persona_denied",
    "b_logout", "a_logout", "admin_logout",
)

checks = {}
statuses = {}
errors = []
cleanup_errors = []
clients = {}
account_ids = {}
fixture_name = None
persona_id = None
journal_created = False
creation_attempted = False
cleanup_verified = False
started_utc = datetime.now(timezone.utc).isoformat()


def record(name, actual, acceptable=None):
    if name not in REQUIRED or name in checks:
        raise RuntimeError("Invalid or repeated check: " + name)
    if acceptable is None:
        passed = bool(actual)
    else:
        passed = actual in acceptable
        statuses[name] = actual
    checks[name] = passed
    print(name, ("HTTP " + str(actual)) if acceptable is not None else "",
          "PASS" if passed else "REVIEW", file=sys.stderr)
    if not passed:
        raise AssertionError("Failed check: " + name)


def client():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )


def call(opener, path, method="GET", payload=None):
    headers = {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method == "POST":
        body = b""
    request = urllib.request.Request(
        BASE + path, data=body, method=method, headers=headers
    )
    try:
        with opener.open(request, timeout=25) as response:
            status, raw = response.status, response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(1_000_000)
        exc.close()
    decoded = None
    if raw:
        try:
            decoded = json.loads(raw)
        except (UnicodeError, ValueError):
            pass
    return status, decoded


def sign_in(opener, account):
    form = urllib.parse.urlencode({
        "username": account["email"], "password": account["password"]
    }).encode()
    req = urllib.request.Request(
        BASE + "/auth/login", data=form, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener.open(req, timeout=25) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
    return status


def fixture_record():
    """Only the exact unique synthetic name may be inspected for cleanup."""
    if not fixture_name:
        return None
    with get_session_with_current_tenant() as session:
        matches = session.query(Persona).filter(Persona.name == fixture_name).all()
        if len(matches) > 1:
            raise RuntimeError("Multiple records matching exact synthetic name")
        if not matches:
            return None
        p = matches[0]
        return {"id": int(p.id), "owner_id": str(p.user_id) if p.user_id else None,
                "deleted": bool(p.deleted), "builtin": bool(p.builtin_persona),
                "is_public": bool(p.is_public)}


def write_recovery(data, *, initial=False):
    destination = RECOVERY if initial else Path(str(RECOVERY) + ".new")
    fd = os.open(str(destination), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if not initial:
        os.replace(destination, RECOVERY)


def cleanup_persona():
    """Recover solely this synthetic persona; do not touch any other object."""
    global persona_id, cleanup_verified
    if not journal_created:
        cleanup_verified = True
        return
    record_db = fixture_record()
    if record_db is None:
        # If the POST response was lost, verify absence by exact synthetic name.
        cleanup_verified = True
    else:
        if record_db["builtin"]:
            raise RuntimeError("Refusing cleanup of a built-in persona")
        if persona_id is not None and record_db["id"] != persona_id:
            raise RuntimeError("Persona ID/name mismatch; cleanup blocked")
        persona_id = record_db["id"]
        if not record_db["deleted"]:
            owner_id = record_db["owner_id"]
            admin_id = account_ids.get("admin")
            if owner_id != admin_id:
                aliases = [alias for alias, uid in account_ids.items() if uid == owner_id]
                if len(aliases) != 1 or "admin" not in clients:
                    raise RuntimeError("Unknown synthetic owner; cleanup blocked")
                owner = aliases[0]
                status, _ = call(
                    clients[owner], f"/persona/{persona_id}/transfer-ownership",
                    "POST", {"new_owner_user_id": admin_id}
                )
                if status not in (200, 204):
                    raise RuntimeError("Could not restore admin ownership for cleanup")
            status, _ = call(clients["admin"], f"/persona/{persona_id}", "DELETE")
            if status not in (200, 204):
                raise RuntimeError("Synthetic persona soft-delete failed")
        final = fixture_record()
        cleanup_verified = bool(final and final["deleted"] and final["id"] == persona_id)
    if cleanup_verified:
        RECOVERY.unlink()


try:
    if RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists():
        raise RuntimeError("Unresolved recovery journal; do not rerun")
    accounts = json.load(sys.stdin)
    assert set(accounts) == {"admin", "a", "b"}
    for alias, details in accounts.items():
        if not isinstance(details.get("email"), str) or not isinstance(details.get("password"), str):
            raise ValueError("Incomplete synthetic credentials for " + alias)
    with SqlEngine.scoped_engine(pool_size=1, max_overflow=0):
        try:
            for alias in ("admin", "a", "b"):
                opener = client()
                clients[alias] = opener
                record(alias + "_login", sign_in(opener, accounts[alias]), {204})

            for alias in ("admin", "a", "b"):
                status, me = call(clients[alias], "/me")
                record(alias + "_me", status, {200})
                account_ids[alias] = str(uuid.UUID(str(me["id"])))
            record("identities_distinct", len(set(account_ids.values())) == 3)

            permissions = {}
            for alias in ("admin", "a", "b"):
                status, result = call(clients[alias], "/me/permissions")
                record(alias + "_permissions", status, {200})
                permissions[alias] = set(result["permissions"])
            forbidden = {"admin", "add:agents", "manage:agents", "read:agents"}
            record("roles_safe", bool(permissions["admin"].intersection({"admin", "manage:agents"}))
                   and not permissions["a"].intersection(forbidden)
                   and not permissions["b"].intersection(forbidden))

            fixture_name = "wp612-nonadmin-isolation-" + uuid.uuid4().hex
            record("fixture_name_unused", fixture_record() is None)
            write_recovery({"test": "WP-6.1.2-Section-22", "synthetic_persona_name": fixture_name,
                            "persona_id": None, "member_b_account_retained": True}, initial=True)
            journal_created = True
            creation_attempted = True
            payload = {
                "name": fixture_name,
                "description": "Synthetic non-admin authorization boundary fixture",
                "document_set_ids": [], "tool_ids": [],
                "system_prompt": "Synthetic security assessment fixture only.",
                "task_prompt": "", "datetime_aware": False,
                "is_public": False, "users": [], "groups": []
            }
            status, created = call(clients["admin"], "/persona", "POST", payload)
            record("persona_created", status, {200, 201})
            persona_id = int(created["id"])
            if persona_id <= 0:
                raise RuntimeError("Invalid new persona ID")
            write_recovery({"test": "WP-6.1.2-Section-22", "synthetic_persona_name": fixture_name,
                            "persona_id": persona_id, "member_b_account_retained": True})

            record("initial_owner_admin", (fixture_record() or {}).get("owner_id") == account_ids["admin"])
            for alias, label in (("a", "a_initial_private_denied"), ("b", "b_initial_private_denied")):
                status, _ = call(clients[alias], f"/persona/{persona_id}")
                record(label, status, {404})

            status, _ = call(clients["admin"], f"/persona/{persona_id}/transfer-ownership",
                             "POST", {"new_owner_user_id": account_ids["a"]})
            record("transfer_admin_to_a", status, {200, 204})
            record("owner_record_a", (fixture_record() or {}).get("owner_id") == account_ids["a"])
            status, data = call(clients["a"], f"/persona/{persona_id}")
            record("a_owner_read_allowed", status, {200})
            status, _ = call(clients["b"], f"/persona/{persona_id}")
            record("b_private_denied", status, {404})

            status, _ = call(clients["b"], f"/persona/{persona_id}/share", "PATCH",
                             {"is_public": True, "user_shares": []})
            record("b_private_share_denied", status, {400, 403, 404})
            status, _ = call(clients["b"], f"/persona/{persona_id}")
            record("b_still_denied", status, {404})
            record("private_remains_private_after_b_attempt",
                   (fixture_record() or {}).get("is_public") is False)

            status, _ = call(clients["a"], f"/persona/{persona_id}/share", "PATCH", {
                "is_public": False,
                "user_shares": [{"user_id": account_ids["b"], "permission": "VIEWER"}],
            })
            record("a_shares_with_b", status, {200, 204})
            status, _ = call(clients["b"], f"/persona/{persona_id}")
            record("b_shared_read_allowed", status, {200})

            status, _ = call(clients["b"], f"/persona/{persona_id}/share", "PATCH",
                             {"user_shares": [], "is_public": True})
            record("b_viewer_cannot_reshare", status, {400, 403, 404})
            record("owner_still_a_after_viewer_attempt",
                   (fixture_record() or {}).get("owner_id") == account_ids["a"])
            record("private_remains_private_after_viewer_attempt",
                   (fixture_record() or {}).get("is_public") is False)
            status, _ = call(clients["b"], f"/persona/{persona_id}/transfer-ownership",
                             "POST", {"new_owner_user_id": account_ids["b"]})
            record("b_viewer_transfer_denied", status, {403})

            status, _ = call(clients["a"], f"/persona/{persona_id}/share", "PATCH",
                             {"is_public": False, "user_shares": []})
            record("a_revokes_b", status, {200, 204})
            status, _ = call(clients["b"], f"/persona/{persona_id}")
            record("b_revoked_access_denied", status, {404})
            status, _ = call(clients["a"], f"/persona/{persona_id}")
            record("a_owner_read_retained", status, {200})

            status, _ = call(clients["b"], f"/persona/{persona_id}/transfer-ownership",
                             "POST", {"new_owner_user_id": account_ids["b"]})
            record("b_ownership_transfer_denied", status, {403})
            record("owner_still_a_after_transfer_attempt",
                   (fixture_record() or {}).get("owner_id") == account_ids["a"])

            status, _ = call(clients["a"], f"/persona/{persona_id}/transfer-ownership",
                             "POST", {"new_owner_user_id": account_ids["admin"]})
            record("a_transfers_back_to_admin", status, {200, 204})
            record("owner_restored_admin", (fixture_record() or {}).get("owner_id") == account_ids["admin"])
            status, _ = call(clients["admin"], f"/persona/{persona_id}", "DELETE")
            record("admin_soft_deletes", status, {200, 204})
            record("database_tombstone_verified", (fixture_record() or {}).get("deleted") is True)
            status, _ = call(clients["b"], f"/persona/{persona_id}")
            record("b_deleted_persona_denied", status, {404})
            status, _ = call(clients["a"], f"/persona/{persona_id}")
            record("a_deleted_persona_denied", status, {404})

        except Exception as exc:
            errors.append("assessment:" + type(exc).__name__)
            print("ASSESSMENT STOPPED:", type(exc).__name__, file=sys.stderr)
        finally:
            try:
                cleanup_persona()
            except Exception as exc:
                cleanup_errors.append("cleanup:" + type(exc).__name__)
                print("CLEANUP REQUIRES REVIEW:", type(exc).__name__, file=sys.stderr)
            for alias in ("b", "a", "admin"):
                if alias not in clients:
                    continue
                try:
                    status, _ = call(clients[alias], "/auth/logout", "POST")
                    record(alias + "_logout", status, {204})
                except Exception as exc:
                    errors.append("logout_" + alias + ":" + type(exc).__name__)
                    print("LOGOUT REVIEW:", alias, type(exc).__name__, file=sys.stderr)
except Exception as exc:
    errors.append("setup:" + type(exc).__name__)
    print("SETUP STOPPED:", type(exc).__name__, file=sys.stderr)

unresolved = RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists()
passed = (set(checks) == set(REQUIRED) and all(checks.values()) and not errors
          and not cleanup_errors and cleanup_verified and not unresolved)
output = {
    "assessment": "WP-6.1.2", "section": "22", "target": "Onyx v5.1.3 single tenant",
    "started_utc": started_utc, "finished_utc": datetime.now(timezone.utc).isoformat(),
    "required_checks": list(REQUIRED), "checks": checks, "http_statuses": statuses,
    "checks_passed": sum(checks.values()), "checks_expected": len(REQUIRED),
    "overall": "PASS" if passed else "REVIEW_REQUIRED",
    "error_types": errors + cleanup_errors,
    "persona_soft_delete_verified": cleanup_verified,
    "unresolved_recovery_record": unresolved,
    "member_b_account_retained": True,
    "member_b_cleanup_verified": False,
    "fixture_creation_attempted": creation_attempted,
}
json.dump(output, sys.stdout, indent=2, sort_keys=True)
sys.stdout.write("\n")
print("Overall:", output["overall"], "checks", output["checks_passed"], "/",
      output["checks_expected"], "persona cleanup:", cleanup_verified,
      "member B retained:", True, file=sys.stderr)
sys.exit(0 if passed else 1)

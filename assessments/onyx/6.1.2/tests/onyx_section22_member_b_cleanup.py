"""WP-6.1.2 Section 22 synthetic user B removal (Onyx v5.1.3).

Accepts JSON via stdin with credentials for admin, member A, member B.
Deletes only the uniquely named synthetic B registered for this assessment.
Requires two distinct confirmed existing accounts, unprivileged B, no B-owned
personas or B persona shares, and a private recovery journal before mutation.
Never prints credentials or email addresses. Emits sanitized JSON evidence.
"""
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID

from onyx.db.engine.sql_engine import SqlEngine, get_session_with_current_tenant
from onyx.db.enums import AccountType
from onyx.db.models import Persona, Persona__User, User
from shared_configs.configs import MULTI_TENANT

BASE = "http://127.0.0.1:8080"
RECOVERY = Path("/var/log/onyx/.onyx-612-member-b-delete-recovery.json")
EXPECTED = (
    "single_tenant", "synthetic_account_pattern", "admin_login", "a_login",
    "b_login", "admin_me", "a_me", "b_me", "identities_distinct",
    "admin_permissions", "a_permissions", "b_permissions", "permissions_safe",
    "b_db_identity", "b_standard_active", "b_no_owned_personas",
    "b_no_persona_shares", "a_logout", "b_logout", "journal_written",
    "b_deactivated", "b_db_inactive", "b_deleted", "b_db_absent",
    "journal_resolved", "admin_logout",
)
checks = {}
statuses = {}
errors = []
started = datetime.now(timezone.utc).isoformat()
clients = {}
ids = {}
b_email = None
journal_written = False
account_deleted = False


def record(name, actual, allowed=None):
    if name not in EXPECTED or name in checks:
        raise RuntimeError("Duplicate or invalid check name")
    ok = actual in allowed if allowed is not None else bool(actual)
    if allowed is not None:
        statuses[name] = actual
    checks[name] = ok
    print(name, "HTTP " + str(actual) if allowed is not None else "",
          "PASS" if ok else "REVIEW", file=sys.stderr)
    if not ok:
        raise AssertionError("Failed check: " + name)


def make_client():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )


def call(client, path, method="GET", payload=None, content_type=None):
    data = payload
    headers = {"Content-Type": content_type} if content_type else {}
    if method == "POST" and data is None:
        data = b""
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers=headers
    )
    try:
        with client.open(request, timeout=30) as response:
            status, raw = response.status, response.read(100_000)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(100_000)
        exc.close()
    try:
        decoded = json.loads(raw) if raw else None
    except (UnicodeError, ValueError):
        decoded = None
    return status, decoded


def api_json(client, path, method, obj):
    return call(client, path, method, json.dumps(obj).encode(),
                "application/json")


def login(client, credentials):
    form = urllib.parse.urlencode({
        "username": credentials["email"],
        "password": credentials["password"],
    }).encode()
    return call(client, "/auth/login", "POST", form,
                "application/x-www-form-urlencoded")[0]


def db_state():
    with get_session_with_current_tenant() as session:
        records = session.query(User).filter(User.email == b_email).all()
        if len(records) > 1:
            raise RuntimeError("Ambiguous synthetic account match")
        if not records:
            return None
        account = records[0]
        return {
            "id": str(account.id),
            "active": bool(account.is_active),
            "superuser": bool(account.is_superuser),
            "standard": account.account_type == AccountType.STANDARD,
            "owned": session.query(Persona).filter(
                Persona.user_id == account.id
            ).count(),
            "shares": session.query(Persona__User).filter(
                Persona__User.user_id == account.id
            ).count(),
        }


def create_journal():
    fd = os.open(str(RECOVERY), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"purpose": "WP-6.1.2 section 22 synthetic member B cleanup",
                   "email": b_email, "user_id": ids["b"],
                   "started_utc": started}, f, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


try:
    if RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists():
        raise RuntimeError("Previous account-deletion recovery journal exists")
    record("single_tenant", not MULTI_TENANT)
    accounts = json.load(sys.stdin)
    if set(accounts) != {"admin", "a", "b"}:
        raise ValueError("Expected three named account roles")
    for name in accounts:
        if not (isinstance(accounts[name].get("email"), str)
                and isinstance(accounts[name].get("password"), str)):
            raise ValueError("Account credentials incomplete")
    b_email = accounts["b"]["email"]
    record("synthetic_account_pattern", bool(re.fullmatch(
        r"wp612-member-b-[a-f0-9]{16}@example\.com", b_email)))
    with SqlEngine.scoped_engine(pool_size=1, max_overflow=0):
        for alias in ("admin", "a", "b"):
            clients[alias] = make_client()
            record(alias + "_login", login(clients[alias], accounts[alias]), {204})
        for alias in ("admin", "a", "b"):
            status, user = call(clients[alias], "/me")
            record(alias + "_me", status, {200})
            ids[alias] = str(UUID(str(user["id"])))
        record("identities_distinct", len(set(ids.values())) == 3)
        perms = {}
        for alias in ("admin", "a", "b"):
            status, body = call(clients[alias], "/me/permissions")
            record(alias + "_permissions", status, {200})
            perms[alias] = set(body["permissions"])
        privileged = {"admin", "add:agents", "manage:agents", "read:agents"}
        record("permissions_safe", "admin" in perms["admin"]
               and not perms["a"].intersection(privileged)
               and not perms["b"].intersection(privileged))
        state = db_state()
        record("b_db_identity", state is not None and state["id"] == ids["b"])
        record("b_standard_active", state["standard"] and state["active"]
               and not state["superuser"])
        record("b_no_owned_personas", state["owned"] == 0)
        record("b_no_persona_shares", state["shares"] == 0)
        record("a_logout", call(clients["a"], "/auth/logout", "POST")[0], {204})
        record("b_logout", call(clients["b"], "/auth/logout", "POST")[0], {204})
        create_journal()
        journal_written = True
        record("journal_written", RECOVERY.is_file())
        status, _ = api_json(clients["admin"],
                             "/manage/admin/deactivate-user", "PATCH",
                             {"user_email": b_email})
        record("b_deactivated", status, {200, 204})
        state = db_state()
        record("b_db_inactive", state is not None
               and state["id"] == ids["b"] and not state["active"])
        status, _ = api_json(clients["admin"],
                             "/manage/admin/delete-user", "DELETE",
                             {"user_email": b_email})
        record("b_deleted", status, {200, 204})
        account_deleted = db_state() is None
        record("b_db_absent", account_deleted)
        RECOVERY.unlink()
        journal_written = False
        record("journal_resolved", not RECOVERY.exists())
except Exception as exc:
    errors.append("assessment:" + type(exc).__name__)
    print("CLEANUP ASSESSMENT STOPPED:", type(exc).__name__, file=sys.stderr)
finally:
    if "admin" in clients:
        try:
            status, _ = call(clients["admin"], "/auth/logout", "POST")
            record("admin_logout", status, {204})
        except Exception as exc:
            errors.append("admin_logout:" + type(exc).__name__)
            print("ADMIN LOGOUT REQUIRES REVIEW:", type(exc).__name__, file=sys.stderr)

unresolved = RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists()
overall = (set(checks) == set(EXPECTED) and all(checks.values())
           and account_deleted and not unresolved and not errors)
result = {
    "assessment": "WP-6.1.2", "section": "22-member-b-cleanup",
    "started_utc": started,
    "finished_utc": datetime.now(timezone.utc).isoformat(),
    "target": "Onyx v5.1.3 isolated single tenant",
    "required_checks": list(EXPECTED),
    "checks": checks,
    "checks_passed": sum(checks.values()),
    "checks_expected": len(EXPECTED),
    "http_statuses": statuses,
    "synthetic_member_b_database_absence_verified": account_deleted,
    "recovery_journal_unresolved": unresolved,
    "error_types": errors,
    "overall": "PASS" if overall else "REVIEW_REQUIRED",
}
json.dump(result, sys.stdout, indent=2, sort_keys=True)
sys.stdout.write("\n")
print("Overall:", result["overall"], "checks", result["checks_passed"], "/",
      result["checks_expected"], "B deleted:", account_deleted,
      "recovery unresolved:", unresolved, file=sys.stderr)
sys.exit(0 if overall else 1)

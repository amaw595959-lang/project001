"""WP-6.1.2: synthetic avatar permission transition in isolated Onyx."""
import binascii
import datetime
import hashlib
import http.cookiejar
import json
import os
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from io import BytesIO
from pathlib import Path

from botocore.exceptions import ClientError
from onyx.configs.app_configs import FILE_STORE_BACKEND, S3_ENDPOINT_URL, S3_LEGACY_ENDPOINT_URL
from onyx.configs.constants import FileOrigin
from onyx.db.engine.sql_engine import SqlEngine, get_session_with_current_tenant
from onyx.db.models import FileRecord, Persona
from onyx.file_store.file_store import S3BackedFileStore, get_default_file_store
from shared_configs.configs import MULTI_TENANT

BASE = "http://127.0.0.1:8080"
RECOVERY = Path("/var/log/onyx/.onyx-612-avatar-ownership-recovery.json")
accounts = json.load(sys.stdin)
results = {}
statuses = {}
errors = []
file_id = str(uuid.uuid4())
persona_name = "wp612-avatar-ownership-" + uuid.uuid4().hex
persona_id = None
admin_id = member_id = None
save_attempted = False
creation_attempted = False
cleanup_verified = False
clients = {}

REQUIRED = {
    "admin_login", "member_login", "admin_me", "member_me", "role_preconditions",
    "synthetic_key_absent", "synthetic_record_absent", "filestore_save",
    "filestore_record_persisted", "s3_object_persisted", "persona_created",
    "initial_persona_owner_admin", "admin_avatar_200", "admin_avatar_digest",
    "member_private_avatar_404", "transfer_to_member", "owner_record_member",
    "member_avatar_200", "member_avatar_digest", "persona_soft_delete",
    "persona_deleted_in_db", "deleted_avatar_404", "filestore_delete",
    "filestore_record_removed", "s3_object_removed", "admin_logout", "member_logout",
}

if RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists():
    raise SystemExit("BLOCKED: unresolved avatar recovery journal")
if FILE_STORE_BACKEND.lower() != "s3" or MULTI_TENANT:
    raise SystemExit("BLOCKED: Unexpected file store or tenant mode")
if S3_ENDPOINT_URL != "http://object-store:8333" or S3_LEGACY_ENDPOINT_URL:
    raise SystemExit("BLOCKED: Unexpected storage endpoint or legacy dual-write")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, url):
        return None


def check(name, good, observed=None, *, strict=True):
    results[name] = bool(good)
    if observed is not None:
        statuses[name] = observed
    print(name, observed if observed is not None else "", "PASS" if good else "REVIEW", file=sys.stderr)
    if strict and not good:
        raise RuntimeError("Unexpected assessment result: " + name)


def request(client, path, method="GET", payload=None, body=None, content_type=None):
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
    elif method == "POST" and body is None:
        body = b""
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with client.open(req, timeout=20) as response:
            return response.status, response.read(1024 * 1024)
    except urllib.error.HTTPError as e:
        status = e.code
        e.read(1024 * 1024)
        e.close()
        return status, b""


def as_json(body):
    return json.loads(body) if body else None


def login(role):
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect()
    )
    clients[role] = client
    form = urllib.parse.urlencode({
        "username": accounts[role]["email"], "password": accounts[role]["password"]
    }).encode()
    status, _ = request(
        client, "/auth/login", "POST", body=form,
        content_type="application/x-www-form-urlencoded",
    )
    check(role + "_login", status == 204, status)
    return client


def write_journal(state, initial=False):
    encoded = (json.dumps(state, sort_keys=True) + "\n").encode()
    path = RECOVERY if initial else Path(str(RECOVERY) + ".new")
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(encoded)
        out.flush()
        os.fsync(out.fileno())
    if not initial:
        os.replace(path, RECOVERY)


def png_chunk(label, data):
    entry = label + data
    return struct.pack(">I", len(data)) + entry + struct.pack(">I", binascii.crc32(entry) & 0xffffffff)


png = (
    b"\x89PNG\r\n\x1a\n"
    + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    + png_chunk(b"IDAT", zlib.compress(b"\x00\x15\x26\x37"))
    + png_chunk(b"IEND", b"")
)
payload_sha256 = hashlib.sha256(png).hexdigest()

with SqlEngine.scoped_engine(pool_size=1, max_overflow=0):
    store = get_default_file_store()
    assert isinstance(store, S3BackedFileStore)
    client = store._get_s3_client()
    bucket = store._get_bucket_name()
    key = store._get_s3_key(file_id)
    assert key.endswith("/" + file_id)
    state = {
        "purpose": "WP-6.1.2 isolated synthetic avatar cleanup",
        "file_id": file_id, "object_key": key, "bucket": bucket,
        "persona_name": persona_name, "persona_id": None,
    }
    try:
        admin = login("admin")
        member = login("member")
        s, body = request(admin, "/me")
        check("admin_me", s == 200, s)
        admin_id = uuid.UUID(as_json(body)["id"])
        s, body = request(member, "/me")
        check("member_me", s == 200, s)
        member_id = uuid.UUID(as_json(body)["id"])
        s1, p1 = request(admin, "/me/permissions")
        s2, p2 = request(member, "/me/permissions")
        ap = set(as_json(p1)["permissions"]) if s1 == 200 else set()
        mp = set(as_json(p2)["permissions"]) if s2 == 200 else set()
        valid_roles = (
            admin_id != member_id
            and {"add:agents", "manage:agents", "read:agents"}.issubset(ap)
            and not {"add:agents", "manage:agents", "read:agents"}.intersection(mp)
        )
        check("role_preconditions", valid_roles)

        try:
            client.head_object(Bucket=bucket, Key=key)
            missing = False
        except ClientError as e:
            missing = e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")
        check("synthetic_key_absent", missing)
        with get_session_with_current_tenant() as db:
            absent = db.query(FileRecord).filter_by(file_id=file_id).first() is None
        check("synthetic_record_absent", absent)

        # A durable journal precedes the first persistent mutation.
        write_journal(state, initial=True)
        save_attempted = True
        returned = store.save_file(
            content=BytesIO(png), display_name="wp612-synthetic.png",
            file_origin=FileOrigin.CHAT_UPLOAD, file_type="image/png", file_id=file_id,
        )
        check("filestore_save", returned == file_id)
        with get_session_with_current_tenant() as db:
            rec = db.query(FileRecord).filter_by(file_id=file_id).first()
            valid = rec is not None and rec.object_key == key and rec.bucket_name == bucket and rec.file_origin == FileOrigin.CHAT_UPLOAD
        check("filestore_record_persisted", valid)
        head = client.head_object(Bucket=bucket, Key=key)
        check("s3_object_persisted", head.get("ContentLength") == len(png))

        persona_payload = {
            "name": persona_name, "description": "WP-6.1.2 synthetic avatar fixture",
            "document_set_ids": [], "tool_ids": [], "system_prompt": "Lab only",
            "task_prompt": "", "datetime_aware": False, "is_public": False,
            "users": [], "groups": [], "uploaded_image_id": file_id,
        }
        creation_attempted = True
        s, body = request(admin, "/persona", "POST", payload=persona_payload)
        created = as_json(body)
        valid_create = s in (200, 201) and isinstance(created, dict) and isinstance(created.get("id"), int) and created["id"] > 0
        check("persona_created", valid_create, s)
        persona_id = created["id"]
        state["persona_id"] = persona_id
        write_journal(state)
        with get_session_with_current_tenant() as db:
            row = db.query(Persona).filter_by(id=persona_id).one()
            owner_correct = row.user_id == admin_id and not row.is_public and not row.deleted and row.uploaded_image_id == file_id
        check("initial_persona_owner_admin", owner_correct)

        path = f"/persona/{persona_id}/avatar"
        s, body = request(admin, path)
        check("admin_avatar_200", s == 200, s)
        check("admin_avatar_digest", hashlib.sha256(body).hexdigest() == payload_sha256)
        s, _ = request(member, path)
        check("member_private_avatar_404", s == 404, s)
        s, _ = request(admin, f"/persona/{persona_id}/transfer-ownership", "POST", payload={"new_owner_user_id": str(member_id)})
        check("transfer_to_member", s in (200, 204), s)
        with get_session_with_current_tenant() as db:
            row = db.query(Persona).filter_by(id=persona_id).one()
            now_member = row.user_id == member_id and not row.deleted
        check("owner_record_member", now_member)
        s, body = request(member, path)
        check("member_avatar_200", s == 200, s)
        check("member_avatar_digest", hashlib.sha256(body).hexdigest() == payload_sha256)
    except Exception as exc:
        errors.append("assessment:" + type(exc).__name__)
        print("ASSESSMENT STOPPED:", type(exc).__name__, file=sys.stderr)
    finally:
        # Identify a creation whose HTTP response was lost, without touching other personas.
        if creation_attempted and persona_id is None:
            try:
                with get_session_with_current_tenant() as db:
                    rows = db.query(Persona).filter_by(name=persona_name).all()
                    if len(rows) == 1 and rows[0].uploaded_image_id == file_id and rows[0].user_id == admin_id and not rows[0].builtin_persona:
                        persona_id = rows[0].id
                        state["persona_id"] = persona_id
                        write_journal(state)
                    elif rows:
                        errors.append("unresolved_persona_identity")
            except Exception:
                errors.append("persona_lookup_error")
        persona_clean = False
        if persona_id is not None:
            try:
                s, _ = request(clients["admin"], f"/persona/{persona_id}", "DELETE")
                check("persona_soft_delete", s in (200, 204), s, strict=False)
                with get_session_with_current_tenant() as db:
                    row = db.query(Persona).filter_by(id=persona_id).one_or_none()
                    deleted = row is not None and row.deleted and row.uploaded_image_id == file_id
                check("persona_deleted_in_db", deleted, strict=False)
                s, _ = request(clients["admin"], f"/persona/{persona_id}/avatar")
                check("deleted_avatar_404", s == 404, s, strict=False)
                persona_clean = deleted and s == 404
            except Exception as exc:
                errors.append("persona_cleanup:" + type(exc).__name__)
        elif not creation_attempted:
            persona_clean = True
        elif creation_attempted and persona_id is None:
            # Request outcome is uncertain: retain recovery journal for review.
            errors.append("uncertain_persona_creation")

        file_clean = not save_attempted
        if save_attempted and persona_clean:
            try:
                with get_session_with_current_tenant() as db:
                    rec = db.query(FileRecord).filter_by(file_id=file_id).one_or_none()
                    if rec is not None and (rec.object_key != key or rec.bucket_name != bucket):
                        raise RuntimeError("Refusing cleanup: unexpected file metadata")
                if rec is not None:
                    store.delete_file(file_id)
                else:
                    # Save may have uploaded before its DB metadata committed.
                    client.delete_object(Bucket=bucket, Key=key)
                check("filestore_delete", True, strict=False)
                with get_session_with_current_tenant() as db:
                    record_gone = db.query(FileRecord).filter_by(file_id=file_id).first() is None
                check("filestore_record_removed", record_gone, strict=False)
                try:
                    client.head_object(Bucket=bucket, Key=key)
                    object_gone = False
                except ClientError as exc:
                    object_gone = exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")
                check("s3_object_removed", object_gone, strict=False)
                file_clean = record_gone and object_gone
            except Exception as exc:
                errors.append("file_cleanup:" + type(exc).__name__)

        for role in ("member", "admin"):
            if role in clients:
                try:
                    s, _ = request(clients[role], "/auth/logout", "POST")
                    check(role + "_logout", s == 204, s, strict=False)
                except Exception as exc:
                    errors.append(role + "_logout:" + type(exc).__name__)
        cleanup_verified = persona_clean and file_clean
        if cleanup_verified:
            if RECOVERY.exists():
                RECOVERY.unlink()
            if Path(str(RECOVERY) + ".new").exists():
                Path(str(RECOVERY) + ".new").unlink()

passed = (
    cleanup_verified and not errors and not RECOVERY.exists()
    and not Path(str(RECOVERY) + ".new").exists()
    and set(results) == REQUIRED and all(results.values())
)
report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "target": "Onyx v5.1.3 owned isolated laboratory",
    "test_type": "Synthetic avatar storage, private access and ownership transfer",
    "synthetic_avatar_sha256": payload_sha256,
    "synthetic_file_id_sha256": hashlib.sha256(file_id.encode()).hexdigest(),
    "persona_id_sha256": hashlib.sha256(str(persona_id).encode()).hexdigest() if persona_id else None,
    "checks": results, "http_statuses": statuses,
    "required_checks": sorted(REQUIRED),
    "cleanup_verified": cleanup_verified,
    "unresolved_recovery_record": RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists(),
    "error_types": errors,
    "overall": "PASS" if passed else "REVIEW_REQUIRED",
    "limitations": [
        "Single-tenant isolated test only",
        "Admin initially creates the resource (not an ordinary nonadmin owner)",
        "Direct in-container FileStore write, not Onyx upload-image HTTP handler",
        "Storage traced by persisted file metadata and object HEAD, not packet capture",
        "Persona deletion is soft deletion; test cleans up synthetic file separately",
        "No third nonadmin identity or cross-tenant test",
        "No passwords, cookies, persona IDs, S3 keys, or user identifiers in evidence",
    ],
}
print(json.dumps(report, indent=2))
print("Overall:", report["overall"], "checks", sum(results.values()), "/", len(REQUIRED), file=sys.stderr)
if RECOVERY.exists():
    print("WARNING: unresolved recovery record; DO NOT RERUN", file=sys.stderr)
sys.exit(0 if passed else 1)

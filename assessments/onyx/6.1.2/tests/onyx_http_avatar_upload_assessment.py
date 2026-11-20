"""WP-6.1.2: controlled HTTP avatar upload, MIME sniffing and storage cleanup.

For an owned, single-tenant Onyx v5.1.3 lab only. Consumes synthetic
account credentials on stdin; never records credentials or session cookies.
"""
import binascii
import datetime
import hashlib
from http.cookiejar import CookieJar
import json
import os
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path

from botocore.exceptions import ClientError
from onyx.configs.app_configs import FILE_STORE_BACKEND, S3_ENDPOINT_URL, S3_LEGACY_ENDPOINT_URL
from onyx.configs.constants import FileOrigin
from onyx.db.engine.sql_engine import SqlEngine, get_session_with_current_tenant
from onyx.db.models import FileRecord
from onyx.file_store.file_store import S3BackedFileStore, get_default_file_store
from shared_configs.configs import MULTI_TENANT

BASE = "http://127.0.0.1:8080"
RECOVERY = Path("/var/log/onyx/.onyx-612-http-avatar-upload-recovery.json")
EXPECTED_ENDPOINT = "http://object-store:8333"
accounts = json.load(sys.stdin)
checks = {}
statuses = {}
errors = []
member = None
upload_attempted = False
file_id = None
file_key = None
cleanup_verified = False
unique = uuid.uuid4().hex
valid_name = f"wp612-http-avatar-{unique}.png"
invalid_name = f"wp612-http-invalid-{unique}.png"

REQUIRED = {
    "member_login", "member_authenticated", "member_role_verified",
    "synthetic_names_unused", "invalid_image_rejected",
    "invalid_file_record_absent", "valid_image_uploaded",
    "returned_file_id_valid", "filerecord_persisted",
    "server_detected_png", "s3_object_persisted",
    "s3_payload_digest", "filestore_read_digest",
    "filestore_delete", "filerecord_removed", "s3_object_removed",
    "member_logout",
}

if RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists():
    raise SystemExit("BLOCKED: existing unresolved HTTP upload recovery journal")
if FILE_STORE_BACKEND.lower() != "s3" or MULTI_TENANT:
    raise SystemExit("BLOCKED: unexpected storage or tenant configuration")
if S3_ENDPOINT_URL != EXPECTED_ENDPOINT or S3_LEGACY_ENDPOINT_URL:
    raise SystemExit("BLOCKED: unexpected S3 endpoint or legacy dual-write")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, url):
        return None


def check(name, condition, status=None, strict=True):
    checks[name] = bool(condition)
    if status is not None:
        statuses[name] = status
    print(name, "HTTP " + str(status) if status is not None else "",
          "PASS" if condition else "REVIEW", file=sys.stderr)
    if strict and not condition:
        raise RuntimeError("Unexpected result: " + name)


def http(client, path, method="GET", body=None, content_type=None):
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with client.open(req, timeout=25) as response:
            return response.status, response.read(1_048_576)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.read(1_048_576)
        exc.close()
        return code, b""


def multipart(filename, content, declared_type):
    boundary = "wp612" + uuid.uuid4().hex
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {declared_type}\r\n\r\n"
    ).encode("ascii")
    body = header + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    return body, f"multipart/form-data; boundary={boundary}"


def image_chunk(kind, payload):
    data = kind + payload
    return (struct.pack(">I", len(payload)) + data
            + struct.pack(">I", binascii.crc32(data) & 0xffffffff))


png = (
    b"\x89PNG\r\n\x1a\n"
    + image_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    + image_chunk(b"IDAT", zlib.compress(b"\x00\x15\x26\x37"))
    + image_chunk(b"IEND", b"")
)
png_digest = hashlib.sha256(png).hexdigest()


def journal(state, initial=False):
    target = RECOVERY if initial else Path(str(RECOVERY) + ".new")
    raw = (json.dumps(state, sort_keys=True) + "\n").encode()
    fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if not initial:
        os.replace(target, RECOVERY)


def records_for(name):
    with get_session_with_current_tenant() as db:
        return [
            (r.file_id, r.display_name, r.bucket_name, r.object_key,
             r.file_type, r.file_origin)
            for r in db.query(FileRecord).filter_by(display_name=name).all()
        ]


def missing_object(client, bucket, key):
    try:
        client.head_object(Bucket=bucket, Key=key)
        return False
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return True
        raise


def remove_named(name, expected_id, store, s3, bucket):
    """Clean only a uniquely named test record and its matching S3 key."""
    rows = records_for(name)
    if len(rows) > 1:
        raise RuntimeError("Ambiguous synthetic file records; manual cleanup required")
    if rows:
        rid, display, bkt, key, ftype, origin = rows[0]
        if (display != name or bkt != bucket
                or origin != FileOrigin.CHAT_UPLOAD
                or key != store._get_s3_key(rid)
                or (expected_id is not None and rid != expected_id)):
            raise RuntimeError("Refusing cleanup: file metadata mismatch")
        store.delete_file(rid)
        return rid, key
    if expected_id is not None:
        key = store._get_s3_key(expected_id)
        # Covers upload completed but metadata missing (or already deleted).
        s3.delete_object(Bucket=bucket, Key=key)
        return expected_id, key
    return None, None


with SqlEngine.scoped_engine(pool_size=1, max_overflow=0):
    store = get_default_file_store()
    if not isinstance(store, S3BackedFileStore):
        raise SystemExit("BLOCKED: FileStore is not S3BackedFileStore")
    s3 = store._get_s3_client()
    bucket = store._get_bucket_name()
    state = {
        "purpose": "WP-6.1.2 controlled HTTP upload cleanup",
        "valid_filename": valid_name,
        "invalid_filename": invalid_name,
        "file_id": None,
    }
    try:
        member = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            NoRedirect(),
        )
        form = urllib.parse.urlencode({
            "username": accounts["member"]["email"],
            "password": accounts["member"]["password"],
        }).encode()
        s, _ = http(member, "/auth/login", "POST", form,
                    "application/x-www-form-urlencoded")
        check("member_login", s == 204, s)
        s, _ = http(member, "/me")
        check("member_authenticated", s == 200, s)
        s, raw = http(member, "/me/permissions")
        perms = set(json.loads(raw)["permissions"]) if s == 200 else set()
        check("member_role_verified", s == 200 and
              not perms.intersection({"add:agents", "manage:agents", "read:agents"}), s)
        check("synthetic_names_unused", not records_for(valid_name)
              and not records_for(invalid_name))

        # Recovery data is durable before any potential application file write.
        journal(state, initial=True)
        upload_attempted = True
        bad_body, bad_type = multipart(invalid_name, b"not a valid image", "image/png")
        s, _ = http(member, "/admin/persona/upload-image", "POST", bad_body, bad_type)
        check("invalid_image_rejected", s in (400, 422), s)
        check("invalid_file_record_absent", not records_for(invalid_name))

        # Deliberately declare text/plain; Onyx should sniff PNG from its bytes.
        body, content_type = multipart(valid_name, png, "text/plain")
        s, raw = http(member, "/admin/persona/upload-image", "POST", body, content_type)
        check("valid_image_uploaded", s in (200, 201), s)
        response = json.loads(raw)
        possible_id = response.get("file_id") if isinstance(response, dict) else None
        check("returned_file_id_valid", isinstance(possible_id, str)
              and str(uuid.UUID(possible_id)) == possible_id)
        file_id = possible_id
        state["file_id"] = file_id
        journal(state)

        rows = records_for(valid_name)
        persisted = len(rows) == 1 and rows[0][0] == file_id
        check("filerecord_persisted", persisted)
        rid, _, stored_bucket, stored_key, mime, origin = rows[0]
        file_key = stored_key
        check("server_detected_png", mime == "image/png"
              and origin == FileOrigin.CHAT_UPLOAD
              and stored_key == store._get_s3_key(file_id)
              and stored_bucket == bucket)
        head = s3.head_object(Bucket=bucket, Key=file_key)
        check("s3_object_persisted", head.get("ContentLength") == len(png))
        object_data = s3.get_object(Bucket=bucket, Key=file_key)["Body"].read()
        check("s3_payload_digest", hashlib.sha256(object_data).hexdigest() == png_digest)
        with store.read_file(file_id, mode="b") as stream:
            copied = stream.read()
        check("filestore_read_digest", hashlib.sha256(copied).hexdigest() == png_digest)

    except Exception as exc:
        errors.append("assessment:" + type(exc).__name__)
        print("ASSESSMENT STOPPED:", type(exc).__name__, file=sys.stderr)
    finally:
        # Resolve the response-lost case by unique display_name. Do not guess IDs.
        try:
            valid_rows = records_for(valid_name)
            if file_id is None and len(valid_rows) == 1:
                file_id = valid_rows[0][0]
                state["file_id"] = file_id
                if RECOVERY.exists() and not Path(str(RECOVERY) + ".new").exists():
                    journal(state)
        except Exception as exc:
            errors.append("recovery_lookup:" + type(exc).__name__)

        valid_clean = invalid_clean = False
        try:
            rid, key = remove_named(valid_name, file_id, store, s3, bucket)
            check("filestore_delete", upload_attempted and rid is not None,
                  strict=False)
            valid_clean = not records_for(valid_name)
            check("filerecord_removed", valid_clean, strict=False)
            if key is not None:
                valid_clean = valid_clean and missing_object(s3, bucket, key)
            else:
                # No confirmed object was written; still require an unambiguous outcome.
                valid_clean = valid_clean and not upload_attempted
            check("s3_object_removed", valid_clean, strict=False)
        except Exception as exc:
            errors.append("valid_cleanup:" + type(exc).__name__)

        try:
            remove_named(invalid_name, None, store, s3, bucket)
            invalid_clean = not records_for(invalid_name)
        except Exception as exc:
            errors.append("invalid_cleanup:" + type(exc).__name__)

        if member is not None:
            try:
                s, _ = http(member, "/auth/logout", "POST", b"")
                check("member_logout", s == 204, s, strict=False)
            except Exception as exc:
                errors.append("logout:" + type(exc).__name__)

        cleanup_verified = valid_clean and invalid_clean
        # Lack of an HTTP response/file ID can leave an unidentifiable orphan;
        # retain journal in that case for manual S3 reconciliation.
        if cleanup_verified:
            RECOVERY.unlink(missing_ok=True)
            Path(str(RECOVERY) + ".new").unlink(missing_ok=True)

success = (cleanup_verified and not errors and set(checks) == REQUIRED
           and all(checks.values()) and not RECOVERY.exists()
           and not Path(str(RECOVERY) + ".new").exists())
report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "test_type": "Live HTTP multipart avatar upload and S3 cleanup",
    "synthetic_png_sha256": png_digest,
    "checks": checks,
    "required_checks": sorted(REQUIRED),
    "http_statuses": statuses,
    "cleanup_verified": cleanup_verified,
    "unresolved_recovery_record": (RECOVERY.exists() or Path(str(RECOVERY) + ".new").exists()),
    "error_types": errors,
    "overall": "PASS" if success else "REVIEW_REQUIRED",
    "limitations": [
        "Single-tenant owned lab only",
        "Valid upload executed by ordinary synthetic member, not admin",
        "Successful upload tested with true PNG bytes and false declared text/plain type",
        "No cross-user HTTP avatar retrieval in this test (covered separately)",
        "No independent non-admin-to-non-admin access comparison",
        "When the HTTP response is lost before file ID is known, manual S3 orphan review may be needed",
        "No credentials, cookies, user IDs, raw file IDs, object keys or filenames in public evidence",
    ],
}
print(json.dumps(report, indent=2))
print("Overall:", report["overall"], "checks", sum(checks.values()), "/", len(REQUIRED), file=sys.stderr)
if report["unresolved_recovery_record"]:
    print("WARNING: unresolved recovery record; DO NOT RERUN", file=sys.stderr)
sys.exit(0 if success else 1)

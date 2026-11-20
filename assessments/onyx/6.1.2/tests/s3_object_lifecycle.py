"""Controlled synthetic S3 object lifecycle assessment."""

import datetime
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

endpoint = os.getenv("S3_ENDPOINT_URL")
bucket = os.getenv(
    "S3_FILE_STORE_BUCKET_NAME", "onyx-file-store-bucket"
)
region = os.getenv("AWS_REGION_NAME") or "us-east-2"

if endpoint != "http://object-store:8333":
    raise SystemExit("BLOCKED: Unexpected S3 endpoint")

if os.getenv("FILE_STORE_BACKEND") != "s3":
    raise SystemExit("BLOCKED: Unexpected file-store backend")

access = os.getenv("S3_AWS_ACCESS_KEY_ID")
secret = os.getenv("S3_AWS_SECRET_ACCESS_KEY")

if not access or not secret:
    raise SystemExit("BLOCKED: S3 credentials unavailable")

# Private recovery record in case cleanup cannot be confirmed.
recovery = Path("/var/log/onyx/.onyx-612-s3-object-recovery.json")

if recovery.exists():
    raise SystemExit(
        "BLOCKED: Previous S3 cleanup record exists. "
        "Investigate before running another test."
    )

config = Config(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 0},
)

client = boto3.client(
    "s3",
    endpoint_url=endpoint,
    region_name=region,
    aws_access_key_id=access,
    aws_secret_access_key=secret,
    config=config,
)

unsigned = boto3.client(
    "s3",
    endpoint_url=endpoint,
    region_name=region,
    config=Config(
        signature_version=UNSIGNED,
        s3={"addressing_style": "path"},
        connect_timeout=5,
        read_timeout=10,
        retries={"max_attempts": 0},
    ),
)

# Never reuse a key from an existing Onyx application object.
key = (
    "wp612-assessment-only/"
    + "synthetic-"
    + uuid.uuid4().hex
    + ".txt"
)

payload = (
    "WP-6.1.2 synthetic test content; no customer data.\n"
).encode()

checks = {}
attempted_put = False
cleanup_ok = False
error_type = None
unsigned_status = None

def check(name, passed):
    checks[name] = bool(passed)
    print(
        name,
        "PASS" if passed else "FAIL",
        file=sys.stderr,
    )

def head_status():
    try:
        client.head_object(Bucket=bucket, Key=key)
        return 200
    except ClientError as error:
        return error.response.get(
            "ResponseMetadata", {}
        ).get("HTTPStatusCode")

try:
    client.head_bucket(Bucket=bucket)
    check("authenticated_bucket_access", True)

    before = head_status()
    check("synthetic_key_initially_absent", before == 404)

    if before != 404:
        raise RuntimeError("Unsafe initial object state")

    # Persist only a private object locator, never S3 credentials.
    fd = os.open(
        str(recovery),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    with os.fdopen(fd, "w") as f:
        json.dump({
            "endpoint": endpoint,
            "bucket": bucket,
            "key": key,
        }, f)

    attempted_put = True
    created = client.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="text/plain",
    )

    check(
        "synthetic_object_created",
        created["ResponseMetadata"]["HTTPStatusCode"] == 200,
    )

    retrieved = client.get_object(
        Bucket=bucket,
        Key=key,
    )
    try:
        received = retrieved["Body"].read()
    finally:
        retrieved["Body"].close()

    check("synthetic_object_read", True)
    check(
        "synthetic_content_integrity",
        hashlib.sha256(received).digest()
        == hashlib.sha256(payload).digest(),
    )

    # Read authorization check on this synthetic object only.
    try:
        result = unsigned.get_object(Bucket=bucket, Key=key)
        unsigned_status = result["ResponseMetadata"]["HTTPStatusCode"]
        result["Body"].close()
    except ClientError as error:
        unsigned_status = error.response.get(
            "ResponseMetadata", {}
        ).get("HTTPStatusCode")

    check(
        "unsigned_object_read_denied",
        unsigned_status in (401, 403),
    )

except Exception as error:
    error_type = type(error).__name__
    print(
        "ASSESSMENT ERROR:", error_type,
        file=sys.stderr,
    )

finally:
    if attempted_put:
        try:
            deleted = client.delete_object(
                Bucket=bucket,
                Key=key,
            )
            check(
                "synthetic_object_delete_request",
                deleted["ResponseMetadata"]["HTTPStatusCode"]
                in (200, 204),
            )

            final_status = head_status()
            cleanup_ok = final_status == 404

            check(
                "synthetic_object_absent_after_delete",
                cleanup_ok,
            )

            if cleanup_ok:
                recovery.unlink()

        except Exception as error:
            error_type = type(error).__name__
            print(
                "CRITICAL: Cleanup not verified:",
                error_type,
                file=sys.stderr,
            )
            check("cleanup_verification", False)

passed = (
    attempted_put
    and cleanup_ok
    and error_type is None
    and all(checks.values())
)

report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "flow": "API-container S3 client -> SeaweedFS object lifecycle",
    "target": "Onyx isolated laboratory",
    "object_key_sha256": hashlib.sha256(
        key.encode()
    ).hexdigest(),
    "synthetic_payload_sha256": hashlib.sha256(
        payload
    ).hexdigest(),
    "unsigned_get_http_status": unsigned_status,
    "checks": checks,
    "final_object_absent": cleanup_ok,
    "error_type": error_type,
    "overall": "PASS" if passed else "FAIL_OR_INCONCLUSIVE",
    "limitations": [
        "Direct S3 client, not Onyx application file API",
        "No tenant isolation or object-level ACL comparison",
        "No other bucket objects inspected or modified",
        "Cleanup verified using authenticated HEAD",
        "S3 transport uses HTTP within the Docker network",
    ],
}

print(json.dumps(report, indent=2))

if not cleanup_ok:
    print(
        "WARNING: Cleanup not confirmed. "
        "Do not repeat the test or commit evidence until resolved.",
        file=sys.stderr,
    )

sys.exit(0 if passed else 1)

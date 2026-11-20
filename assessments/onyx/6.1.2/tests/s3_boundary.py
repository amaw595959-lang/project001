"""Read-only Onyx / SeaweedFS S3 boundary assessment."""

import datetime
import json
import os
import sys

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

endpoint = os.getenv("S3_ENDPOINT_URL")
bucket = os.getenv(
    "S3_FILE_STORE_BUCKET_NAME", "onyx-file-store-bucket"
)
region = os.getenv("AWS_REGION_NAME") or "us-east-2"
access_key = os.getenv("S3_AWS_ACCESS_KEY_ID")
secret_key = os.getenv("S3_AWS_SECRET_ACCESS_KEY")

if endpoint != "http://object-store:8333":
    raise SystemExit("BLOCKED: Unexpected S3 endpoint")

if not access_key or not secret_key:
    raise SystemExit("BLOCKED: S3 credentials missing")

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
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    config=config,
)

checks = {}

try:
    client.head_bucket(Bucket=bucket)
    checks["authenticated_bucket_head"] = "PASS"

    result = client.list_objects_v2(
        Bucket=bucket,
        Prefix="wp612-assessment-only/",
        MaxKeys=1,
    )
    checks["authenticated_read_only_listing"] = "PASS"

except Exception as error:
    print(
        "Authenticated S3 assessment failed:",
        type(error).__name__,
        file=sys.stderr,
    )
    checks["authenticated_access"] = "FAIL"

# A separate unsigned client; no credentials are sent.
anonymous = boto3.client(
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

anonymous_status = "INCONCLUSIVE"

try:
    anonymous.head_bucket(Bucket=bucket)
    anonymous_status = "HTTP_200"
except ClientError as error:
    status = error.response.get(
        "ResponseMetadata", {}
    ).get("HTTPStatusCode")
    anonymous_status = (
        "HTTP_" + str(status)
        if isinstance(status, int)
        else "UNKNOWN_ERROR_STATUS"
    )
except Exception:
    anonymous_status = "TRANSPORT_OR_CLIENT_ERROR"

checks["unsigned_bucket_head"] = anonymous_status

positive_passed = (
    checks.get("authenticated_bucket_head") == "PASS"
    and checks.get("authenticated_read_only_listing") == "PASS"
)

negative_confirmed = anonymous_status in ("HTTP_401", "HTTP_403")

overall = (
    "PASS" if positive_passed and negative_confirmed
    else "REVIEW_REQUIRED"
)

report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "flow": "API-container test client -> SeaweedFS S3",
    "destination": "object-store:8333",
    "transport": "HTTP, internal Docker network",
    "authentication": "S3 Signature V4",
    "checks": checks,
    "overall": overall,
    "limitations": [
        "Direct S3 client test, not an Onyx file API request",
        "No existing object content read or disclosed",
        "No objects or buckets created, changed or removed",
        "Unsigned HEAD result alone is not a complete ACL assessment",
        "No cross-tenant object authorization tested",
    ],
}

print(json.dumps(report, indent=2))
sys.exit(0 if overall == "PASS" else 1)

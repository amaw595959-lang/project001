"""Read-only Onyx-to-OpenSearch boundary assessment."""

import datetime
import json
import os
import sys

from opensearchpy import OpenSearch

host = os.getenv("OPENSEARCH_HOST", "opensearch")
port = int(os.getenv("OPENSEARCH_REST_API_PORT", "9200"))
auth_method = os.getenv("OPENSEARCH_AUTH_METHOD", "basic").lower()
use_ssl = os.getenv("OPENSEARCH_USE_SSL", "true").lower() == "true"
verify_certs = os.getenv(
    "OPENSEARCH_VERIFY_CERTS", "false"
).lower() == "true"

password = os.getenv("OPENSEARCH_ADMIN_PASSWORD")
username = os.getenv("OPENSEARCH_ADMIN_USERNAME", "admin")

if host != "opensearch" or port != 9200:
    raise SystemExit("BLOCKED: Unexpected OpenSearch target")

if auth_method != "basic" or not password:
    raise SystemExit("BLOCKED: Expected configured basic authentication")

client = OpenSearch(
    hosts=[{"host": host, "port": port}],
    http_auth=(username, password),
    use_ssl=use_ssl,
    verify_certs=verify_certs,
    ca_certs=os.getenv("OPENSEARCH_CA_CERTS") or None,
    ssl_show_warn=False,
    timeout=15,
    max_retries=0,
)

try:
    info = client.info()
    health = client.cluster.health()
    indices = client.cat.indices(
        format="json",
        h="index",
        expand_wildcards="open",
    )

    if not isinstance(indices, list):
        raise ValueError("Unexpected index metadata response")

except Exception as error:
    print(
        "OpenSearch read-only verification failed:",
        type(error).__name__,
        file=sys.stderr,
    )
    sys.exit(1)

report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "source": "Onyx API container",
    "destination": "opensearch:9200",
    "operation": "Read-only authenticated OpenSearch API requests",
    "transport": {
        "tls_enabled": use_ssl,
        "server_certificate_verification": verify_certs,
        "authentication_method": auth_method,
    },
    "cluster": {
        "version": info.get("version", {}).get("number"),
        "health": health.get("status"),
        "nodes": health.get("number_of_nodes"),
        "visible_open_indices": len(indices),
    },
    "checks": {
        "authenticated_info_request": True,
        "cluster_health_request": True,
        "index_metadata_request": True,
    },
    "limitations": [
        "Uses credentials from the API container environment",
        "Does not prove an Onyx API handler performed a search",
        "Does not prove indexing or retrieval correctness",
        "No document content was queried",
        "Index count may include system indices",
        "No access-control bypass was tested",
    ],
}

print(json.dumps(report, indent=2))

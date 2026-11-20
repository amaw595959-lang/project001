"""WP-6.1.2 read-only S3 workload privilege evidence; run from Codespaces host.

This script never prints, hashes, or persists credential values. It does not
make IAM or S3 write calls. Docker environment values are held only in memory.
"""

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

ROOT = Path('/workspaces/project001')
COMPOSE = Path('/workspaces/onyx-runtime-v5.1.3/deployment/docker_compose/docker-compose.yml')
EVIDENCE = ROOT / 'assessments/onyx/6.1.2/evidence/onyx-s3-workload-privilege.json'
EXPECTED_IMAGE = (
    'docker.io/chrislusf/seaweedfs:4.47@sha256:'
    'ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882'
)
BASE = ['docker', 'compose', '-f', str(COMPOSE), '--project-directory', str(COMPOSE.parent)]

API_CODE = r'''
import json
from urllib.parse import urlsplit
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from onyx.configs.app_configs import (
    AWS_REGION_NAME, S3_AWS_ACCESS_KEY_ID, S3_AWS_SECRET_ACCESS_KEY,
    S3_ENDPOINT_URL, S3_FILE_STORE_BUCKET_NAME, S3_VERIFY_SSL,
)
assert S3_AWS_ACCESS_KEY_ID and S3_AWS_SECRET_ACCESS_KEY
assert S3_ENDPOINT_URL == 'http://object-store:8333'
assert S3_FILE_STORE_BUCKET_NAME
cfg = Config(
    signature_version='s3v4', s3={'addressing_style': 'path'},
    connect_timeout=3, read_timeout=10, retries={'max_attempts': 0},
)
common = dict(
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=S3_AWS_ACCESS_KEY_ID,
    aws_secret_access_key=S3_AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION_NAME or 'us-east-1', config=cfg,
)
def probe(call):
    try:
        response = call()
        status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        return {'status': status, 'allowed': status == 200, 'error_type': None}
    except ClientError as e:
        return {
            'status': e.response.get('ResponseMetadata', {}).get('HTTPStatusCode'),
            'allowed': False,
            'error_type': str(e.response.get('Error', {}).get('Code', 'Unknown')),
        }
    except Exception as e:
        return {'status': None, 'allowed': False, 'error_type': type(e).__name__}
s3 = boto3.client('s3', **common)
iam = boto3.client('iam', **common)
result = {
    'endpoint_scheme': urlsplit(S3_ENDPOINT_URL).scheme,
    'tls_verify_configured': bool(S3_VERIFY_SSL),
    'operations': {
        'configured_bucket_head': probe(lambda: s3.head_bucket(Bucket=S3_FILE_STORE_BUCKET_NAME)),
        'list_all_buckets': probe(lambda: s3.list_buckets()),
        'iam_list_users': probe(lambda: iam.list_users(MaxItems=1)),
    },
}
print(json.dumps(result, sort_keys=True))
'''


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, check=False)
    if p.returncode:
        raise RuntimeError('Subprocess failed: ' + ' '.join(args[:3]))
    return p.stdout.strip()


def env_for(info):
    data = {}
    for item in info['Config'].get('Env', []):
        k, sep, v = item.partition('=')
        if sep:
            data[k] = v
    return data


def main():
    if EVIDENCE.exists():
        raise SystemExit('BLOCKED: Evidence already exists; inspect it first')
    if not ROOT.is_dir() or not COMPOSE.is_file():
        raise SystemExit('BLOCKED: Expected Codespaces paths are not available')

    start = dt.datetime.now(dt.timezone.utc).isoformat()
    ids = {}
    for service in ('api_server', 'object-store'):
        output = run(BASE + ['ps', '-q', service]).splitlines()
        if len(output) != 1 or not output[0]:
            raise SystemExit('BLOCKED: Expected container missing: ' + service)
        ids[service] = output[0]

    containers = json.loads(run(['docker', 'inspect', *ids.values()]))
    by_service = {
        c['Config']['Labels']['com.docker.compose.service']: c
        for c in containers
    }
    if not {'api_server', 'object-store'} <= set(by_service):
        raise SystemExit('BLOCKED: Docker inspect service mapping mismatch')

    api = by_service['api_server']
    store = by_service['object-store']
    api_env = env_for(api)
    store_env = env_for(store)
    key_names = (
        (api_env.get('S3_AWS_ACCESS_KEY_ID'), store_env.get('AWS_ACCESS_KEY_ID')),
        (api_env.get('S3_AWS_SECRET_ACCESS_KEY'), store_env.get('AWS_SECRET_ACCESS_KEY')),
    )
    both_present = all(a and b for a, b in key_names)
    if not both_present:
        raise SystemExit('BLOCKED: Expected explicit credentials absent')
    equal = all(a == b for a, b in key_names)

    shared = bool(
        set(api['NetworkSettings']['Networks'])
        & set(store['NetworkSettings']['Networks'])
    )
    host_ports_published = any(
        (store['HostConfig'].get('PortBindings') or {}).values()
    )
    image_match = store['Config']['Image'] == EXPECTED_IMAGE

    probe = json.loads(run(BASE + ['exec', '-T', 'api_server', 'python', '-c', API_CODE]))
    operations = probe['operations']
    admin_api_allowed = operations['iam_list_users']['status'] == 200
    global_list_allowed = operations['list_all_buckets']['status'] == 200
    bucket_allowed = operations['configured_bucket_head']['status'] == 200

    evidence = {
        'assessment': 'WP-6.1.2-Section-23',
        'started_utc': start,
        'finished_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'read_only': True,
        'image_reference_matches_v4_47_digest': image_match,
        'image_reference': store['Config']['Image'],
        'both_credential_pairs_present': bool(both_present),
        'credentials_identical': bool(equal),
        'shared_docker_network': shared,
        'object_store_host_ports_published': bool(host_ports_published),
        's3_endpoint_scheme': probe['endpoint_scheme'],
        'tls_verify_configured': probe['tls_verify_configured'],
        'operations': operations,
        'finding': 'CONFIRMED' if (
            image_match and equal and bucket_allowed
            and global_list_allowed and admin_api_allowed
        ) else 'REVIEW_REQUIRED',
        'limitations': [
            'Only read-only S3/IAM operations were tested',
            'No IAM modifications or unauthorized object accesses were attempted',
            'Container image reference is not a supply-chain attestation',
            'Administrative identity semantics are supported by SeaweedFS 4.47 source review',
            'Severity requires deployment-specific impact evaluation',
        ],
    }
    # Guard against accidental credential persistence if code is later modified.
    serialized = json.dumps(evidence, indent=2, sort_keys=True) + '\n'
    for pair in key_names:
        for secret in pair:
            if secret and secret in serialized:
                raise SystemExit('BLOCKED: Credential value detected in evidence')

    fd = os.open(str(EVIDENCE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(serialized)

    print('Image digest reference match:', image_match)
    print('Credential pairs identical:', bool(equal))
    print('Shared network:', shared)
    print('Host ports published:', bool(host_ports_published))
    for k, item in operations.items():
        print(k, 'HTTP', item['status'], 'ALLOWED' if item['allowed'] else 'NOT CONFIRMED')
    print('Finding:', evidence['finding'])
    print('Evidence:', EVIDENCE.relative_to(ROOT))
    print('RESULT: S3 WORKLOAD PRIVILEGE EVIDENCE CAPTURED')


if __name__ == '__main__':
    main()

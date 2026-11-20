#!/usr/bin/env python3
"""WP-6.1.2 Section 24.7: synthetic object lifecycle in isolated S3 sandbox.

The script neither accesses nor changes the original Onyx storage instance.
A private recovery record is written before the first S3 mutation. Never rerun
if that record or the evidence already exists; investigate recovery instead.
"""

import datetime as dt
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path('/workspaces/project001/assessments/onyx/6.1.2')
CREDS = Path('/workspaces/.onyx-612-s3-lp-sandbox.json')
JOURNAL = Path('/workspaces/.onyx-612-s3-lp-object-recovery.json')
EVIDENCE = ROOT / 'evidence/onyx-s3-restricted-object-lifecycle.json'
COMPOSE = Path('/workspaces/onyx-runtime-v5.1.3/deployment/docker_compose/docker-compose.yml')
NETWORK = 'wp612-s3-lp-net'
STORE = 'wp612-s3-lp-store'

# Runs in a short-lived client container connected ONLY to the isolated network.
# No credentials, bucket names, or keys are printed to stdout.
RUNNER = r'''
import hashlib
import json
import secrets
import sys
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

credentials = json.loads(Path('/run/wp612-creds.json').read_text())
journal = json.loads(Path('/run/wp612-journal.json').read_text())
bucket = credentials['bucket']
key = journal['key']
assert bucket == journal['bucket'] == 'onyx-file-store-bucket'
assert key.startswith('wp612-restricted-lifecycle/')
assert credentials['app']['accessKey'] != credentials['admin']['accessKey']
assert credentials['app']['secretKey'] != credentials['admin']['secretKey']

s3 = boto3.client(
    's3',
    endpoint_url='http://wp612-s3-lp-store:8333',
    aws_access_key_id=credentials['app']['accessKey'],
    aws_secret_access_key=credentials['app']['secretKey'],
    region_name='us-east-1',
    config=Config(
        signature_version='s3v4',
        s3={'addressing_style': 'path'},
        connect_timeout=3, read_timeout=10,
        retries={'max_attempts': 0},
    ),
)

checks = {}
status_codes = {}
payload_digest = None
error_type = None


def expect_response(name, call, expected):
    resp = call()
    status = resp['ResponseMetadata']['HTTPStatusCode']
    status_codes[name] = status
    assert status in expected, (name, status)
    checks[name] = True
    return resp


def expect_absent(name):
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        status_codes[name] = status
        assert status == 404, (name, status)
        checks[name] = True
    else:
        status_codes[name] = 200
        raise AssertionError(name + ': unexpected existing object')


try:
    expect_response(
        'bucket_head', lambda: s3.head_bucket(Bucket=bucket), (200,)
    )
    expect_absent('object_initially_absent')
    payload = b'WP612-restricted-identity-test:' + secrets.token_bytes(96)
    payload_digest = hashlib.sha256(payload).hexdigest()
    expect_response(
        'put_object',
        lambda: s3.put_object(
            Bucket=bucket, Key=key, Body=payload,
            ContentType='application/octet-stream',
        ),
        (200,),
    )
    metadata = expect_response(
        'head_object', lambda: s3.head_object(Bucket=bucket, Key=key), (200,)
    )
    assert metadata['ContentLength'] == len(payload)
    result = s3.get_object(Bucket=bucket, Key=key)
    status_codes['get_object_digest'] = result['ResponseMetadata']['HTTPStatusCode']
    try:
        actual = result['Body'].read()
    finally:
        result['Body'].close()
    assert status_codes['get_object_digest'] == 200
    assert hashlib.sha256(actual).hexdigest() == payload_digest
    checks['get_object_digest'] = True
    expect_response(
        'delete_object',
        lambda: s3.delete_object(Bucket=bucket, Key=key),
        (200, 204),
    )
    expect_absent('deleted_object_absent')
except Exception as exc:
    error_type = type(exc).__name__

expected_checks = (
    'bucket_head', 'object_initially_absent', 'put_object',
    'head_object', 'get_object_digest', 'delete_object',
    'deleted_object_absent',
)
passed = not error_type and all(checks.get(name) for name in expected_checks)
result = {
    'assessment': 'WP-6.1.2-Section-24.7',
    'sandbox_only': True,
    'restricted_identity_only': True,
    'checks_expected': len(expected_checks),
    'checks_passed': sum(bool(checks.get(n)) for n in expected_checks),
    'checks': checks,
    'http_statuses': status_codes,
    'payload_sha256': payload_digest,
    'synthetic_key_sha256': hashlib.sha256(key.encode()).hexdigest(),
    'synthetic_object_deletion_verified': bool(checks.get('deleted_object_absent')),
    'error_type': error_type,
    'overall': 'PASS' if passed else 'REVIEW_REQUIRED',
}
print(json.dumps(result, sort_keys=True))
sys.exit(0 if passed else 2)
'''


def docker(args, *, check=True):
    result = subprocess.run(['docker', *args], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError('Docker preflight failure')
    return result


def preflight():
    if not ROOT.is_dir() or not EVIDENCE.parent.is_dir() or not COMPOSE.is_file():
        raise RuntimeError('Required repository/runtime path missing')
    if EVIDENCE.exists() or EVIDENCE.is_symlink():
        raise RuntimeError('Evidence already exists — do not rerun')
    if JOURNAL.exists() or JOURNAL.is_symlink():
        raise RuntimeError('Recovery journal exists — inspect before rerun')
    if not CREDS.is_file() or CREDS.is_symlink():
        raise RuntimeError('Synthetic credentials missing or symlinked')
    if CREDS.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('Synthetic credentials must have mode 0600')
    config = json.loads(CREDS.read_text())
    if config.get('bucket') != 'onyx-file-store-bucket':
        raise RuntimeError('Unexpected synthetic bucket')
    if not str(config.get('unrelated_bucket', '')).startswith('wp612-unrelated-'):
        raise RuntimeError('Unexpected unrelated bucket')
    if docker(['network', 'inspect', '-f', '{{.Internal}}', NETWORK]).stdout.strip() != 'true':
        raise RuntimeError('Sandbox network is not internal')
    store = json.loads(docker(['inspect', STORE]).stdout)[0]
    if (store.get('State', {}).get('Health') or {}).get('Status') != 'healthy':
        raise RuntimeError('Sandbox object store is not healthy')
    if any((store['HostConfig'].get('PortBindings') or {}).values()):
        raise RuntimeError('Sandbox object store publishes host ports')
    service_id = docker([
        'compose', '-f', str(COMPOSE), '--project-directory', str(COMPOSE.parent),
        'ps', '-q', 'api_server',
    ]).stdout.strip().splitlines()
    if len(service_id) != 1 or not service_id[0]:
        raise RuntimeError('Original API image reference unavailable')
    api = json.loads(docker(['inspect', service_id[0]]).stdout)[0]
    image = api['Config']['Image']
    docker(['image', 'inspect', image])
    compile(RUNNER, '<isolated-test-runner>', 'exec')
    return config, image


def create_private_journal(config):
    recovery = {
        'assessment': 'WP-6.1.2-Section-24.7',
        'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'bucket': config['bucket'],
        'key': 'wp612-restricted-lifecycle/' + secrets.token_hex(16) + '.bin',
        'status': 'unresolved_until_evidence_verified',
    }
    fd = os.open(str(JOURNAL), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(recovery, stream)
        stream.write('\n')
    assert JOURNAL.stat().st_mode & 0o777 == 0o600
    return recovery


def write_evidence(data):
    fd = os.open(str(EVIDENCE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')


def main():
    config, image = preflight()
    create_private_journal(config)
    print('PASS: Private recovery journal created before S3 mutations', flush=True)
    args = [
        'run', '--rm', '-i', '--pull', 'never',
        '--network', NETWORK, '--user', f'{os.getuid()}:{os.getgid()}',
        '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
        '--mount', f'type=bind,src={CREDS},dst=/run/wp612-creds.json,readonly',
        '--mount', f'type=bind,src={JOURNAL},dst=/run/wp612-journal.json,readonly',
        '--entrypoint', 'python', image, '-c', RUNNER,
    ]
    executed = docker(args, check=False)
    try:
        data = json.loads(executed.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError(
            'Runner did not return valid JSON; journal retained. '
            'Check sandbox status before any retry.'
        )
    if data.get('assessment') != 'WP-6.1.2-Section-24.7':
        raise RuntimeError('Unexpected runner response; journal retained')
    data['runner_exit_code'] = executed.returncode
    data['journal_retained_for_independent_verification'] = True
    write_evidence(data)
    print('RESULT:', data['overall'])
    print('Checks:', data['checks_passed'], '/', data['checks_expected'])
    for name in (
        'bucket_head', 'object_initially_absent', 'put_object',
        'head_object', 'get_object_digest', 'delete_object',
        'deleted_object_absent',
    ):
        print(name, data['http_statuses'].get(name),
              'PASS' if data['checks'].get(name) else 'REVIEW')
    print('Evidence:', EVIDENCE.relative_to(ROOT.parent.parent.parent))
    print('Recovery journal remains private until evidence review')
    if data['overall'] != 'PASS' or executed.returncode != 0:
        raise RuntimeError('REVIEW REQUIRED; no reruns or cleanup until inspected')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('BLOCKED/REVIEW:', type(exc).__name__, str(exc), file=sys.stderr)
        sys.exit(1)

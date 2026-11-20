#!/usr/bin/env python3
"""WP-6.1.2 Section 24.8: Onyx FileStore compatibility with restricted S3.

Modes:
  verify-24-7  Independently confirm prior object deletion and resolve its journal.
  run-24-8     Exercise genuine Onyx S3BackedFileStore against isolated SeaweedFS
               with an in-memory DB metadata adapter (not production Postgres).
  verify-24-8  Independently confirm cleanup and resolve the new journal.

NEVER connects to the original Onyx Docker network, S3 store, or PostgreSQL.
Never rerun a mutation mode after evidence or its recovery record exists.
"""
import datetime as dt
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path('/workspaces/project001/assessments/onyx/6.1.2')
CREDS = Path('/workspaces/.onyx-612-s3-lp-sandbox.json')
JOURNAL_247 = Path('/workspaces/.onyx-612-s3-lp-object-recovery.json')
JOURNAL_248 = Path('/workspaces/.onyx-612-s3-lp-filestore-recovery.json')
EVIDENCE_247 = ROOT / 'evidence/onyx-s3-restricted-object-lifecycle.json'
VERIFIED_247 = ROOT / 'evidence/onyx-s3-restricted-object-cleanup-verified.json'
EVIDENCE_248 = ROOT / 'evidence/onyx-s3-restricted-filestore-compatibility.json'
VERIFIED_248 = ROOT / 'evidence/onyx-s3-restricted-filestore-cleanup-verified.json'
COMPOSE = Path('/workspaces/onyx-runtime-v5.1.3/deployment/docker_compose/docker-compose.yml')
NETWORK = 'wp612-s3-lp-net'
STORE = 'wp612-s3-lp-store'
ENDPOINT = 'http://wp612-s3-lp-store:8333'

CHECK_RUNNER = r'''
import hashlib
import json
import sys
from pathlib import Path
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

creds = json.loads(Path('/run/wp612-creds.json').read_text())
journal = json.loads(Path('/run/wp612-journal.json').read_text())
assert journal['bucket'] == creds['bucket'] == 'onyx-file-store-bucket'
assert journal['key'].startswith(('wp612-restricted-lifecycle/', 'wp612-filestore/'))
assert creds['app']['accessKey'] != creds['admin']['accessKey']
assert creds['app']['secretKey'] != creds['admin']['secretKey']
client = boto3.client(
    's3', endpoint_url='http://wp612-s3-lp-store:8333',
    aws_access_key_id=creds['app']['accessKey'],
    aws_secret_access_key=creds['app']['secretKey'],
    region_name='us-east-1',
    config=Config(signature_version='s3v4',
                  s3={'addressing_style': 'path'},
                  connect_timeout=3, read_timeout=10,
                  retries={'max_attempts': 0}),
)
status = None
error_type = None
try:
    try:
        client.head_object(Bucket=journal['bucket'], Key=journal['key'])
        status = 200
    except ClientError as exc:
        status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
except Exception as exc:
    error_type = type(exc).__name__
result = {
    'assessment': journal['assessment'] + '-independent-cleanup-check',
    'read_only': True,
    'sandbox_only': True,
    'synthetic_key_sha256': hashlib.sha256(journal['key'].encode()).hexdigest(),
    'head_status': status,
    'object_absent': status == 404,
    'error_type': error_type,
    'overall': 'PASS' if status == 404 and error_type is None else 'REVIEW_REQUIRED',
}
print(json.dumps(result, sort_keys=True))
sys.exit(0 if result['overall'] == 'PASS' else 2)
'''

FILESTORE_RUNNER = r'''
import hashlib
import io
import json
import secrets
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from onyx.configs.constants import FileOrigin
from onyx.file_store import file_store as fs
from onyx.file_store.s3_key_utils import generate_s3_key

credentials = json.loads(Path('/run/wp612-creds.json').read_text())
journal = json.loads(Path('/run/wp612-journal.json').read_text())
assert credentials['bucket'] == journal['bucket'] == 'onyx-file-store-bucket'
assert credentials['app']['accessKey'] != credentials['admin']['accessKey']
assert credentials['app']['secretKey'] != credentials['admin']['secretKey']
assert journal['key'].startswith('wp612-filestore/')
assert journal['file_id'].startswith('wp612-fi-')
assert not credentials.get('legacy_endpoint')

checks = {}
http_statuses = {}
error_type = None
file_id = journal['file_id']
bucket = journal['bucket']
key = journal['key']
prefix = 'wp612-filestore'
tenant = 'wp612-sandbox'
record_map = {}

class MetadataSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1

session = MetadataSession()

@contextmanager
def sandbox_session(given):
    assert given is session, 'Original database access blocked'
    yield session

def upsert_record(*, file_id, display_name, file_origin, file_type,
                  bucket_name, object_key, db_session, file_metadata,
                  file_size):
    assert db_session is session
    assert bucket_name == bucket and object_key == key
    record_map[file_id] = SimpleNamespace(
        file_id=file_id, display_name=display_name,
        file_origin=file_origin, file_type=file_type,
        bucket_name=bucket_name, object_key=object_key,
        file_metadata=file_metadata, file_size=file_size,
    )

def read_record(*, file_id, db_session):
    assert db_session is session
    return record_map[file_id]

def read_optional_record(*, file_id, db_session):
    assert db_session is session
    return record_map.get(file_id)

def locked_record(*, file_id, db_session):
    assert db_session is session
    return record_map.get(file_id)

def remove_record(*, file_id, db_session):
    assert db_session is session
    assert file_id in record_map
    del record_map[file_id]

s3 = boto3.client(
    's3', endpoint_url='http://wp612-s3-lp-store:8333',
    aws_access_key_id=credentials['app']['accessKey'],
    aws_secret_access_key=credentials['app']['secretKey'],
    region_name='us-east-1',
    config=Config(signature_version='s3v4',
                  s3={'addressing_style':'path'},
                  connect_timeout=3, read_timeout=10,
                  retries={'max_attempts': 0}),
)

def expect_absent(label):
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        http_statuses[label] = status
        assert status == 404, (label, status)
        checks[label] = True
        return
    http_statuses[label] = 200
    raise AssertionError('Unexpected synthetic object exists: ' + label)

expected = (
    'restricted_identity_separate', 'factory_uses_sandbox_configuration',
    'filestore_initialize', 'synthetic_object_initially_absent',
    'filestore_save_file', 'metadata_adapter_written',
    's3_object_head', 'filestore_has_file', 'filestore_read_file_digest',
    'filestore_get_file_size', 'filestore_read_file_record',
    'filestore_delete_file', 'metadata_adapter_removed',
    'synthetic_object_deleted', 'unrelated_bucket_denied',
    'iam_enumeration_denied', 'bucket_visibility_scoped',
)
try:
    checks['restricted_identity_separate'] = True
    computed_key = generate_s3_key(file_id, prefix, tenant)
    assert key == computed_key

    patches = {
        'S3_FILE_STORE_BUCKET_NAME': bucket,
        'S3_AWS_ACCESS_KEY_ID': credentials['app']['accessKey'],
        'S3_AWS_SECRET_ACCESS_KEY': credentials['app']['secretKey'],
        'S3_ENDPOINT_URL': 'http://wp612-s3-lp-store:8333',
        'S3_FILE_STORE_PREFIX': prefix,
        'AWS_REGION_NAME': 'us-east-1',
        'S3_VERIFY_SSL': False,
        'S3_LEGACY_ENDPOINT_URL': None,
        'S3_LEGACY_AWS_ACCESS_KEY_ID': None,
        'S3_LEGACY_AWS_SECRET_ACCESS_KEY': None,
        'get_current_tenant_id': lambda: tenant,
        'get_session_with_current_tenant_if_none': sandbox_session,
        'upsert_filerecord': upsert_record,
        'get_filerecord_by_file_id': read_record,
        'get_filerecord_by_file_id_optional': read_optional_record,
        'get_filerecord_by_file_id_for_update': locked_record,
        'delete_filerecord_by_file_id': remove_record,
    }
    with patch.multiple(fs, **patches):
        store = fs.get_s3_file_store()
        assert isinstance(store, fs.S3BackedFileStore)
        assert store._bucket_name == bucket
        assert store._s3_endpoint_url == 'http://wp612-s3-lp-store:8333'
        assert store._aws_access_key_id == credentials['app']['accessKey']
        assert store._legacy_endpoint_url is None
        checks['factory_uses_sandbox_configuration'] = True

        store.initialize()
        checks['filestore_initialize'] = True
        expect_absent('synthetic_object_initially_absent')

        payload = b'WP612-S3-restricted-FILESTORE:' + secrets.token_bytes(128)
        digest = hashlib.sha256(payload).hexdigest()
        returned = store.save_file(
            io.BytesIO(payload), 'wp612-sandbox.bin',
            FileOrigin.OTHER, 'application/octet-stream',
            file_id=file_id, db_session=session,
        )
        assert returned == file_id
        checks['filestore_save_file'] = True
        assert file_id in record_map
        assert record_map[file_id].object_key == key
        assert record_map[file_id].file_size == len(payload)
        checks['metadata_adapter_written'] = True

        head = s3.head_object(Bucket=bucket, Key=key)
        http_statuses['s3_object_head'] = head['ResponseMetadata']['HTTPStatusCode']
        assert http_statuses['s3_object_head'] == 200
        assert head['ContentLength'] == len(payload)
        checks['s3_object_head'] = True

        assert store.has_file(file_id, FileOrigin.OTHER,
                              'application/octet-stream', db_session=session)
        checks['filestore_has_file'] = True

        content = store.read_file(file_id, db_session=session)
        try:
            result = content.read()
        finally:
            content.close()
        assert hashlib.sha256(result).hexdigest() == digest
        checks['filestore_read_file_digest'] = True

        assert store.get_file_size(file_id, db_session=session) == len(payload)
        checks['filestore_get_file_size'] = True
        record = store.read_file_record(file_id, db_session=session)
        assert record.file_id == file_id and record.object_key == key
        checks['filestore_read_file_record'] = True

        store.delete_file(file_id, db_session=session)
        checks['filestore_delete_file'] = True
        assert file_id not in record_map
        checks['metadata_adapter_removed'] = True
        expect_absent('synthetic_object_deleted')

    try:
        s3.head_bucket(Bucket=credentials['unrelated_bucket'])
    except ClientError as exc:
        status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        http_statuses['unrelated_bucket_denied'] = status
        assert status == 403
        checks['unrelated_bucket_denied'] = True
    else:
        raise AssertionError('Restricted identity accessed unrelated bucket')

    iam = boto3.client(
        'iam', endpoint_url='http://wp612-s3-lp-store:8333',
        aws_access_key_id=credentials['app']['accessKey'],
        aws_secret_access_key=credentials['app']['secretKey'],
        region_name='us-east-1',
        config=Config(connect_timeout=3, read_timeout=10,
                      retries={'max_attempts': 0}),
    )
    try:
        iam.list_users(MaxItems=1)
    except ClientError as exc:
        status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        http_statuses['iam_enumeration_denied'] = status
        assert status == 403
        checks['iam_enumeration_denied'] = True
    else:
        raise AssertionError('Restricted identity has IAM ListUsers')

    response = s3.list_buckets()
    names = {item['Name'] for item in response['Buckets']}
    assert bucket in names and credentials['unrelated_bucket'] not in names
    checks['bucket_visibility_scoped'] = True

except Exception as exc:
    error_type = type(exc).__name__

passed = error_type is None and all(checks.get(name, False) for name in expected)
result = {
    'assessment': 'WP-6.1.2-Section-24.8',
    'sandbox_only': True,
    'production_database_used': False,
    'metadata_adapter': 'in-memory-stub',
    'onyx_s3_file_store_actual_code': True,
    'read_only': False,
    'checks_expected': len(expected),
    'checks_passed': sum(bool(checks.get(name)) for name in expected),
    'checks': checks,
    'http_statuses': http_statuses,
    'synthetic_key_sha256': hashlib.sha256(key.encode()).hexdigest(),
    'synthetic_object_deletion_verified': bool(checks.get('synthetic_object_deleted')),
    'error_type': error_type,
    'overall': 'PASS' if passed else 'REVIEW_REQUIRED',
    'limitations': [
        'Real Onyx S3BackedFileStore implementation and factory used',
        'Database metadata calls replaced with an in-memory adapter',
        'No live PostgreSQL or HTTP upload endpoint was exercised',
        'Original Onyx runtime credentials and storage remain unchanged',
    ],
}
print(json.dumps(result, sort_keys=True))
sys.exit(0 if passed else 2)
'''


def docker(*args, check=True):
    proc = subprocess.run(['docker', *args], capture_output=True, text=True)
    if check and proc.returncode:
        raise RuntimeError('Docker inspection or execution failed')
    return proc


def ensure_isolated_environment():
    if not ROOT.is_dir() or not EVIDENCE_247.is_file() or not COMPOSE.is_file():
        raise RuntimeError('Required workpaper, evidence or Compose path missing')
    if CREDS.is_symlink() or not CREDS.is_file() or CREDS.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('Private synthetic credentials missing or unsafe')
    cred = json.loads(CREDS.read_text())
    assert cred['bucket'] == 'onyx-file-store-bucket'
    assert str(cred['unrelated_bucket']).startswith('wp612-unrelated-')
    assert cred['app']['accessKey'] != cred['admin']['accessKey']
    assert cred['app']['secretKey'] != cred['admin']['secretKey']
    assert docker('network', 'inspect', '-f', '{{.Internal}}', NETWORK).stdout.strip() == 'true'
    store = json.loads(docker('inspect', STORE).stdout)[0]
    assert store['State']['Running'] is True
    assert store['State']['Health']['Status'] == 'healthy'
    assert set(store['NetworkSettings']['Networks']) == {NETWORK}
    assert not any((store['HostConfig'].get('PortBindings') or {}).values())
    assert any(m['Type'] == 'volume' and m['Name'] == 'wp612-s3-lp-data' for m in store['Mounts'])
    assert any(m['Type'] == 'bind' and m['Destination'] == '/tmp/wp612-s3-config.json'
               and m['RW'] is False for m in store['Mounts'])
    ids = docker('compose', '-f', str(COMPOSE), '--project-directory',
                 str(COMPOSE.parent), 'ps', '-q', 'api_server').stdout.strip().splitlines()
    assert len(ids) == 1 and ids[0], 'Original API container not found'
    api = json.loads(docker('inspect', ids[0]).stdout)[0]
    assert NETWORK not in api['NetworkSettings']['Networks'], 'Original API connected to sandbox!'
    image = api['Config']['Image']
    docker('image', 'inspect', image)
    return image


def run_sandbox_runner(image, source, journal_path):
    args = [
        'run', '--rm', '-i', '--pull', 'never', '--network', NETWORK,
        '--user', f'{os.getuid()}:{os.getgid()}', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
        '--env', 'PYTHONDONTWRITEBYTECODE=1',
        '--mount', f'type=bind,src={CREDS},dst=/run/wp612-creds.json,readonly',
        '--mount', f'type=bind,src={journal_path},dst=/run/wp612-journal.json,readonly',
        '--entrypoint', 'python', image, '-c', source,
    ]
    proc = docker(*args, check=False)
    candidates = [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith('{')]
    if len(candidates) != 1:
        raise RuntimeError('Runner returned no unique JSON result; review journal before any action')
    try:
        data = json.loads(candidates[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError('Invalid runner JSON; recovery journal retained') from exc
    return proc.returncode, data


def assert_private_journal(path, assessment):
    assert path.is_file() and not path.is_symlink()
    assert path.stat().st_mode & 0o777 == 0o600
    data = json.loads(path.read_text())
    assert data['assessment'] == assessment
    assert data['bucket'] == 'onyx-file-store-bucket'
    return data


def write_evidence(path, data):
    fd = os.open(str(path), os.O_EXCL | os.O_CREAT | os.O_WRONLY, 0o644)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')


def verify_247():
    image = ensure_isolated_environment()
    if VERIFIED_247.exists() or VERIFIED_247.is_symlink():
        raise RuntimeError('Step 24.7 independent evidence exists; do not rerun')
    journal = assert_private_journal(JOURNAL_247, 'WP-6.1.2-Section-24.7')
    data = json.loads(EVIDENCE_247.read_text())
    assert data['overall'] == 'PASS' and data['checks_passed'] == data['checks_expected'] == 7
    assert data['synthetic_object_deletion_verified'] is True
    assert data['synthetic_key_sha256'] == hashlib.sha256(journal['key'].encode()).hexdigest()
    code, result = run_sandbox_runner(image, CHECK_RUNNER, JOURNAL_247)
    assert code == 0 and result['overall'] == 'PASS', 'REVIEW: 24.7 object not independently absent'
    assert result['synthetic_key_sha256'] == data['synthetic_key_sha256']
    result['checked_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    result['prior_evidence_sha256'] = hashlib.sha256(EVIDENCE_247.read_bytes()).hexdigest()
    result['recovery_journal_resolved'] = True
    write_evidence(VERIFIED_247, result)
    JOURNAL_247.unlink()
    print('PASS: Step 24.7 synthetic object independently absent (HEAD 404)')
    print('PASS: Step 24.7 recovery journal resolved')
    print('Evidence:', VERIFIED_247.relative_to(ROOT))


def run_248():
    image = ensure_isolated_environment()
    if JOURNAL_247.exists() or JOURNAL_247.is_symlink() or not VERIFIED_247.is_file():
        raise RuntimeError('BLOCKED: Resolve Step 24.7 journal first')
    prior = json.loads(VERIFIED_247.read_text())
    assert prior['overall'] == 'PASS' and prior['recovery_journal_resolved'] is True
    for path in (EVIDENCE_248, JOURNAL_248, VERIFIED_248):
        if path.exists() or path.is_symlink():
            raise RuntimeError('BLOCKED: Previous Section 24.8 artifact exists')
    # Probe source imports before any mutation or recovery journal is created.
    import_check = docker(
        'run', '--rm', '--pull', 'never', '--network', 'none',
        '--user', f'{os.getuid()}:{os.getgid()}', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
        '--env', 'PYTHONDONTWRITEBYTECODE=1',
        '--entrypoint', 'python', image, '-c',
        'from onyx.file_store import file_store as fs; '
        'from onyx.configs.constants import FileOrigin; '
        'assert hasattr(fs, "get_s3_file_store")',
        check=False,
    )
    if import_check.returncode:
        raise RuntimeError('BLOCKED: Isolated API image FileStore import preflight failed')
    print('PASS: Actual Onyx FileStore imports available in isolated test image', flush=True)
    # Generate an object key using Onyx's documented key layout.
    # No production database connection or production S3 mount is attached.
    fid = 'wp612-fi-' + secrets.token_hex(16)
    key = 'wp612-filestore/wp612-sandbox/' + fid
    journal = {
        'assessment': 'WP-6.1.2-Section-24.8',
        'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'bucket': 'onyx-file-store-bucket',
        'file_id': fid,
        'key': key,
        'status': 'unresolved_until_independent_verification',
    }
    fd = os.open(str(JOURNAL_248), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(journal, stream)
        stream.write('\n')
    assert_private_journal(JOURNAL_248, 'WP-6.1.2-Section-24.8')
    print('PASS: Section 24.8 private recovery journal created', flush=True)
    exit_code, data = run_sandbox_runner(image, FILESTORE_RUNNER, JOURNAL_248)
    assert data['assessment'] == 'WP-6.1.2-Section-24.8'
    assert data['synthetic_key_sha256'] == hashlib.sha256(key.encode()).hexdigest()
    data['runner_exit_code'] = exit_code
    data['started_from_verified_step_24_7'] = True
    data['evidence_timestamp_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    data['recovery_journal_retained_for_review'] = True
    write_evidence(EVIDENCE_248, data)
    print('Overall:', data['overall'])
    print('Checks:', data['checks_passed'], '/', data['checks_expected'])
    print('Synthetic object deletion:', data['synthetic_object_deletion_verified'])
    print('Metadata adapter:', data['metadata_adapter'])
    print('Error type:', data['error_type'])
    print('Evidence:', EVIDENCE_248.relative_to(ROOT))
    print('Recovery journal retained until independent verification')
    if data['overall'] != 'PASS' or exit_code != 0:
        raise RuntimeError('REVIEW_REQUIRED: Do not rerun; inspect journal and evidence')


def verify_248():
    image = ensure_isolated_environment()
    if VERIFIED_248.exists() or VERIFIED_248.is_symlink():
        raise RuntimeError('Independent verification already exists; do not rerun')
    journal = assert_private_journal(JOURNAL_248, 'WP-6.1.2-Section-24.8')
    data = json.loads(EVIDENCE_248.read_text())
    assert data['overall'] == 'PASS' and data['runner_exit_code'] == 0
    assert data['checks_passed'] == data['checks_expected'] == 17
    assert all(data['checks'].values())
    assert data['synthetic_object_deletion_verified'] is True
    assert data['synthetic_key_sha256'] == hashlib.sha256(journal['key'].encode()).hexdigest()
    code, result = run_sandbox_runner(image, CHECK_RUNNER, JOURNAL_248)
    assert code == 0 and result['overall'] == 'PASS', 'REVIEW: synthetic object remains'
    assert result['synthetic_key_sha256'] == data['synthetic_key_sha256']
    result['checked_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    result['prior_evidence_sha256'] = hashlib.sha256(EVIDENCE_248.read_bytes()).hexdigest()
    result['recovery_journal_resolved'] = True
    write_evidence(VERIFIED_248, result)
    JOURNAL_248.unlink()
    print('PASS: Step 24.8 synthetic object independently absent (HEAD 404)')
    print('PASS: Step 24.8 recovery journal resolved')
    print('Evidence:', VERIFIED_248.relative_to(ROOT))


def main():
    if len(sys.argv) != 2:
        raise RuntimeError('Usage: python3 SCRIPT verify-24-7 | run-24-8 | verify-24-8')
    mode = sys.argv[1]
    compile(CHECK_RUNNER, '<read-only-cleanup-check>', 'exec')
    compile(FILESTORE_RUNNER, '<isolated-filestore-runner>', 'exec')
    if mode == 'verify-24-7':
        verify_247()
    elif mode == 'run-24-8':
        run_248()
    elif mode == 'verify-24-8':
        verify_248()
    else:
        raise RuntimeError('Invalid mode: ' + mode)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('BLOCKED/REVIEW:', type(exc).__name__, str(exc), file=sys.stderr)
        sys.exit(1)

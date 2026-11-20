#!/usr/bin/env python3
"""WP-6.1.2 Section 24 sandbox teardown. Single-use guarded operation.

Validates four test evidence files, two immutable test-script checksums,
sandbox ownership/isolation, and emptiness of both synthetic buckets before
removing only the named sandbox container, network, volume and private secrets.
Original Onyx resources are never targeted. This is NOT a production fix.

On any error, STOP. Preserve evidence and any recovery marker. Never rerun
without inspecting the exact remaining Docker and file state.
"""
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path('/workspaces/project001/assessments/onyx/6.1.2')
CREDS = Path('/workspaces/.onyx-612-s3-lp-sandbox.json')
CONFIG = Path('/workspaces/.onyx-612-s3-lp-config.json')
JOURNAL = Path('/workspaces/.onyx-612-s3-lp-teardown-recovery.json')
EVIDENCE = ROOT / 'evidence/onyx-s3-sandbox-teardown.json'
NETWORK = 'wp612-s3-lp-net'
CONTAINER = 'wp612-s3-lp-store'
VOLUME = 'wp612-s3-lp-data'
COMPOSE = Path('/workspaces/onyx-runtime-v5.1.3/deployment/docker_compose/docker-compose.yml')
SEAWEED_IMAGE = ('docker.io/chrislusf/seaweedfs:4.47@sha256:'
                 'ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882')
ARTIFACTS = {
    'object_test_script': 'tests/onyx_s3_restricted_object_lifecycle.py',
    'filestore_test_script': 'tests/onyx_s3_restricted_filestore_compatibility.py',
    'object_test_evidence': 'evidence/onyx-s3-restricted-object-lifecycle.json',
    'object_cleanup_evidence': 'evidence/onyx-s3-restricted-object-cleanup-verified.json',
    'filestore_test_evidence': 'evidence/onyx-s3-restricted-filestore-compatibility.json',
    'filestore_cleanup_evidence': 'evidence/onyx-s3-restricted-filestore-cleanup-verified.json',
}
EXPECTED_SCRIPT_SHA256 = {
    'object_test_script': 'c242c31f8971eaacc826e2506ee993e0aa5eeaa5e3b7c4eafc3003cbe55c57f9',
    'filestore_test_script': '741abd163ee99c74d3d5e1d36162a66b7a3c89f5d8904142b1c4b21f31c4826f',
}

# Executed inside a disposable container on the sandbox-only network.
# Prints no names, credentials, object keys or object bodies.
PREFLIGHT_RUNNER = r'''
import json
from pathlib import Path
import boto3
from botocore.config import Config

c = json.loads(Path('/run/wp612-creds.json').read_text())
assert c['bucket'] == 'onyx-file-store-bucket'
assert c['unrelated_bucket'].startswith('wp612-unrelated-')
assert c['unrelated_bucket'] != c['bucket']
assert c['admin']['accessKey'] != c['app']['accessKey']
assert c['admin']['secretKey'] != c['app']['secretKey']
client = boto3.client('s3', endpoint_url='http://wp612-s3-lp-store:8333',
    aws_access_key_id=c['admin']['accessKey'],
    aws_secret_access_key=c['admin']['secretKey'], region_name='us-east-1',
    config=Config(signature_version='s3v4', s3={'addressing_style':'path'},
        connect_timeout=3, read_timeout=10, retries={'max_attempts':0}))
expected = {c['bucket'], c['unrelated_bucket']}
listed = {e['Name'] for e in client.list_buckets()['Buckets']}
assert listed == expected, 'Unexpected bucket inventory: teardown blocked'
for bucket in expected:
    r = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
    assert not r.get('Contents') and not r.get('IsTruncated'), 'Nonempty bucket: teardown blocked'
print(json.dumps({'exactly_two_expected_buckets':True,
                  'both_synthetic_buckets_empty':True,
                  'read_only':True, 'sandbox_only':True}, sort_keys=True))
'''


def command(*args, check=True):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and result.returncode:
        raise RuntimeError(f'Command failed: {args[0]} {args[1] if len(args)>1 else ""}')
    return result


def docker(*args, check=True):
    return command('docker', *args, check=check)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_private(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('Required private file missing, symlinked, or not 0600')
    return json.loads(path.read_text())


def validate_evidence(credentials):
    files = {}
    for name, relative in ARTIFACTS.items():
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError('Required evidence or test script missing')
        files[name] = path
    for name, digest in EXPECTED_SCRIPT_SHA256.items():
        if sha(files[name]) != digest:
            raise RuntimeError('Test script checksum mismatch')
        compile(files[name].read_text(), str(files[name]), 'exec')
    obj = json.loads(files['object_test_evidence'].read_text())
    obj_cleanup = json.loads(files['object_cleanup_evidence'].read_text())
    fs = json.loads(files['filestore_test_evidence'].read_text())
    fs_cleanup = json.loads(files['filestore_cleanup_evidence'].read_text())
    for data, expected in ((obj, 7), (fs, 17)):
        assert data['overall'] == 'PASS'
        assert data['checks_passed'] == data['checks_expected'] == expected
        assert len(data['checks']) == expected and all(data['checks'].values())
        assert data['synthetic_object_deletion_verified'] is True
        assert data['error_type'] is None and data['runner_exit_code'] == 0
    assert fs['metadata_adapter'] == 'in-memory-stub'
    for prior, independent, prior_path in (
        (obj, obj_cleanup, files['object_test_evidence']),
        (fs, fs_cleanup, files['filestore_test_evidence']),
    ):
        assert independent['overall'] == 'PASS'
        assert independent['read_only'] is True
        assert independent['head_status'] == 404
        assert independent['object_absent'] is True
        assert independent['recovery_journal_resolved'] is True
        assert independent['prior_evidence_sha256'] == sha(prior_path)
        assert independent['synthetic_key_sha256'] == prior['synthetic_key_sha256']
    for j in (
        '/workspaces/.onyx-612-s3-lp-object-recovery.json',
        '/workspaces/.onyx-612-s3-lp-filestore-recovery.json',
    ):
        p = Path(j)
        assert not p.exists() and not p.is_symlink(), 'Unresolved prior recovery journal'
    for path in files.values():
        raw = path.read_bytes()
        for key in ('admin', 'app'):
            assert credentials[key]['accessKey'].encode() not in raw
            assert credentials[key]['secretKey'].encode() not in raw
    return files


def validate_sandbox(credentials, config):
    assert COMPOSE.is_file()
    assert len(config['identities']) == 2
    admin, app = config['identities']
    assert admin['credentials'] == [credentials['admin']]
    assert app['credentials'] == [credentials['app']]
    assert 'Admin' in admin['actions']
    assert app['actions'] == [
        'Read:' + credentials['bucket'],
        'Write:' + credentials['bucket'],
        'List:' + credentials['bucket'],
    ]
    net = json.loads(docker('network', 'inspect', NETWORK).stdout)[0]
    assert net['Internal'] is True
    attached = {x['Name'] for x in (net.get('Containers') or {}).values()}
    assert attached == {CONTAINER}, 'Unknown container on sandbox network'
    store = json.loads(docker('inspect', CONTAINER).stdout)[0]
    assert store['Config']['Image'] == SEAWEED_IMAGE
    assert store['State']['Running'] is True
    assert store['State']['Health']['Status'] == 'healthy'
    assert set(store['NetworkSettings']['Networks']) == {NETWORK}
    assert not any((store['HostConfig'].get('PortBindings') or {}).values())
    assert any(m['Type'] == 'volume' and m['Name'] == VOLUME and m['Destination'] == '/data'
               for m in store['Mounts'])
    assert any(m['Type'] == 'bind' and m['Destination'] == '/tmp/wp612-s3-config.json'
               and m['Source'] == str(CONFIG) and m['RW'] is False for m in store['Mounts'])
    vol = json.loads(docker('volume', 'inspect', VOLUME).stdout)[0]
    assert vol['Name'] == VOLUME
    use = docker('ps', '-a', '--filter', 'volume='+VOLUME, '--format', '{{.Names}}').stdout.splitlines()
    assert set(use) == {CONTAINER}, 'Unexpected volume user'
    active = docker('compose', '-f', str(COMPOSE), '--project-directory', str(COMPOSE.parent),
                    'ps', '-q', 'api_server').stdout.strip().splitlines()
    assert len(active) == 1 and active[0], 'Original API not available'
    api = json.loads(docker('inspect', active[0]).stdout)[0]
    assert NETWORK not in api['NetworkSettings']['Networks'], 'Original API on sandbox network'
    image = api['Config']['Image']
    docker('image', 'inspect', image)
    return image


def verify_empty_buckets(api_image):
    runner = docker('run', '--rm', '-i', '--pull', 'never', '--network', NETWORK,
                    '--user', f'{os.getuid()}:{os.getgid()}', '--read-only',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                    '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                    '--env', 'PYTHONDONTWRITEBYTECODE=1',
                    '--mount', f'type=bind,src={CREDS},dst=/run/wp612-creds.json,readonly',
                    '--entrypoint', 'python', api_image, '-c', PREFLIGHT_RUNNER)
    lines = runner.stdout.strip().splitlines()
    assert len(lines) == 1, 'Unexpected bucket-preflight output'
    result = json.loads(lines[0])
    assert result == {'exactly_two_expected_buckets':True,
                      'both_synthetic_buckets_empty':True,
                      'read_only':True, 'sandbox_only':True}
    print('PASS: Exactly the two intended sandbox buckets exist', flush=True)
    print('PASS: Both buckets empty (S3 object listing)', flush=True)


def write_journal():
    record = {'assessment': 'WP-6.1.2-Section-24-teardown',
              'started_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'resources': {'container': CONTAINER, 'network': NETWORK, 'volume': VOLUME},
              'status': 'unresolved_until_resource_cleanup_verified'}
    fd = os.open(str(JOURNAL), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(record, output, sort_keys=True)
        output.write('\n')


def main():
    if not ROOT.is_dir() or not EVIDENCE.parent.is_dir():
        raise RuntimeError('Assessment root unavailable')
    if EVIDENCE.exists() or EVIDENCE.is_symlink() or JOURNAL.exists() or JOURNAL.is_symlink():
        raise RuntimeError('Teardown evidence or journal already exists: do not rerun')
    start = dt.datetime.now(dt.timezone.utc).isoformat()
    credentials = read_private(CREDS)
    config = read_private(CONFIG)
    assert credentials['bucket'] == 'onyx-file-store-bucket'
    assert credentials['unrelated_bucket'].startswith('wp612-unrelated-')
    assert credentials['app']['accessKey'] != credentials['admin']['accessKey']
    assert credentials['app']['secretKey'] != credentials['admin']['secretKey']
    validate_evidence(credentials)
    print('PASS: 7/7 and 17/17 runtime evidence, both independent cleanup records', flush=True)
    image = validate_sandbox(credentials, config)
    print('PASS: Isolated container, network, volume, and credentials validated', flush=True)
    verify_empty_buckets(image)
    print('PASS: Pre-teardown review complete; original Onyx unaffected', flush=True)
    write_journal()
    print('PASS: Private teardown recovery journal created', flush=True)
    docker('stop', '--time', '20', CONTAINER)
    assert docker('inspect', '-f', '{{.State.Running}}', CONTAINER).stdout.strip() == 'false'
    print('PASS: Sandbox object-store stopped', flush=True)
    docker('rm', CONTAINER)
    assert docker('container', 'inspect', CONTAINER, check=False).returncode != 0
    print('PASS: Sandbox object-store removed', flush=True)
    docker('network', 'rm', NETWORK)
    assert docker('network', 'inspect', NETWORK, check=False).returncode != 0
    print('PASS: Dedicated internal network removed', flush=True)
    docker('volume', 'rm', VOLUME)
    assert docker('volume', 'inspect', VOLUME, check=False).returncode != 0
    print('PASS: Dedicated sandbox volume removed', flush=True)
    for path in (CONFIG, CREDS):
        path.unlink()
        assert not path.exists()
    print('PASS: Both private sandbox credential/configuration files unlinked', flush=True)
    data = {
        'assessment': 'WP-6.1.2-Section-24-sandbox-teardown',
        'started_utc': start,
        'completed_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'overall': 'PASS',
        'read_only': False,
        'evidence_validated': True,
        'object_lifecycle_checks_passed': 7,
        'filestore_compatibility_checks_passed': 17,
        'both_independent_object_deletion_checks_passed': True,
        'both_buckets_empty_before_teardown': True,
        'only_expected_sandbox_buckets_present': True,
        'sandbox_container_absent': True,
        'sandbox_network_absent': True,
        'sandbox_volume_absent': True,
        'private_credentials_files_absent': True,
        'prior_recovery_journals_absent': True,
        'teardown_recovery_journal_resolved': True,
        'production_onyx_remediation_performed': False,
        'limitations': [
            'Original Onyx API still has administrative storage credentials',
            'FileStore compatibility used an in-memory metadata adapter',
            'Bucket listing verified absence of visible objects, not forensic erasure',
            'Removing files is not cryptographic secret destruction',
            'No original Onyx runtime or PostgreSQL integration retest performed',
        ],
    }
    fd = os.open(str(EVIDENCE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, 'w') as output:
        json.dump(data, output, indent=2, sort_keys=True)
        output.write('\n')
    JOURNAL.unlink()
    assert not JOURNAL.exists()
    print('PASS: Sanitized teardown evidence stored', flush=True)
    print('PASS: Recovery journal resolved', flush=True)
    print('Evidence:', EVIDENCE.relative_to(ROOT), flush=True)
    print('RESULT: SECTION 24 SANDBOX TEARDOWN PASS', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('BLOCKED/REVIEW:', type(exc).__name__, str(exc), file=sys.stderr)
        sys.exit(1)

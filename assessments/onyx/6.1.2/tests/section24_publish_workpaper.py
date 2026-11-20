#!/usr/bin/env python3
"""Validate Section 24 evidence and append an auditable closure section once."""
from pathlib import Path
from hashlib import sha256
import json

root = Path('/workspaces/project001/assessments/onyx/6.1.2')
report = root / 'WP-6.1.2-Onyx-Architecture-Assessment-2026-11-20.md'
files = {
    'Restricted object lifecycle test': 'tests/onyx_s3_restricted_object_lifecycle.py',
    'Restricted FileStore test': 'tests/onyx_s3_restricted_filestore_compatibility.py',
    'Sandbox teardown test': 'tests/onyx_s3_sandbox_teardown.py',
    'Section 24 report validator': 'tests/section24_publish_workpaper.py',
    'Restricted object lifecycle evidence': 'evidence/onyx-s3-restricted-object-lifecycle.json',
    'Object cleanup verification': 'evidence/onyx-s3-restricted-object-cleanup-verified.json',
    'FileStore compatibility evidence': 'evidence/onyx-s3-restricted-filestore-compatibility.json',
    'FileStore cleanup verification': 'evidence/onyx-s3-restricted-filestore-cleanup-verified.json',
    'Sandbox teardown evidence': 'evidence/onyx-s3-sandbox-teardown.json',
}
def digest(path):
    return sha256(path.read_bytes()).hexdigest()
def load(relative):
    return json.loads((root / relative).read_text(encoding='utf-8'))
assert Path(__file__).resolve() == (root / 'tests/section24_publish_workpaper.py').resolve(), 'Run only from designated tests path'
assert report.is_file(), 'Missing original workpaper'
original = report.read_text(encoding='utf-8')
assert '## 23.' in original and 'ONYX-S3-001' in original, 'Unexpected workpaper state'
assert '## 24.' not in original, 'Section 24 already exists; do not append again'
for relative in files.values():
    assert (root / relative).is_file(), f'Missing artifact: {relative}'
expected_sha = {
    'tests/onyx_s3_restricted_object_lifecycle.py': 'c242c31f8971eaacc826e2506ee993e0aa5eeaa5e3b7c4eafc3003cbe55c57f9',
    'tests/onyx_s3_restricted_filestore_compatibility.py': '741abd163ee99c74d3d5e1d36162a66b7a3c89f5d8904142b1c4b21f31c4826f',
    'tests/onyx_s3_sandbox_teardown.py': '64fd3440b740438956349e4e741eb8c8751d2b53603104131a3fc1092876c48e',
}
for relative, expected in expected_sha.items():
    assert digest(root / relative) == expected, f'Script checksum mismatch: {relative}'
obj = load('evidence/onyx-s3-restricted-object-lifecycle.json')
fs = load('evidence/onyx-s3-restricted-filestore-compatibility.json')
obj_cleanup = load('evidence/onyx-s3-restricted-object-cleanup-verified.json')
fs_cleanup = load('evidence/onyx-s3-restricted-filestore-cleanup-verified.json')
teardown = load('evidence/onyx-s3-sandbox-teardown.json')
for result, count in ((obj, 7), (fs, 17)):
    assert result['overall'] == 'PASS'
    assert result['checks_passed'] == result['checks_expected'] == count
    assert len(result['checks']) == count and all(result['checks'].values())
    assert result['synthetic_object_deletion_verified'] is True
    assert result['runner_exit_code'] == 0 and result['error_type'] is None
for check, prior, relative in (
    (obj_cleanup, obj, 'evidence/onyx-s3-restricted-object-lifecycle.json'),
    (fs_cleanup, fs, 'evidence/onyx-s3-restricted-filestore-compatibility.json'),
):
    assert check['overall'] == 'PASS' and check['read_only'] is True
    assert check['head_status'] == 404 and check['object_absent'] is True
    assert check['recovery_journal_resolved'] is True
    assert check['prior_evidence_sha256'] == digest(root / relative)
    assert check['synthetic_key_sha256'] == prior['synthetic_key_sha256']
assert fs['metadata_adapter'] == 'in-memory-stub'
assert fs['production_database_used'] is False
for key, value in {
    'overall': 'PASS',
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
}.items():
    assert teardown[key] == value, f'Teardown evidence mismatch: {key}'
for name in ('object', 'filestore', 'teardown'):
    assert not Path(f'/workspaces/.onyx-612-s3-lp-{name}-recovery.json').exists(), f'Unresolved {name} journal'
for relative in ('.onyx-612-s3-lp-sandbox.json', '.onyx-612-s3-lp-config.json'):
    assert not (Path('/workspaces') / relative).exists(), f'Private file still present: {relative}'
rows = '\n'.join(f'| `{relative}` | `{digest(root / relative)}` |' for relative in files.values())
section = f'''\n\n## 24. Restricted S3 workload identity — isolated remediation feasibility assessment\n\n**Assessment:** Onyx v5.1.3 / SeaweedFS 4.47; finding `ONYX-S3-001`.\n\n**Disposition:** Sandbox control validation **PASS**; original Onyx deployment **NOT REMEDIATED**.\nThe Section 23 administrative credential-reuse finding remains **OPEN**.\n\n### Purpose and isolation\n\n- Created separate SeaweedFS 4.47 instance on a dedicated internal Docker network, without published host ports, and a dedicated volume.\n- Generated private, independent administrator and application S3 credentials, with the application identity scoped to `Read`, `Write`, and `List` on `onyx-file-store-bucket`; no application `Admin` action.\n- Provisioned the application bucket and an unrelated synthetic bucket using the sandbox administrator.\n- Tested the isolated storage endpoint only. The original API workload, its administrator storage credentials, and original database were not migrated.\n\n### Observations\n\n| Control or workflow | Verified observation |\n|---|---|\n| Designated-bucket HEAD | HTTP 200 with restricted identity |\n| Unrelated-bucket HEAD and LIST | HTTP 403 |\n| S3 `ListBuckets` | Only authorized bucket visible |\n| IAM `ListUsers` | HTTP 403 |\n| Restricted S3 object create/head/read/digest/delete/absence | **7/7 PASS** |\n| Actual Onyx `S3BackedFileStore` initialize/save/read/size/delete | **17/17 PASS** in isolated test runner |\n| FileStore metadata | **In-memory stub**, not PostgreSQL |\n| Post-test object absence | Two independent read-only HEAD checks, each HTTP 404 |\n| Sandbox teardown | PASS; both buckets empty; dedicated container/network/volume removed; private sandbox config and credential files unlinked |\n\n### Interpretation and limits\n\nThe tested Onyx FileStore backend is compatible with a bucket-scoped S3 identity for the selected operations.\nThis is a **restricted-identity feasibility demonstration**, not deployed remediation.\nIt does **not** establish end-to-end HTTP/database compatibility, successful migration and rotation of the original administrative credentials, tenant-level object isolation within the permitted bucket, or full production authorization coverage.\nThe standalone sandbox configuration used an S3 API credential file; persistent secret governance, credential rotation and transport security require separate assessment.\nBucket listing verified absence of visible objects before teardown, not forensic erasure of deleted data.\n\n### Outstanding actions\n\n1. Plan and authorize a separate, reversible migration of the original Onyx API to dedicated restricted S3 credentials; preprovision the bucket.\n2. Retest live Onyx HTTP upload/read/delete paths with actual PostgreSQL metadata, confirm the API has no administrative storage credentials, and verify administrative IAM and cross-bucket requests remain denied.\n3. Validate failure/rollback behavior across S3 and PostgreSQL.\n4. Continue cross-tenant isolation, service identities, internal transport and additional runtime data-flow assessments under their respective scopes.\n5. Keep `ONYX-S3-001` **OPEN** until the original workload is remediated and independently retested.\n\n### Section 24 evidence integrity (SHA-256)\n\n| Artifact | SHA-256 |\n|---|---|\n{rows}\n\n### Source references\n\n- https://github.com/onyx-dot-app/onyx/blob/v5.1.3/backend/onyx/file_store/file_store.py\n- https://github.com/seaweedfs/seaweedfs/blob/4.47/weed/command/s3.go\n- https://github.com/seaweedfs/seaweedfs/blob/4.47/weed/s3api/auth_credentials.go\n'''
report.write_text(original.rstrip('\n') + section, encoding='utf-8')
assert '## 24.' in report.read_text(encoding='utf-8')
print('PASS: 7/7 object checks and 17/17 FileStore checks validated')
print('PASS: Two independent cleanup evidence records validated')
print('PASS: Teardown and recovery evidence validated')
print('PASS: Three trusted test-script checksums validated')
print('PASS: Section 24 appended, with SHA-256 inventory')
print('PASS: ONYX-S3-001 remains explicitly OPEN')
print('RESULT: SECTION 24 REPORT READY FOR REVIEW')

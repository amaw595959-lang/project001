# WP-6.1.1 — Independent source defense and verification addendum

**Date (UTC):** 2026-11-20  
**Target:** `onyx-dot-app/onyx` @ `0135795fdf8f28f2c8bd8b52361a5b7f2172586f`  
**Assessment reference:** `WP-6.1.1-Onyx-Source-Assessment-2026-11-20.md`  
**Evidence:** [GitHub Actions run 44785361831](https://github.com/amaw535353-ai/p001/actions/runs/44785361831), [verification workflow](../../../../.github/workflows/onyx-611-verification.yml)  
**Execution class:** ISOLATED PINNED SOURCE FUNCTIONS / SYNTHETIC FIXTURES / MOCKED DEPENDENCIES — **NOT DEPLOYED APPLICATION RUNTIME**.

## Independently reviewed source conclusions

- Scoped managers can see prospective members outside their managed groups by design. The pinned integration test `test_manager_reads_member_candidates` expects this candidate visibility. Visible membership records are separately redacted, and out-of-scope *group mutation* remains forbidden by the documented authorization design.
- `require_permission(..., allow_scope=True)` handles Gate 1 (permission to reach a handler), not resource-specific Gate 2. Individual route handlers remain responsible for filtering reads and checking writes.
- `token_allows` adds an applicable token-scope cap to the user's effective authority.
- **No vulnerability is claimed.** This assessment is not a product security certification.

Sources: [permissions.py](https://github.com/onyx-dot-app/onyx/blob/0135795fdf8f28f2c8bd8b52361a5b7f2172586f/backend/onyx/auth/permissions.py), [scoped_permissions.py](https://github.com/onyx-dot-app/onyx/blob/0135795fdf8f28f2c8bd8b52361a5b7f2172586f/backend/onyx/auth/scoped_permissions.py), [user-directory route](https://github.com/onyx-dot-app/onyx/blob/0135795fdf8f28f2c8bd8b52361a5b7f2172586f/backend/onyx/server/manage/users.py), [group-manager integration test](https://github.com/onyx-dot-app/onyx/blob/0135795fdf8f28f2c8bd8b52361a5b7f2172586f/backend/tests/integration/tests/permissions_membership/test_group_manager_membership.py#L576-L595).

## CI execution results

**GitHub Actions job:** `pinned-source-execution`  
**Workflow result:** SUCCESS  
**Seven isolated behavioral tests:** 7 passed; 0 failed.

1. Permission implication
2. GLOBAL / SCOPED / NONE authority
3. Gate 1 and full-administrator access protection
4. Request-token restriction
5. Gate 2 unmanaged-group and public-resource denial decisions
6. Candidate-directory visibility and redaction
7. Paginated directory behavior

**How executed:** The CI job checked out the *exact* Onyx commit, compiled selected original Python function bodies using Python's AST, and invoked them with synthetic users and mocked infrastructure dependencies. No Onyx API, database, OAuth server, Enterprise application deployment, live group data, or production configuration was started or accessed.

**Interpretation:** These results support limited function-level source behavior only. They cannot establish full Onyx authorization enforcement, HTTP-layer behavior, real persistence, tenant segregation, connector permissions, security configuration, or production safety. Existing vendor tests were consulted as *policy evidence* but not run by this job.

## Open verification gates

- [ ] Deploy or obtain explicit authorization for an owned Onyx test environment and record edition, configuration and commit/image digest.
- [ ] Establish synthetic Group A / Group B, manager, basic member and administrator fixtures; use dummy data only.
- [ ] Exercise actual authenticated HTTP endpoints with a positive control and a negative scope control.
- [ ] Assert response status **and content**, and for denied writes independently verify persisted group membership remains unchanged.
- [ ] Exercise both paginated and unpaginated `GET /manage/users` with actual scoped-manager sessions.
- [ ] Independently evaluate user explanations of Gate 1, Gate 2, and the distinction between code assertions and runtime evidence.

**Final labels:** SOURCE_REVIEW_DEFENDED_BY_ASSISTANT; ISOLATED_FUNCTION_TESTS_PASSED; LIVE_RUNTIME_NOT_TESTED; USER_INDEPENDENT_DEFENSE_NOT_DEMONSTRATED.

Do not use this addendum as a penetration-test report or production assurance statement.

## Subsequent runtime verification — 2026-11-20

The preceding final labels are historical and describe the original
source-function verification, not the subsequent deployed application tests.

### New runtime evidence
- Target: Onyx v5.1.3
- Runtime source checkout: 3835aa4294198de48d318536081ea09fbde7aea6
- Docker API health: healthy
- Unauthenticated and ordinary-user directory access: denied
- Administrator directory access: allowed
- Unauthorized administrator self-promotion: denied
- Administrator privilege grant and revocation: verified
- Privilege revocation using the unchanged original token: verified
- Final administrator permissions and group memberships: verified in PostgreSQL

### Updated scope status
- Live API authorization verification: PASS for tested scenarios
- Independent database verification: PASS
- Enterprise scoped-manager behavior: NOT RUNTIME VERIFIED
- Cross-tenant authorization: NOT TESTED
- Independent human competency defense: NOT DEMONSTRATED
- No authorization bypass observed in the tested scenarios

### Evidence references
- ONYX-611-Runtime-Authorization-Evidence.md
- ONYX-611-Runtime-Provenance.md

### Limitations
The runtime tests use Onyx v5.1.3, whereas the earlier source
assessment uses another commit. The results are not interchangeable.
The current runtime evidence summary is manually transcribed;
a machine-generated regression evidence package is still pending.

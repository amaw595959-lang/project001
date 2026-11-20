# ONYX-611 — Runtime Authorization Evidence

## Assessment target
- Application: Onyx
- Application release: v5.1.3
- Source commit: 3835aa4294198de48d318536081ea09fbde7aea6
- Environment: Isolated GitHub Codespaces Docker lab
- Authentication: Basic authentication
- Model server: Disabled
- Evidence type: Live HTTP tests and PostgreSQL verification

## Verified test results

| Test | Observed result |
|---|---|
| Unauthenticated GET /manage/users | HTTP 403 |
| Admin GET /manage/users | HTTP 200 |
| Ordinary member GET /manage/users | HTTP 403 |
| Unauthorized self-promotion PATCH | HTTP 403 |
| Member persisted admin permission | Absent |
| Member persisted Admin-group membership | Absent |
| Authorized grant | HTTP 200 |
| Member directory access after grant | HTTP 200 |
| Authorized revocation | HTTP 200 |
| Member directory access after revocation | HTTP 403 |
| Original authentication token after grant | HTTP 200 |
| Original authentication token after revocation | HTTP 403 |
| Original token GET /me after revocation | HTTP 200 |

## Assessment conclusion

No authorization bypass was observed in the tested scenarios.

The application enforced the tested administrator permissions
and reflected privilege revocation for an already authenticated
user without requiring a new authentication token.

## Limitations

- Enterprise scoped group-manager controls were not tested.
- Cross-tenant authorization was not tested.
- Other administrative write endpoints were not exhaustively tested.
- Inference-dependent functionality was not tested.
- The fixed-token test verified authorization revocation,
  not authentication-session termination.
- Results were transcribed from terminal output;
  this document does not contain raw HTTP evidence.

# ONYX-611 — Runtime Deployment Provenance

## Evidence timestamp
2026-11-20T19:17:49Z

## Assessment repository
amaw595959-lang/project001

## Assessment commit
4b83f295b70546bee337b6ad80f5d5cb7bfc19c8

## Application
Onyx v5.1.3

## Runtime source revision
3835aa4294198de48d318536081ea09fbde7aea6

## Container image
onyxdotapp/onyx-backend:v5.1.3

## Docker image ID
sha256:5c50b2c74d9e73a8e70b5113a0a8060e51a3b5463e56cb32b04ca628eee55fcc

## Docker-reported repository digest
onyxdotapp/onyx-backend@sha256:5c50b2c74d9e73a8e70b5113a0a8060e51a3b5463e56cb32b04ca628eee55fcc

## Runtime configuration
- Authentication: basic
- DISABLE_MODEL_SERVER: true
- API health: healthy
- Container restarts: 0
- Backend: PostgreSQL, Redis, OpenSearch, SeaweedFS
- Deployment: GitHub Codespaces / Docker Compose

## Provenance limitations
- Source checkout and deployed image were identified independently.
- Image build provenance against the source commit was not established.
- The earlier source assessment used a different Onyx revision.
- Runtime verification is limited to explicitly tested endpoints.
- Health and restart values are a point-in-time observation.

## Evidence collection
- git rev-parse HEAD
- git -C /workspaces/onyx-runtime-v5.1.3 rev-parse HEAD
- docker inspect onyx-api_server-1
- docker image inspect onyxdotapp/onyx-backend:v5.1.3
- date -u

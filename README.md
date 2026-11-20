# P-001 — Reproducible Cloud-Native AI Security Laboratory and Infrastructure-as-Code Platform

**Synthetic lab / not a real client engagement / no independently verified findings yet.**
**INCOMPLETE / PARTIALLY VERIFIED:** GitHub Codespaces control plane and Calico have been bootstrapped successfully. The complete workload lifecycle and security assessments remain pending. See [validation](docs/validation-status.md).

## Supported-host matrix

| Host | Installer | Actually verified | Full kind lab |
|---|---|---|---|
| GitHub Codespaces, Linux x86_64, 4 CPUs / 16 GB RAM / 32 GB disk | Supported | Tools, unit/HTTP tests, pinned images, kind control plane and Calico | In progress; full lifecycle not yet validated |
| Linux x86_64 with kind-compatible Docker | Target | Not tested on another host | Pending reproduction |
| Ubuntu 24.04 GitHub runner | CI target | Workflow not executed | Manual gated job only |
| macOS Docker Desktop | Not supported by pinned binary installer | NOT TESTED | Requires reviewed host-specific tool pins |
| Windows WSL2 x86_64 | Linux installer target | NOT TESTED | Requires compatible Docker/cgroup setup |
| Native Windows / ARM | Unsupported | NOT TESTED | No compatibility claim |

## Quick start

Supported installer target: **Linux x86_64**, Python 3.12+, curl, Make, working Docker.
The standard profile requires an estimated **4 CPUs, 8 GiB RAM and 20 GiB free disk**.

For the explicitly experimental Codespaces profile, select it with
\`P001_PROFILE=codespaces\`. Its default 10 GiB free-disk preflight threshold is
**provisional**, not a validated minimum for the complete lab. It can be raised
with \`P001_CODESPACES_MIN_FREE_DISK_GIB\` (accepted range 10–20 GiB). A passing
doctor is not proof that the full deployment will fit; monitor host disk and
Docker storage throughout deployment.

\`\`\`bash
cd /workspaces/p001
./scripts/install
P001_PROFILE=codespaces make doctor
P001_PROFILE=codespaces make lab-up
make lab-test
make lab-evidence
make lab-down
make lab-rebuild-verify
\`\`\`

No paid services, AWS account, model API or GPU are required. Default commands never
call real AWS. \`make aws-plan\` intentionally refuses until a separate authorized,
reviewed account-specific configuration exists.

When deployed: tenant-a API \`127.0.0.1:8081\`, tenant-b \`127.0.0.1:8082\`, emulator
\`127.0.0.1:4566\`. They are loopback port-forwards, not public previews.
\`GET /readyz\` returns a simulator health response. Example synthetic request:

\`\`\`bash
curl --fail http://127.0.0.1:8081/infer -H 'Authorization: Bearer p001-fixture-tenant-a' \\
  -H 'Content-Type: application/json' -d '{"prompt":"P001 synthetic"}'
\`\`\`

Expect \`simulator:true\`, \`model:p001-deterministic-v1\`, and a stable digest. Tenant-b's
fixture on tenant-a must yield 403. Fixtures are public test identities, **not secrets**.

Container-only fallback (does not validate Kubernetes): after install,
\`docker build -f services/lab-api/Dockerfile -t p001-workload:local .\`, then
\`python3 tests/integration/container-smoke.py\`. It creates and removes only its named,
labelled containers/internal networks; existing name collisions fail closed.

Troubleshooting: [operations](docs/operations-and-troubleshooting.md),
[architecture](docs/architecture.md), [limitations](docs/aws-parity-and-limitations.md),
[assessment brief](docs/assessment-brief.md), [workbook](docs/learner-workbook.md).

P-007 adds a local AI security control regression framework: [offline runs,
guarded tool adapters and evidence reproduction](p-007/README.md). Its synthetic
test results do not establish P-006 integration or independent human verification.

Engineering review: [inventories](docs/inventories.md),
[traceability matrix](docs/traceability-matrix.md), [command evidence](docs/reproducible-command-transcript.md),
[extension contract](docs/extension-contract.md), [AI/license disclosure](docs/publication-and-ai-disclosure.md).

#!/usr/bin/env python3
import json, os, platform, shutil, subprocess, sys
from pathlib import Path

from resource_policy import disk_threshold_gib

fail = []
profile = os.environ.get("P001_PROFILE", "standard").strip().lower()
print("P-001 local-only doctor; no AWS API calls")
print(f"Execution profile: {profile}")

try:
    minimum_free_disk_gib = disk_threshold_gib(profile)
except ValueError as exc:
    fail.append(str(exc))
    minimum_free_disk_gib = disk_threshold_gib("standard")

for name, args in [
    ("python3", ["--version"]),
    ("docker", ["version", "--format", "{{.Server.Version}}"]),
    ("kind", ["version"]),
    ("kubectl", ["version", "--client=true", "-o", "json"]),
    ("tofu", ["version"]),
]:
    if not shutil.which(name):
        fail.append(name + " missing")
        continue
    result = subprocess.run([name] + args, capture_output=True, text=True)
    print(name, result.stdout.strip()[:350])
    if result.returncode:
        fail.append(name + " unusable: " + result.stderr[:200])

for name in [
    "AWS_PROFILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
]:
    if os.environ.get(name):
        fail.append(name + " is set; launch from a clean shell without real-cloud bindings")

for name in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]:
    if os.environ.get(name) and os.environ[name] != "test":
        fail.append(name + " has a non-fixture value; unset it (value not displayed)")

info = subprocess.run(
    ["docker", "info", "--format", "{{json .}}"],
    capture_output=True,
    text=True,
)
if info.returncode == 0:
    docker_info = json.loads(info.stdout)
    print(
        "Docker resource report: CPUs",
        docker_info.get("NCPU"),
        "memory_GiB",
        docker_info.get("MemTotal", 0) / 2**30,
        "cgroup_driver",
        docker_info.get("CgroupDriver"),
    )
    if docker_info.get("NCPU", 0) < 4 or docker_info.get("MemTotal", 0) < 8 * 2**30:
        fail.append("Docker needs estimated 4 CPUs and 8 GiB RAM")
    if any("rootless" in option for option in docker_info.get("SecurityOptions", [])) and docker_info.get("CgroupDriver") == "none":
        fail.append("BLOCKED: rootless Docker lacks delegated cgroup driver; kind bootstrap is unsupported on this host")

free_disk_gib = shutil.disk_usage(".").free / 2**30
print(
    f"Disk report: free={free_disk_gib:.2f} GiB "
    f"minimum_for_{profile}={minimum_free_disk_gib:.1f} GiB"
)
if free_disk_gib < minimum_free_disk_gib:
    fail.append(
        f"less than {minimum_free_disk_gib:.1f} GiB free disk required by {profile} profile"
    )

if profile == "codespaces":
    print(
        "WARNING: Codespaces profile is experimental. Its 10 GiB preflight "
        "threshold is provisional; full-lab resource suitability is not yet validated."
    )

print("host", platform.system(), platform.machine())
print("ports: 4566 emulator, 8081 tenant-a, 8082 tenant-b; startup checks exclusivity")
print("Rootless Docker/CNI compatibility requires actual bootstrap; available RAM is host-reported.")
print(json.dumps({"status": "FAIL" if fail else "PASS", "issues": fail}))
sys.exit(bool(fail))

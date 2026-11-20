"""Owned-lab Onyx authorization regression tests."""

import datetime
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

accounts = json.load(sys.stdin)
base = "http://127.0.0.1:8080"
member_email = accounts["member"]["email"]
cookie_name = os.getenv("AUTH_COOKIE_NAME") or "fastapiusersauth"

results = []
cleanup_ok = False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(path, method="GET", body=None, opener=None, cookie=None):
    headers = {"Accept": "application/json"}
    data = None

    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    if cookie:
        headers["Cookie"] = cookie

    request = urllib.request.Request(
        base + path, data=data, headers=headers, method=method
    )

    client = opener or urllib.request.build_opener(NoRedirect())

    try:
        with client.open(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def check(name, actual, expected):
    passed = actual == expected

    results.append({
        "test": name,
        "actual": actual,
        "expected": expected,
        "pass": passed,
    })

    print(
        f"{name}: {actual!r} => {'PASS' if passed else 'FAIL'}",
        file=sys.stderr,
    )
    return passed


def login(role):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        NoRedirect(),
    )

    form = urllib.parse.urlencode({
        "username": accounts[role]["email"],
        "password": accounts[role]["password"],
    }).encode()

    request = urllib.request.Request(
        base + "/auth/login",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    with opener.open(request, timeout=20) as response:
        check(role + "_login", response.status, 204)

    return opener, jar


def role_flag(admin):
    query = urllib.parse.urlencode({"q": member_email})
    status, body = fetch("/manage/users?" + query, opener=admin)

    if status != 200:
        raise RuntimeError("Administrator directory lookup failed")

    matches = [
        user for user in json.loads(body)["accepted"]
        if user["email"] == member_email
    ]

    if len(matches) != 1:
        raise RuntimeError("Synthetic member lookup was not unique")

    return matches[0]["is_admin"]


def change_role(admin, value):
    status, _ = fetch(
        "/manage/admin/users/admin-access",
        method="PATCH",
        body={"user_email": member_email, "is_admin": value},
        opener=admin,
    )
    return status


check("health", fetch("/health")[0], 200)
check("readiness", fetch("/health/ready")[0], 200)
check("unauthenticated_directory", fetch("/manage/users")[0], 403)
check("unauthenticated_me", fetch("/me")[0], 403)

admin, _ = login("admin")
_, member_jar = login("member")

auth_cookies = [
    cookie for cookie in member_jar
    if cookie.name == cookie_name
]

if len(auth_cookies) != 1:
    raise RuntimeError("Expected exactly one authentication cookie")

fixed_cookie = (
    auth_cookies[0].name + "=" + auth_cookies[0].value
)


def member_get(path):
    return fetch(path, cookie=fixed_cookie)[0]


check("admin_directory", fetch("/manage/users", opener=admin)[0], 200)
check("member_me", member_get("/me"), 200)
check("member_directory_denied", member_get("/manage/users"), 403)
check("member_initial_role", role_flag(admin), False)

status, _ = fetch(
    "/manage/admin/users/admin-access",
    method="PATCH",
    body={"user_email": member_email, "is_admin": True},
    cookie=fixed_cookie,
)

check("member_self_promotion_denied", status, 403)
check("member_role_unchanged", role_flag(admin), False)

if all(item["pass"] for item in results):
    try:
        check("admin_grant", change_role(admin, True), 200)
        check("member_role_after_grant", role_flag(admin), True)
        check(
            "same_token_access_after_grant",
            member_get("/manage/users"),
            200,
        )
    finally:
        try:
            check("admin_revoke", change_role(admin, False), 200)
            final_role = role_flag(admin)

            check("member_final_role", final_role, False)
            check(
                "same_token_access_after_revoke",
                member_get("/manage/users"),
                403,
            )
            check("same_token_me_after_revoke", member_get("/me"), 200)

            cleanup_ok = final_role is False
        except Exception as error:
            print(
                "CRITICAL: revocation verification failed:",
                type(error).__name__,
                file=sys.stderr,
            )
            check("revocation_verification", "ERROR", "PASS")
else:
    print(
        "Baseline failed; no privilege grant attempted",
        file=sys.stderr,
    )
    cleanup_ok = role_flag(admin) is False

passed = cleanup_ok and all(item["pass"] for item in results)

report = {
    "schema": "onyx-611-authz-regression/v1",
    "timestamp_utc": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "target": "Onyx v5.1.3 / local API container",
    "authentication": "Basic; original member token replayed",
    "tests": results,
    "final_member_admin_absent": cleanup_ok,
    "overall": "PASS" if passed else "FAIL_OR_INCONCLUSIVE",
    "limitations": [
        "PostgreSQL state not independently queried by this script",
        "Enterprise group-manager behavior not tested",
        "Cross-tenant isolation not tested",
    ],
}

print(json.dumps(report, indent=2))

if not cleanup_ok:
    print(
        "CRITICAL: Member revocation not verified. Stop testing.",
        file=sys.stderr,
    )

sys.exit(0 if passed else 1)

"""Non-destructive HTTP preflight for Onyx avatar authorization."""

import datetime
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8080"
SENTINEL = "/persona/-1/avatar"

accounts = json.load(sys.stdin)
checks = {}
observations = {}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, url):
        return None

def new_client():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(
            http.cookiejar.CookieJar()
        ),
        NoRedirect(),
    )

def request(client, path, method="GET", data=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers=headers or {},
    )
    try:
        with client.open(req, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        return code

def record(name, actual, accepted):
    observations[name] = actual
    checks[name] = actual in accepted
    print(
        name, actual,
        "PASS" if checks[name] else "REVIEW",
        file=sys.stderr,
    )

# An unauthenticated client has no authentication cookie.
record(
    "anonymous_avatar_request",
    request(new_client(), SENTINEL),
    {401, 403},
)

for role in ("member", "admin"):
    account = accounts[role]
    client = new_client()

    form = urllib.parse.urlencode({
        "username": account["email"],
        "password": account["password"],
    }).encode()

    login_status = request(
        client,
        "/auth/login",
        method="POST",
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
    )

    record(role + "_login", login_status, {204})

    if login_status != 204:
        continue

    try:
        record(
            role + "_authenticated_me",
            request(client, "/me"),
            {200},
        )

        # Negative-path sentinel, not a real cross-user fixture.
        record(
            role + "_sentinel_avatar",
            request(client, SENTINEL),
            {404},
        )

    finally:
        record(
            role + "_logout",
            request(
                client,
                "/auth/logout",
                method="POST",
                data=b"",
            ),
            {204},
        )

        record(
            role + "_session_invalidated",
            request(client, "/me"),
            {401, 403},
        )

required_checks = {
    "anonymous_avatar_request",
    "member_login",
    "member_authenticated_me",
    "member_sentinel_avatar",
    "member_logout",
    "member_session_invalidated",
    "admin_login",
    "admin_authenticated_me",
    "admin_sentinel_avatar",
    "admin_logout",
    "admin_session_invalidated",
}

passed = (
    set(checks) == required_checks
    and all(checks.values())
)

report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "target": "Onyx v5.1.3 isolated API",
    "test_type": "Live HTTP negative-path preflight",
    "checks": checks,
    "http_statuses": observations,
    "overall": "PASS" if passed else "REVIEW_REQUIRED",
    "limitations": [
        "Sentinel persona ID -1, not a real private persona",
        "Does not prove cross-user or cross-tenant isolation",
        "Does not test successful avatar retrieval",
        "No S3 object read verified",
        "Synthetic login sessions created and logged out",
        "No credentials, cookies or response bodies recorded",
    ],
}

print(json.dumps(report, indent=2))

sys.exit(0 if passed else 1)

"""Verify Onyx API / Redis session lifecycle in an owned lab."""

import datetime
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import redis

accounts = json.load(sys.stdin)
backend = (os.getenv("AUTH_BACKEND") or "redis").lower()

if backend != "redis":
    raise SystemExit("BLOCKED: Runtime auth backend is not Redis")

client = redis.Redis(
    host=os.getenv("REDIS_HOST", "cache"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    password=os.getenv("REDIS_PASSWORD") or None,
    socket_connect_timeout=5,
    socket_timeout=5,
)

base = "http://127.0.0.1:8080"
cookie_name = os.getenv("AUTH_COOKIE_NAME") or "fastapiusersauth"
checks = []
token = None
logout_status = None
post_logout_status = None

def check(name, actual, expected):
    passed = actual == expected
    checks.append({
        "name": name,
        "actual": actual,
        "expected": expected,
        "pass": passed,
    })
    print(
        name, "PASS" if passed else "FAIL",
        file=sys.stderr,
    )

def request(path, method="GET", cookie=None):
    headers = {}
    if cookie:
        headers["Cookie"] = cookie

    req = urllib.request.Request(
        base + path,
        data=b"" if method == "POST" else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code

check("redis_ping", client.ping(), True)

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar)
)

form = urllib.parse.urlencode({
    "username": accounts["member"]["email"],
    "password": accounts["member"]["password"],
}).encode()

login_request = urllib.request.Request(
    base + "/auth/login",
    data=form,
    headers={
        "Content-Type": "application/x-www-form-urlencoded"
    },
    method="POST",
)

with opener.open(login_request, timeout=20) as response:
    check("login_status", response.status, 204)

matches = [c for c in jar if c.name == cookie_name]

if len(matches) != 1:
    raise SystemExit("BLOCKED: Expected one authentication cookie")

token = matches[0].value
fixed_cookie = cookie_name + "=" + token

try:
    # Redis key is constructed in memory and never logged.
    session_key = "fastapi_users_token:" + token

    check(
        "redis_session_record_exists",
        client.exists(session_key),
        1,
    )

    check(
        "redis_session_ttl_positive",
        client.ttl(session_key) > 0,
        True,
    )

    check(
        "same_token_authenticated_me",
        request("/me", cookie=fixed_cookie),
        200,
    )

finally:
    # End only the synthetic session created by this test.
    logout_status = request(
        "/auth/logout",
        method="POST",
        cookie=fixed_cookie,
    )

    post_logout_status = request(
        "/me",
        cookie=fixed_cookie,
    )

    check("logout_status", logout_status, 204)
    check(
        "original_token_denied_after_logout",
        post_logout_status,
        403,
    )

passed = all(item["pass"] for item in checks)

report = {
    "assessment": "WP-6.1.2",
    "timestamp_utc": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "flow": "Onyx API -> Redis session lifecycle",
    "authentication_backend": "redis",
    "tests": checks,
    "overall": "PASS" if passed else "INCONCLUSIVE_OR_FAIL",
    "limitations": [
        "Synthetic session only",
        "No Redis values, session tokens, or passwords recorded",
        "PostgreSQL interactions not traced by this test",
        "No production session configuration assessed",
    ],
}

print(json.dumps(report, indent=2))
sys.exit(0 if passed else 1)

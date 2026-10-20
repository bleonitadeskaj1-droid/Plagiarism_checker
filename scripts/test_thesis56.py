"""Test thesis 56 internal duplicate detection.

Credentials come from the environment: set ADMIN_USERNAME and ADMIN_PASSWORD.
"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
THESIS_ID = 56


def req(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read())


def main():
    login = req(
        "POST",
        "/api/auth/login",
        {
            "username": os.environ.get("ADMIN_USERNAME", "admin"),
            "password": os.environ["ADMIN_PASSWORD"],
        },
    )
    token = login["access_token"]
    print("API OK")

    start = req(
        "POST",
        "/api/analysis/start",
        {
            "thesis_id": THESIS_ID,
            "compare_confidential": False,
            "compare_internal": True,
            "search_web": False,
        },
        token=token,
    )
    rid = start["result_id"]
    print(f"Started analysis result_id={rid}")

    for i in range(120):
        res = req("GET", f"/api/analysis/result/{rid}", token=token)
        st = res.get("status")
        if st in ("completed", "error"):
            print(
                f"DONE thesis {THESIS_ID}: overall={res.get('overall_score')}% "
                f"internal={res.get('internal_score')}% "
                f"matches={len(res.get('matches') or [])} error={res.get('error')}"
            )
            for s in (res.get("internal_sources") or res.get("matches") or [])[:5]:
                sim = s.get("similarity_score") or s.get("similarity")
                print(f"  -> {s.get('source_title', '')[:55]} ({sim}%)")
            return 0 if st == "completed" else 1
        if i % 5 == 0:
            print(f"  waiting... {i*2}s status={st}")
        time.sleep(2)
    print("TIMEOUT")
    return 1


if __name__ == "__main__":
    sys.exit(main())

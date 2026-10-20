"""Quick E2E test — internal plagiarism on latest thesis.

Credentials come from the environment: set ADMIN_USERNAME and ADMIN_PASSWORD.
"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def req(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=90) as resp:
        return json.loads(resp.read())


def main():
    seen = set()
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

    ws = req("GET", "/api/analysis/web-search/status")
    print("Web:", ws.get("message", "")[:100])

    for tid in [56, 55, 54, 53, 52, 29, 28, 20] + list(range(29, 0, -1)):
        if tid in seen:
            continue
        seen.add(tid)
        try:
            start = req(
                "POST",
                "/api/analysis/start",
                {
                    "thesis_id": tid,
                    "compare_confidential": False,
                    "compare_internal": True,
                    "search_web": False,
                },
                token=token,
            )
            rid = start["result_id"]
            for _ in range(90):
                res = req("GET", f"/api/analysis/result/{rid}", token=token)
                st = res.get("status")
                if st in ("completed", "error"):
                    print(
                        f"thesis {tid}: overall={res.get('overall_score')}% "
                        f"internal={res.get('internal_score')}% "
                        f"matches={len(res.get('matches') or [])} status={st}"
                    )
                    for s in (res.get("internal_sources") or [])[:3]:
                        sim = s.get("similarity_score") or s.get("similarity")
                        print(f"  -> {s.get('source_title', '')[:50]} ({sim}%)")
                    return 0 if st == "completed" else 1
                time.sleep(2)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            print(f"thesis {tid} error:", e.code, e.read().decode()[:200])
        except Exception as e:
            print(f"thesis {tid} skip:", e)
    print("No thesis analyzed")
    return 1


if __name__ == "__main__":
    sys.exit(main())

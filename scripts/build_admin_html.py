import json
import re
import pathlib

TRANSCRIPT = pathlib.Path(
    r"C:\Users\PC\.cursor\projects\c-Users-PC-Desktop-Plagiarism-checker\agent-transcripts"
    r"\28f517b3-323b-4e4f-bcdf-93d4b4e99bef\28f517b3-323b-4e4f-bcdf-93d4b4e99bef.jsonl"
)
OUT = pathlib.Path(__file__).resolve().parents[1] / "admin.html"

html = None
best_len = 0
for line in TRANSCRIPT.open(encoding="utf-8"):
    if "<!DOCTYPE html>" not in line or "Admin Panel" not in line:
        continue
    obj = json.loads(line)
    for part in obj.get("message", {}).get("content", []):
        text = part.get("text", "")
        if part.get("type") == "text" and "<!DOCTYPE html>" in text and len(text) > best_len:
            html = text
            best_len = len(text)

if not html:
    raise SystemExit("HTML not found in transcript")

if html.startswith("<user_query>"):
    html = html[len("<user_query>") :]
if html.endswith("</user_query>"):
    html = html[: -len("</user_query>")]

# Hiq tekstin e mbetur pas </html> (p.sh. mesazhi i user_query)
html = re.sub(r"</html>\s*.*$", "</html>", html, flags=re.DOTALL)

html = re.sub(
    r"<script>[\s\S]*?</script>\s*(?=</body>)",
    "",
    html,
    count=1,
)
js_path = OUT.parent / "admin_temat.js"
if js_path.exists():
    js = js_path.read_text(encoding="utf-8")
    html = html.replace("</body>", f"<script>\n{js}\n</script>\n</body>")
else:
    html = html.replace("</body>", '<script src="admin_temat.js"></script>\n</body>')
html = html.replace(
    "onclick=\"window.location.href='index.html'\"",
    'onclick="logout()"',
)

OUT.write_text(html, encoding="utf-8")
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")

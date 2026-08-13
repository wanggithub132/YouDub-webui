import json
import urllib.request

url = "https://api.github.com/repos/wanggithub132/YouDub-webui/actions/runs?per_page=15"
with urllib.request.urlopen(url, timeout=30) as resp:
    data = json.load(resp)

for r in data.get("workflow_runs", []):
    print(
        f"{r['id']} {r['name'][:24]:24} {r['status']:9} "
        f"{r['conclusion'] or '-':11} {r['created_at']} event={r['event']} head={r['head_sha'][:7]}"
    )

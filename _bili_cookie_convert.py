"""Gist B站 cookie 转 Netscape 格式，供 yt-dlp 使用。"""
import json
import os
import sys

import requests

GIST_ID = "d2c0302a1f508529e37c1fe25059ebe8"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bili_cookies.txt")


def main():
    token = os.environ.get("GIT_TOKEN", "")
    if not token:
        sys.exit("缺少 GIT_TOKEN 环境变量")
    r = requests.get(
        f"https://api.github.com/gists/{GIST_ID}",
        headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if r.status_code != 200:
        sys.exit(f"Gist 读取失败：HTTP {r.status_code}")
    data = json.loads(r.json()["files"]["cookie.json"]["content"])
    cookies = (data.get("cookie_info") or {}).get("cookies", [])
    if not cookies:
        sys.exit("cookie.json 里没有 cookie")

    lines = ["# Netscape HTTP Cookie File", "# 由 Gist cookie.json 转换，仅供本次下载使用"]
    for c in cookies:
        domain = c.get("domain") or ".bilibili.com"
        if not domain.startswith("."):
            domain = "." + domain
        path = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = int(c.get("expires") or 0)
        lines.append(f"{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{c['name']}\t{c['value']}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Netscape cookie 已写入：{OUT}（{len(cookies)} 条）")
    print("cookie 名：", ", ".join(c["name"] for c in cookies))


if __name__ == "__main__":
    main()

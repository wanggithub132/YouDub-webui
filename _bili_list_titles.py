"""B 站投稿标题筛选：yt-dlp 列 bvid -> view API 取标题/封面 -> 筛关键词。

用法：.\.venv\Scripts\python.exe _bili_list_titles.py [关键词]
"""
import subprocess
import sys
import time

import requests

MID = 689146730
KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "拳击零基础"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def list_bvids():
    out = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--cookies", "_bili_cookies.txt",
         "--flat-playlist", "--print", "%(id)s",
         f"https://space.bilibili.com/{MID}/video"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if out.returncode != 0:
        sys.exit(f"yt-dlp 列表失败：{out.stderr[-300:]}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip().startswith("BV")]


def get_view(bvid):
    r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                     headers=UA, timeout=10)
    d = r.json()
    if d.get("code") != 0:
        return None
    return d["data"]


def main():
    bvids = list_bvids()
    print(f"yt-dlp 共列出 {len(bvids)} 个投稿，开始取标题...")
    videos = []
    for i, bvid in enumerate(bvids, 1):
        v = get_view(bvid)
        if v:
            videos.append({
                "bvid": bvid, "title": v.get("title", ""), "pic": v.get("pic", ""),
                "duration": v.get("duration", 0),
                "pubdate": v.get("pubdate", 0),
                "stat": (v.get("stat") or {}).get("view", 0),
            })
            print(f"[{i}/{len(bvids)}] {v.get('title', '')[:30]}")
        else:
            print(f"[{i}/{len(bvids)}] {bvid} 获取失败")
        time.sleep(0.3)

    hits = [v for v in videos if KEYWORD in v["title"]]
    print(f"\n===== 标题包含「{KEYWORD}」：{len(hits)}/{len(videos)} =====\n")
    for i, v in enumerate(hits, 1):
        dur = f"{v['duration']//60}:{v['duration']%60:02d}"
        print(f"{i:2}. [{v['bvid']}] {v['title']}（{dur}，播放 {v['stat']}）")
    print(f"\n封面 pic 字段已就绪，下载时直接复用。")


if __name__ == "__main__":
    main()

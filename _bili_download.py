"""批量下载 B 站「拳击零基础」系列视频 + 封面到 workfolder/拳击零基础/。

用法：.venv\Scripts\python.exe _bili_download.py
依赖：yt-dlp + ffmpeg（系统 PATH）+ _bili_cookies.txt（Gist 转换）
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[0]
OUT_DIR = ROOT / "workfolder" / "拳击零基础"
COOKIE_FILE = "_bili_cookies.txt"
MID = 689146730
KEYWORD = "拳击零基础"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def log(msg):
    print(msg, flush=True)


def sanitize(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(". ")


def list_targets():
    """yt-dlp 列 bvid -> view API 取标题/pic -> 筛关键词。"""
    out = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--cookies", COOKIE_FILE,
         "--flat-playlist", "--print", "%(id)s",
         f"https://space.bilibili.com/{MID}/video"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if out.returncode != 0:
        sys.exit(f"yt-dlp 列表失败：{out.stderr[-300:]}")
    bvids = [line.strip() for line in out.stdout.splitlines() if line.strip().startswith("BV")]

    targets = []
    for bvid in bvids:
        d = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                         headers=UA, timeout=10).json()
        if d.get("code") == 0:
            v = d["data"]
            if KEYWORD in v.get("title", ""):
                targets.append({"bvid": bvid, "title": v["title"], "pic": v.get("pic", "")})
        time.sleep(0.3)
    return targets


def download_video(video, folder):
    cmd = [sys.executable, "-m", "yt_dlp",
           "--cookies", COOKIE_FILE,
           "-f", "bv*+ba/b",
           "--merge-output-format", "mp4",
           "--retries", "5", "--fragment-retries", "5",
           "-o", str(folder / "video.%(ext)s"),
           "--no-progress", "--quiet",
           f"https://www.bilibili.com/video/{video['bvid']}"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0:
        return False, r.stderr.strip()[-300:]
    mp4 = folder / "video.mp4"
    if not mp4.is_file():
        return False, "下载后未找到 video.mp4"
    return True, f"{mp4.stat().st_size >> 20} MB"


def download_cover(video, folder):
    if not video["pic"]:
        return "无封面 URL"
    r = requests.get(video["pic"], headers=UA, timeout=30)
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    (folder / "cover.jpg").write_bytes(r.content)
    return "OK"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = list_targets()
    log(f"命中 {len(targets)} 个视频，输出目录：{OUT_DIR}")
    log("=" * 60)

    results = []
    for i, v in enumerate(targets, 1):
        folder = OUT_DIR / f"{i:02d}_{sanitize(v['title'])}"
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / "video.mp4").is_file():
            log(f"[{i}/{len(targets)}] 已存在，跳过：{v['title'][:30]}")
            results.append({"bvid": v["bvid"], "title": v["title"], "dir": str(folder), "ok": True, "skip": True})
            continue
        log(f"[{i}/{len(targets)}] 下载中：{v['title'][:40]}...")
        ok, msg = download_video(v, folder)
        cover = download_cover(v, folder)
        if ok:
            log(f"    [OK] {msg} | 封面 {cover}")
        else:
            log(f"    [FAIL] {msg}")
        results.append({"bvid": v["bvid"], "title": v["title"], "dir": str(folder),
                        "ok": ok, "skip": False, "msg": msg, "cover": cover})
        time.sleep(1)

    ok_count = sum(1 for r in results if r["ok"])
    log("=" * 60)
    log(f"完成：成功 {ok_count}/{len(results)}")
    manifest = OUT_DIR / "_manifest.json"
    manifest.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"清单已保存：{manifest}")


if __name__ == "__main__":
    main()

"""推送下一个未发布的抖音视频：按标题集数从小到大，每次只推一个。

用法：
  .venv/Scripts/python.exe _douyin_push_next.py --dry-run   # 只预览下一个要推的
  .venv/Scripts/python.exe _douyin_push_next.py             # 推下一个（推完记录）

记录文件：workfolder/拳击零基础/_douyin_pushed.json（已推 bvid 列表）
账号：account2（新抖音账号，cookies/douyin_account2.json）
"""
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(r"f:\work\fromgithub\YouDub-webui\workfolder\拳击零基础")
PUSHED_FILE = BASE / "_douyin_pushed.json"
SAU_PY = r"f:\money\dapang\venv\Scripts\python.exe"
SAU_CLI = r"f:\money\dapang\.sau\sau_cli.py"
ACCOUNT = "account2"
TAGS = "拳击,FightCamp,健身,格斗"
DESC = "翻译配音不易，喜欢点个关注"


def series_no(title):
    m = re.search(r"(\d+)\s*集", title)
    if m:
        return int(m.group(1))
    m = re.search(r"系列(\d+)", title)
    return int(m.group(1)) if m else 0


def load_pushed():
    if PUSHED_FILE.exists():
        return json.loads(PUSHED_FILE.read_text(encoding="utf8"))
    return []


def main():
    dry_run = "--dry-run" in sys.argv
    manifest = json.loads((BASE / "_manifest.json").read_text(encoding="utf8"))
    pushed = load_pushed()

    pending = [v for v in manifest if v["bvid"] not in pushed and not v.get("skip")]
    if not pending:
        print(f"全部 {len(manifest)} 个视频已推送完毕，无待推项")
        return
    pending.sort(key=lambda v: series_no(v["title"]))
    v = pending[0]
    video = [str(Path(v["dir"]) / "video.mp4")]
    if not video:
        sys.exit(f"文件不存在: {v['dir']}")

    no = series_no(v["title"])
    print(f"下一个待推：第{no}期 [{v['bvid']}] {v['title']}")
    print(f"文件：{video[0]}")
    print(f"账号：{ACCOUNT}（已推 {len(pushed)}/{len(manifest)}）")
    if dry_run:
        print("[dry-run] 预览模式，未执行上传")
        return

    cmd = [SAU_PY, SAU_CLI, "douyin", "upload-video",
           "--account", ACCOUNT,
           "--file", video[0],
           "--title", v["title"],
           "--desc", DESC,
           "--tags", TAGS,
           "--headed"]
    r = subprocess.run(cmd, cwd=r"f:\money\dapang\.sau")
    if r.returncode == 0:
        pushed.append(v["bvid"])
        PUSHED_FILE.write_text(json.dumps(pushed, ensure_ascii=False, indent=2),
                               encoding="utf8")
        print(f"[已记录] {v['bvid']}，剩余 {len(manifest) - len(pushed)} 个未推")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()

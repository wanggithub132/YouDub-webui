"""投稿：task.json + {vid}.zip -> B 站（复用 submodule/dapang 的原子能力）。

在仓库根目录执行：python .github/script/publish_bilibili.py

前置：sheet_task.py claim 已生成 task.json；{vid}.zip 已就位于 ZIP_DIR。
流程：解压取 video_final.mp4 -> Gist 拉 cookie.json 写 cookies.json ->
      BilibiliUploader 投稿（字段优先级：表格 override > 表格标题/链接 > 环境变量默认）->
      成功 mark_success(bvid) / 失败 mark_failed -> biliup renew 后 cookie 回 Gist。

环境变量：
  GOOGLE_CREDENTIALS / SHEET_ID / SHEET_TAB   同 sheet_task.py（Google 凭证只走 Secrets，不进 Gist）
  GIST_ID / GIT_TOKEN              YouDub 专属 Gist（只存 B站 cookie.json，renew 后回写）
  ZIP_DIR                          产物 zip 所在目录（默认 out）
  BILI_TID / BILI_TAGS             表格未提供 tid/标签 时的兜底默认值
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "submodule" / "dapang"))

from bili_uploader import BilibiliUploader, default_biliup_path  # noqa: E402
from gist_store import GistStore                                  # noqa: E402
from gsheet_source import GoogleSheetSource                       # noqa: E402

TASK_FILE = ROOT / "task.json"
MARK_FLAG = ROOT / "task_marked.flag"
EXTRACT_DIR = ROOT / "_publish_tmp"
COOKIE_FILE = "cookie.json"   # Gist 里的文件名（biliup 本地约定名是 cookies.json）


def log(msg):
    print(msg, flush=True)


def extract_final_video(zip_dir, vid):
    """解压 {vid}.zip，取 media/video_final.mp4（兜底递归找）。"""
    zip_path = Path(zip_dir) / f"{vid}.zip"
    if not zip_path.is_file():
        raise FileNotFoundError(f"产物不存在：{zip_path}")
    dest = EXTRACT_DIR / vid
    shutil.rmtree(dest, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    for p in [dest / "media" / "video_final.mp4", *dest.rglob("video_final.mp4")]:
        if p.is_file():
            log(f"解压完成：{p}（{p.stat().st_size >> 20} MB）")
            return p
    raise FileNotFoundError(f"{zip_path} 内没有 video_final.mp4")


def fetch_cover(vid):
    """从 metadata/ytdlp_info.json 读封面 URL 并下载（转码交给 dapang upload_cover 内部处理）。

    任一环节失败仅告警并返回 None——封面是锦上添花，绝不阻塞投稿。
    封面文件放在 EXTRACT_DIR/vid/ 下，finally 的 rmtree 会自动清理。
    """
    info_file = EXTRACT_DIR / vid / "metadata" / "ytdlp_info.json"
    if not info_file.is_file():
        log("[WARN] 无 ytdlp_info.json，跳过封面")
        return None
    try:
        info = json.loads(info_file.read_text(encoding="utf8"))
    except (OSError, ValueError):
        log("[WARN] ytdlp_info.json 解析失败，跳过封面")
        return None
    thumb = str(info.get("thumbnail") or "")
    if not thumb:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            thumb = str((thumbs[-1] or {}).get("url") or "")
    if not thumb:
        log("[WARN] 无封面 URL，跳过封面")
        return None
    cover = EXTRACT_DIR / vid / "cover.jpg"
    try:
        resp = requests.get(
            thumb, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"})
        resp.raise_for_status()
        cover.write_bytes(resp.content)
        log(f"封面已下载：{thumb}（{len(resp.content) >> 10} KB）")
        return cover
    except Exception as exc:
        log(f"[WARN] 封面下载失败（{exc}），跳过封面")
        return None


def renew_and_sync_cookie(store):
    """biliup renew 刷新 B站 cookie 并回写 Gist（YouDub 专属 cookie，不碰 dapang 的）。"""
    log("开始刷新 B站 cookies")
    result = subprocess.run([default_biliup_path(), "renew"], capture_output=True)
    if result.returncode != 0:
        log(f"[WARN] biliup renew 失败(code={result.returncode})，跳过 cookie 回写")
        return
    with open("cookies.json", encoding="utf8") as f:
        store.update(COOKIE_FILE, json.loads(f.read()))
    log("B站 cookies 已同步回 Gist")


def main():
    task = json.loads(TASK_FILE.read_text(encoding="utf8"))
    vid = task["vid"]
    ov = task.get("override", {})
    zip_dir = os.environ.get("ZIP_DIR", "out")

    store = GistStore(os.environ["GIST_ID"], os.environ["GIT_TOKEN"], log=log)
    tab = os.environ.get("SHEET_TAB", "").strip()
    if not tab:
        raise SystemExit("SHEET_TAB 未配置：必须与认领时同一工作表，否则回写会写错 tab")
    sheet = GoogleSheetSource(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]), os.environ["SHEET_ID"],
        worksheet=int(tab) if tab.isdigit() else tab, log=log)

    try:
        video = extract_final_video(zip_dir, vid)

        cookie = store.get_json(COOKIE_FILE)
        if cookie is None:
            raise SystemExit(f"Gist 缺少 {COOKIE_FILE}（先本地 biliup login 后上传）")
        Path("cookies.json").write_text(json.dumps(cookie), encoding="utf8")

        tid = ov.get("tid") or os.environ.get("BILI_TID", "")
        if not tid:
            raise SystemExit("tid 缺失：表格「分区」列为空且未配 BILI_TID 变量")
        cover = fetch_cover(vid)
        ret = BilibiliUploader(log=log).upload(
            str(video),
            title=ov.get("title") or task["title"],
            tid=tid,
            tags=ov.get("tags") or os.environ.get("BILI_TAGS", "YouDub"),
            source=ov.get("source") or task["video_url"],
            copyright=ov.get("copyright"),
            desc=ov.get("desc"),
            dtime=ov.get("dtime"),
            cover=str(cover) if cover else None,
        )
        if ret.get("code") != 0:
            raise RuntimeError(f"B站返回异常：{ret}")
        bvid = (ret.get("data") or {}).get("bvid", "")
        log(f"投稿成功 bvid={bvid}")
        sheet.mark_success(task["row"], task["status_col"], bvid=bvid)
        MARK_FLAG.write_text("success", encoding="utf8")
        renew_and_sync_cookie(store)
        return 0
    except Exception as exc:
        log(f"[ERROR] 投稿失败: {exc}")
        sheet.mark_failed(task["row"], task["status_col"])
        MARK_FLAG.write_text("failed", encoding="utf8")
        return 1
    finally:
        Path("cookies.json").unlink(missing_ok=True)
        shutil.rmtree(EXTRACT_DIR, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

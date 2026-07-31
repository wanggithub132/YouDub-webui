"""Google 表格单条任务认领/回写（复用 submodule/dapang 的原子能力）。

用法（在仓库根目录执行）：
  python .github/script/sheet_task.py claim        # 认领一条待处理行 -> task.json + GITHUB_OUTPUT
  python .github/script/sheet_task.py mark-failed  # 兜底：流程失败时把已认领行标记「失败」

环境变量：
  GOOGLE_CREDENTIALS   Google 服务账号 JSON 全文（放 GitHub Secrets，绝不进 Gist——
                       Gist 会被 Google 扫描导致私钥被自动撤销）
  SHEET_ID             Google 表格 ID
  SHEET_TAB            工作表名（YouDub 专属 tab，如「拳击零基础」；必填，避免与 dapang 抢行）
  INPUT_VIDEO_URL      可选。手动单条调试：跳过表格认领，直接输出该链接（不写表格）

GITHUB_OUTPUT 输出：has_video / claimed / video_url / vid
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "submodule" / "dapang"))

from gsheet_source import GoogleSheetSource  # noqa: E402

TASK_FILE = ROOT / "task.json"
MARK_FLAG = ROOT / "task_marked.flag"     # 防止失败时 publish 与兜底 step 重复回写


def log(msg):
    print(msg, flush=True)


def gh_output(**kv):
    """写 GITHUB_OUTPUT；本地调试时退化为打印。"""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        for k, v in kv.items():
            log(f"[output] {k}={v}")
        return
    with open(out, "a", encoding="utf8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def extract_vid(url):
    """从 URL 预测视频 ID（与 gpu_runner.extract_vid 同规则，产物 zip 以此命名）。"""
    url = (url or "").strip()
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"(BV[A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    return url.rstrip("/").split("/")[-1].split("?")[0]


def make_source():
    """Secrets 里的 Service Account 凭证 -> GoogleSheetSource（读 SHEET_TAB 指定的工作表）。"""
    creds = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    tab = os.environ.get("SHEET_TAB", "").strip()
    if not tab:
        raise SystemExit("SHEET_TAB 未配置：必须指定 YouDub 专属工作表名，避免与 dapang 抢「表格1」")
    worksheet = int(tab) if tab.isdigit() else tab
    return GoogleSheetSource(creds, os.environ["SHEET_ID"], worksheet=worksheet, log=log)


def cmd_claim():
    manual = os.environ.get("INPUT_VIDEO_URL", "").strip()
    if manual:
        log(f"手动单条模式（不动表格，处理完也不投稿）：{manual}")
        gh_output(has_video="true", claimed="false",
                  video_url=manual, vid=extract_vid(manual))
        return
    task = make_source().next_pending()
    if task is None:
        log("表格没有待处理行，本轮空跑收工")
        gh_output(has_video="false", claimed="false", video_url="", vid="")
        return
    task["vid"] = extract_vid(task["video_url"])
    TASK_FILE.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf8")
    log(f"已认领：第{task['row']}行 vid={task['vid']} title={task['title']}")
    gh_output(has_video="true", claimed="true",
              video_url=task["video_url"], vid=task["vid"])


def cmd_mark_failed():
    if MARK_FLAG.exists():
        log("状态已回写过，跳过")
        return
    if not TASK_FILE.exists():
        log("无 task.json（未认领过），跳过")
        return
    task = json.loads(TASK_FILE.read_text(encoding="utf8"))
    make_source().mark_failed(task["row"], task["status_col"])
    MARK_FLAG.write_text("failed", encoding="utf8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "claim":
        cmd_claim()
    elif cmd == "mark-failed":
        cmd_mark_failed()
    else:
        raise SystemExit("用法: sheet_task.py claim|mark-failed")

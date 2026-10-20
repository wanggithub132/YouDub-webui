"""YouDub GPU Runner — 由 GitHub Actions 推送到 Kaggle 执行（单条认领模式）
================================================================
流程：GPU 门禁(必须 T4) → 克隆仓库 → 装依赖 → 恢复模型缓存
     → 处理认领的视频 → 打包 {vid}.zip 到 /kaggle/working

去重：由 Google 表格认领机制保证（只有状态列为空的才会被认领）。

结果协议：/kaggle/working/RESULT.txt 首行为
  SUCCESS / GPU_NOT_T4 / FAILED
GitHub Actions 依据它决定：成功收工 / 重推抽卡 / 报错终止。
"""

import base64
import contextlib
import csv
import io
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ── 占位符：由 GitHub Actions 注入 ─────────────────────
VIDEO_URL = "__VIDEO_URL__"              # 单条模式（可空）
SHEET_CSV_URL = "__SHEET_CSV_URL__"      # Google 表格发布的 CSV 链接（批量模式）
OPENAI_API_KEY = "__OPENAI_API_KEY__"
YOUTUBE_COOKIE_B64 = "__YOUTUBE_COOKIE_B64__"
BATCH_SIZE = "__BATCH_SIZE__"            # 每批处理几个，默认 6

WORKING = Path("/kaggle/working")          # 只放最终产物（= kernel Output）
BUILD = Path("/tmp/youdub")                # 仓库+中间产物，不进 Output
REPO = BUILD / "YouDub-webui"
OUTPUT = BUILD / "output"

# 阶段耗时记录：((名称, 秒), ...)，结束时汇总进 RESULT.txt 与 kernel 日志
_STAGE_COST: list[tuple[str, float]] = []


@contextlib.contextmanager
def timed(label: str):
    """记录一个阶段的墙钟耗时，结束（含异常）时打印一行，便于定位慢点。"""
    started = time.monotonic()
    print(f"\n[TIMER] >>> {label}", flush=True)
    try:
        yield
    finally:
        cost = time.monotonic() - started
        _STAGE_COST.append((label, cost))
        print(f"[TIMER] <<< {label} = {cost / 60:.1f} min", flush=True)


def dump_timings() -> str:
    """打印并返回阶段耗时汇总（单行，可直接写进 RESULT.txt 第二行）。"""
    if not _STAGE_COST:
        return ""
    print("\n===== 阶段耗时汇总（分钟）=====", flush=True)
    for label, cost in _STAGE_COST:
        print(f"  {label:26} {cost / 60:8.1f}", flush=True)
    return "TIMINGS: " + " | ".join(f"{label}={cost / 60:.1f}min" for label, cost in _STAGE_COST)


def torch_ready() -> bool:
    """判断预装 torch 能否直接用于 T4：CUDA 12.x 构建 + torchaudio 可导入。

    Kaggle T4 镜像已预装 cu124 的 torch，满足条件时跳过 2-3GB 的强制重装，
    避免网络抖动把这一步拖成 1-2 小时。设 FORCE_TORCH_REINSTALL=1 可强制重装。
    """
    if os.getenv("FORCE_TORCH_REINSTALL", "").strip().lower() not in ("", "0", "false"):
        print("FORCE_TORCH_REINSTALL 已设置，强制重装 torch", flush=True)
        return False
    probe = (
        "import torch, torchaudio; "
        "assert str(torch.version.cuda).startswith('12'), 'cuda ' + str(torch.version.cuda); "
        "assert torch.cuda.is_available(), 'cuda unavailable'; "
        "print('torch ' + torch.__version__ + ' / torchaudio ' + torchaudio.__version__ "
        "+ ' / cuda ' + str(torch.version.cuda))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=300
        )
    except subprocess.TimeoutExpired:
        print("torch 探测超时，按不满足处理", flush=True)
        return False
    print((result.stdout or "").strip() or (result.stderr or "").strip(), flush=True)
    return result.returncode == 0


def write_result(tag: str, extra: str = "") -> None:
    (WORKING / "RESULT.txt").write_text(tag + ("\n" + extra if extra else ""))
    print(f"RESULT::{tag} {extra}", flush=True)


def sh(cmd: str, **kw) -> subprocess.CompletedProcess:
    print(f"\n$ {cmd}", flush=True)
    kw.setdefault("shell", True)
    kw.setdefault("timeout", 1800)
    result = subprocess.run(cmd, **kw)
    if result.returncode != 0:
        print(f"!! 退出码 {result.returncode}", flush=True)
    return result


def extract_vid(url: str) -> str:
    """从 URL 预测视频 ID（与 yt-dlp 生成的 id 大概率一致，用于去重）。"""
    url = url.strip()
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url)          # youtube watch?v=
    if m:
        return m.group(1)
    m = re.search(r"(BV[A-Za-z0-9]+)", url)                    # bilibili BV 号
    if m:
        return m.group(1)
    return url.rstrip("/").split("/")[-1].split("?")[0]        # youtu.be/xxx 等


def read_sources() -> list[str]:
    """读视频源：优先 Google 表格 CSV，其次单条 URL。返回去空去重后的 URL 列表。"""
    urls: list[str] = []
    if SHEET_CSV_URL.strip() and not SHEET_CSV_URL.startswith("__"):
        print("从 Google 表格读取视频源:", SHEET_CSV_URL, flush=True)
        req = urllib.request.Request(SHEET_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
        for row in csv.reader(io.StringIO(text)):
            for cell in row:
                cell = cell.strip()
                if cell.startswith("http") and ("youtu" in cell or "bilibili" in cell):
                    urls.append(cell)
    elif VIDEO_URL.strip() and not VIDEO_URL.startswith("__"):
        urls.append(VIDEO_URL.strip())

    seen, unique = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def done_vids() -> set[str]:
    """已完成的 vid 集合。
    
    注：单条认领模式下，去重由表格认领机制保证，此函数返回空集合。
    历史逻辑（批量模式）依赖 Kaggle 数据集挂载，已废弃。
    """
    return set()


def restore_model_cache() -> None:
    ds = None
    for cand in Path("/kaggle/input").rglob("*"):
        if cand.is_dir() and (cand / "modelscope").exists():
            ds = cand
            break

    def restore(src: Path, dst) -> None:
        dst = Path(dst).expanduser()
        if not src.exists():
            print("数据集里没有，跳过:", src.name, flush=True)
            return
        if dst.exists() and any(dst.iterdir()):
            print("目标已存在，跳过:", dst, flush=True)
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        print("已恢复:", dst, flush=True)

    if ds:
        print("模型数据集位置:", ds, flush=True)
        restore(ds / "modelscope", REPO / "data" / "modelscope")      # VoxCPM2
        restore(ds / "whisper", "~/.cache/whisper")                   # Whisper
        restore(ds / "demucs", "~/.cache/torch/hub/checkpoints")      # Demucs
    else:
        print("警告: 未挂载 youdub-models，模型将走公网下载（慢但能跑）", flush=True)


def package_finished(done: set[str]) -> list[str]:
    """扫描 output，把有 video_final.mp4 且尚未打包的 session 打成 {vid}.zip。"""
    zips = []
    for session in sorted(OUTPUT.glob("*/*")):
        if not (session / "media" / "video_final.mp4").exists():
            continue
        vid = session.name.rsplit("__", 1)[-1]
        if vid in done or (WORKING / f"{vid}.zip").exists():
            continue
        stage = BUILD / "_pack" / vid
        shutil.rmtree(stage, ignore_errors=True)
        shutil.copytree(session / "media", stage / "media")
        shutil.copytree(session / "metadata", stage / "metadata")
        zip_path = shutil.make_archive(str(WORKING / vid), "zip", stage)
        shutil.rmtree(stage, ignore_errors=True)
        size_mb = Path(zip_path).stat().st_size / 1e6
        print(f"已打包: {zip_path} ({size_mb:.1f} MB)", flush=True)
        zips.append(zip_path)
    return zips


def run_one(url: str, env: dict) -> int:
    print(f"\n{'='*60}\n>>> 开始处理: {url}\n{'='*60}", flush=True)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "scripts.run_pipeline", url],
        cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        print(line, end="", flush=True)
    rc = proc.wait()
    print(f"\n=== [{url}] pipeline 退出码: {rc} ===", flush=True)
    return rc


def main() -> int:
    # ── 0. GPU 门禁：用预装 torch 秒查，不是 T4 立刻退出省时间 ──
    gpu = "none"
    with timed("gpu-gate"):
        import torch

        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
            print(f"分到的 GPU: {gpu} × {torch.cuda.device_count()}", flush=True)
        else:
            print("分到的 GPU: none（无 CUDA 设备）", flush=True)
    if "T4" not in gpu:
        write_result("GPU_NOT_T4", gpu)
        return 0  # 正常退出，让 Actions 重推抽卡

    # ── 校验注入参数 ──
    if not OPENAI_API_KEY.isascii() or OPENAI_API_KEY.startswith("__"):
        write_result("FAILED", "OPENAI_API_KEY 未注入或含非 ASCII 字符")
        return 0

    batch = 6
    if BATCH_SIZE.strip().isdigit():
        batch = max(1, int(BATCH_SIZE.strip()))

    # ── 1. 克隆仓库 + demucs/dapang 子模块 ──
    BUILD.mkdir(parents=True, exist_ok=True)
    with timed("clone-repos"):
        if not REPO.exists():
            sh(f"git clone --depth=1 https://github.com/wanggithub132/YouDub-webui.git {REPO}")
        demucs = REPO / "submodule" / "demucs"
        if not (demucs / "demucs" / "api.py").exists():
            shutil.rmtree(demucs, ignore_errors=True)
            sh(f"git clone --depth=1 https://github.com/facebookresearch/demucs.git {demucs}")
        # 下载能力（YoutubeDownloader）依赖 dapang 子模块，必须同步 clone
        dapang = REPO / "submodule" / "dapang"
        if not (dapang / "youtube_downloader.py").exists():
            shutil.rmtree(dapang, ignore_errors=True)
            sh(f"git clone --depth=1 https://github.com/wanggithub132/dapang.git {dapang}")

    # ── 2. 系统依赖：ffmpeg + deno（yt-dlp EJS 求解器需要）──
    with timed("apt-ffmpeg"):
        sh("apt-get update -qq && apt-get install -y -qq ffmpeg")
    with timed("install-deno"):
        sh("curl -fsSL https://deno.land/install.sh | sh -s -- -y")
        deno = Path.home() / ".deno" / "bin" / "deno"
        sh(f"ln -sf {deno} /usr/local/bin/deno && deno --version")

    # ── 3. Python 依赖（cu124 torch 为已验证组合）──
    # pip 步骤单独放宽到 90 分钟：默认 30 分钟超时会直接抛异常，
    # 导致 RESULT.txt 写不出来（Actions 侧只能看到 NO_RESULT）
    with timed("pip-requirements"):
        sh(f"cd {REPO} && pip install -q -r requirements.txt 2>&1 | tail -3", timeout=5400)
    # Kaggle T4 镜像已预装 cu124 的 torch：满足要求就跳过 2-3GB 的强制重装，
    # 避免网络抖动把这一步拖成 1-2 小时（FORCE_TORCH_REINSTALL=1 可强制重装）
    if torch_ready():
        torch_mode = "reuse(preinstalled)"
        print("预装 torch 满足要求，跳过 cu124 重装", flush=True)
    else:
        torch_mode = "reinstalled-cu124"
        with timed("pip-torch-cu124"):
            sh("pip install -q --force-reinstall torch torchaudio "
               "--index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -3", timeout=5400)
    with timed("pip-ytdlp"):
        sh('pip install -q -U "yt-dlp[default]" yt-dlp-ejs 2>&1 | tail -3', timeout=1800)

    # ── 4. 恢复模型缓存 ──
    with timed("restore-model-cache"):
        restore_model_cache()

    # ── 5. cookie + .env（.env 必须纯 ASCII）──
    if YOUTUBE_COOKIE_B64.strip() and not YOUTUBE_COOKIE_B64.startswith("__"):
        cookie = base64.b64decode(YOUTUBE_COOKIE_B64).decode("utf-8")
        cookie_dir = REPO / "data" / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        (cookie_dir / "youtube.txt").write_text(cookie + "\n", encoding="utf-8")
        print("YouTube cookie 已写入", flush=True)
    else:
        print("警告: 未提供 YOUTUBE_COOKIE，YouTube 下载可能受限", flush=True)

    (REPO / ".env").write_text(
        f"WORKFOLDER={OUTPUT}\n"
        f"OPENAI_BASE_URL=https://api.deepseek.com/v1\n"
        f"OPENAI_API_KEY={OPENAI_API_KEY}\n"
        f"OPENAI_MODEL=deepseek-v4-flash\n")

    # ── 6. 组装待处理清单：单条认领模式，从环境变量读取
    all_urls = read_sources()
    if not all_urls:
        write_result("FAILED", "无视频源")
        return 0
    
    print(f"\n视频源: {all_urls[0]}", flush=True)
    todo = [(all_urls[0], extract_vid(all_urls[0]))]

    if not todo:
        write_result("FAILED", "无待处理视频")
        return 0

    print(f"本批处理 {len(todo)} 个: " + ", ".join(v for _, v in todo), flush=True)

    # ── 7. 环境已就绪，循环跑（子进程隔离；TTS 阶段自动多卡）──
    env = dict(os.environ)
    env["TORCHDYNAMO_DISABLE"] = "1"  # T4 不支持 bfloat16 编译
    env["OPENAI_API_KEY"] = OPENAI_API_KEY

    # 软截止：开跑 4h40m 后不再接新视频，留时间收尾打包
    # （Actions 轮询 5.3h / kernel 硬超时 5.5h，硬杀会丢掉已完成的产物）
    deadline = time.monotonic() + 4 * 3600 + 40 * 60

    ok, fail, skipped = [], [], []
    produced = []
    for url, vid in todo:
        if time.monotonic() > deadline:
            skipped.append(vid)
            print(f"⏰ 时间预算已用尽，跳过: {vid}（下次定时会自动补跑）", flush=True)
            continue
        try:
            with timed(f"pipeline-{vid}"):
                rc = run_one(url, env)
        except Exception as exc:  # 单个视频崩溃不影响后续
            print(f"!! [{url}] 异常: {exc}", flush=True)
            rc = 1
        with timed("package-zip"):
            produced += package_finished(set(ok))
        (ok if rc == 0 else fail).append(vid)

    # ── 9. 汇总结果（第二行起写阶段耗时，head -1 仍取状态）──
    summary = f"成功 {len(ok)} [{', '.join(ok)}] / 失败 {len(fail)} [{', '.join(fail)}]"
    if skipped:
        summary += f" / 超时顺延 {len(skipped)} [{', '.join(skipped)}]"
    print("\n" + summary, flush=True)
    timings = dump_timings()
    tail = ("\n" + timings if timings else "") + f"\nTORCH: {torch_mode}"
    if produced and not fail:
        write_result("SUCCESS", summary + tail)
    elif produced:
        write_result("SUCCESS", summary + "（部分失败，成功的已打包）" + tail)
    else:
        write_result("FAILED", summary + tail)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        dump_timings()  # 崩溃时也把慢点打出来，便于下次定位
        raise

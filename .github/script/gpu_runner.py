"""
YouDub GPU Runner — 由 GitHub Actions 推送到 Kaggle 执行
========================================================
流程：GPU 门禁(必须 T4) → 克隆仓库 → 装依赖 → 恢复模型缓存
     → 写 cookie/.env → 跑 pipeline → 打包 {vid}.zip 到 /kaggle/working

结果协议：/kaggle/working/RESULT.txt 首行为
  SUCCESS / GPU_NOT_T4 / FAILED
GitHub Actions 依据它决定：成功收工 / 重推抽卡 / 报错终止。
"""

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── 占位符：由 GitHub Actions 注入 ─────────────────────
VIDEO_URL = "__VIDEO_URL__"
OPENAI_API_KEY = "__OPENAI_API_KEY__"
YOUTUBE_COOKIE_B64 = "__YOUTUBE_COOKIE_B64__"

WORKING = Path("/kaggle/working")          # 只放最终产物（= kernel Output）
BUILD = Path("/tmp/youdub")                # 仓库+中间产物，不进 Output
REPO = BUILD / "YouDub-webui"
OUTPUT = BUILD / "output"


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


def main() -> int:
    # ── 0. GPU 门禁：用预装 torch 秒查，不是 T4 立刻退出省时间 ──
    import torch

    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"
    print(f"分到的 GPU: {gpu}", flush=True)
    if "T4" not in gpu:
        write_result("GPU_NOT_T4", gpu)
        return 0  # 正常退出，让 Actions 重推抽卡

    # ── 校验注入参数 ──
    if not VIDEO_URL or VIDEO_URL.startswith("__"):
        write_result("FAILED", "VIDEO_URL 未注入")
        return 0
    if not OPENAI_API_KEY.isascii() or OPENAI_API_KEY.startswith("__"):
        write_result("FAILED", "OPENAI_API_KEY 未注入或含非 ASCII 字符")
        return 0

    # ── 1. 克隆仓库 + demucs 子模块（浅克隆不带子模块，需单独克隆）──
    BUILD.mkdir(parents=True, exist_ok=True)
    if not REPO.exists():
        sh(f"git clone --depth=1 https://github.com/wanggithub132/YouDub-webui.git {REPO}")
    demucs = REPO / "submodule" / "demucs"
    if not (demucs / "demucs" / "api.py").exists():
        shutil.rmtree(demucs, ignore_errors=True)
        sh(f"git clone --depth=1 https://github.com/facebookresearch/demucs.git {demucs}")

    # ── 2. 系统依赖：ffmpeg + deno（yt-dlp EJS 求解器需要）──
    sh("apt-get update -qq && apt-get install -y -qq ffmpeg")
    sh("curl -fsSL https://deno.land/install.sh | sh -s -- -y")
    deno = Path.home() / ".deno" / "bin" / "deno"
    sh(f"ln -sf {deno} /usr/local/bin/deno && deno --version")

    # ── 3. Python 依赖（cu124 torch 为已验证组合）──
    sh(f"cd {REPO} && pip install -q -r requirements.txt 2>&1 | tail -3")
    sh("pip install -q --force-reinstall torch torchaudio "
       "--index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -3")
    sh('pip install -q -U "yt-dlp[default]" yt-dlp-ejs 2>&1 | tail -3')

    # ── 4. 热改 ytdlp.py：JS 运行时 node → deno ──
    ytdlp_py = REPO / "backend" / "app" / "adapters" / "ytdlp.py"
    ytdlp_py.write_text(
        ytdlp_py.read_text(encoding="utf-8").replace(
            '"js_runtimes": {"node": {}}', '"js_runtimes": {"deno": {}}'),
        encoding="utf-8")

    # ── 5. 从 youdub-models 数据集恢复模型缓存（自动定位挂载路径）──
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
        print("数据集位置:", ds, flush=True)
        restore(ds / "modelscope", REPO / "data" / "modelscope")      # VoxCPM2
        restore(ds / "whisper", "~/.cache/whisper")                   # Whisper
        restore(ds / "demucs", "~/.cache/torch/hub/checkpoints")      # Demucs
    else:
        print("警告: 未挂载 youdub-models，模型将走公网下载（慢但能跑）", flush=True)

    # ── 6. cookie + .env（.env 必须纯 ASCII，且首个 DB 播种源于它）──
    if YOUTUBE_COOKIE_B64.strip() and not YOUTUBE_COOKIE_B64.startswith("__"):
        cookie = base64.b64decode(YOUTUBE_COOKIE_B64).decode("utf-8")
        cookie_dir = REPO / "data" / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        (cookie_dir / "youtube.txt").write_text(cookie + "\n", encoding="utf-8")
        print("YouTube cookie 已写入", flush=True)
    else:
        print("警告: 未提供 YOUTUBE_COOKIE，YouTube 下载可能受限", flush=True)

    env_text = (
        f"WORKFOLDER={OUTPUT}\n"
        f"OPENAI_BASE_URL=https://api.deepseek.com/v1\n"
        f"OPENAI_API_KEY={OPENAI_API_KEY}\n"
        f"OPENAI_MODEL=deepseek-v4-flash\n")
    (REPO / ".env").write_text(env_text)

    # ── 7. 跑 pipeline（实时日志）──
    env = dict(os.environ)
    env["TORCHDYNAMO_DISABLE"] = "1"  # T4 不支持 bfloat16 编译
    env["OPENAI_API_KEY"] = OPENAI_API_KEY
    print(f"\n>>> 开始处理: {VIDEO_URL}\n", flush=True)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "scripts.run_pipeline", VIDEO_URL],
        cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        print(line, end="", flush=True)
    rc = proc.wait()
    print(f"\n=== pipeline 退出码: {rc} ===", flush=True)

    # ── 8. 打包产物：每个视频 media+metadata → /kaggle/working/{vid}.zip ──
    zips = []
    for session in sorted(OUTPUT.glob("*/*")):
        if not (session / "media" / "video_final.mp4").exists():
            continue
        vid = session.name.rsplit("__", 1)[-1]
        stage = BUILD / "_pack" / vid
        shutil.rmtree(stage, ignore_errors=True)
        shutil.copytree(session / "media", stage / "media")
        shutil.copytree(session / "metadata", stage / "metadata")
        zip_path = shutil.make_archive(str(WORKING / vid), "zip", stage)
        shutil.rmtree(stage, ignore_errors=True)
        size_mb = Path(zip_path).stat().st_size / 1e6
        print(f"已打包: {zip_path} ({size_mb:.1f} MB)", flush=True)
        zips.append(zip_path)

    if rc == 0 and zips:
        write_result("SUCCESS", "; ".join(Path(z).name for z in zips))
    else:
        write_result("FAILED", f"pipeline 退出码 {rc}, 产物数 {len(zips)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

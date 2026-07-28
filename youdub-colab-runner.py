#!/usr/bin/env python
"""
YouDub Colab CLI Runner
Run via: colab run --gpu T4 youdub-colab-runner.py <youtube-url>
"""
import os, subprocess, sys, json, urllib.request

VIDEO_URL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("VIDEO_URL", "")

# ── 提取视频 ID ────────────────────────────────────────
from urllib.parse import urlparse, parse_qs
video_id = (parse_qs(urlparse(VIDEO_URL).query).get("v") or [""])[0] or VIDEO_URL.strip("/").split("/")[-1]

print(f"VIDEO_URL={VIDEO_URL}")
print(f"VIDEO_ID={video_id}")

# ── 深度求索 API Key（从环境变量读取）─────────────────
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# ── YouTube Cookie（从环境变量读取）────────────────────
# 用 Get cookies.txt LOCALLY 扩展导出 Netscape 格式后，
# 传给 colab run:  colab run --gpu T4 --env YOUTUBE_COOKIE="$(cat cookies.txt)" youdub-colab-runner.py <url>
YOUTUBE_COOKIE = os.environ.get("YOUTUBE_COOKIE", "")

# ── 克隆仓库 ───────────────────────────────────────────
WORK = Path("/content")
os.chdir(WORK)

from pathlib import Path

if not (WORK / "YouDub-webui").exists():
    subprocess.run(["git", "clone", "--depth=1",
                    "https://github.com/wanggithub132/YouDub-webui.git"],
                   capture_output=True)

os.chdir(WORK / "YouDub-webui")

# 子模块
demucs_dir = WORK / "YouDub-webui" / "submodule" / "demucs"
api_py = demucs_dir / "demucs" / "api.py"
if not api_py.exists():
    import shutil
    if demucs_dir.exists():
        shutil.rmtree(demucs_dir)
    subprocess.run(["git", "clone", "--depth=1",
                    "https://github.com/facebookresearch/demucs.git",
                    str(demucs_dir)], capture_output=True)

# ── 系统依赖 ───────────────────────────────────────────
subprocess.run("apt-get update -qq && apt-get install -y -qq ffmpeg",
               shell=True, capture_output=True)

# ── Python 依赖 ───────────────────────────────────────
subprocess.run("pip install -q --upgrade pip", shell=True, capture_output=True)
subprocess.run("pip install -q -r requirements.txt 2>&1 | tail -3",
               shell=True, capture_output=True)
subprocess.run("pip install -q --force-reinstall torch torchaudio "
               "--index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -3",
               shell=True, capture_output=True)

os.environ["TORCHDYNAMO_DISABLE"] = "1"

# ── 验证 GPU ──────────────────────────────────────────
import torch
print(f"GPU: {torch.cuda.get_device_name(0)}  "
      f"显存: {torch.cuda.get_device_properties(0).total_memory/1024**3:.1f} GB")
torch.cuda.empty_cache()

# ── .env ──────────────────────────────────────────────
api_key = DEEPSEEK_KEY or os.environ.get("OPENAI_API_KEY", "")
(WORK / "YouDub-webui" / ".env").write_text(f"""
WORKFOLDER=/content/output
DB_PATH=/content/output/youdub.sqlite
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY={api_key}
OPENAI_MODEL=deepseek-v4-flash
""".strip())

# ── YouTube Cookie ────────────────────────────────────
if YOUTUBE_COOKIE:
    cookie_dir = WORK / "YouDub-webui" / "data" / "cookies"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    (cookie_dir / "youtube.txt").write_text(YOUTUBE_COOKIE + "\n")
    print(">>> YouTube cookie 已写入 data/cookies/youtube.txt")
else:
    print(">>> 警告: 未设置 YOUTUBE_COOKIE，YouTube 下载可能受限")

# ── 运行 pipeline ────────────────────────────────────
print(f">>> 开始处理: {VIDEO_URL}")
result = subprocess.run(
    ["python", "-m", "scripts.run_pipeline", VIDEO_URL],
    capture_output=True, text=True
)
out, err = result.stdout, result.stderr
print(out[-2000:] if len(out) > 2000 else out)
if err:
    print("STDERR:", err[-2000:] if len(err) > 2000 else err)
print(f"退出码: {result.returncode}")

# ── 保存到 Google Drive ──────────────────────────────
from google.colab import drive
drive.mount("/content/drive")

output = Path("/content/output")
dest = Path(f"/content/drive/MyDrive/YouDub-Output/{video_id}")
dest.mkdir(parents=True, exist_ok=True)

if output.exists():
    import shutil
    count = 0
    for f in sorted(output.rglob("*")):
        if f.is_file() and f.stat().st_size > 0:
            rel = f.relative_to(output)
            d = dest / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, d)
            count += 1
    (dest / "_source_url.txt").write_text(f"VIDEO_URL={VIDEO_URL}\nVIDEO_ID={video_id}\n")
    print(f">>> 已保存 {count} 个文件到 Google Drive/YouDub-Output/{video_id}/")

# ── 输出产物列表 ──────────────────────────────────────
print("\n>>> 输出文件:")
if output.exists():
    for f in sorted(output.rglob("*")):
        if f.is_file() and f.stat().st_size > 0:
            print(f"  {f.relative_to(output)}  ({f.stat().st_size/1024**2:.1f} MB)")

"""试传一个视频到抖音 account2（新账号）。用 subprocess 避免 PowerShell 中文编码问题。"""
import glob
import subprocess
import sys

SAU_PY = r"f:\money\dapang\venv\Scripts\python.exe"
SAU_CLI = r"f:\money\dapang\.sau\sau_cli.py"

files = glob.glob(r"f:\work\fromgithub\YouDub-webui\workfolder\拳击零基础\01_*\video.mp4")
if not files:
    sys.exit("未找到 01_*/video.mp4")

cmd = [
    SAU_PY, SAU_CLI, "douyin", "upload-video",
    "--account", "account2",
    "--file", files[0],
    "--title", "拳击零基础系列37，周末同练对战拳击（上/下）FightCamp",
    "--desc", "B站搬运测试视频，正片后续更新",
    "--tags", "拳击,FightCamp,健身,格斗",
    "--headed",
]
print("上传文件:", files[0], flush=True)
print("账号: account2 (cookies/douyin_account2.json)", flush=True)
print("-" * 60, flush=True)
r = subprocess.run(cmd, cwd=r"f:\money\dapang\.sau")
sys.exit(r.returncode)

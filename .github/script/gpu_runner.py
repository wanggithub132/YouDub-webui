"""
YouDub GPU Runner — 在 Kaggle 免费 GPU 上执行
===============================================
此脚本由 GitHub Actions 自动推送到 Kaggle 运行。
Kaggle 环境: NVIDIA P100 16GB / 4核CPU / 29GB RAM / Ubuntu
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def sh(cmd, **kw):
    """执行 shell 命令并实时输出"""
    print(f"\n❯ {cmd}", flush=True)
    kw.setdefault("timeout", 600)
    result = subprocess.run(cmd, shell=True, capture_output=False, **kw)
    if result.returncode != 0:
        print(f"⚠️ 退出码 {result.returncode}", flush=True)
    return result


def main():
    workdir = Path("/kaggle/working")
    os.chdir(workdir)

    # ─────────────────────────────────────────────────
    # 这个占位符会被 GitHub Actions 自动替换
    VIDEO_URL = "__VIDEO_URL__"
    # ─────────────────────────────────────────────────

    print("=" * 60, flush=True)
    print("YouDub  |  Kaggle GPU Runner", flush=True)
    print(f"视频链接: {VIDEO_URL or '(无 → 只跑单元测试)'}", flush=True)
    print("=" * 60, flush=True)

    # ── 1. 克隆仓库 ─────────────────────────────────
    repo = "https://github.com/wanggithub132/YouDub-webui.git"
    code_dir = workdir / "YouDub-webui"

    if not code_dir.exists():
        sh(f"git clone --depth=1 {repo}")
    os.chdir(code_dir)

    # ── 2. 系统依赖 ─────────────────────────────────
    # Kaggle 镜像源可能较慢，加超时保护
    sh("apt-get update -qq 2>/dev/null; apt-get install -y -qq ffmpeg 2>/dev/null")

    # ── 3. Python 依赖 ─────────────────────────────
    sh("pip install -q --upgrade pip 2>&1 | tail -3")
    sh("pip install -q -r requirements.txt 2>&1 | tail -5")
    # Kaggle 自带 CUDA，单独装 PyTorch GPU 版
    sh("pip install -q torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -5")

    # ── 4. GPU 检测 ─────────────────────────────────
    sh("nvidia-smi 2>&1 | head -20")

    gpu_ok = False
    try:
        import torch
        gpu_ok = torch.cuda.is_available()
        if gpu_ok:
            name = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            mem = getattr(props, "total_memory", getattr(props, "total_mem", 0)) / 1024**3
            print(f"\n✅ GPU OK: {name}  |  {mem:.1f} GB", flush=True)
        else:
            print("\n❌ CUDA 不可用", flush=True)
    except Exception as e:
        print(f"\n❌ Torch 导入失败: {e}", flush=True)

    # ── 5. 执行 ────────────────────────────────────
    if VIDEO_URL and gpu_ok:
        # 模式 A: 处理视频
        print("\n" + "=" * 60, flush=True)
        print("模式 A: 运行完整 Pipeline", flush=True)
        print(f"视频: {VIDEO_URL}", flush=True)
        print("=" * 60, flush=True)

        # 准备 .env
        env_path = code_dir / ".env"
        env_path.write_text(f"""
WORKFOLDER=/kaggle/working/output
DB_PATH=/kaggle/working/output/youdub.sqlite
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=deepseek-chat
""".strip())

        # 运行 pipeline
        result = sh(f"python scripts/run_pipeline.py \"{VIDEO_URL}\"")

        # 列出产物
        print("\n--- 输出文件 ---", flush=True)
        for p in sorted(Path("/kaggle/working/output").rglob("*")):
            if p.is_file() and p.stat().st_size > 0:
                print(f"  {p.relative_to('/kaggle/working/output')}  ({p.stat().st_size / 1024**2:.1f} MB)", flush=True)

    else:
        # 模式 B: 跑 GPU 单元测试
        mode = "GPU 不可用" if not gpu_ok else "无视频链接"
        print(f"\n模式 B: 跑 GPU 单元测试（原因: {mode}）", flush=True)

        for test_file in [
            "backend/tests/test_demucs_adapter.py",
            "backend/tests/test_whisper_asr.py",
        ]:
            print(f"\n--- {test_file} ---", flush=True)
            sh(f"python -m pytest {test_file} -v --tb=short 2>&1 | tail -40")

    # ── 6. 输出汇总 ────────────────────────────────
    import torch
    summary = {
        "gpu_available": gpu_ok,
        "gpu_name": torch.cuda.get_device_name(0) if gpu_ok else None,
        "video_url": VIDEO_URL or None,
        "status": "completed",
    }
    Path("/kaggle/working/summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    print(f"\n{'=' * 60}", flush=True)
    print("完成!", flush=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

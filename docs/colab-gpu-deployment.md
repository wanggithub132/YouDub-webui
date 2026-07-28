# YouDub  |  Google Colab T4 GPU 部署方案

> 将 YouDub 的 GPU 密集型任务（Demucs、Whisper、VoxCPM）卸载到 Google Colab 免费 T4 GPU 上运行的完整方案。

---

## 一、为什么需要这个方案

YouDub 的 9 阶段 Pipeline 中，有 3 个阶段重度依赖 GPU：

| 阶段 | 模型 | GPU 需求 | 本地 CPU 耗时 |
|:----:|:----:|:--------:|:------------:|
| 音源分离 | Demucs | 高 | 30-60 分钟 |
| 语音识别 | Whisper | 高 | 20-40 分钟 |
| 语音合成 | VoxCPM | 高 | 15-30 分钟 |

本地无 NVIDIA GPU 时，可借助 Google Colab 免费 T4（16GB 显存）完成加速，然后将产物下载到本地合成最终视频。

---

## 二、整体架构

```
用户输入 YouTube 链接
        │
        ▼
┌─────────────────────────────────────┐
│  Google Colab (T4 GPU, 免费)        │
│                                     │
│  1. git clone YouDub-webui          │
│  2. pip install 依赖                │
│  3. 设置 .env (DeepSeek API)        │
│  4. python -m scripts.run_pipeline  │
│                                     │
│  产出的中间文件:                      │
│   ├─ audio_vocals.wav  (人声)       │
│   ├─ audio_bgm.wav     (背景音乐)   │
│   ├─ translation.zh.json (翻译)    │
│   ├─ segments/tts/*.wav (合成语音) │
│   └─ video_final.mp4   (成品视频)   │
└──────────┬──────────────────────────┘
           │ 自动保存到 Google Drive
           ▼
┌─────────────────────────────────────┐
│  Google Drive → 本地下载             │
└─────────────────────────────────────┘
```

---

## 三、完整可运行脚本

```python
# ============================================================
# YouDub  |  Google Colab 免费 T4 GPU 运行脚本
# ============================================================
# 使用方法:
#   1. Colab → 运行时 → 更改运行时类型 → 选 T4 GPU
#   2. 修改 VIDEO_URL 和 OPENAI_API_KEY
#   3. 点 ▶ 运行
# ============================================================

import os, subprocess, shutil
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ══════ 你要修改的配置 ══════
VIDEO_URL      = "https://www.youtube.com/watch?v=YOUR_VIDEO_ID"
OPENAI_API_KEY = "sk-your-deepseek-api-key-here"
# ════════════════════════════

# ★ 提取视频 ID
video_id = parse_qs(urlparse(VIDEO_URL).query).get("v", [""])[0]
if not video_id:
    video_id = VIDEO_URL.strip("/").split("/")[-1]
print(f">>> 视频 ID: {video_id}")

WORK = Path("/content")
os.chdir(WORK)

# ── 1. 克隆 / 更新仓库 ─────────────────────────────────────
if not (WORK / "YouDub-webui").exists():
    subprocess.run(["git", "clone", "--depth=1",
                    "https://github.com/wanggithub132/YouDub-webui.git"],
                   capture_output=True)
else:
    subprocess.run("git pull", shell=True, cwd=WORK / "YouDub-webui",
                   capture_output=True)

os.chdir(WORK / "YouDub-webui")

# 清除 Python 缓存，防止旧 .pyc 干扰
subprocess.run("find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true",
               shell=True, capture_output=True)

# ★ 子模块（Demucs）手动补全
demucs_dir = WORK / "YouDub-webui" / "submodule" / "demucs"
api_py = demucs_dir / "demucs" / "api.py"
if not api_py.exists():
    print(">>> 子模块缺失，手动克隆 demucs ...")
    if demucs_dir.exists():
        shutil.rmtree(demucs_dir)
    subprocess.run(["git", "clone", "--depth=1",
                    "https://github.com/facebookresearch/demucs.git",
                    str(demucs_dir)], capture_output=True)
    print(f">>> demucs 克隆完成: {api_py.exists()}")

# ── 2. 系统依赖 ──────────────────────────────────────────
subprocess.run("apt-get update -qq && apt-get install -y -qq ffmpeg",
               shell=True, capture_output=True)

# ── 3. Python 依赖 ──────────────────────────────────────
subprocess.run("pip install -q --upgrade pip", shell=True, capture_output=True)
subprocess.run("pip install -q -r requirements.txt 2>&1 | tail -5",
               shell=True, capture_output=True)
# 强制重装 CUDA 版 torch（覆盖 requirements.txt 中的 CPU 版）
subprocess.run("pip install -q --force-reinstall torch torchaudio "
               "--index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -5",
               shell=True, capture_output=True)

# 环境变量禁用 torch.compile（T4 不支持 bfloat16 编译）
os.environ["TORCHDYNAMO_DISABLE"] = "1"

# ── 4. 验证 GPU ─────────────────────────────────────────
import torch
assert torch.cuda.is_available(), "CUDA 不可用"
props = torch.cuda.get_device_properties(0)
mem = getattr(props, "total_memory", getattr(props, "total_mem", 0)) / 1024**3
print(f"GPU: {torch.cuda.get_device_name(0)}  显存: {mem:.1f} GB")

# ── 5. 清空 GPU 缓存 ───────────────────────────────────
torch.cuda.empty_cache()
torch.cuda.reset_peak_memory_stats()
avail = (props.total_memory - torch.cuda.memory_allocated()) / 1024**3
print(f"释放后可用显存: {avail:.1f} GB")

# ── 6. 准备 .env ────────────────────────────────────────
(WORK / "YouDub-webui" / ".env").write_text(f"""
WORKFOLDER=/content/output
DB_PATH=/content/output/youdub.sqlite
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY={OPENAI_API_KEY}
OPENAI_MODEL=deepseek-v4-flash
""".strip())

# ── 7. 运行 pipeline ───────────────────────────────────
print(f">>> 开始处理: {VIDEO_URL}")
result = subprocess.run(
    ["python", "-m", "scripts.run_pipeline", VIDEO_URL],
    capture_output=True, text=True
)
print("=== STDOUT (后 3000 字符) ===")
out = result.stdout
print(out[-3000:] if len(out) > 3000 else out)
if result.stderr:
    print("=== STDERR (后 3000 字符) ===")
    err = result.stderr
    print(err[-3000:] if len(err) > 3000 else err)
print(f"=== 退出码: {result.returncode} ===")

# ── 8. 列出产物 ─────────────────────────────────────────
output = Path("/content/output")
print("\n>>> 本地产物:")
if output.exists():
    files_found = []
    for f in sorted(output.rglob("*")):
        if f.is_file() and f.stat().st_size > 0:
            files_found.append(f)
            print(f"  {f.relative_to(output)}  ({f.stat().st_size/1024**2:.1f} MB)")
    if not files_found:
        print("  (无产物文件)")
else:
    print("  (output 目录不存在)")

# ── 9. 保存到 Google Drive（自动授权）────────────────
print("\n>>> 正在保存到 Google Drive ...")
from google.colab import drive
drive.mount("/content/drive")

drive_root = Path("/content/drive/MyDrive/YouDub-Output")
dest_dir = drive_root / video_id
dest_dir.mkdir(parents=True, exist_ok=True)

if output.exists():
    count = 0
    for f in sorted(output.rglob("*")):
        if f.is_file() and f.stat().st_size > 0:
            rel = f.relative_to(output)
            dest = dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            count += 1

    # 保存源 URL 对照文件
    (dest_dir / "_source_url.txt").write_text(
        f"VIDEO_URL: {VIDEO_URL}\nVIDEO_ID: {video_id}\n"
    )
    print(f">>> 已保存 {count} 个文件到 Google Drive/YouDub-Output/{video_id}/")
else:
    print("  (无产物可保存)")
```

---

## 四、技术原理

### 4.1 Pipeline 的 9 个阶段

| # | 阶段 | 适配器 | 功能 | 需要 GPU |
|:-:|:----:|:------:|:----|:--------:|
| 1 | download | ytdlp | 下载 YouTube 视频 (yt-dlp) | 否 |
| 2 | separate | demucs | 分离人声和背景音乐 | **是** |
| 3 | asr | whisper_asr | 语音识别，生成带时间戳的文本 | **是** |
| 4 | asr_fix | asr_sentence_fixer | 将 ASR 结果按句子重新分段 | 否 |
| 5 | translate | openai_translate | 调用 DeepSeek API 翻译文本 | 否 (云端 API) |
| 6 | split_audio | audio | 按分段裁剪人声参考片段 | 否 |
| 7 | tts | voxcpm | 用 VoxCPM 合成目标语言语音 | **是** |
| 8 | merge_audio | audio | 混合合成语音 + 背景音乐 | 否 |
| 9 | merge_video | ffmpeg | 将字幕烧入视频 + 替换音轨 | 否 |

### 4.2 为什么需要重新安装 PyTorch

`requirements.txt` 中依赖的 `whisper`、`opunmix` 等包会拉取 **CPU 版 PyTorch**，覆盖 Colab 预装的 CUDA 版。必须在安装完所有依赖后，显式从 CUDA 12.4 索引强制重装：

```bash
pip install --force-reinstall torch torchaudio \
    --index-url https://download.pytorch.org/whl/cu124
```

### 4.3 T4 GPU 的 bfloat16 兼容性问题

Colab T4 GPU 基于 Turing 架构，**不支持原生 bfloat16 编译**（Turing 架构仅支持 FP16）。

VoxCPM 加载时以 `dtype: bfloat16` 运行，PyTorch 的 `torch.compile` 尝试编译 bfloat16 操作时会抛出异常。

解决方案（双重保险）：

```python
# 方式一：在 run_pipeline.py 的 main() 中设置
import torch._dynamo
torch._dynamo.config.suppress_errors = True
torch._dynamo.config.disable = True

# 方式二：在调用 subprocess 前设置环境变量（子进程继承）
os.environ["TORCHDYNAMO_DISABLE"] = "1"
```

注意：方式一必须放在 `main()` 函数内部（而非模块顶部），因为子进程会重新加载所有模块。

### 4.4 子模块问题

Demucs 是 Git 子模块（`submodule/demucs`），浅克隆 `--depth=1` 会导致子模块拉取不完整。

检测方式：`run_pipeline.py` 中的 `_demucs_source_path()` 检查 `submodule/demucs/demucs/api.py` 是否存在。

解决方案：手动克隆 demucs 仓库到子模块目录。

### 4.5 Python 模块导入路径问题

`scripts/run_pipeline.py` 中通过 `from backend.app import database` 导入后端模块。直接 `python scripts/run_pipeline.py` 执行时，Python 找不到 `backend` 包。

必须使用 `python -m scripts.run_pipeline`，这样才能将工作目录加入 `sys.path`。

---

## 五、注意事项

### 5.1 Colab 资源限制

| 资源 | 免费版 | 能否修改 |
|:----:|:------:|:--------:|
| GPU | T4 (16GB 显存) | ❌ 固定（付费可升级 A100） |
| 系统内存 | ~12 GB | ❌ 固定 |
| 磁盘 | ~78 GB | ❌ 固定 |
| 运行时长 | 最长 12 小时 | 空闲 90 分钟自动断开 |
| 每周配额 | 有限制（约 50 CU） | Colab 付费可增加 |

### 5.2 视频地域限制

Colab 服务器位于美国，部分 YouTube 视频可能因地域限制无法下载。
- 换用全球可访问的视频
- 或上传 YouTube cookies（需要先通过 Colab web 界面配置）

### 5.3 DeepSeek API 配置

- API 地址：`https://api.deepseek.com/v1`
- 模型名：`deepseek-v4-flash`（或 `deepseek-chat`，具体查看 DeepSeek 最新文档）
- `.env` 文件中的 `OPENAI_BASE_URL` 需指向 DeepSeek 兼容 OpenAI 的端点

### 5.4 第一次运行需要 Google Drive 授权

`drive.mount("/content/drive")` 首次执行时会弹出 OAuth 链接：
1. 点击链接 → 选 Google 账号 → 允许
2. 复制授权码
3. 粘贴到 Colab 输入框
以后在同一 Colab 会话中不再需要重新授权。

### 5.5 GitHub 代码同步

脚本通过 `git pull` 获取最新代码。如果仓库有更新（如修复了 issue），只需重新执行第 1 步即可。

---

## 六、自动化方案对比

| 方案 | 平台 | 成本 | 自动化 | 状态 |
|:----:|:----:|:----:|:------:|:----:|
| Colab 网页 | Colab | 免费 | ❌ 手动 | ✅ **已验证可用** |
| Colab CLI | Colab | 免费 | ✅ 命令行 | ✅ 可用（需 WSL） |
| Kaggle Action | Kaggle | 免费 | ✅ GitHub Actions | ⏳ 待审核通过 |
| 本地 CPU | 本机 | 0 | ✅ | ✅ 可用（慢） |

### 6.1 Colab CLI（推荐下一步）

Google 2026 年 6 月发布官方 CLI，支持一行命令完成所有操作：

```bash
# 安装（需 WSL/Linux/macOS）
pip install google-colab-cli

# 一键运行
colab run --gpu T4 scripts/run_pipeline.py https://youtube.com/watch?v=...
```

### 6.2 Kaggle + GitHub Actions

已经准备好的文件：
- `.github/workflows/kaggle-gpu.yml` — GitHub Actions 工作流
- `.github/script/gpu_runner.py` — Kaggle 运行脚本

需要 Kaggle 身份验证通过后才能使用。

---

## 七、Pipeline 产物说明

运行成功后，输出目录结构：

```
/content/output/
└── {video_title}__{video_id}/
    ├── media/
    │   ├── video_source.mp4          ← 原始视频
    │   ├── audio_vocals.wav          ← 分离出的人声
    │   ├── audio_bgm.wav             ← 分离的背景音乐
    │   └── video_final.mp4           ← ★ 成品（带翻译字幕）
    ├── metadata/
    │   ├── asr.json                  ← 语音识别原始结果
    │   ├── asr_fixed.json            ← 修正后的 ASR
    │   ├── translation.zh.json       ← 翻译结果（中文）
    │   ├── subtitles.zh.srt          ← ★ 独立字幕文件
    │   └── ytdlp_info.json           ← 视频元信息
    ├── segments/
    │   ├── vocals/                   ← 人声片段（用作 TTS 参考音色）
    │   └── tts/                      ← 合成语音片段
    └── tmp/
        └── audio_mixed.m4a           ← 混合音频（中间产物）
```

成品 `video_final.mp4`：
- **自带硬字幕**（通过 FFmpeg subtitles filter 烧录）
- 字幕文本 = ASR 输出 → DeepSeek 翻译 → SRT 格式化
- **不需要 YouTube 本身有字幕**，Whisper 从音频中直接识别

---

## 八、已知问题和排查指南

| 问题 | 表现 | 解决方案 |
|:----|:----|:---------|
| CUDA 不可用 | `Torch not compiled with CUDA enabled` | 强制重装 CUDA 版 PyTorch (cu124) |
| 子模块缺失 | `Demucs source submodule is missing` | 手动 `git clone` demucs 到子模块目录 |
| bfloat16 编译错 | `str.isalnum` dynamo 异常 | 设置 `suppress_errors + disable`，环境变量 `TORCHDYNAMO_DISABLE=1` |
| 显存不足 | 退出码 -9 (SIGKILL) | 换 A100，或减少视频长度 |
| 模块找不到 | `No module named 'backend'` | 用 `python -m` 而不是 `python` 执行 |
| 视频地域限制 | `Video not available` | 换视频或配置 cookies |
| 退出码 1 | VoxCPM 或其他阶段报错 | 查看 `error_message` 字段的内容 |

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yt_dlp

from .. import runtime_security
from ..config import ffprobe_binary
from ..sanitize import sanitize_text
from ..sources import SourceConfig
from ..youtube import extract_video_id, validate_video_url

# 下载能力复用 dapang 子模块的 YoutubeDownloader（EJS 挑战/deno 注入/失败分类/
# 完整性校验都封装在那边），本模块只保留元数据获取、路径规划与调用胶水。
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "submodule" / "dapang"))

try:
    from youtube_downloader import YoutubeDownloader  # noqa: E402
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "submodule/dapang 缺失（下载能力依赖）：先执行 git submodule update --init --recursive"
    ) from exc


# 下载格式候选链：1080p 高清优先（允许 vp9/webm，排除 av01 便于 ffmpeg 重编码），
# mp4 兼容兜底，最后 best。与 dapang DEFAULT_FORMATS 保持一致。
YOU_DUB_FORMATS = {
    "1080": "bestvideo[height<=1080][vcodec!*=av01]+bestaudio/best",
    "mp4": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
    "best": "best",
}

# 下载健全性阈值：低于该值视为残缺/截断文件，
# 防止 yt-dlp "假成功"（仅写出几 KB 的错误页/空壳文件）进入后续阶段
MIN_VIDEO_SIZE_BYTES = 5 * 1024 * 1024
MIN_VIDEO_DURATION_SECONDS = 30

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _bootstrap_bilibili_cookie(cookie_path: Path) -> None:
    response = requests.get(
        "https://www.bilibili.com/",
        headers={"User-Agent": DEFAULT_USER_AGENT, "Referer": "https://www.bilibili.com/"},
        timeout=10,
    )
    response.raise_for_status()
    expires = int(time.time()) + 3600 * 24 * 365
    lines = ["# Netscape HTTP Cookie File", ""]
    cookies = dict(response.cookies)
    cookies.setdefault("SESSDATA", "anonymous_for_webpage_playinfo")
    for name, value in cookies.items():
        lines.append("\t".join([".bilibili.com", "TRUE", "/", "FALSE", str(expires), name, value]))
    runtime_security.atomic_write_private_text(cookie_path, "\n".join(lines) + "\n")


def _proxy_url(proxy_port: str = "") -> str:
    if proxy_port.strip():
        return f"http://127.0.0.1:{proxy_port.strip()}"
    return os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or ""


def _ensure_cookie(source: SourceConfig) -> None:
    cookie_path = source.cookie_path
    if not cookie_path or source.name != "bilibili":
        return
    metadata = runtime_security.private_file_stat(cookie_path)
    if metadata and metadata.st_size > 0:
        return
    _bootstrap_bilibili_cookie(cookie_path)


def _ydl_base(source: SourceConfig, proxy_port: str = "") -> dict[str, Any]:
    opts: dict[str, Any] = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}, "deno": {}},
    }
    # 不再显式覆盖 player_client：yt-dlp 默认多客户端自动 fallback 可拿全格式；
    # 实测显式 android 会把格式锁死在 360p，web 单用常返回空响应（2026-07）。
    if source.name != "youtube":
        opts["http_headers"] = {"User-Agent": DEFAULT_USER_AGENT}
    cookie_path = source.cookie_path
    if cookie_path:
        metadata = runtime_security.private_file_stat(cookie_path)
        if metadata and metadata.st_size > 0:
            opts["cookiefile"] = str(cookie_path)
    if not source.use_proxy:
        opts["proxy"] = ""
        return opts
    proxy = _proxy_url(proxy_port)
    if proxy:
        opts["proxy"] = proxy
    return opts


def _session_path(workfolder: Path, info: dict[str, Any]) -> Path:
    uploader = sanitize_text(str(info.get("uploader") or "unknown"))
    title = sanitize_text(str(info.get("title") or "untitled"))
    video_id = str(info.get("id") or extract_video_id(str(info.get("webpage_url") or "")))
    return workfolder / uploader / f"{title}__{video_id}"


def _is_format_unavailable(exc: Exception) -> bool:
    return "Requested format is not available" in str(exc)

def _remove_partial_outputs(video_file: Path) -> None:
    for candidate in video_file.parent.glob(f"{video_file.name}*"):
        if candidate == video_file:
            continue
        if candidate.is_file():
            candidate.unlink(missing_ok=True)


def _probe_duration(video_file: Path) -> float | None:
    result = subprocess.run(
        [
            ffprobe_binary(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _is_valid_video(video_file: Path) -> bool:
    if not video_file.exists() or video_file.stat().st_size < MIN_VIDEO_SIZE_BYTES:
        return False
    duration = _probe_duration(video_file)
    return duration is not None and duration >= MIN_VIDEO_DURATION_SECONDS


def _build_downloader(source: SourceConfig, proxy_port: str = "") -> YoutubeDownloader:
    """按 SourceConfig 组装 dapang 下载器（proxy 三态：指定/自动/显式禁用）。"""
    proxy: str | None = None
    if source.use_proxy:
        proxy = _proxy_url(proxy_port) or None  # 指定端口，否则自动识别环境变量
    else:
        proxy = ""  # 显式禁用（忽略环境变量）
    return YoutubeDownloader(
        cookies_file=str(source.cookie_path) if source.cookie_path else "",
        proxy=proxy,
        extractor_retries=3,
        timeout=1800,
        fragment_retries=10,
        retries=10,
        ffprobe_path=ffprobe_binary(),
        min_size_mb=MIN_VIDEO_SIZE_BYTES // (1024 * 1024),
        min_duration_s=MIN_VIDEO_DURATION_SECONDS,
        log=lambda msg: print(msg, flush=True),
    )


def _download_with_format_candidates(
    url: str, video_file: Path, source: SourceConfig, proxy_port: str
) -> None:
    """复用 dapang YoutubeDownloader（CLI）按候选链下载，产物统一为 video_source.mp4。"""
    out_prefix = str(video_file.with_suffix(""))
    ext = _build_downloader(source, proxy_port).download_with_fallback(
        url, out_prefix, formats=YOU_DUB_FORMATS
    )
    if ext is None:
        raise RuntimeError(f"All download format candidates failed: {url}")
    produced = Path(f"{out_prefix}.{ext}")
    if produced != video_file:
        produced.replace(video_file)


def download_video(
    url: str, workfolder: Path, source: SourceConfig, proxy_port: str = ""
) -> tuple[Path, dict[str, Any]]:
    validated = validate_video_url(url)
    if validated.source != source.name:
        raise ValueError("The submitted URL does not match the selected video source.")
    canonical_url = validated.url
    video_id = validated.video_id
    _ensure_cookie(source)
    info_opts = _ydl_base(source, proxy_port)
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(canonical_url, download=False)

    if str(info.get("id", video_id)) != video_id:
        raise ValueError("The resolved video id does not match the submitted URL.")

    session = _session_path(workfolder, info)
    media_dir = session / "media"
    metadata_dir = session / "metadata"
    media_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    video_file = media_dir / "video_source.mp4"
    metadata_file = metadata_dir / "ytdlp_info.json"
    metadata_file.write_text(json.dumps(ydl.sanitize_info(info), ensure_ascii=False, indent=2), encoding="utf-8")

    if video_file.exists() and _is_valid_video(video_file):
        return session, info

    # 缓存文件无效（残缺/时长过短）时删除后重新下载，避免旧残片"假成功"
    video_file.unlink(missing_ok=True)
    _remove_partial_outputs(video_file)

    _download_with_format_candidates(canonical_url, video_file, source, proxy_port)

    if not _is_valid_video(video_file):
        size = video_file.stat().st_size if video_file.exists() else 0
        duration = _probe_duration(video_file) if video_file.exists() else None
        _remove_partial_outputs(video_file)
        video_file.unlink(missing_ok=True)
        raise RuntimeError(
            "yt-dlp produced an invalid or truncated video "
            f"({size} bytes, {duration if duration is not None else 'unknown'} s); "
            "partial outputs removed"
        )

    return session, info

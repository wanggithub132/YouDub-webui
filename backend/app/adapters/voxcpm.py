from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import soundfile as sf
from pydub import AudioSegment

from ..config import MODEL_CACHE_DIR, REPO_ROOT

_MODEL = None

_PROMPT_CACHE_GENERATION_DEFAULTS = {
    "min_len": 2,
    "max_len": 4096,
    "retry_badcase": True,
    "retry_badcase_max_times": 3,
    "retry_badcase_ratio_threshold": 6.0,
}


def _model_path() -> Path:
    configured_dir = os.getenv("VOXCPM_MODEL_DIR")
    if configured_dir:
        return Path(configured_dir).expanduser()

    model_id = os.getenv("VOXCPM_MODEL", "OpenBMB/VoxCPM2")
    local_dir = MODEL_CACHE_DIR / model_id.replace("/", "__")
    from modelscope import snapshot_download

    downloaded = snapshot_download(model_id, local_dir=str(local_dir))
    return Path(downloaded)


def _load_model():
    global _MODEL
    if _MODEL is None:
        from voxcpm import VoxCPM

        _MODEL = VoxCPM.from_pretrained(
            str(_model_path()),
            load_denoiser=os.getenv("VOXCPM_LOAD_DENOISER", "false").lower() == "true",
        )
    return _MODEL


def _first_reference(files: list[Path], min_ms: int) -> Path | None:
    for path in files:
        if len(AudioSegment.from_file(path)) >= min_ms:
            return path
    if files:
        return files[0]
    return None


def _speaker(item: dict) -> str:
    speaker = item.get("speaker")
    if speaker is None:
        return "1"
    speaker = str(speaker).strip()
    return speaker or "1"


def _fallback_references(vocals_dir: Path, items: list[dict], min_ms: int) -> tuple[dict[str, Path], Path]:
    files = sorted(vocals_dir.glob("*.wav"))
    if not files:
        raise FileNotFoundError("No vocal segments were generated for VoxCPM references.")

    global_fallback = _first_reference(files, min_ms) or files[0]
    speaker_files: dict[str, list[Path]] = {}
    for index, item in enumerate(items, start=1):
        reference = vocals_dir / f"{index:04d}.wav"
        if reference.exists():
            speaker_files.setdefault(_speaker(item), []).append(reference)

    fallbacks: dict[str, Path] = {}
    for speaker, refs in speaker_files.items():
        fallback = _first_reference(refs, min_ms)
        if fallback is not None:
            fallbacks[speaker] = fallback

    return fallbacks, global_fallback


def _tts_text(item: dict) -> str:
    text = item.get("dst") or item.get("zh", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("target text must be a non-empty string")
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text)


def _multi_gpu_devices() -> list[str]:
    raw = os.getenv("VOXCPM_GPU_DEVICES")
    if raw is not None:
        # explicit override: "0,1" forces sharding, "" or a single id disables it
        devices = [d.strip() for d in raw.split(",") if d.strip()]
        return devices if len(devices) >= 2 else []
    try:
        import torch
    except ImportError:
        return []
    if not torch.cuda.is_available():
        return []
    count = torch.cuda.device_count()
    return [str(i) for i in range(count)] if count >= 2 else []


def _pending_count(total: int, output_dir: Path) -> int:
    return sum(
        1
        for index in range(1, total + 1)
        if not (output_dir / f"{index:04d}.wav").exists()
    )


def _run_shard_workers(
    devices: list[str],
    translation_file: Path,
    vocals_dir: Path,
    session: Path,
    output_dir: Path,
    total: int,
    progress_callback: Callable[[int, str], None] | None,
) -> None:
    num_shards = len(devices)
    procs = []
    for shard, device in enumerate(devices):
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = device
        env["VOXCPM_GPU_DEVICES"] = ""  # workers must take the single-GPU path
        procs.append(
            subprocess.Popen(
                [
                    sys.executable, "-m", "backend.app.adapters.voxcpm",
                    str(translation_file), str(vocals_dir), str(session),
                    "--shard", str(shard), "--num-shards", str(num_shards),
                ],
                cwd=REPO_ROOT,
                env=env,
            )
        )
    while any(proc.poll() is None for proc in procs):
        if progress_callback:
            done = total - _pending_count(total, output_dir)
            progress = min(round(done / total * 100), 99)
            progress_callback(progress, f"Prepared {done}/{total} TTS clips (multi-GPU)")
        time.sleep(5)
    for shard, proc in enumerate(procs):
        if proc.wait() != 0:
            print(f"VoxCPM shard worker {shard} exited with {proc.returncode}; "
                  "missing clips will be regenerated in-process", flush=True)


def generate_tts(
    translation_file: Path,
    vocals_dir: Path,
    session: Path,
    progress_callback: Callable[[int, str], None] | None = None,
    *,
    shard: int = 0,
    num_shards: int = 1,
) -> Path:
    output_dir = session / "segments" / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(translation_file.read_text(encoding="utf-8"))
    items = data["translation"]
    total = len(items)
    if total == 0:
        if progress_callback:
            progress_callback(100, "No TTS clips to generate")
        return output_dir

    devices = _multi_gpu_devices()
    min_items = int(os.getenv("VOXCPM_MULTI_GPU_MIN_ITEMS", "8"))
    if num_shards == 1 and devices and _pending_count(total, output_dir) >= min_items:
        _run_shard_workers(
            devices, translation_file, vocals_dir, session, output_dir, total, progress_callback
        )
        # fall through: the sweep below regenerates any clips a worker failed to produce

    min_reference_ms = int(os.getenv("VOXCPM_MIN_REFERENCE_MS", "1200"))
    fallback_references, global_fallback = _fallback_references(vocals_dir, items, min_reference_ms)
    cfg_value = float(os.getenv("VOXCPM_CFG_VALUE", "2.0"))
    inference_timesteps = int(os.getenv("VOXCPM_INFERENCE_TIMESTEPS", "10"))

    model = None
    fallback_caches = {}

    for index, item in enumerate(items, start=1):
        if (index - 1) % num_shards != shard:
            continue
        output_file = output_dir / f"{index:04d}.wav"
        if not output_file.exists():
            if model is None:
                model = _load_model()
            reference = vocals_dir / f"{index:04d}.wav"
            text = _tts_text(item)
            if not reference.exists() or len(AudioSegment.from_file(reference)) < min_reference_ms:
                speaker = _speaker(item)
                if speaker not in fallback_caches:
                    fallback = fallback_references.get(speaker, global_fallback)
                    fallback_caches[speaker] = model.tts_model.build_prompt_cache(
                        reference_wav_path=str(fallback)
                    )
                result = model.tts_model.generate_with_prompt_cache(
                    target_text=text,
                    prompt_cache=fallback_caches[speaker],
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                    **_PROMPT_CACHE_GENERATION_DEFAULTS,
                )
                wav_tensor, _, _ = result
                wav = wav_tensor.squeeze(0).cpu().numpy()
            else:
                wav = model.generate(
                    text=text,
                    reference_wav_path=str(reference),
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                )
            sf.write(output_file, wav, model.tts_model.sample_rate)
        if progress_callback:
            progress = round(index / total * 100)
            progress_callback(progress, f"Prepared {index}/{total} TTS clips")

    return output_dir


def _main(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="VoxCPM TTS shard worker")
    parser.add_argument("translation_file")
    parser.add_argument("vocals_dir")
    parser.add_argument("session")
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    args = parser.parse_args(argv)
    generate_tts(
        Path(args.translation_file),
        Path(args.vocals_dir),
        Path(args.session),
        shard=args.shard,
        num_shards=args.num_shards,
    )


if __name__ == "__main__":
    _main(sys.argv[1:])

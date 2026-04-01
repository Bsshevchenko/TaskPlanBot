import asyncio
import tempfile
import os
from functools import partial

import mlx_whisper


# Модель загружается из HuggingFace кэша (~/.cache/huggingface/hub/).
# Уже скачана: mlx-community/whisper-large-v3-mlx (используется по умолчанию)
MODEL_REPO = os.getenv("WHISPER_MODEL", "mlx-community/whisper-large-v3-mlx")


def _transcribe_sync(audio_path: str) -> str:
    result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=MODEL_REPO)
    return result["text"].strip()  # type: ignore[index]


async def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    suffix = os.path.splitext(filename)[1] or ".ogg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, partial(_transcribe_sync, tmp_path))
    finally:
        os.unlink(tmp_path)

    return text

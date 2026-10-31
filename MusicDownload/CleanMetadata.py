import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Tuple

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

DEFAULT_DOWNLOAD_PATH = r"Z:\TElugu wap"
AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.wma')
DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_CONCURRENCY = 10

logger = logging.getLogger("CleanMetadata")


def setup_logger(level: int = logging.INFO) -> logging.Logger:
    """Configures structured console logging if no handlers are attached."""
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger


def clean_song_title_metadata(value: str) -> str:
    """Removes the exact TeluguWap title suffix and trims leftover separators/spaces."""
    if not value:
        return value

    cleaned = re.sub(r"\s+-\s+TeluguWap\.Net", "", str(value), flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    cleaned = cleaned.rstrip(" -_.")

    return cleaned.strip()


def clean_general_metadata_text(value: str) -> str:
    """Removes TeluguWap/TeluguMp3 references from non-title metadata fields."""
    if not value:
        return value

    cleaned = str(value)
    cleaned = re.sub(r"TeluguWap\.Net", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"TeluguWap\.net", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"TeluguMp3", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(" -_,;|/")

    return cleaned


def metadata_value_to_text(value) -> str:
    """Converts common mutagen metadata values to editable text."""
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item is not None)

    return str(value)


def update_metadata_value(original_value, cleaned_text: str):
    """Preserves list-style metadata values where possible."""
    if isinstance(original_value, list):
        return [cleaned_text] if cleaned_text else []

    return cleaned_text


def is_title_metadata_key(key: str) -> bool:
    """Detects title metadata keys across common audio formats."""
    normalized = key.lower()

    return normalized in {
        "title",
        "\xa9nam",
        "tit2",
    }


def should_clean_general_metadata_key(key: str) -> bool:
    """Detects non-title metadata fields that commonly contain site branding."""
    normalized = key.lower()

    return normalized in {
        "genre",
        "mood",
        "producer",
        "producers",
        "tcon",
        "tmood",
        "tpe3",
        "copyright",
        "\xa9gen",
    }


def clean_audio_metadata_file(filepath: str) -> bool:
    """Synchronously cleans TeluguWap/TeluguMp3 references from one supported audio file."""
    if MutagenFile is None:
        logger.warning(f"Metadata cleanup skipped for '{filepath}': mutagen is not installed.")
        return False

    if not filepath.lower().endswith(AUDIO_EXTENSIONS):
        logger.debug(f"Skipping non-audio file: {filepath}")
        return False

    try:
        audio = MutagenFile(filepath, easy=True)
        if audio is None or audio.tags is None:
            logger.debug(f"No audio tags found in: {filepath}")
            return False

        changed = False

        for key in list(audio.keys()):
            original_value = audio.get(key)
            original_text = metadata_value_to_text(original_value)

            if is_title_metadata_key(key):
                cleaned_text = clean_song_title_metadata(original_text)
            elif should_clean_general_metadata_key(key):
                cleaned_text = clean_general_metadata_text(original_text)
            else:
                cleaned_text = clean_general_metadata_text(original_text)

            if cleaned_text != original_text:
                if cleaned_text:
                    audio[key] = update_metadata_value(original_value, cleaned_text)
                else:
                    del audio[key]
                changed = True
                logger.debug(f"Modified tag [{key}] in '{filepath}': '{original_text}' -> '{cleaned_text}'")

        if changed:
            audio.save()
            logger.info(f"Metadata cleaned: {filepath}")
        else:
            logger.debug(f"No metadata modifications needed: {filepath}")

        return changed

    except Exception as e:
        logger.error(f"Metadata cleanup failed for '{filepath}': {e}", exc_info=True)
        return False


async def async_clean_audio_metadata_file(
    filepath: str, semaphore: asyncio.Semaphore = None
) -> bool:
    """Asynchronously cleans metadata for a single file offloaded to a worker thread."""
    if semaphore:
        async with semaphore:
            return await asyncio.to_thread(clean_audio_metadata_file, filepath)
    return await asyncio.to_thread(clean_audio_metadata_file, filepath)


async def async_clean_audio_metadata_batch(
    batch: List[str], semaphore: asyncio.Semaphore = None
) -> Tuple[int, int]:
    """
    Processes a batch of audio files concurrently.
    Returns (checked_count, cleaned_count).
    """
    tasks = [async_clean_audio_metadata_file(filepath, semaphore) for filepath in batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    checked = len(batch)
    cleaned = 0

    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            logger.error(f"Error processing '{batch[idx]}': {res}")
        elif res is True:
            cleaned += 1

    return checked, cleaned


async def clean_audio_metadata_from_root_async(
    root_path: str = DEFAULT_DOWNLOAD_PATH,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> Tuple[int, int]:
    """
    Recursively scans root_path and processes audio files asynchronously in concurrent batches.
    Returns (total_checked, total_cleaned).
    """
    setup_logger()

    if MutagenFile is None:
        logger.warning("Metadata cleanup requires mutagen. Please install mutagen first.")
        return 0, 0

    root = Path(root_path)
    if not root.exists():
        logger.warning(f"Metadata cleanup skipped. Root path does not exist: {root_path}")
        return 0, 0

    start_time = time.time()
    logger.info(
        f"Scanning for audio files in '{root_path}' (batch_size={batch_size}, max_concurrency={max_concurrency})..."
    )

    audio_files: List[str] = [
        str(p)
        for p in root.rglob("*")
        if p.is_file() and p.name.lower().endswith(AUDIO_EXTENSIONS)
    ]

    total_files = len(audio_files)
    if total_files == 0:
        logger.info(f"No audio files found for metadata cleanup in: {root_path}")
        return 0, 0

    batches = [
        audio_files[i : i + batch_size]
        for i in range(0, total_files, batch_size)
    ]
    total_batches = len(batches)

    logger.info(
        f"Discovered {total_files} audio files across {total_batches} batch(es). Starting async cleanup..."
    )

    semaphore = asyncio.Semaphore(max_concurrency)
    total_checked = 0
    total_cleaned = 0

    for batch_num, batch in enumerate(batches, start=1):
        logger.info(f"Processing Batch {batch_num}/{total_batches} ({len(batch)} files)...")
        b_checked, b_cleaned = await async_clean_audio_metadata_batch(batch, semaphore=semaphore)
        total_checked += b_checked
        total_cleaned += b_cleaned
        logger.info(
            f"Completed Batch {batch_num}/{total_batches} - Batch cleaned: {b_cleaned}/{b_checked} "
            f"(Total Progress: {total_checked}/{total_files}, Total Cleaned: {total_cleaned})"
        )

    elapsed_time = max(time.time() - start_time, 0.001)
    rate = total_checked / elapsed_time
    logger.info(
        f"Metadata cleanup finished in {elapsed_time:.2f}s | Checked: {total_checked}, "
        f"Cleaned: {total_cleaned}, Rate: {rate:.2f} files/s"
    )

    return total_checked, total_cleaned


def clean_audio_metadata_from_root(
    root_path: str = DEFAULT_DOWNLOAD_PATH,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> Tuple[int, int]:
    """Synchronous entry point that runs the async batch cleanup workflow."""
    return asyncio.run(
        clean_audio_metadata_from_root_async(
            root_path=root_path,
            batch_size=batch_size,
            max_concurrency=max_concurrency,
        )
    )


if __name__ == "__main__":
    setup_logger()
    clean_audio_metadata_from_root()

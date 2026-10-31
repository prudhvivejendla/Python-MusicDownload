import asyncio
import logging
import os
import re
import string
import sys
import time
import urllib.parse
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

MAX_INDEX_WORKERS = 2
MAX_ALBUM_WORKERS = 3
MAX_SONG_WORKERS = 4

DEFAULT_SONG_BATCH_SIZE = 5
DEFAULT_ALBUM_BATCH_SIZE = 4
DEFAULT_INDEX_BATCH_SIZE = 2

# Mobile user agent required by teluguwap.net mobile version
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
    "Sec-Fetch-Dest": "iframe",
    "Referer": "https://teluguwap.net/",
}

ROOT_PAGE = "https://teluguwap.net/"
DEFAULT_DOWNLOAD_PATH = r"Z:\TElugu wap"
INDEXING = ["09"] + list(string.ascii_uppercase)

ALBUM_PATTERN = re.compile(r'^[a-zA-Z0-9\-_]+-A[a-zA-Z0-9]+$')
SONG_PATTERN = re.compile(r'^[a-zA-Z0-9\-_]+-F[a-zA-Z0-9]+$')
AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.wma')

logger = logging.getLogger("DownloadFile")


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


def create_resilient_session(pool_size: int = 50) -> requests.Session:
    """Creates a requests session configured with retries and connection pooling for network resilience."""
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def sanitize_filename(name: str) -> str:
    """Removes invalid filesystem characters."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return sanitized if sanitized else "Untitled"


def ensure_mobile_url(url: str) -> str:
    """Ensures URL has sticky_frame parameter for mobile view."""
    if not url.startswith("http"):
        url = urllib.parse.urljoin(ROOT_PAGE, url)
    parsed = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed.query)
    if "__sticky_frame" not in query_params:
        query_params["__sticky_frame"] = ["1"]
    new_query = urllib.parse.urlencode(query_params, doseq=True)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def get_quality_score(ext: str, qlty: str, button_text: str) -> int:
    """Ranks quality options from highest to lowest."""
    ext = (ext or "").lower()
    qlty = (qlty or "").lower()
    btn = (button_text or "").lower()

    # FLAC lossless (Highest)
    if ext == "flac" or "flac" in qlty or "flac" in btn:
        return 100

    # ALAC Apple Lossless (Highest/Lossless)
    if (ext == "m4a" and ("alac" in qlty or "lossless" in btn or "alac" in btn)) or "alac" in btn:
        return 90

    # 320 Kbps
    if "320" in qlty or "320" in btn:
        return 80

    # 256 Kbps / High m4a
    if "256" in qlty or "256" in btn or ext == "m4a":
        return 70

    # 128 Kbps
    if "128" in qlty or "128" in btn:
        return 60

    # MP3 default / other
    if ext == "mp3":
        return 50

    return 10


def get_highest_quality_download_url(song_page_url: str, session: requests.Session = None) -> tuple[Optional[str], Optional[str]]:
    """
    Parses song page, finds download forms, picks highest quality option,
    and resolves the direct CDN download URL.
    Returns (direct_download_url, quality_description).
    """
    s = session or create_resilient_session()
    req_url = ensure_mobile_url(song_page_url)
    try:
        resp = s.get(req_url, headers=HEADERS, timeout=30)
    except Exception as e:
        logger.error(f"Failed to fetch song page {song_page_url}: {e}")
        return None, None

    if resp.status_code != 200:
        logger.warning(f"Failed to fetch song page: {song_page_url} (status: {resp.status_code})")
        return None, None

    soup = BeautifulSoup(resp.content, "html.parser")
    forms = soup.find_all("form")

    best_score = -1
    best_form_params = None
    best_desc = "Unknown"

    for form in forms:
        action = form.get("action", "")
        if "files.php" not in action:
            continue

        params = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                params[name] = inp.get("value", "")

        btn = form.find("button")
        btn_text = " ".join(btn.text.split()) if btn else ""
        ext = params.get("ext", "")
        qlty = params.get("qlty", "")

        score = get_quality_score(ext, qlty, btn_text)
        if score > best_score:
            best_score = score
            best_form_params = params
            best_desc = btn_text or f"{ext} {qlty}"

    if not best_form_params:
        logger.warning(f"No download forms found on {song_page_url}")
        return None, None

    # Request files.php to get redirect Location
    files_url = urllib.parse.urljoin(ROOT_PAGE, "files.php")
    try:
        redir_resp = s.get(files_url, params=best_form_params, headers=HEADERS, allow_redirects=False, timeout=30)
    except Exception as e:
        logger.error(f"Failed to resolve download redirect on {files_url}: {e}")
        return None, None

    direct_url = redir_resp.headers.get("Location")
    if not direct_url and redir_resp.status_code == 200:
        # Check if direct url is inside HTML
        redir_soup = BeautifulSoup(redir_resp.content, "html.parser")
        link = redir_soup.find("a", href=True)
        if link:
            direct_url = link["href"]

    if direct_url and not direct_url.startswith("http"):
        direct_url = urllib.parse.urljoin(ROOT_PAGE, direct_url)

    return direct_url, best_desc


def download_file_stream(url: str, destination_dir: str, default_filename: str = None, session: requests.Session = None) -> Optional[str]:
    """Streams and saves audio file to destination directory."""
    s = session or create_resilient_session()
    Path(destination_dir).mkdir(parents=True, exist_ok=True)
    temp_filepath = None

    try:
        with s.get(url, headers=HEADERS, stream=True, timeout=30) as resp:
            if resp.status_code != 200:
                logger.error(f"Failed to stream download: {url} (status: {resp.status_code})")
                return None

            # Determine filename
            filename = default_filename
            content_disposition = resp.headers.get("Content-Disposition", "")
            if "filename=" in content_disposition:
                fname_match = re.findall(r'filename=["\']?([^"\';]+)["\']?', content_disposition)
                if fname_match:
                    filename = fname_match[0]

            if not filename:
                parsed_path = urllib.parse.urlparse(url).path
                filename = urllib.parse.unquote(parsed_path.split("/")[-1])

            if not filename:
                filename = "song.mp3"

            filename = sanitize_filename(filename)
            filepath = os.path.join(destination_dir, filename)

            total_size = int(resp.headers.get("Content-Length", 0))

            # Skip if complete file already exists
            if os.path.exists(filepath):
                existing_size = os.path.getsize(filepath)
                if (total_size > 0 and existing_size == total_size) or (total_size == 0 and existing_size > 0):
                    logger.info(f"  [Already downloaded] {filename} ({existing_size / (1024 * 1024):.2f} MB)")
                    return filepath

            temp_filepath = filepath + ".part"
            size_str = f" ({total_size / (1024 * 1024):.2f} MB)" if total_size > 0 else ""
            logger.info(f"  [Downloading] {filename}{size_str}...")

            with open(temp_filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)

            # Move completed download into final filename atomically
            os.replace(temp_filepath, filepath)
            logger.info(f"  [Complete] Saved to {filepath}")
            return filepath
    except Exception as e:
        logger.error(f"Error downloading {url}: {e}")
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except OSError:
                pass
        return None


def download_song(song_page_url: str, output_dir: str = DEFAULT_DOWNLOAD_PATH, session: requests.Session = None) -> Optional[str]:
    """Downloads a single song with highest available quality (synchronous)."""
    direct_url, quality_desc = get_highest_quality_download_url(song_page_url, session=session)
    if not direct_url:
        logger.warning(f"Could not resolve direct download URL for {song_page_url}")
        return None

    logger.info(f"Found highest quality ({quality_desc}) for {song_page_url}")
    return download_file_stream(direct_url, output_dir, session=session)


async def async_download_song(
    song_page_url: str,
    output_dir: str = DEFAULT_DOWNLOAD_PATH,
    session: requests.Session = None,
    semaphore: asyncio.Semaphore = None,
) -> Optional[str]:
    """Asynchronously downloads a single song with highest available quality."""
    if semaphore:
        async with semaphore:
            return await asyncio.to_thread(download_song, song_page_url, output_dir, session)
    return await asyncio.to_thread(download_song, song_page_url, output_dir, session)


async def async_download_songs_batch(
    song_urls: List[str],
    output_dir: str,
    session: requests.Session = None,
    semaphore: asyncio.Semaphore = None,
) -> List[Optional[str]]:
    """Downloads a batch of song URLs concurrently."""
    tasks = [
        async_download_song(song_url, output_dir, session=session, semaphore=semaphore)
        for song_url in song_urls
    ]
    return await asyncio.gather(*tasks, return_exceptions=False)


def fetch_album_info(album_url: str, session: requests.Session = None) -> Tuple[str, List[str]]:
    """Fetches album title and list of song URLs across all pages of the album."""
    s = session or create_resilient_session()
    clean_album_base = album_url.split("?")[0].rstrip("/")
    if not clean_album_base.startswith("http"):
        clean_album_base = urllib.parse.urljoin(ROOT_PAGE, clean_album_base)

    first_page_url = ensure_mobile_url(clean_album_base)
    try:
        resp = s.get(first_page_url, headers=HEADERS, timeout=30)
    except Exception as e:
        logger.error(f"Failed to open album {album_url}: {e}")
        return "Album", []

    if resp.status_code != 200:
        logger.warning(f"Failed to open album {album_url} (status: {resp.status_code})")
        return "Album", []

    soup = BeautifulSoup(resp.content, "html.parser")
    header_tag = soup.find("div", class_="header")
    album_title = header_tag.text.strip() if header_tag else (soup.title.text if soup.title else "Album")
    album_title = sanitize_filename(album_title)

    song_urls: List[str] = []
    page_num = 1
    while True:
        page_url = clean_album_base if page_num == 1 else f"{clean_album_base}-{page_num}"
        try:
            p_resp = s.get(ensure_mobile_url(page_url), headers=HEADERS, timeout=30)
        except Exception as e:
            logger.error(f"Failed to load album page {page_url}: {e}")
            break

        if p_resp.status_code != 200:
            break

        p_soup = BeautifulSoup(p_resp.content, "html.parser")
        new_songs_found = 0
        for a in p_soup.find_all("a"):
            href = a.get("href", "").split("?")[0].strip()
            if SONG_PATTERN.match(href):
                full_song_url = urllib.parse.urljoin(ROOT_PAGE, href)
                if full_song_url not in song_urls:
                    song_urls.append(full_song_url)
                    new_songs_found += 1

        if new_songs_found == 0:
            break
        page_num += 1

    return album_title, song_urls


async def async_download_album(
    album_url: str,
    output_dir: str = DEFAULT_DOWNLOAD_PATH,
    session: requests.Session = None,
    song_batch_size: int = DEFAULT_SONG_BATCH_SIZE,
    max_song_concurrency: int = MAX_SONG_WORKERS,
    album_semaphore: asyncio.Semaphore = None,
) -> int:
    """
    Asynchronously processes an album, fetching song URLs and downloading songs in batches.
    Returns the count of successfully downloaded/verified songs.
    """
    async def _process_album():
        s = session or create_resilient_session()
        album_title, song_urls = await asyncio.to_thread(fetch_album_info, album_url, s)

        album_dir = os.path.join(output_dir, album_title)
        logger.info(f"Processing Album: '{album_title}' ({len(song_urls)} songs) -> {album_dir}")

        if not song_urls:
            logger.warning(f"No songs found for album: {album_url}")
            return 0

        # Skip entire album if all songs are already downloaded
        if os.path.isdir(album_dir):
            existing_audio_files = [
                f for f in os.listdir(album_dir)
                if f.lower().endswith(AUDIO_EXTENSIONS)
                and not f.endswith(".part")
                and not f.endswith(".tmp")
                and os.path.isfile(os.path.join(album_dir, f))
                and os.path.getsize(os.path.join(album_dir, f)) > 0
            ]
            if len(existing_audio_files) >= len(song_urls):
                logger.info(f"  [Album already downloaded] All {len(song_urls)} songs present in '{album_dir}'. Skipping.")
                return len(existing_audio_files)

        # Batch songs within the album
        song_batches = [
            song_urls[i : i + song_batch_size]
            for i in range(0, len(song_urls), song_batch_size)
        ]
        total_batches = len(song_batches)
        song_semaphore = asyncio.Semaphore(max_song_concurrency)

        downloaded_count = 0
        for b_idx, batch in enumerate(song_batches, start=1):
            logger.info(f"Album '{album_title}': Song Batch {b_idx}/{total_batches} ({len(batch)} songs)...")
            results = await async_download_songs_batch(
                batch, output_dir=album_dir, session=s, semaphore=song_semaphore
            )
            downloaded_count += sum(1 for r in results if r is not None)

        logger.info(f"Finished Album '{album_title}': {downloaded_count}/{len(song_urls)} songs completed.")
        return downloaded_count

    if album_semaphore:
        async with album_semaphore:
            return await _process_album()
    return await _process_album()


def download_album(
    album_url: str,
    output_dir: str = DEFAULT_DOWNLOAD_PATH,
    session: requests.Session = None,
    song_batch_size: int = DEFAULT_SONG_BATCH_SIZE,
    max_song_concurrency: int = MAX_SONG_WORKERS,
) -> int:
    """Synchronous wrapper for downloading an album."""
    return asyncio.run(
        async_download_album(
            album_url,
            output_dir=output_dir,
            session=session,
            song_batch_size=song_batch_size,
            max_song_concurrency=max_song_concurrency,
        )
    )


def fetch_index_page_count(index_char: str, session: requests.Session = None, max_pages: int = None) -> int:
    """Determines the total number of pages under a specific index letter/number."""
    s = session or create_resilient_session()
    first_index_url = ensure_mobile_url(f"{ROOT_PAGE}{index_char}")
    try:
        resp = s.get(first_index_url, headers=HEADERS, timeout=30)
    except Exception as e:
        logger.error(f"Could not load index {index_char}: {e}")
        return 0

    if resp.status_code != 200:
        logger.warning(f"Could not load index {index_char} (status: {resp.status_code})")
        return 0

    soup = BeautifulSoup(resp.content, "html.parser")
    page_nums = [1]
    for a in soup.find_all("a"):
        href = a.get("href", "").split("?")[0].strip()
        m = re.match(r"^" + re.escape(index_char) + r"-(\d+)$", href)
        if m:
            page_nums.append(int(m.group(1)))

    total_pages = max(page_nums)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    return total_pages


def fetch_index_page_albums(page_url: str, session: requests.Session = None) -> List[str]:
    """Fetches all album URLs from an index page."""
    s = session or create_resilient_session()
    try:
        p_resp = s.get(ensure_mobile_url(page_url), headers=HEADERS, timeout=30)
    except Exception as e:
        logger.error(f"Failed to load index page {page_url}: {e}")
        return []

    if p_resp.status_code != 200:
        return []

    p_soup = BeautifulSoup(p_resp.content, "html.parser")
    albums = []
    for a in p_soup.find_all("a"):
        href = a.get("href", "").split("?")[0].strip()
        if ALBUM_PATTERN.match(href):
            full_album_url = urllib.parse.urljoin(ROOT_PAGE, href)
            if full_album_url not in albums:
                albums.append(full_album_url)
    return albums


async def async_download_albums_batch(
    album_urls: List[str],
    output_dir: str,
    session: requests.Session = None,
    song_batch_size: int = DEFAULT_SONG_BATCH_SIZE,
    max_song_concurrency: int = MAX_SONG_WORKERS,
    album_semaphore: asyncio.Semaphore = None,
) -> List[int]:
    """Downloads a batch of albums concurrently."""
    tasks = [
        async_download_album(
            album_url,
            output_dir=output_dir,
            session=session,
            song_batch_size=song_batch_size,
            max_song_concurrency=max_song_concurrency,
            album_semaphore=album_semaphore,
        )
        for album_url in album_urls
    ]
    return await asyncio.gather(*tasks, return_exceptions=False)


async def async_download_index(
    index_char: str,
    output_dir: str = DEFAULT_DOWNLOAD_PATH,
    session: requests.Session = None,
    max_pages: int = None,
    album_batch_size: int = DEFAULT_ALBUM_BATCH_SIZE,
    max_album_concurrency: int = MAX_ALBUM_WORKERS,
    song_batch_size: int = DEFAULT_SONG_BATCH_SIZE,
    max_song_concurrency: int = MAX_SONG_WORKERS,
    index_semaphore: asyncio.Semaphore = None,
) -> int:
    """Asynchronously downloads all albums and songs in an index char in concurrent batches."""
    async def _process_index():
        s = session or create_resilient_session()
        logger.info(f"=== Starting Index: '{index_char}' ===")
        total_pages = await asyncio.to_thread(fetch_index_page_count, index_char, s, max_pages)
        if total_pages == 0:
            logger.warning(f"No pages found for index: {index_char}")
            return 0

        logger.info(f"Index '{index_char}': Found {total_pages} page(s).")
        album_semaphore = asyncio.Semaphore(max_album_concurrency)
        total_songs_downloaded = 0

        for page in range(1, total_pages + 1):
            page_slug = index_char if page == 1 else f"{index_char}-{page}"
            page_url = f"{ROOT_PAGE}{page_slug}"
            logger.info(f"Loading index page {page}/{total_pages}: {page_url}")

            albums = await asyncio.to_thread(fetch_index_page_albums, page_url, s)
            logger.info(f"Index '{index_char}' Page {page}: Discovered {len(albums)} album(s).")

            if not albums:
                continue

            album_batches = [
                albums[i : i + album_batch_size]
                for i in range(0, len(albums), album_batch_size)
            ]
            total_album_batches = len(album_batches)

            for ab_idx, album_batch in enumerate(album_batches, start=1):
                logger.info(
                    f"Index '{index_char}' Page {page}: Processing Album Batch {ab_idx}/{total_album_batches} ({len(album_batch)} albums)..."
                )
                batch_results = await async_download_albums_batch(
                    album_batch,
                    output_dir=output_dir,
                    session=s,
                    song_batch_size=song_batch_size,
                    max_song_concurrency=max_song_concurrency,
                    album_semaphore=album_semaphore,
                )
                total_songs_downloaded += sum(batch_results)

        logger.info(f"=== Completed Index: '{index_char}' | Total Songs Processed: {total_songs_downloaded} ===")
        return total_songs_downloaded

    if index_semaphore:
        async with index_semaphore:
            return await _process_index()
    return await _process_index()


def download_index(
    index_char: str,
    output_dir: str = DEFAULT_DOWNLOAD_PATH,
    session: requests.Session = None,
    max_pages: int = None,
    album_batch_size: int = DEFAULT_ALBUM_BATCH_SIZE,
    max_album_concurrency: int = MAX_ALBUM_WORKERS,
    song_batch_size: int = DEFAULT_SONG_BATCH_SIZE,
    max_song_concurrency: int = MAX_SONG_WORKERS,
) -> int:
    """Synchronous wrapper for downloading an index."""
    return asyncio.run(
        async_download_index(
            index_char,
            output_dir=output_dir,
            session=session,
            max_pages=max_pages,
            album_batch_size=album_batch_size,
            max_album_concurrency=max_album_concurrency,
            song_batch_size=song_batch_size,
            max_song_concurrency=max_song_concurrency,
        )
    )


async def async_download_file(
    output_dir: str = DEFAULT_DOWNLOAD_PATH,
    indexes: List[str] = None,
    index_batch_size: int = DEFAULT_INDEX_BATCH_SIZE,
    max_index_concurrency: int = MAX_INDEX_WORKERS,
    album_batch_size: int = DEFAULT_ALBUM_BATCH_SIZE,
    max_album_concurrency: int = MAX_ALBUM_WORKERS,
    song_batch_size: int = DEFAULT_SONG_BATCH_SIZE,
    max_song_concurrency: int = MAX_SONG_WORKERS,
    max_pages_per_index: int = None,
) -> int:
    """
    Main asynchronous downloader running across all indexes, albums, and songs in batches.
    Returns total songs processed.
    """
    setup_logger()
    target_indexes = indexes or INDEXING
    session = create_resilient_session()
    start_time = time.time()

    logger.info(
        f"Starting async batch music download into: '{output_dir}' across {len(target_indexes)} index(es) "
        f"(max_index_concurrency={max_index_concurrency}, max_album_concurrency={max_album_concurrency}, "
        f"max_song_concurrency={max_song_concurrency})"
    )

    index_semaphore = asyncio.Semaphore(max_index_concurrency)
    index_batches = [
        target_indexes[i : i + index_batch_size]
        for i in range(0, len(target_indexes), index_batch_size)
    ]
    total_index_batches = len(index_batches)

    total_songs = 0
    for idx_b_num, idx_batch in enumerate(index_batches, start=1):
        logger.info(f"Processing Index Batch {idx_b_num}/{total_index_batches}: {idx_batch}")
        tasks = [
            async_download_index(
                index_char,
                output_dir=output_dir,
                session=session,
                max_pages=max_pages_per_index,
                album_batch_size=album_batch_size,
                max_album_concurrency=max_album_concurrency,
                song_batch_size=song_batch_size,
                max_song_concurrency=max_song_concurrency,
                index_semaphore=index_semaphore,
            )
            for index_char in idx_batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        total_songs += sum(results)

    elapsed_time = max(time.time() - start_time, 0.001)
    logger.info(
        f"Async music download completed in {elapsed_time:.2f}s | Total Songs Processed: {total_songs}"
    )
    return total_songs


def download_file(
    output_dir: str = DEFAULT_DOWNLOAD_PATH,
    indexes: List[str] = None,
    index_batch_size: int = DEFAULT_INDEX_BATCH_SIZE,
    max_index_concurrency: int = MAX_INDEX_WORKERS,
    album_batch_size: int = DEFAULT_ALBUM_BATCH_SIZE,
    max_album_concurrency: int = MAX_ALBUM_WORKERS,
    song_batch_size: int = DEFAULT_SONG_BATCH_SIZE,
    max_song_concurrency: int = MAX_SONG_WORKERS,
    max_pages_per_index: int = None,
) -> int:
    """Synchronous entry point that runs the full async batch download workflow."""
    return asyncio.run(
        async_download_file(
            output_dir=output_dir,
            indexes=indexes,
            index_batch_size=index_batch_size,
            max_index_concurrency=max_index_concurrency,
            album_batch_size=album_batch_size,
            max_album_concurrency=max_album_concurrency,
            song_batch_size=song_batch_size,
            max_song_concurrency=max_song_concurrency,
            max_pages_per_index=max_pages_per_index,
        )
    )


if __name__ == "__main__":
    setup_logger()
    download_file()

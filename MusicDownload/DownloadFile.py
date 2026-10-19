import os
import re
import string
import urllib.parse
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

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


def create_resilient_session() -> requests.Session:
    """Creates a requests session configured with retries for network resilience."""
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
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


def get_highest_quality_download_url(song_page_url: str, session: requests.Session = None) -> tuple[str | None, str | None]:
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
        print(f"Failed to fetch song page {song_page_url}: {e}")
        return None, None

    if resp.status_code != 200:
        print(f"Failed to fetch song page: {song_page_url} (status: {resp.status_code})")
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
        print(f"No download forms found on {song_page_url}")
        return None, None

    # Request files.php to get redirect Location
    files_url = urllib.parse.urljoin(ROOT_PAGE, "files.php")
    try:
        redir_resp = s.get(files_url, params=best_form_params, headers=HEADERS, allow_redirects=False, timeout=30)
    except Exception as e:
        print(f"Failed to resolve download redirect on {files_url}: {e}")
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


def download_file_stream(url: str, destination_dir: str, default_filename: str = None, session: requests.Session = None) -> str | None:
    """Streams and saves audio file to destination directory."""
    s = session or create_resilient_session()
    Path(destination_dir).mkdir(parents=True, exist_ok=True)
    temp_filepath = None

    try:
        with s.get(url, headers=HEADERS, stream=True, timeout=30) as resp:
            if resp.status_code != 200:
                print(f"Failed to stream download: {url} (status: {resp.status_code})")
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
                    print(f"  [Already downloaded] {filename} ({existing_size / (1024 * 1024):.2f} MB)")
                    return filepath

            temp_filepath = filepath + ".part"
            size_str = f" ({total_size / (1024 * 1024):.2f} MB)" if total_size > 0 else ""
            print(f"  [Downloading] {filename}{size_str}...")

            with open(temp_filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)

            # Move completed download into final filename atomically
            os.replace(temp_filepath, filepath)
            print(f"  [Complete] Saved to {filepath}")
            return filepath
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except OSError:
                pass
        return None


def download_song(song_page_url: str, output_dir: str = DEFAULT_DOWNLOAD_PATH, session: requests.Session = None) -> str | None:
    """Downloads a single song with highest available quality."""
    direct_url, quality_desc = get_highest_quality_download_url(song_page_url, session=session)
    if not direct_url:
        print(f"Could not resolve direct download URL for {song_page_url}")
        return None

    print(f"Found highest quality: {quality_desc}")
    return download_file_stream(direct_url, output_dir, session=session)


def download_album(album_url: str, output_dir: str = DEFAULT_DOWNLOAD_PATH, session: requests.Session = None):
    """Downloads all songs from an album at highest quality."""
    s = session or create_resilient_session()
    clean_album_base = album_url.split("?")[0].rstrip("/")
    if not clean_album_base.startswith("http"):
        clean_album_base = urllib.parse.urljoin(ROOT_PAGE, clean_album_base)

    # 1. Fetch first page to get title and songs
    first_page_url = ensure_mobile_url(clean_album_base)
    try:
        resp = s.get(first_page_url, headers=HEADERS, timeout=30)
    except Exception as e:
        print(f"Failed to open album {album_url}: {e}")
        return

    if resp.status_code != 200:
        print(f"Failed to open album {album_url}")
        return

    soup = BeautifulSoup(resp.content, "html.parser")
    header_tag = soup.find("div", class_="header")
    album_title = header_tag.text.strip() if header_tag else (soup.title.text if soup.title else "Album")
    album_title = sanitize_filename(album_title)

    album_dir = os.path.join(output_dir, album_title)
    print(f"\nProcessing Album: {album_title}")
    print(f"Destination: {album_dir}")

    # 2. Collect all song URLs across all pages of the album
    song_urls = []
    page_num = 1
    while True:
        page_url = clean_album_base if page_num == 1 else f"{clean_album_base}-{page_num}"
        try:
            p_resp = s.get(ensure_mobile_url(page_url), headers=HEADERS, timeout=30)
        except Exception as e:
            print(f"Failed to load album page {page_url}: {e}")
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

    print(f"Found {len(song_urls)} song(s) in album.")

    # 3. Skip entire album if all songs are already downloaded
    if os.path.isdir(album_dir) and song_urls:
        existing_audio_files = [
            f for f in os.listdir(album_dir)
            if f.lower().endswith(AUDIO_EXTENSIONS)
            and not f.endswith(".part")
            and not f.endswith(".tmp")
            and os.path.isfile(os.path.join(album_dir, f))
            and os.path.getsize(os.path.join(album_dir, f)) > 0
        ]
        if len(existing_audio_files) >= len(song_urls):
            print(f"  [Album already downloaded] All {len(song_urls)} songs present in {album_dir}. Skipping album.\n")
            return

    for idx, song_url in enumerate(song_urls, 1):
        print(f"Song {idx}/{len(song_urls)}: {song_url}")
        download_song(song_url, output_dir=album_dir, session=s)


def download_index(index_char: str, output_dir: str = DEFAULT_DOWNLOAD_PATH, session: requests.Session = None, max_pages: int = None):
    """Downloads all albums under a specific index letter/number."""
    s = session or create_resilient_session()
    print(f"\n=== Index: {index_char} ===")

    # Get max page count
    first_index_url = ensure_mobile_url(f"{ROOT_PAGE}{index_char}")
    try:
        resp = s.get(first_index_url, headers=HEADERS, timeout=30)
    except Exception as e:
        print(f"Could not load index {index_char}: {e}")
        return

    if resp.status_code != 200:
        print(f"Could not load index {index_char}")
        return

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

    print(f"Total pages for '{index_char}': {total_pages}")

    for page in range(1, total_pages + 1):
        page_slug = index_char if page == 1 else f"{index_char}-{page}"
        page_url = ensure_mobile_url(f"{ROOT_PAGE}{page_slug}")
        print(f"\n--- Loading index page {page}/{total_pages}: {page_url} ---")

        try:
            p_resp = s.get(page_url, headers=HEADERS, timeout=30)
        except Exception as e:
            print(f"Failed to load index page {page_url}: {e}")
            continue

        if p_resp.status_code != 200:
            continue

        p_soup = BeautifulSoup(p_resp.content, "html.parser")
        albums = []
        for a in p_soup.find_all("a"):
            href = a.get("href", "").split("?")[0].strip()
            if ALBUM_PATTERN.match(href):
                full_album_url = urllib.parse.urljoin(ROOT_PAGE, href)
                if full_album_url not in albums:
                    albums.append(full_album_url)

        print(f"Found {len(albums)} album(s) on page {page}.")
        for album_url in albums:
            download_album(album_url, output_dir=output_dir, session=s)


def download_file(output_dir: str = DEFAULT_DOWNLOAD_PATH):
    """Iterates through all indexes and downloads all albums/songs with highest quality."""
    session = create_resilient_session()
    print(f"Starting highest quality music download into: {output_dir}")
    for index in INDEXING:
        download_index(index, output_dir=output_dir, session=session)


if __name__ == "__main__":
    download_file()

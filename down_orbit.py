#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sync ASF AUX_POEORB directory with Earthdata Login (EDL) authentication.
- First run: downloads all matching files (default: *.EOF)
- Next runs: only downloads files that are new or have changed size
- Preserves server Last-Modified time on local files
- Auth via --user/--password OR EARTHDATA_USERNAME/EARTHDATA_PASSWORD envs OR ~/.netrc
- Robust to EDL redirects (manual hop-following; only sends auth on auth hosts)
"""

import argparse
import os
import re
import sys
import time
import json
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from email.utils import parsedate_to_datetime

import requests
from requests.auth import HTTPBasicAuth

BASE_URL = "https://s1qc.asf.alaska.edu/aux_poeorb/"
DEFAULT_PATTERN = r".*\.EOF$"
MANIFEST_NAME = ".sync_manifest.json"
CHUNK_SIZE = 1024 * 1024  # 1 MiB
TIMEOUT = 90

# EDL / ASF auth hosts where credentials are required
EDL_HOSTS = {
    "urs.earthdata.nasa.gov",
    "auth.asf.alaska.edu",
}

# --------------------- HTML directory listing parser ---------------------
class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

# --------------------- Session / Auth helpers ---------------------
def resolve_creds(cli_user: str | None, cli_pass: str | None):
    """Resolve creds from CLI, then env. .netrc is handled by requests automatically."""
    user = cli_user or os.environ.get("EARTHDATA_USERNAME")
    pwd  = cli_pass or os.environ.get("EARTHDATA_PASSWORD")
    return user, pwd

def build_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "aux-poeorb-sync/1.3 (+https://asf.alaska.edu)",
        "Accept": "*/*",
        "Accept-Encoding": "identity",  # avoid gzip confusing ranged size
    })
    s.trust_env = True  # respect proxies/.netrc if present
    return s

def same_origin(host: str, hostset: set[str]) -> bool:
    return any(h == host or host.endswith("." + h) for h in hostset)

def get_with_edl(
    session: requests.Session,
    url: str,
    username: str | None,
    password: str | None,
    *,
    headers: dict | None = None,
    stream: bool = False,
    max_hops: int = 15,
) -> requests.Response:
    """
    Manually follow redirects. Only present HTTP BasicAuth when the current hop's host
    is an Earthdata/ASF auth host. Carries cookies between hops. Returns the final Response.
    """
    headers = headers or {}
    current = url
    auth = HTTPBasicAuth(username, password) if (username and password) else None

    for _ in range(max_hops):
        host = urlparse(current).hostname or ""
        use_auth = auth if same_origin(host, EDL_HOSTS) else None

        r = session.get(current, allow_redirects=False, timeout=TIMEOUT, stream=stream, headers=headers, auth=use_auth)
        status = r.status_code

        if status in (200, 206):
            return r

        if status in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location")
            if not loc:
                r.raise_for_status()
            nxt = urljoin(current, loc)
            try:
                r.close()
            except Exception:
                pass
            current = nxt
            continue

        # If 401 at auth host and no creds were sent, retry once with creds
        if status == 401 and same_origin(host, EDL_HOSTS) and use_auth is None and auth is not None:
            try:
                r.close()
            except Exception:
                pass
            r = session.get(current, allow_redirects=False, timeout=TIMEOUT, stream=stream, headers=headers, auth=auth)
            if r.status_code in (200, 206):
                return r
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location")
                if not loc:
                    r.raise_for_status()
                nxt = urljoin(current, loc)
                try:
                    r.close()
                except Exception:
                    pass
                current = nxt
                continue
            r.raise_for_status()

        r.raise_for_status()

    raise RuntimeError("Exceeded maximum EDL redirect hops")

# --------------------- Remote listing / metadata ---------------------
def list_remote_files(session, base_url, pattern, username, password):
    r = get_with_edl(session, base_url, username, password, stream=False)
    html = r.text
    r.close()

    parser = LinkExtractor()
    parser.feed(html)

    rx = re.compile(pattern)
    items = []
    for href in parser.links:
        if href in ("../", "/"):
            continue
        if href.endswith("/"):
            continue
        name = href
        url = urljoin(base_url, href)
        if rx.match(name):
            items.append((name, url))

    # de-dup + sort
    return sorted(set(items), key=lambda x: x[0])

def probe_size_mtime(session, url, username, password):
    """
    Use Range: bytes=0-0 to get total size from Content-Range and capture Last-Modified.
    Returns dict: {"content_length": int|None, "last_modified": int|None}
    """
    info = {"content_length": None, "last_modified": None}
    # First try ranged GET
    headers = {"Range": "bytes=0-0", "Accept-Encoding": "identity"}
    r = get_with_edl(session, url, username, password, headers=headers, stream=True)
    try:
        # Accept either 206 (partial) or 200 (some servers ignore Range)
        cr = r.headers.get("Content-Range")
        if cr and "/" in cr:
            try:
                info["content_length"] = int(cr.split("/")[-1])
            except Exception:
                pass

        if info["content_length"] is None:
            cl = r.headers.get("Content-Length")
            if cl:
                try:
                    cl_int = int(cl)
                    if cl_int > 1:  # if server honored range, CL might be 1
                        info["content_length"] = cl_int
                except Exception:
                    pass

        lm = r.headers.get("Last-Modified")
        if lm:
            try:
                info["last_modified"] = int(parsedate_to_datetime(lm).timestamp())
            except Exception:
                pass
    finally:
        try:
            r.close()
        except Exception:
            pass

    # Last fallback: plain GET headers (no body read)
    if info["content_length"] is None or info["last_modified"] is None:
        r2 = get_with_edl(session, url, username, password, stream=True)
        try:
            if info["content_length"] is None:
                cl = r2.headers.get("Content-Length")
                if cl:
                    try:
                        info["content_length"] = int(cl)
                    except Exception:
                        pass
            if info["last_modified"] is None:
                lm = r2.headers.get("Last-Modified")
                if lm:
                    try:
                        info["last_modified"] = int(parsedate_to_datetime(lm).timestamp())
                    except Exception:
                        pass
        finally:
            try:
                r2.close()
            except Exception:
                pass

    return info

# --------------------- Local helpers ---------------------
def ensure_dir(path): os.makedirs(path, exist_ok=True)

def load_manifest(dest_dir):
    path = os.path.join(dest_dir, MANIFEST_NAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_manifest(dest_dir, data):
    path = os.path.join(dest_dir, MANIFEST_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except Exception as e:
        sys.stderr.write(f"WARNING: couldn't save manifest: {e}\n")

def needs_download(local_path, expected_size):
    if not os.path.exists(local_path):
        return True
    if expected_size is None:
        # if we don't know, conservatively skip (assume it's already present)
        return False
    try:
        return os.path.getsize(local_path) != expected_size
    except Exception:
        return True

def set_mtime(local_path, epoch):
    if epoch is None:
        return
    try:
        os.utime(local_path, (epoch, epoch))
    except Exception:
        pass

# --------------------- Downloader ---------------------
def download_file(session, url, dest_path, expected_size, username, password):
    tmp_path = dest_path + ".part"
    bytes_written = 0
    r = get_with_edl(session, url, username, password, stream=True)
    try:
        r.raise_for_status()
        with open(tmp_path, "wb") as out:
            for chunk in r.iter_content(CHUNK_SIZE):
                if chunk:
                    out.write(chunk)
                    bytes_written += len(chunk)
    finally:
        try:
            r.close()
        except Exception:
            pass

    if expected_size is not None and bytes_written != expected_size:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise RuntimeError(f"Incomplete download: got {bytes_written}, expected {expected_size}")

    os.replace(tmp_path, dest_path)
    # Return Last-Modified for final timestamping
    lm = r.headers.get("Last-Modified")
    return lm

# --------------------- Main ---------------------
def main():
    ap = argparse.ArgumentParser(description="Sync ASF AUX_POEORB (EDL auth; incremental).")
    ap.add_argument("--url", default=BASE_URL, help=f"Listing URL (default: {BASE_URL})")
    ap.add_argument("--dest", default="aux_poeorb", help="Destination directory")
    ap.add_argument("--pattern", default=DEFAULT_PATTERN, help="Regex to select filenames (default: *.EOF)")
    ap.add_argument("--dry-run", action="store_true", help="List planned downloads without saving files")
    ap.add_argument("--user", help="Earthdata username (else uses env or ~/.netrc)")
    ap.add_argument("--password", help="Earthdata password (else uses env or ~/.netrc)")
    ap.add_argument("--verbose", action="store_true", help="Print extra info")
    args = ap.parse_args()

    username, password = resolve_creds(args.user, args.password)
    if args.verbose:
        print(f"Using creds: {'CLI/env' if (username and password) else '~/.netrc or challenge-based'}")

    session = build_session()
    ensure_dir(args.dest)
    manifest = load_manifest(args.dest)
    manifest.setdefault("files", {})

    print(f"Listing: {args.url}")
    items = list_remote_files(session, args.url, args.pattern, username, password)
    if not items:
        print("No matching files found (check pattern or credentials).")
        return

    to_download = []
    for name, url in items:
        info = probe_size_mtime(session, url, username, password)
        local_path = os.path.join(args.dest, name)
        if needs_download(local_path, info["content_length"]):
            to_download.append((name, url, info))
        elif args.verbose:
            print(f"Up-to-date: {name}")

    if not to_download:
        print("Everything is up to date. No downloads needed.")
        # update manifest timestamp so you know when you checked
        manifest["last_sync"] = int(time.time())
        save_manifest(args.dest, manifest)
        return

    print(f"{len(to_download)} file(s) to download:")
    for name, _, info in to_download:
        size = info["content_length"]
        size_str = f"{size:,} B" if size is not None else "unknown size"
        print(f"  - {name} ({size_str})")

    if args.dry_run:
        print("\nDry run complete. Nothing downloaded.")
        return

    for name, url, info in to_download:
        local_path = os.path.join(args.dest, name)
        if args.verbose:
            print(f"Downloading {name} -> {local_path}")
        else:
            print(f"Downloading {name} ... ", end="", flush=True)
        try:
            lm_hdr = download_file(session, url, local_path, info["content_length"], username, password)
            # choose a timestamp: prefer header from download, else probe
            lm_epoch = None
            for candidate in (lm_hdr,):
                if candidate:
                    try:
                        lm_epoch = int(parsedate_to_datetime(candidate).timestamp())
                        break
                    except Exception:
                        pass
            if lm_epoch is None:
                lm_epoch = info["last_modified"]
            set_mtime(local_path, lm_epoch)
            manifest["files"][name] = {
                "size": info["content_length"],
                "last_modified": lm_epoch,
                "url": url,
            }
            if not args.verbose:
                print("done.")
        except Exception as e:
            print(f"failed: {e}", file=sys.stderr)

    manifest["last_sync"] = int(time.time())
    save_manifest(args.dest, manifest)
    print("Sync complete.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.", file=sys.stderr)
        sys.exit(130)


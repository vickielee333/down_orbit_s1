# down_orbit.py

## Overview
`down_orbit.py` is a Python script for synchronizing **Sentinel-1 AUX_POEORB** orbit files from the Alaska Satellite Facility (ASF) NASA server:  
<https://s1qc.asf.alaska.edu/aux_poeorb/>

The script authenticates via **NASA Earthdata Login (EDL)** and performs **incremental synchronization**, downloading only new or changed files on subsequent runs.  
It preserves the `Last-Modified` timestamps and metadata for reproducibility.

---

## Features
- **Automatic Earthdata Login (EDL) authentication**
  - Supports credentials via `~/.netrc`, environment variables, or command-line flags
- **Incremental updates**
  - Downloads only files not yet present locally or whose size has changed
- **Preserves metadata**
  - Local file modification time matches the server’s `Last-Modified` header
- **Safe download process**
  - Uses temporary `.part` files to avoid incomplete downloads
- **Flexible pattern matching**
  - Use `--pattern` to limit files by filename or date (regex)
- **Portable and dependency-light**
  - Requires only the `requests` library

---

## Requirements
- Python < 3.10  
- `requests` library (usually preinstalled with Anaconda)

If missing:
```bash
pip install requests
```

---

## Authentication Setup

### Option 1 — `.netrc` file (recommended)
Create or edit `~/.netrc` and restrict permissions:
```bash
chmod 600 ~/.netrc
```

Add the following entries:
```
machine urs.earthdata.nasa.gov
  login YOUR_USERNAME
  password YOUR_PASSWORD
machine auth.asf.alaska.edu
  login YOUR_USERNAME
  password YOUR_PASSWORD
machine s1qc.asf.alaska.edu
  login YOUR_USERNAME
  password YOUR_PASSWORD
```

### Option 2 — Environment variables
```bash
export EARTHDATA_USERNAME=YOUR_USERNAME
export EARTHDATA_PASSWORD=YOUR_PASSWORD
```

### Option 3 — Command-line arguments
```bash
python3 down_orbit.py --user YOUR_USERNAME --password YOUR_PASSWORD
```

---

## Usage

### Basic command
```bash
python3 down_orbit.py --dest ./aux_poeorb
```
- Downloads all `.EOF` orbit files into `./aux_poeorb`
- Creates a local `.sync_manifest.json` to track downloaded files

### Incremental update
```bash
python3 down_orbit.py --dest ./aux_poeorb
```
Only downloads files not already present.

### List files only (no download)
```bash
python3 down_orbit.py --dry-run
```

### Download all file types
```bash
python3 down_orbit.py --pattern ".*"
```

### Filter by year or pattern
Example: only 2025 AUX_POEORB files
```bash
python3 down_orbit.py --pattern ".*2025.*\.EOF$"
```

### Verbose output
```bash
python3 down_orbit.py --verbose
```

---

## Directory Structure
```
aux_poeorb/
│
├── S1A_OPER_AUX_POEORB_OPOD_20250101T120000_V20241231T210000.EOF
├── S1A_OPER_AUX_POEORB_OPOD_20250102T120000_V20250101T210000.EOF
├── ...
└── .sync_manifest.json
```

Manifest example:
```json
{
  "files": {
    "S1A_OPER_AUX_POEORB_OPOD_20250101T120000_V20241231T210000.EOF": {
      "size": 102400,
      "last_modified": 1735689600,
      "url": "https://s1qc.asf.alaska.edu/aux_poeorb/..."
    }
  },
  "last_sync": 1736980000
}
```

---

## Command-line Options

| Argument | Description | Default |
|-----------|--------------|----------|
| `--url` | Base directory URL | `https://s1qc.asf.alaska.edu/aux_poeorb/` |
| `--dest` | Local destination directory | `./aux_poeorb` |
| `--pattern` | Regex filter for filenames | `.*\.EOF$` |
| `--dry-run` | List matching files without downloading | Off |
| `--user` | Earthdata username | (from env or `.netrc`) |
| `--password` | Earthdata password | (from env or `.netrc`) |
| `--verbose` | Display extra logging information | Off |

---

## Example Workflows

### 1. Full initial sync
```bash
python3 down_orbit.py --dest /data/orbit_files
```

### 2. Automated daily update (cron job)
Run daily at 02:00 UTC:
```
0 2 * * * /usr/bin/python3 /path/to/down_orbit.py --dest /data/orbit_files >> /data/orbit_files/sync.log 2>&1
```

### 3. Restrict by year
```bash
python3 down_orbit.py --pattern ".*2024.*\.EOF$"
```

---

## Common Errors and Fixes

| Error Message | Cause | Resolution |
|----------------|--------|-------------|
| `401 Unauthorized` | Missing or incorrect Earthdata credentials | Verify `.netrc` or use `--user/--password` |
| `stuck at Listing:` | EDL redirect loop (authorization issue) | Ensure ASF app authorized in Earthdata profile |
| `Incomplete download` | Network interruption or disk full | Retry; partial files are safely handled |
| `Permission denied` | Destination directory not writable | Run with appropriate user permissions |

---

## License
This software is licensed under the **Creative Commons Attribution 4.0 International License (CC BY 4.0)**.  
You are free to use, share, and adapt the code for any purpose, even commercially, as long as you provide appropriate credit to the author.

© 2025 Jui-Chi (Vickie) Lee. All rights reserved.

For more information about this license, visit:  
<https://creativecommons.org/licenses/by/4.0/>

---

## Developer Notes
- Compatible with Linux, macOS, and WSL
- Handles multi-stage EDL redirects with cookies and selective authentication
- Stores synchronization state in `.sync_manifest.json`
- Recommended improvements:
  - Optional parallel downloads (`--workers N`)
  - Optional checksum verification for archive integrity

---

**Author:**  
Developed by Jui-Chi (Vickie) Lee  
Version 1.0 — October 15 2025

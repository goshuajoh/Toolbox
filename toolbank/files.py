"""File arrival, archive, hashing, newest file, safe names, zip."""
import glob
import hashlib
import os
import re
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

from .robust import SystemError_


def wait_for_file(pattern, timeout_s=600, poll_s=10, stable_s=10, min_mtime=None):
    """Block until a file matching the glob exists, is newer than min_mtime (datetime), and has stopped growing."""
    deadline = time.monotonic() + timeout_s
    last = (None, None, None)
    while time.monotonic() < deadline:
        cands = sorted(glob.glob(str(pattern)), key=lambda p: Path(p).stat().st_mtime, reverse=True)
        if min_mtime is not None:
            cands = [p for p in cands if datetime.fromtimestamp(Path(p).stat().st_mtime) >= min_mtime]
        if cands:
            p, size = cands[0], Path(cands[0]).stat().st_size
            if (p, size) == last[:2] and time.monotonic() - last[2] >= stable_s and size > 0:
                return Path(p)
            if (p, size) != last[:2]:
                last = (p, size, time.monotonic())
        time.sleep(poll_s)
    raise SystemError_(f"no stable file matching {pattern} within {timeout_s}s", code="FILE_TIMEOUT")


def newest_file(pattern):
    """Most recently modified file matching a glob, or None."""
    files = glob.glob(str(pattern))
    return Path(max(files, key=os.path.getmtime)) if files else None


def is_locked(path):
    """True if the file is open/locked by someone else (Windows Excel lock). A missing file is not locked, and
    checking never creates it."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    os.close(fd)
    return False


def archive(path, archive_dir):
    """Move a processed input to archive_dir/YYYYMMDD_name; no-op if already there."""
    path, archive_dir = Path(path), Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{date.today():%Y%m%d}_{path.name}"
    if not target.exists():
        os.replace(path, target)
    return target


def file_sha256(path):
    """SHA-256 of a file (evidence that the input/output was not changed)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_filename(text, max_len=80):
    """Text usable as a Windows file name; a long name is shortened before its extension ('.pdf' is kept)."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text)).strip(" .") or "_"
    if len(clean) <= max_len:
        return clean
    stem, dot, ext = clean.rpartition(".")
    if dot and stem and 0 < len(ext) <= 10 and " " not in ext:
        return stem[: max(1, max_len - len(ext) - 1)].rstrip(" .") + "." + ext
    return clean[:max_len].rstrip(" .") or "_"


def unique_path(folder, name, max_len=80):
    """folder/<safe name> that does not exist yet: report.pdf, report_2.pdf, ... (never overwrite)."""
    folder = Path(folder)
    base = safe_filename(name, max_len)
    p, n = folder / base, 1
    stem, suffix = Path(base).stem, Path(base).suffix
    while p.exists():
        n += 1
        p = folder / f"{stem}_{n}{suffix}"
    return p


def zip_files(paths, zip_path):
    """Zip a list of files (flat) into zip_path."""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, Path(p).name)
    return zip_path


def unzip(zip_path, out_dir):
    """Extract a zip; refuses entries that would escape out_dir. Returns extracted paths."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            target = (out_dir / m.filename).resolve()
            if out_dir not in target.parents and target != out_dir:
                raise ValueError(f"unsafe path in zip: {m.filename}")
            z.extract(m, out_dir)
            if not m.is_dir():
                extracted.append(target)
    return extracted

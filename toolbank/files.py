"""File arrival, archive, hashing, newest file, safe names, zip."""
import glob
import hashlib
import os
import re
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

from .robust import RobotError, SystemError_


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


_WINDOWS = os.name == "nt"


def _win_write_error(path):
    """Windows: 0 if the file opens for writing (others may still read and write), else the Win32 error code."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x40000000, 0x1 | 0x2, None, 3, 0, None)   # GENERIC_WRITE, share r/w, OPEN_EXISTING
    if h == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error()
    k32.CloseHandle(h)
    return 0


def is_locked(path):
    """True if the file is open/locked by someone else (Windows Excel lock). A missing file is not locked, and
    checking never creates it. The check needs modify rights: without them RobotError NO_ACCESS (fix the access,
    do not wait); a folder -> RobotError NOT_A_FILE."""
    p = Path(path)
    if p.is_dir():
        raise RobotError(f"{p.name} is a folder, not a file", code="NOT_A_FILE")
    if _WINDOWS:                                          # os.open reports a sharing violation as plain 'denied'
        err = _win_write_error(p)
        if err in (0, 2, 3):                              # opened, file not found, path not found
            return False
        if err in (32, 33):                               # sharing / lock violation: someone has it open
            return True
        raise RobotError(f"no write access to {p.name} (Windows error {err}): the robot account needs modify "
                         "rights", code="NO_ACCESS")
    try:
        fd = os.open(p, os.O_WRONLY | os.O_APPEND)
    except FileNotFoundError:
        return False
    except PermissionError as e:
        raise RobotError(f"no write access to {p.name}: the robot account needs modify rights",
                         code="NO_ACCESS") from e
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

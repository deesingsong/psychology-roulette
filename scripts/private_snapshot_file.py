"""Create a new UTF-8 file with restrictive permissions before writing data."""

from __future__ import annotations

import csv
import os
import re
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import TextIO


def _restrict_windows_acl(path: Path) -> None:
    identity = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    rows = list(csv.reader(identity.stdout.splitlines()))
    sid = rows[0][-1].strip() if identity.returncode == 0 and rows and rows[0] else ""
    if not re.fullmatch(r"S-1-(?:\d+-)*\d+", sid):
        raise OSError("could not identify the current Windows user")

    acl = subprocess.run(
        [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:(F)",
            "*S-1-5-18:(F)",
            "*S-1-5-32-544:(F)",
        ],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if acl.returncode != 0:
        raise OSError("could not restrict snapshot file access")


def open_private_text_file(path: Path) -> TextIO:
    """Exclusively create ``path`` and restrict access before writing its contents."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name == "nt":
            _restrict_windows_acl(path)
        return os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise

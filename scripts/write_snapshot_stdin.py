"""Validate a snapshot received on stdin and write it to a new protected file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from import_snapshot import TABLES, load
from private_snapshot_file import open_private_text_file


def disable_console_echo() -> None:
    if os.name != "nt" or not sys.stdin.isatty():
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_mode = kernel32.GetConsoleMode
    get_mode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_mode.restype = wintypes.BOOL
    set_mode = kernel32.SetConsoleMode
    set_mode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    set_mode.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    mode = wintypes.DWORD()
    if not get_mode(handle, ctypes.byref(mode)):
        raise OSError("cannot disable terminal input echo")
    if not set_mode(handle, mode.value & ~0x0004):
        raise OSError("cannot disable terminal input echo")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="new protected snapshot file path")
    parser.add_argument("--terminator", required=True, help="final line marking complete input")
    args = parser.parse_args()
    phase = "input"
    try:
        disable_console_echo()
        chunks = []
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                raise ValueError("incomplete input")
            chunk = line.rstrip(b"\r\n")
            if chunk.decode("ascii", errors="ignore") == args.terminator:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        phase = "validation"
        document = load(raw.decode("utf-8"))
        payload = json.dumps(document, separators=(",", ":")) + "\n"
        destination = Path(args.file)
        phase = "file creation"
        try:
            with open_private_text_file(destination) as output:
                output.write(payload)
        except OSError:
            destination.unlink(missing_ok=True)
            raise
    except SystemExit as error:
        reason = error.code if isinstance(error.code, str) else "validation error"
        raise SystemExit(
            f"snapshot transfer failed during {phase} ({reason}); content was not displayed"
        ) from None
    except (OSError, UnicodeDecodeError, ValueError) as error:
        category = type(error).__name__
        code = f", code={error.errno}" if isinstance(error, OSError) else ""
        raise SystemExit(
            f"snapshot transfer failed during {phase} ({category}{code}); content was not displayed"
        ) from None

    counts = ", ".join(f"{table}={len(document[table])}" for table in TABLES)
    print(f"snapshot received and validated ({counts})")


if __name__ == "__main__":
    main()

import os
from contextlib import contextmanager
from pathlib import Path


class BusyError(OSError):
    pass


@contextmanager
def file_lock(path, *, shared=False, blocking=True):
    """Kernel owned lock; Windows LockFileEx supports real shared locks."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            class Overlapped(ctypes.Structure):
                _fields_ = [
                    ("Internal", ctypes.c_size_t),
                    ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE),
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LockFileEx.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(Overlapped),
            ]
            kernel.UnlockFileEx.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(Overlapped),
            ]
            handle = msvcrt.get_osfhandle(stream.fileno())
            overlap = Overlapped()
            flags = (0 if shared else 2) | (0 if blocking else 1)
            if not kernel.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlap)):
                raise BusyError(ctypes.get_last_error(), f"Resource locked: {path}")
            try:
                yield
            finally:
                kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
        else:
            import fcntl

            flags = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(stream.fileno(), flags)
            except OSError as exc:
                raise BusyError(str(path)) from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

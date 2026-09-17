"""Shared Windows process identity and atomic Job association (Windows 10+)."""

import ctypes as c
import msvcrt
import subprocess
from ctypes import wintypes as w

kernel = c.WinDLL("kernel32", use_last_error=True)


class Startup(c.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("xs", w.DWORD),
        ("ys", w.DWORD),
        ("xc", w.DWORD),
        ("yc", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_ptr", c.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class StartupEx(c.Structure):
    _fields_ = [("startup", Startup), ("attributes", c.c_void_p)]


class ProcessInfo(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class Limits(c.Structure):
    _fields_ = [
        ("process_time", c.c_int64),
        ("job_time", c.c_int64),
        ("flags", w.DWORD),
        ("min_ws", c.c_size_t),
        ("max_ws", c.c_size_t),
        ("active", w.DWORD),
        ("affinity", c.c_size_t),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
    ]


class Extended(c.Structure):
    _fields_ = [
        ("basic", Limits),
        ("io", c.c_uint64 * 6),
        ("process_memory", c.c_size_t),
        ("job_memory", c.c_size_t),
        ("peak_process", c.c_size_t),
        ("peak_job", c.c_size_t),
    ]


class Accounting(c.Structure):
    _fields_ = [
        ("times", c.c_int64 * 4),
        ("faults", w.DWORD),
        ("total", w.DWORD),
        ("active", w.DWORD),
        ("terminated", w.DWORD),
    ]


def api(name, result, args):
    function = getattr(kernel, name)
    function.restype, function.argtypes = result, args
    return function


close = api("CloseHandle", w.BOOL, [w.HANDLE])
create_job = api("CreateJobObjectW", w.HANDLE, [c.c_void_p, w.LPCWSTR])
set_job = api("SetInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD])
query_job = api(
    "QueryInformationJobObject", w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]
)
terminate_job = api("TerminateJobObject", w.BOOL, [w.HANDLE, w.UINT])
open_job = api("OpenJobObjectW", w.HANDLE, [w.DWORD, w.BOOL, w.LPCWSTR])
open_process = api("OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD])
get_times = api(
    "GetProcessTimes", w.BOOL, [w.HANDLE, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p]
)
wait = api("WaitForSingleObject", w.DWORD, [w.HANDLE, w.DWORD])
get_exit = api("GetExitCodeProcess", w.BOOL, [w.HANDLE, c.POINTER(w.DWORD)])
init_attrs = api(
    "InitializeProcThreadAttributeList",
    w.BOOL,
    [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)],
)
update_attrs = api(
    "UpdateProcThreadAttribute",
    w.BOOL,
    [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p],
)
delete_attrs = api("DeleteProcThreadAttributeList", None, [c.c_void_p])
create_process = api(
    "CreateProcessW",
    w.BOOL,
    [
        w.LPCWSTR,
        w.LPWSTR,
        c.c_void_p,
        c.c_void_p,
        w.BOOL,
        w.DWORD,
        c.c_void_p,
        w.LPCWSTR,
        c.c_void_p,
        c.POINTER(ProcessInfo),
    ],
)


def check(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


def creation(handle):
    values = [c.c_uint64() for _ in range(4)]
    check(get_times(handle, *[c.byref(v) for v in values]))
    return str(values[0].value)


def process_identity(pid):
    handle = open_process(0x1000 | 0x100000, False, pid)
    if not handle:
        if c.get_last_error() == 87:
            return None
        raise c.WinError(c.get_last_error())
    try:
        return None if wait(handle, 0) == 0 else {"pid": pid, "created": creation(handle)}
    finally:
        close(handle)


def job_alive(name):
    handle = open_job(4, False, name)
    if not handle:
        if c.get_last_error() == 2:
            return False
        raise c.WinError(c.get_last_error())
    try:
        counts = Accounting()
        check(query_job(handle, 1, c.byref(counts), c.sizeof(counts), None))
        return counts.active > 0
    finally:
        close(handle)


class JobProcess:
    def __init__(self, command, stdout, stderr, name):
        self.descendants = []
        self.job = check(create_job(None, name))
        self.handle = None
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        try:
            check(set_job(self.job, 9, c.byref(limits), c.sizeof(limits)))
            size = c.c_size_t()
            init_attrs(None, 2, 0, c.byref(size))
            attributes = c.create_string_buffer(size.value)
            check(init_attrs(attributes, 2, 0, c.byref(size)))
            try:
                jobs = (w.HANDLE * 1)(self.job)
                check(update_attrs(attributes, 0, 0x2000D, jobs, c.sizeof(jobs), None, None))
                with open("NUL", "rb") as stdin:
                    handles = (w.HANDLE * 3)(
                        *[msvcrt.get_osfhandle(f.fileno()) for f in (stdin, stdout, stderr)]
                    )
                    import os

                    for handle in handles:
                        os.set_handle_inheritable(handle, True)
                    try:
                        check(
                            update_attrs(
                                attributes, 0, 0x20002, handles, c.sizeof(handles), None, None
                            )
                        )
                        startup = StartupEx()
                        startup.startup.cb = c.sizeof(startup)
                        startup.startup.flags = 0x100
                        startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = (
                            handles
                        )
                        startup.attributes = c.cast(attributes, c.c_void_p)
                        info = ProcessInfo()
                        command_line = c.create_unicode_buffer(subprocess.list2cmdline(command))
                        check(
                            create_process(
                                command[0],
                                command_line,
                                None,
                                None,
                                True,
                                0x80000 | 0x08000000,
                                None,
                                None,
                                c.byref(startup),
                                c.byref(info),
                            )
                        )
                        self.handle, self.pid = info.process, info.pid
                        close(info.thread)
                        self.identity = {"pid": self.pid, "created": creation(self.handle)}
                    finally:
                        for handle in handles:
                            os.set_handle_inheritable(handle, False)
            finally:
                delete_attrs(attributes)
        except BaseException:
            self.close()
            raise

    def poll(self):
        if wait(self.handle, 0) != 0:
            return None
        value = w.DWORD()
        check(get_exit(self.handle, c.byref(value)))
        return value.value

    def kill(self):
        buffer = c.create_string_buffer(65536)
        check(query_job(self.job, 3, buffer, c.sizeof(buffer), None))
        count = c.c_uint32.from_buffer(buffer, 4).value
        pids = (c.c_size_t * count).from_buffer(buffer, 8)
        for pid in pids:
            handle = open_process(0x100000, False, pid)
            if handle:
                self.descendants.append(handle)
        check(terminate_job(self.job, 1))

    def empty(self):
        value = Accounting()
        check(query_job(self.job, 1, c.byref(value), c.sizeof(value), None))
        return value.active == 0 and all(wait(handle, 0) == 0 for handle in self.descendants)

    def close(self):
        if self.job:
            close(self.job)
            self.job = None
        if self.handle:
            close(self.handle)
            self.handle = None
        for handle in self.descendants:
            close(handle)
        self.descendants.clear()

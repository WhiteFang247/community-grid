"""Windows process-tree limits using a private Win32 Job Object."""
import ctypes as c
from ctypes import wintypes as w


class BasicLimits(c.Structure):
    _fields_ = [
        ("process_time", c.c_longlong), ("job_time", c.c_longlong), ("flags", w.DWORD),
        ("minimum_ws", c.c_size_t), ("maximum_ws", c.c_size_t), ("processes", w.DWORD),
        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling_class", w.DWORD),
    ]


class ExtendedLimits(c.Structure):
    _fields_ = [
        ("basic", BasicLimits), ("io", c.c_ulonglong * 6),
        ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
        ("peak_process_memory", c.c_size_t), ("peak_job_memory", c.c_size_t),
    ]


class ProcessMemory(c.Structure):
    _fields_ = [("size", w.DWORD), ("faults", w.DWORD), ("peak_working_set", c.c_size_t),
                ("other_counters", c.c_size_t * 7)]


def api(name, args, result=w.BOOL):
    fn = getattr(c.WinDLL("kernel32", use_last_error=True), name)
    fn.argtypes = args
    fn.restype = result
    return fn


def checked(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


class WindowsJob:
    def __init__(self, memory_bytes, cpu_seconds):
        self.handle = checked(api("CreateJobObjectW", [c.c_void_p, w.LPCWSTR], w.HANDLE)(None, None))
        try:
            limits = ExtendedLimits()
            # Job user CPU time, total job commit, and kill on last handle close.
            limits.basic.flags = 0x4 | 0x200 | 0x2000
            limits.basic.job_time = cpu_seconds * 10_000_000
            limits.job_memory = memory_bytes
            checked(api("SetInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD])(
                self.handle, 9, c.byref(limits), c.sizeof(limits)))
            # Terminate the whole job when its accumulated user time is exhausted.
            action = w.DWORD(0)
            checked(api("SetInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD])(
                self.handle, 6, c.byref(action), c.sizeof(action)))
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        handle = checked(api("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)(
            0x100 | 0x1, False, process.pid))
        try:
            checked(api("AssignProcessToJobObject", [w.HANDLE, w.HANDLE])(self.handle, handle))
        finally:
            api("CloseHandle", [w.HANDLE])(handle)

    def close(self):
        if self.handle:
            checked(api("CloseHandle", [w.HANDLE])(self.handle))
            self.handle = None


def peak_rss_kib(pid):
    handle = checked(api("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)(
        0x400 | 0x10, False, pid))
    try:
        counters = ProcessMemory()
        counters.size = c.sizeof(counters)
        checked(api("K32GetProcessMemoryInfo", [w.HANDLE, c.c_void_p, w.DWORD])(
            handle, c.byref(counters), counters.size))
        return counters.peak_working_set // 1024
    finally:
        api("CloseHandle", [w.HANDLE])(handle)


def memory_status():
    class MemoryStatus(c.Structure):
        _fields_ = [("length", w.DWORD), ("load", w.DWORD), ("values", c.c_ulonglong * 7)]

    status = MemoryStatus()
    status.length = c.sizeof(status)
    checked(api("GlobalMemoryStatusEx", [c.POINTER(MemoryStatus)])(c.byref(status)))
    return status.values[0], status.values[1]

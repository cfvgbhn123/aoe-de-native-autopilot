"""Small, dependency-free Windows process and global-hotkey helpers.

Public API:
    processes() -> list[dict]: entries contain pid, name, path (or None), and
        path_error for processes whose executable cannot be queried.
    pe_architecture(path) -> str: "x86", "x64", "arm64", or "machine-0x....".
    ProcessMemory(pid, *, writable=False): an explicitly closeable context
        manager. read(address, size) -> bytes; write(address, data) requires
        writable=True; is_alive() -> bool. creation_time is the integer Windows
        FILETIME (100 ns ticks since 1601). process_exe is the executable path.
    Hotkeys(event_queue, bindings=None): bindings map key chords to actions.
        start() registers keys on a background message thread and returns the
        conflicts list; actions are delivered via event_queue.put(action).
        stop() unregisters every key. running, conflicts, and error expose state.
        Default chords are Ctrl+Alt+F8 (toggle), Ctrl+Alt+F9 (status),
        Ctrl+Alt+F10 (off), and Ctrl+Alt+Pause (off).

Importing this module is safe on other platforms; Windows-specific operations
raise UnsupportedPlatformError there. This module never injects code, DLLs,
or remote threads. A 64-bit Python process can access a 32-bit target normally;
32-bit Python cannot represent addresses outside its own pointer range.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import struct
import threading
from typing import Mapping


class UnsupportedPlatformError(RuntimeError):
    """Raised when a Windows-only operation is requested elsewhere."""


class Win32CallError(OSError):
    """A failed Win32 call, including its native error code and operation."""

    def __init__(self, operation: str, code: int):
        message = ctypes.FormatError(code).strip() if os.name == "nt" else str(code)
        super().__init__(code, f"{operation}: {message}")
        self.winerror = code
        self.operation = operation


_WINDOWS = os.name == "nt"
_POINTER_MAX = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_ERROR_NO_MORE_FILES = 18
_ERROR_INSUFFICIENT_BUFFER = 122
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_VM_READ = 0x0010
_PROCESS_VM_WRITE = 0x0020
_PROCESS_VM_OPERATION = 0x0008
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_MOD_NOREPEAT = 0x4000


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


if _WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    def _declare(dll, name, restype, *argtypes):
        function = getattr(dll, name)
        function.restype = restype
        function.argtypes = list(argtypes)
        return function

    _CreateToolhelp32Snapshot = _declare(
        _kernel32, "CreateToolhelp32Snapshot", wintypes.HANDLE,
        wintypes.DWORD, wintypes.DWORD,
    )
    _Process32FirstW = _declare(
        _kernel32, "Process32FirstW", wintypes.BOOL,
        wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W),
    )
    _Process32NextW = _declare(
        _kernel32, "Process32NextW", wintypes.BOOL,
        wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W),
    )
    _OpenProcess = _declare(
        _kernel32, "OpenProcess", wintypes.HANDLE,
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    )
    _CloseHandle = _declare(_kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    _QueryFullProcessImageNameW = _declare(
        _kernel32, "QueryFullProcessImageNameW", wintypes.BOOL,
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    _GetProcessTimes = _declare(
        _kernel32, "GetProcessTimes", wintypes.BOOL, wintypes.HANDLE,
        *([ctypes.POINTER(wintypes.FILETIME)] * 4),
    )
    _ReadProcessMemory = _declare(
        _kernel32, "ReadProcessMemory", wintypes.BOOL,
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    )
    _WriteProcessMemory = _declare(
        _kernel32, "WriteProcessMemory", wintypes.BOOL,
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    )
    _WaitForSingleObject = _declare(
        _kernel32, "WaitForSingleObject", wintypes.DWORD,
        wintypes.HANDLE, wintypes.DWORD,
    )
    _GetCurrentThreadId = _declare(_kernel32, "GetCurrentThreadId", wintypes.DWORD)
    _OpenThread = _declare(
        _kernel32, "OpenThread", wintypes.HANDLE,
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    )
    _GetProcessIdOfThread = _declare(
        _kernel32, "GetProcessIdOfThread", wintypes.DWORD, wintypes.HANDLE,
    )
    _SuspendThread = _declare(_kernel32, "SuspendThread", wintypes.DWORD, wintypes.HANDLE)
    _ResumeThread = _declare(_kernel32, "ResumeThread", wintypes.DWORD, wintypes.HANDLE)
    _WindowEnumCallback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _EnumWindows = _declare(_user32, "EnumWindows", wintypes.BOOL, _WindowEnumCallback, wintypes.LPARAM)
    _GetWindowThreadProcessId = _declare(
        _user32, "GetWindowThreadProcessId", wintypes.DWORD,
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    )
    _IsWindowVisible = _declare(_user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
    _GetWindowTextW = _declare(
        _user32, "GetWindowTextW", ctypes.c_int, wintypes.HWND,
        wintypes.LPWSTR, ctypes.c_int,
    )
    _GetClassNameW = _declare(
        _user32, "GetClassNameW", ctypes.c_int, wintypes.HWND,
        wintypes.LPWSTR, ctypes.c_int,
    )
    _RegisterHotKey = _declare(
        _user32, "RegisterHotKey", wintypes.BOOL,
        wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
    )
    _UnregisterHotKey = _declare(
        _user32, "UnregisterHotKey", wintypes.BOOL, wintypes.HWND, ctypes.c_int,
    )
    _GetMessageW = _declare(
        _user32, "GetMessageW", wintypes.BOOL,
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
    )
    _PeekMessageW = _declare(
        _user32, "PeekMessageW", wintypes.BOOL,
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
        wintypes.UINT, wintypes.UINT,
    )
    _PostThreadMessageW = _declare(
        _user32, "PostThreadMessageW", wintypes.BOOL,
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    )


def _require_windows() -> None:
    if not _WINDOWS:
        raise UnsupportedPlatformError("This operation requires Windows.")


def _last_error(operation: str) -> Win32CallError:
    return Win32CallError(operation, ctypes.get_last_error())


def _close_handle(handle) -> None:
    if not _CloseHandle(handle):
        raise _last_error("CloseHandle")


def _executable_path(handle) -> str:
    capacity = 260
    while capacity <= 32768:
        buffer = ctypes.create_unicode_buffer(capacity)
        length = wintypes.DWORD(capacity)
        if _QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return buffer.value
        code = ctypes.get_last_error()
        if code != _ERROR_INSUFFICIENT_BUFFER:
            raise Win32CallError("QueryFullProcessImageNameW", code)
        capacity = min(capacity * 2, 32768) if capacity < 32768 else 32769
    raise Win32CallError("QueryFullProcessImageNameW", _ERROR_INSUFFICIENT_BUFFER)


def processes() -> list[dict]:
    """Enumerate process identity; inaccessible/vanished paths include errors."""
    _require_windows()
    snapshot = _CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == _INVALID_HANDLE_VALUE or snapshot is None:
        raise _last_error("CreateToolhelp32Snapshot")
    result = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not _Process32FirstW(snapshot, ctypes.byref(entry)):
            code = ctypes.get_last_error()
            if code == _ERROR_NO_MORE_FILES:
                return result
            raise Win32CallError("Process32FirstW", code)
        while True:
            item = {"pid": int(entry.th32ProcessID), "name": entry.szExeFile, "path": None}
            handle = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, item["pid"])
            if handle:
                try:
                    try:
                        item["path"] = _executable_path(handle)
                    except Win32CallError as exc:
                        item["path_error"] = str(exc)
                finally:
                    _close_handle(handle)
            else:
                item["path_error"] = str(_last_error("OpenProcess(query)"))
            result.append(item)
            if not _Process32NextW(snapshot, ctypes.byref(entry)):
                code = ctypes.get_last_error()
                if code == _ERROR_NO_MORE_FILES:
                    break
                raise Win32CallError("Process32NextW", code)
        return result
    finally:
        _close_handle(snapshot)


def pe_architecture(path: str | os.PathLike) -> str:
    """Read a PE file's COFF machine; malformed files raise ValueError."""
    with Path(path).open("rb") as stream:
        dos = stream.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            raise ValueError("Not a PE executable: missing DOS header.")
        pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
        file_size = stream.seek(0, os.SEEK_END)
        if pe_offset < 64 or pe_offset + 24 > file_size:
            raise ValueError("Not a PE executable: invalid PE header offset.")
        stream.seek(pe_offset)
        header = stream.read(24)
        if header[:4] != b"PE\0\0":
            raise ValueError("Not a PE executable: missing PE signature.")
        machine = struct.unpack_from("<H", header, 4)[0]
    return {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(
        machine, f"machine-0x{machine:04x}"
    )


class ProcessMemory:
    """Process handle with explicit read/write access and lifetime identity."""

    def __init__(self, pid: int, *, writable: bool = False):
        _require_windows()
        if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("pid must be a positive Windows process ID.")
        if not isinstance(writable, bool):
            raise TypeError("writable must be an explicit boolean.")
        self.pid = pid
        self.writable = bool(writable)
        self._handle = None
        rights = _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ | _SYNCHRONIZE
        if self.writable:
            rights |= _PROCESS_VM_WRITE | _PROCESS_VM_OPERATION
        handle = _OpenProcess(rights, False, pid)
        if not handle:
            raise _last_error(f"OpenProcess(pid={pid}, writable={self.writable})")
        self._handle = handle
        try:
            creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not _GetProcessTimes(handle, *(ctypes.byref(x) for x in (creation, exited, kernel, user))):
                raise _last_error("GetProcessTimes")
            self.creation_time = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
            self.process_exe = _executable_path(handle)
        except BaseException:
            self.close()
            raise

    @property
    def closed(self) -> bool:
        return self._handle is None

    def _open_handle(self):
        if self.closed:
            raise RuntimeError("ProcessMemory handle is closed.")
        return self._handle

    @staticmethod
    def _validate_range(address: int, size: int) -> None:
        if isinstance(address, bool) or not isinstance(address, int) or address < 0:
            raise ValueError("address must be a nonnegative integer.")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("size must be a nonnegative integer.")
        if address > _POINTER_MAX or size > _POINTER_MAX or size > _POINTER_MAX - address + 1:
            raise OverflowError("Memory range exceeds this Python process's pointer range.")

    def read(self, address: int, size: int) -> bytes:
        handle = self._open_handle()
        self._validate_range(address, size)
        if size == 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        if not _ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(count)):
            raise _last_error(f"ReadProcessMemory(address=0x{address:x}, size={size}, read={count.value})")
        if count.value != size:
            raise OSError(f"ReadProcessMemory returned a short read: {count.value}/{size} bytes.")
        return buffer.raw

    def write(self, address: int, data: bytes | bytearray | memoryview) -> None:
        handle = self._open_handle()
        if not self.writable:
            raise PermissionError("ProcessMemory was opened read-only; writable=True is required.")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes, bytearray, or memoryview.")
        payload = bytes(data)
        self._validate_range(address, len(payload))
        if not payload:
            return
        buffer = ctypes.create_string_buffer(payload, len(payload))
        count = ctypes.c_size_t()
        if not _WriteProcessMemory(handle, ctypes.c_void_p(address), buffer, len(payload), ctypes.byref(count)):
            raise _last_error(f"WriteProcessMemory(address=0x{address:x}, size={len(payload)}, written={count.value})")
        if count.value != len(payload):
            raise OSError(f"WriteProcessMemory returned a short write: {count.value}/{len(payload)} bytes.")

    def is_alive(self) -> bool:
        outcome = _WaitForSingleObject(self._open_handle(), 0)
        if outcome == _WAIT_TIMEOUT:
            return True
        if outcome == _WAIT_OBJECT_0:
            return False
        if outcome == _WAIT_FAILED:
            raise _last_error("WaitForSingleObject")
        raise OSError(f"WaitForSingleObject returned unexpected result {outcome}.")

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            _close_handle(handle)

    def __enter__(self) -> "ProcessMemory":
        self._open_handle()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def __del__(self):
        handle = getattr(self, "_handle", None)
        if handle is not None and _WINDOWS:
            # A destructor cannot report errors reliably; explicit close does.
            self._handle = None
            _CloseHandle(handle)


DEFAULT_HOTKEYS = {
    "Ctrl+Alt+F8": "toggle",
    "Ctrl+Alt+F9": "status",
    "Ctrl+Alt+F10": "off",
    "Ctrl+Alt+Pause": "off",
}


def _parse_hotkey(chord: str) -> tuple[int, int]:
    parts = [part.strip().upper() for part in chord.split("+")]
    if not parts or any(not part for part in parts):
        raise ValueError(f"Invalid hotkey chord: {chord!r}")
    modifiers = 0
    seen = set()
    for part in parts[:-1]:
        modifier = {"ALT": 0x0001, "CTRL": 0x0002, "CONTROL": 0x0002,
                    "SHIFT": 0x0004, "WIN": 0x0008, "WINDOWS": 0x0008}.get(part)
        if modifier is None or modifier in seen:
            raise ValueError(f"Invalid or duplicate hotkey modifier: {chord!r}")
        seen.add(modifier)
        modifiers |= modifier
    key = parts[-1]
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        virtual_key = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
        virtual_key = ord(key)
    else:
        virtual_key = {"PAUSE": 0x13, "BREAK": 0x13, "ESC": 0x1B, "ESCAPE": 0x1B,
                       "SPACE": 0x20, "HOME": 0x24, "END": 0x23, "INSERT": 0x2D,
                       "DELETE": 0x2E}.get(key)
        if virtual_key is None:
            raise ValueError(f"Unsupported hotkey key: {chord!r}")
    return modifiers | _MOD_NOREPEAT, virtual_key


class Hotkeys:
    """Register global hotkeys in a dedicated thread and enqueue action names."""

    def __init__(self, event_queue, bindings: Mapping[str, str] | None = None):
        if not callable(getattr(event_queue, "put", None)):
            raise TypeError("event_queue must provide put(action).")
        self.event_queue = event_queue
        self.bindings = dict(DEFAULT_HOTKEYS if bindings is None else bindings)
        self._parsed = []
        unique_chords = set()
        for chord, action in self.bindings.items():
            if not isinstance(chord, str) or not isinstance(action, str):
                raise TypeError("Hotkey bindings must map string chords to string actions.")
            parsed = _parse_hotkey(chord)
            if parsed in unique_chords:
                raise ValueError(f"Duplicate hotkey chord: {chord!r}")
            unique_chords.add(parsed)
            self._parsed.append((chord, action, *parsed))
        self.conflicts: list[dict] = []
        self.error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> list[dict]:
        _require_windows()
        if self.running:
            return list(self.conflicts)
        self.conflicts = []
        self.error = None
        self._ready.clear()
        self._thread = threading.Thread(target=self._message_loop, name="AoE-autopilot-hotkeys", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise TimeoutError("The hotkey thread did not initialize within 5 seconds.")
        if self.error is not None:
            raise self.error
        return list(self.conflicts)

    def _message_loop(self) -> None:
        registered: dict[int, str] = {}
        try:
            message = wintypes.MSG()
            # Ensure a message queue exists before exposing the thread ID.
            _PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self._thread_id = int(_GetCurrentThreadId())
            for identifier, (chord, action, modifiers, virtual_key) in enumerate(self._parsed, 1):
                if _RegisterHotKey(None, identifier, modifiers, virtual_key):
                    registered[identifier] = action
                else:
                    code = ctypes.get_last_error()
                    self.conflicts.append({"chord": chord, "action": action,
                                           "winerror": code, "error": str(Win32CallError("RegisterHotKey", code))})
            self._ready.set()
            while True:
                outcome = _GetMessageW(ctypes.byref(message), None, 0, 0)
                if outcome == -1:
                    raise _last_error("GetMessageW")
                if outcome == 0:
                    break
                if message.message == _WM_HOTKEY:
                    action = registered.get(int(message.wParam))
                    if action is not None:
                        self.event_queue.put(action)
        except BaseException as exc:
            self.error = exc
        finally:
            for identifier in registered:
                if not _UnregisterHotKey(None, identifier) and self.error is None:
                    self.error = _last_error("UnregisterHotKey")
            self._thread_id = None
            self._ready.set()

    def stop(self) -> None:
        if self._thread is None:
            return
        if self.running:
            self._ready.wait(5)
            thread_id = self._thread_id
            if thread_id is not None and not _PostThreadMessageW(thread_id, _WM_QUIT, 0, 0):
                if self.running:
                    raise _last_error("PostThreadMessageW(WM_QUIT)")
            if self._thread is threading.current_thread():
                return
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise TimeoutError("The hotkey thread did not stop within 5 seconds.")
        self._thread = None
        if self.error is not None:
            raise self.error

    def __enter__(self) -> "Hotkeys":
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()

from __future__ import annotations
import ctypes
from ctypes import wintypes as w
import struct
from .win32 import Win32CallError

class BridgeError(RuntimeError): pass

def _apis():
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    signatures={
        "OpenProcess":(w.HANDLE,[w.DWORD,w.BOOL,w.DWORD]),
        "CloseHandle":(w.BOOL,[w.HANDLE]),
        "VirtualAllocEx":(ctypes.c_void_p,[w.HANDLE,ctypes.c_void_p,ctypes.c_size_t,w.DWORD,w.DWORD]),
        "VirtualFreeEx":(w.BOOL,[w.HANDLE,ctypes.c_void_p,ctypes.c_size_t,w.DWORD]),
        "WriteProcessMemory":(w.BOOL,[w.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]),
        "CreateRemoteThread":(w.HANDLE,[w.HANDLE,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_void_p,w.DWORD,ctypes.c_void_p]),
        "WaitForSingleObject":(w.DWORD,[w.HANDLE,w.DWORD]),
        "GetExitCodeThread":(w.BOOL,[w.HANDLE,ctypes.POINTER(w.DWORD)]),
        "CreateToolhelp32Snapshot":(w.HANDLE,[w.DWORD,w.DWORD]),
        "GetTickCount":(w.DWORD,[]),
    }
    for name,(result,args) in signatures.items():
        fn=getattr(kernel,name);fn.restype=result;fn.argtypes=args
    return kernel


class ModuleEntry(ctypes.Structure):
    _fields_=[("dwSize",w.DWORD),("th32ModuleID",w.DWORD),("th32ProcessID",w.DWORD),
        ("GlblcntUsage",w.DWORD),("ProccntUsage",w.DWORD),("modBaseAddr",ctypes.c_void_p),
        ("modBaseSize",w.DWORD),("hModule",w.HMODULE),("szModule",w.WCHAR*256),("szExePath",w.WCHAR*260)]


class ProcessLink:
    def __init__(self, memory):
        self.memory=memory
        self.kernel=_apis()
        self.handle=None
        self.shared=None
        self.module=None
        self.started=False
        self.sequence=0
        self.last_world=None
        self.pending=()
        self.pending_sequence=None
        self._heartbeat_pump=None

    def _error(self, action):
        return Win32CallError(action,ctypes.get_last_error())

    def modules(self):
        kernel=self.kernel
        for name in ("Module32FirstW","Module32NextW"):
            fn=getattr(kernel,name);fn.restype=w.BOOL;fn.argtypes=[w.HANDLE,ctypes.POINTER(ModuleEntry)]
        handle=kernel.CreateToolhelp32Snapshot(0x18,self.memory.pid)
        if handle in (None,ctypes.c_void_p(-1).value):
            raise self._error("CreateToolhelp32Snapshot(modules)")
        result={}
        try:
            entry=ModuleEntry();entry.dwSize=ctypes.sizeof(entry)
            if not kernel.Module32FirstW(handle,ctypes.byref(entry)):
                raise self._error("Module32FirstW")
            while True:
                result[entry.szModule.lower()]=(int(entry.modBaseAddr),entry.szExePath)
                if not kernel.Module32NextW(handle,ctypes.byref(entry)):
                    break
        finally:
            kernel.CloseHandle(handle)
        return result

    def _call(self, address, argument=0):
        thread=self.kernel.CreateRemoteThread(self.handle,None,0,address,argument,0,None)
        if not thread:
            raise self._error("CreateRemoteThread(AoE bridge)")
        try:
            if self.kernel.WaitForSingleObject(thread,5000)!=0:
                raise BridgeError("桥接初始化线程未完成；不要重复注入，请重启游戏后重试")
            result=w.DWORD()
            if not self.kernel.GetExitCodeThread(thread,ctypes.byref(result)):
                raise self._error("GetExitCodeThread")
            return result.value
        finally:
            self.kernel.CloseHandle(thread)

"""Windows research-worker tokens. No elevation or unrestricted fallback.

This is an ACL/token boundary, not a firewall or a separate logon identity.
Runtime files must be worker-readable; private controller resources must not
grant Users, Everyone or Restricted Code access. Production ACLs are untouched.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import io
import os
from pathlib import Path
import subprocess
import uuid


class SandboxError(RuntimeError):
    """The worker security contract could not be established."""


USERS_SID = "S-1-5-32-545"
PUBLIC_GROUPS = {USERS_SID, "S-1-1-0", "S-1-5-11"}
RESTRICTING_SIDS = {USERS_SID, "S-1-1-0", "S-1-5-12"}
DENY_ONLY = 0x10
INTEGRITY_GROUP = 0x20
DISABLE_MAX_PRIVILEGE = 1


def restricted_token():
    """Caller owns the returned primary token; close it after process creation."""
    if os.name != "nt":
        raise SandboxError("windows_worker_token_required")
    import win32api
    import win32security as security

    source = security.OpenProcessToken(win32api.GetCurrentProcess(), security.TOKEN_ALL_ACCESS)
    token = None
    try:
        user = security.GetTokenInformation(source, security.TokenUser)[0]
        groups = security.GetTokenInformation(source, security.TokenGroups)
        disabled = [(user, 0)] + [
            (sid, 0) for sid, flags in groups
            if security.ConvertSidToStringSid(sid) not in PUBLIC_GROUPS
            and not flags & INTEGRITY_GROUP
            and flags & 0xC0000000 != 0xC0000000  # Logon SID needed for USER32 initialization.
        ]
        # CredRead requires an enabled user SID. Privilege removal ALONE does
        # not prevent a same-logon worker from reading the controller's vault.
        token = security.CreateRestrictedToken(
            source, DISABLE_MAX_PRIVILEGE, disabled, [],
            [(security.ConvertStringSidToSid(sid), 0) for sid in sorted(RESTRICTING_SIDS)],
        )
        if not security.GetTokenInformation(token, security.TokenUser)[1] & DENY_ONLY:
            raise SandboxError("worker_user_sid_not_disabled")
        restricted = {security.ConvertSidToStringSid(sid) for sid, _ in
                      security.GetTokenInformation(token, security.TokenRestrictedSids)}
        privileges = {security.LookupPrivilegeName(None, luid) for luid, _ in
                      security.GetTokenInformation(token, security.TokenPrivileges)}
        if restricted != RESTRICTING_SIDS or privileges - {"SeChangeNotifyPrivilege"}:
            raise SandboxError("worker_token_restrictions_mismatch")
        # Worker runtime initialization needs access to its new objects. This
        # does NOT isolate workers from other local Users; deployment still
        # needs separate identities. Never change the controller's object DACL.
        dacl = security.ACL()
        dacl.AddAccessAllowedAce(security.ACL_REVISION, 0x10000000,
                                 security.ConvertStringSidToSid(USERS_SID))
        dacl.AddAccessAllowedAce(security.ACL_REVISION, 0x10000000,
                                 security.ConvertStringSidToSid("S-1-5-18"))
        security.SetTokenInformation(token, security.TokenDefaultDacl, dacl)
        return token
    except Exception:
        if token is not None:
            token.Close()
        raise
    finally:
        source.Close()


def worker_environment(base: dict[str, str], authentication: dict[str, str]) -> dict[str, str]:
    """Only declared provider authentication crosses; no ambient secret env."""
    home = base.get("HARNESS_WORKER_HOME", "")
    if not home or not Path(home).is_absolute() or not Path(home).is_dir():
        raise SandboxError("dedicated_HARNESS_WORKER_HOME_required")
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT",
               "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}
    env = {k.upper(): v for k, v in base.items() if k.upper() in allowed}
    env.update({"HOME": home, "USERPROFILE": home, "TEMP": home, "TMP": home,
                "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1"})
    # Provider transport supplies this small mapping, not arbitrary worker input.
    # Reject configuration/loader overrides even if misdeclared as auth fields.
    for name, value in authentication.items():
        if not name.isidentifier() or not name.upper().endswith(("_API_KEY", "_TOKEN")):
            raise SandboxError("unsupported_worker_authentication_variable")
        env[name] = value
    return env


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR),
                ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                *[(name, w.DWORD) for name in ("dwX", "dwY", "dwXSize", "dwYSize",
                  "dwXCountChars", "dwYCountChars", "dwFillAttribute", "dwFlags")],
                ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", w.LPVOID), ("hStdInput", w.HANDLE),
                ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class _STARTUPINFOEX(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFO), ("lpAttributeList", w.LPVOID)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", w.LPVOID),
                ("bInheritHandle", w.BOOL)]


def _api():
    if os.name != "nt":
        raise SandboxError("windows_worker_token_required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    signatures = [
        (kernel.CloseHandle, [w.HANDLE], w.BOOL),
        (kernel.WaitForSingleObject, [w.HANDLE, w.DWORD], w.DWORD),
        (kernel.GetExitCodeProcess, [w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        (kernel.TerminateProcess, [w.HANDLE, w.UINT], w.BOOL),
        (kernel.InitializeProcThreadAttributeList,
         [w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
        (kernel.UpdateProcThreadAttribute,
         [w.LPVOID, w.DWORD, ctypes.c_size_t, w.LPVOID, ctypes.c_size_t, w.LPVOID, w.LPVOID], w.BOOL),
        (kernel.DeleteProcThreadAttributeList, [w.LPVOID], None),
        (advapi.CreateProcessAsUserW,
         [w.HANDLE, w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD,
          w.LPVOID, w.LPCWSTR, ctypes.POINTER(_STARTUPINFOEX),
          ctypes.POINTER(_PROCESS_INFORMATION)], w.BOOL),
        (user.CreateDesktopW,
         [w.LPCWSTR, w.LPCWSTR, w.LPVOID, w.DWORD, w.DWORD,
          ctypes.POINTER(_SECURITY_ATTRIBUTES)], w.HANDLE),
        (user.CloseDesktop, [w.HANDLE], w.BOOL),
    ]
    for function, arguments, result in signatures:
        function.argtypes, function.restype = arguments, result
    return kernel, advapi, user


def _check(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


class RestrictedProcess:
    """Minimal wait/kill/pipe API consumed by the contained research launcher."""

    def __init__(self, handle, pid, args, streams, desktop):
        self._handle, self.pid, self.args = handle, pid, args
        self.stdin, self.stdout, self.stderr = streams
        self._desktop = desktop
        self.returncode = None

    def wait(self, timeout=None):
        kernel, _, _ = _api()
        milliseconds = 0xFFFFFFFF if timeout is None else min(int(max(0, timeout) * 1000), 0xFFFFFFFE)
        status = kernel.WaitForSingleObject(self._handle, milliseconds)
        if status == 258:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        result = w.DWORD()
        _check(kernel.GetExitCodeProcess(self._handle, ctypes.byref(result)))
        self.returncode = result.value
        return self.returncode

    def poll(self):
        try:
            return self.wait(0)
        except subprocess.TimeoutExpired:
            return None

    def kill(self):
        kernel, _, _ = _api()
        if self.poll() is None:
            try:
                _check(kernel.TerminateProcess(self._handle, 75))
            except OSError:
                pass

    def close(self):
        kernel, _, user = _api()
        for stream in (self.stdin, self.stdout, self.stderr):
            stream.close()
        if self._handle:
            kernel.CloseHandle(self._handle)
            self._handle = None
        if self._desktop:
            user.CloseDesktop(self._desktop)
            self._desktop = None


def spawn_suspended(command: list[str], cwd, env: dict[str, str]) -> RestrictedProcess:
    """Native restricted spawn. The caller MUST assign to a job before resume."""
    if not command or not Path(command[0]).is_absolute() or env is None:
        raise SandboxError("absolute_worker_executable_and_explicit_environment_required")
    if any(not key or "=" in key or "\0" in key or "\0" in value for key, value in env.items()):
        raise SandboxError("invalid_worker_environment_block")
    # Native CreateProcessAsUser bypasses subprocess.Popen's test guard.
    if os.environ.get("AGI_LIVE_EXECUTION_ALLOWED") == "0":
        forbidden = {"hermes", "hermes.exe", "ollama", "ollama.exe", "controlled_hermes.py",
                     "batch_runner.py", "run_task.py", "onboarding_autonomy.py", "run_daily.py"}
        if any(Path(arg).name.lower() in forbidden for arg in command):
            raise SandboxError("live_worker_blocked_in_model_free_test")
    import msvcrt
    import win32security as security
    kernel, advapi, user = _api()
    token = restricted_token()
    fds, streams = [], []
    attributes = None
    desktop = None
    process = _PROCESS_INFORMATION()
    success = False
    try:
        descriptor = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            "D:P(A;;GA;;;SY)(A;;GA;;;BU)", 1)
        sd = ctypes.create_string_buffer(bytes(descriptor))
        sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), ctypes.addressof(sd), False)
        desktop_name = "AGI_worker_" + uuid.uuid4().hex
        desktop = user.CreateDesktopW(desktop_name, None, None, 0, 0xF01FF, ctypes.byref(sa))
        _check(desktop)
        si = _STARTUPINFOEX()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.StartupInfo.lpDesktop = desktop_name
        si.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
        children = []
        parents = []
        for index in range(3):
            read, write = os.pipe()
            fds.extend((read, write))
            child, parent = (read, write) if index == 0 else (write, read)
            os.set_handle_inheritable(msvcrt.get_osfhandle(child), True)
            children.append(msvcrt.get_osfhandle(child))
            parents.append(parent)
        si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = children
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        _check(size.value)
        storage = ctypes.create_string_buffer(size.value)
        _check(kernel.InitializeProcThreadAttributeList(storage, 1, 0, ctypes.byref(size)))
        attributes = storage
        handles = (w.HANDLE * 3)(*children)
        _check(kernel.UpdateProcThreadAttribute(storage, 0, 0x20002, handles,
                                               ctypes.sizeof(handles), None, None))
        si.lpAttributeList = ctypes.addressof(storage)
        environment = ctypes.create_unicode_buffer(
            "\0".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0\0")
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        _check(advapi.CreateProcessAsUserW(
            int(token), command[0], command_line, None, None, True,
            0x4 | 0x400 | 0x80000 | 0x08000000,  # suspended, Unicode env, extended, no window
            environment, str(cwd) if cwd else None, ctypes.byref(si), ctypes.byref(process)))
        for index, fd in enumerate(parents):
            raw = os.fdopen(fd, "wb" if index == 0 else "rb", buffering=0)
            fds.remove(fd)
            streams.append(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"))
        result = RestrictedProcess(process.hProcess, process.dwProcessId, command, streams, desktop)
        success = True
        return result
    finally:
        token.Close()
        if attributes is not None:
            kernel.DeleteProcThreadAttributeList(attributes)
        for fd in fds:
            os.close(fd)
        if process.hThread:
            kernel.CloseHandle(process.hThread)
        if not success:
            if process.hProcess:
                kernel.TerminateProcess(process.hProcess, 75)
                kernel.WaitForSingleObject(process.hProcess, 5000)
                kernel.CloseHandle(process.hProcess)
            for stream in streams:
                stream.close()
            if desktop:
                user.CloseDesktop(desktop)

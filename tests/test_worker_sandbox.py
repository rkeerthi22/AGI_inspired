"""F124: real restricted Windows children; synthetic resources, no model calls."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import pty_daemon as pty
import worker_sandbox as sandbox
import audit_signer_pipe
import win32api
import win32con
import win32cred
import win32event
import win32file
import win32pipe
import win32security as security
import pywintypes


# The child uses only the disposable standard library, never the live Hermes
# runtime or controller imports. Native calls prove OS denial, not Python mocks.
PROBE = r'''
import ctypes as c, ctypes.wintypes as w, json, os, socket, sys
data=json.loads(sys.argv[1])
k=c.WinDLL('kernel32',use_last_error=True)
a=c.WinDLL('advapi32',use_last_error=True)
k.GetCurrentProcess.restype=w.HANDLE
k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
k.CloseHandle.argtypes=[w.HANDLE]
k.SetEvent.argtypes=[w.HANDLE];k.SetEvent.restype=w.BOOL
a.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
a.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,w.LPVOID,w.DWORD,c.POINTER(w.DWORD)]
a.CredReadW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.POINTER(w.LPVOID)]
a.CredFree.argtypes=[w.LPVOID]
k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
k.CreateFileW.restype=w.HANDLE
token=w.HANDLE()
assert a.OpenProcessToken(k.GetCurrentProcess(),8,c.byref(token)),c.get_last_error()
class SA(c.Structure): _fields_=[('sid',w.LPVOID),('attributes',w.DWORD)]
needed=w.DWORD()
a.GetTokenInformation(token,1,None,0,c.byref(needed))
buffer=c.create_string_buffer(needed.value)
assert a.GetTokenInformation(token,1,buffer,len(buffer),c.byref(needed))
result={'user_deny_only':bool(SA.from_buffer(buffer).attributes&16)}
linked=w.HANDLE()
ok=a.GetTokenInformation(token,19,c.byref(linked),c.sizeof(linked),c.byref(needed))
result['linked_token_error']=0 if ok else c.get_last_error()
if ok:
 a.DuplicateTokenEx.argtypes=[w.HANDLE,w.DWORD,w.LPVOID,c.c_int,c.c_int,c.POINTER(w.HANDLE)]
 duplicate=w.HANDLE()
 duplicated=a.DuplicateTokenEx(linked,0xE,None,2,2,c.byref(duplicate))
 result['linked_duplicate_error']=0 if duplicated else c.get_last_error()
 if duplicated:k.CloseHandle(duplicate)
 a.ImpersonateLoggedOnUser.argtypes=[w.HANDLE]
 impersonated=a.ImpersonateLoggedOnUser(linked)
 result['linked_impersonation_error']=0 if impersonated else c.get_last_error()
 if impersonated:
  a.OpenThreadToken.argtypes=[w.HANDLE,w.DWORD,w.BOOL,c.POINTER(w.HANDLE)]
  k.GetCurrentThread.restype=w.HANDLE
  thread_token=w.HANDLE()
  assert a.OpenThreadToken(k.GetCurrentThread(),8,True,c.byref(thread_token))
  level=w.DWORD()
  assert a.GetTokenInformation(thread_token,9,c.byref(level),c.sizeof(level),c.byref(needed))
  result['linked_impersonation_level']=level.value
  k.CloseHandle(thread_token)
  result['linked_credential_errors']={}
  for label,target in data.get('credentials',{}).items():
   value=w.LPVOID()
   read=a.CredReadW(target,1,0,c.byref(value))
   result['linked_credential_errors'][label]=0 if read else c.get_last_error()
   if read:a.CredFree(value)
  assert a.RevertToSelf()
 k.CloseHandle(linked)
k.CloseHandle(token)
in_job=w.BOOL()
k.IsProcessInJob.argtypes=[w.HANDLE,w.HANDLE,c.POINTER(w.BOOL)]
assert k.IsProcessInJob(k.GetCurrentProcess(),None,c.byref(in_job))
result['in_job']=bool(in_job.value)
if data.get('ui'):
 u=c.WinDLL('user32',use_last_error=True)
 u.GetThreadDesktop.argtypes=[w.DWORD];u.GetThreadDesktop.restype=w.HANDLE
 u.GetUserObjectInformationW.argtypes=[w.HANDLE,c.c_int,w.LPVOID,w.DWORD,c.POINTER(w.DWORD)]
 desktop=u.GetThreadDesktop(k.GetCurrentThreadId())
 name=c.create_unicode_buffer(256)
 assert u.GetUserObjectInformationW(desktop,2,name,c.sizeof(name),c.byref(needed))
 result['desktop']=name.value
 limits=w.DWORD()
 k.QueryInformationJobObject.argtypes=[w.HANDLE,c.c_int,w.LPVOID,w.DWORD,c.POINTER(w.DWORD)]
 assert k.QueryInformationJobObject(None,4,c.byref(limits),c.sizeof(limits),c.byref(needed))
 result['ui_limits']=limits.value
 u.CreateDesktopW.argtypes=[w.LPCWSTR,w.LPCWSTR,w.LPVOID,w.DWORD,w.DWORD,w.LPVOID]
 u.CreateDesktopW.restype=w.HANDLE
 forbidden=u.CreateDesktopW('F124_child_forbidden',None,None,0,0xF01FF,None)
 result['create_desktop_error']=0 if forbidden else c.get_last_error()
 if forbidden:
  u.CloseDesktop.argtypes=[w.HANDLE];u.CloseDesktop(forbidden)
for label,path in data.get('files',{}).items():
 h=k.CreateFileW(path,0x80000000,1,None,3,0,None)
 result[label]=c.get_last_error() if h==w.HANDLE(-1).value else 0
 if h!=w.HANDLE(-1).value:k.CloseHandle(h)
for label,path in data.get('file_dac',{}).items():
 h=k.CreateFileW(path,0x40000,1,None,3,0,None)
 result[label]=c.get_last_error() if h==w.HANDLE(-1).value else 0
 if h!=w.HANDLE(-1).value:k.CloseHandle(h)
for label,target in data.get('credentials',{}).items():
 value=w.LPVOID()
 ok=a.CredReadW(target,1,0,c.byref(value))
 result[label]=0 if ok else c.get_last_error()
 if ok:a.CredFree(value)
if 'pipe' in data:
 h=k.CreateFileW(data['pipe'],0x12019b,0,None,3,0x110000,None)
 result['pipe_error']=c.get_last_error() if h==w.HANDLE(-1).value else 0
 if h!=w.HANDLE(-1).value:k.CloseHandle(h)
if 'parent' in data:
 result['parent_errors']=[]
 for access in (0x2,0x8,0x10,0x20,0x40,0x80,0x40000):
  h=k.OpenProcess(access,False,data['parent'])
  result['parent_errors'].append(0 if h else c.get_last_error())
  if h:k.CloseHandle(h)
if 'event' in data:
 result['inherited_event']=bool(k.SetEvent(data['event']))
if 'port' in data:
 with socket.socket() as s:
  s.settimeout(5);s.connect(('127.0.0.1',data['port']));s.sendall(b'broker-probe')
  result['broker']=s.recv(32).decode()
if data.get('descendant'):
 import subprocess
 child=subprocess.run([sys.executable,'-I','-B',__file__,'{}'],capture_output=True,text=True,timeout=10)
 result['descendant_code']=child.returncode
 result['descendant']=json.loads(child.stdout)
if data.get('bulk'):
 sys.stderr.write('E'*150000+'\n');sys.stderr.flush()
 sys.stdout.write('O'*150000+'\n');sys.stdout.flush()
print(json.dumps(result),flush=True)
'''


class WorkerSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agi_worker_f124_")
        cls.root = Path(cls.temp.name)
        cls.sid = audit_signer_pipe.current_sid()
        # Temporary ACL fixture only. No production interpreter ACL is changed.
        sd = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;OICI;FA;;;{cls.sid})(A;OICI;FA;;;SY)(A;OICI;GRGX;;;BU)", 1)
        security.SetFileSecurity(str(cls.root), security.DACL_SECURITY_INFORMATION, sd)
        cls.python_root = cls.root / "python"
        shutil.copytree(sys.base_prefix, cls.python_root,
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "include", "libs"))
        cls.python = cls.python_root / "python.exe"
        cls.probe = cls.root / "probe.py"
        cls.probe.write_text(PROBE, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_probe(self, data):
        command = [str(self.python), "-I", "-B", str(self.probe), json.dumps(data)]
        process, job, out, err = pty.create_contained_process(
            command, cwd=self.root, env={"SystemRoot": os.environ["SystemRoot"]},
            restricted_worker=True)
        try:
            code = process.wait(25)
            pty.close_job(job)
            job = None
            out.wait(5)
            err.wait(5)
            self.assertEqual(code, 0, f"native child exit={code:#x}: {err.text[-2000:]}")
            return json.loads(out.text.splitlines()[-1]), out.text, err.text
        finally:
            if job:
                pty.close_job(job)
            process.wait(5)
            process.close()

    def test_token_removes_privileges_and_disables_user_and_admin(self):
        token = sandbox.restricted_token()
        try:
            self.assertTrue(security.IsTokenRestricted(token))
            self.assertTrue(security.GetTokenInformation(token, security.TokenUser)[1] & 16)
            names = {security.LookupPrivilegeName(None, luid) for luid, _ in
                     security.GetTokenInformation(token, security.TokenPrivileges)}
            self.assertEqual(names, {"SeChangeNotifyPrivilege"})
            for sid, flags in security.GetTokenInformation(token, security.TokenGroups):
                if (security.ConvertSidToStringSid(sid) not in sandbox.PUBLIC_GROUPS
                        and not flags & 32 and flags & 0xC0000000 != 0xC0000000):
                    self.assertTrue(flags & 16)
            self.assertEqual({security.ConvertSidToStringSid(sid) for sid, _ in
                              security.GetTokenInformation(token, security.TokenRestrictedSids)},
                             sandbox.RESTRICTING_SIDS)
        finally:
            token.Close()

    def test_worker_and_descendant_retain_restrictions_inside_job(self):
        result, _, _ = self.run_probe({"descendant": True})
        self.assertTrue(result["in_job"])
        self.assertTrue(result["user_deny_only"])
        self.assertEqual(result["descendant_code"], 0)
        self.assertTrue(result["descendant"]["in_job"])
        self.assertTrue(result["descendant"]["user_deny_only"])

    def test_linked_uac_token_cannot_escalate_above_identification(self):
        result, _, _ = self.run_probe({})
        if result["linked_token_error"] == 0:
            self.assertIn(result["linked_duplicate_error"], (5, 1346))
            if result["linked_impersonation_error"] == 0:
                self.assertLessEqual(result["linked_impersonation_level"], 1)
            else:
                self.assertIn(result["linked_impersonation_error"], (5, 1346))
        else:
            self.assertIn(result["linked_token_error"], (5, 1312))

    def test_private_desktop_and_job_ui_restrictions(self):
        result, _, _ = self.run_probe({"ui": True})
        self.assertTrue(result["desktop"].startswith("AGI_worker_"), result)
        self.assertEqual(result["ui_limits"], 0xFF)
        self.assertEqual(result["create_desktop_error"], 5)

    def test_private_controller_file_and_process_denied(self):
        private = self.root / "controller-private.txt"
        private.write_text("synthetic controller data", encoding="ascii")
        sd = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;;FA;;;{self.sid})(A;;FA;;;SY)", 1)
        security.SetFileSecurity(str(private), security.DACL_SECURITY_INFORMATION, sd)
        self.assertTrue(private.read_text())  # Negative control: it exists and controller can read.
        result, _, _ = self.run_probe({"files": {"private_error": str(private)}, "parent": os.getpid(),
                                      "file_dac": {"file_dac_error": str(private)}})
        self.assertEqual(result["private_error"], 5)
        self.assertEqual(result["file_dac_error"], 5)
        self.assertEqual(result["parent_errors"], [5] * 7)

    def test_credential_manager_denies_existing_synthetic_keys(self):
        targets = {label: "AGI_like/test_F124_" + uuid.uuid4().hex
                   for label in ("controller_cred_error", "signer_cred_error")}
        created = []
        try:
            for target in targets.values():
                win32cred.CredWrite({"Type": 1, "TargetName": target, "UserName": "F124-test",
                                    "CredentialBlob": "synthetic-not-a-production-key", "Persist": 1}, 0)
                created.append(target)
                self.assertTrue(win32cred.CredRead(target, 1, 0))
            result, _, _ = self.run_probe({"credentials": targets})
            # Missing-target 1168 is NOT accepted as proof of denied access.
            for label in targets:
                self.assertIn(result[label], (5, 1312), result)
                if result.get("linked_impersonation_error") == 0:
                    self.assertIn(result["linked_credential_errors"][label], (5, 1312, 1346, 1702), result)
        finally:
            for target in created:
                win32cred.CredDelete(target, 1, 0)

    def test_signer_production_dacl_denies_same_sid_restricted_worker(self):
        name = "\\\\.\\pipe\\AGI_like_audit_f124_" + uuid.uuid4().hex
        config = SimpleNamespace(controller_sid=self.sid,
                                 signer_sid="S-1-5-21-1-2-3-1001", worker_sid="S-1-5-21-1-2-3-1002")
        sa = pywintypes.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            audit_signer_pipe.pipe_sddl(config), 1)
        pipe = win32pipe.CreateNamedPipe(name, 3 | 0x80000, 0x8, 1, 4096, 4096, 1000, sa)
        try:
            # An unrestricted controller can connect to this same existing pipe.
            client = win32file.CreateFile(name, 0x12019B, 0, None, 3, 0, None)
            client.Close()
            win32pipe.DisconnectNamedPipe(pipe)
            result, _, _ = self.run_probe({"pipe": name})
            self.assertEqual(result["pipe_error"], 5)
        finally:
            pipe.Close()

    def test_only_standard_pipes_inherited_and_large_output_drained(self):
        sa = pywintypes.SECURITY_ATTRIBUTES()
        sa.bInheritHandle = True
        event = win32event.CreateEvent(sa, True, False, None)
        try:
            result, out, err = self.run_probe({"event": int(event), "bulk": True})
            self.assertFalse(result["inherited_event"])
            self.assertEqual(win32event.WaitForSingleObject(event, 0), win32event.WAIT_TIMEOUT)
            self.assertGreater(len(out), 150000)
            self.assertEqual(len(err.strip()), 150000)
        finally:
            event.Close()

    def test_authorized_loopback_channel_works(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(10)
            errors = []
            def echo():
                try:
                    with server.accept()[0] as peer:
                        peer.settimeout(5)
                        peer.sendall(peer.recv(32))
                except Exception as exc:
                    errors.append(type(exc).__name__)
            thread = threading.Thread(target=echo, daemon=True)
            thread.start()
            result, _, _ = self.run_probe({"port": server.getsockname()[1]})
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(result["broker"], "broker-probe")

    def test_job_close_kills_running_worker_and_descendant(self):
        code = ("import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-I','-B','-c','import time;time.sleep(60)']); "
                "print(child.pid,flush=True);time.sleep(60)")
        process, job, out, err = pty.create_contained_process(
            [str(self.python), "-I", "-B", "-c", code],
            cwd=self.root, env={"SystemRoot": os.environ["SystemRoot"]}, restricted_worker=True)
        child = None
        try:
            deadline = time.monotonic() + 10
            while not out.text.strip() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(out.text.strip(), err.text)
            child = win32api.OpenProcess(0x100000, False, int(out.text.strip()))
            self.assertIsNone(process.poll())
            self.assertEqual(win32event.WaitForSingleObject(child, 0), win32event.WAIT_TIMEOUT)
            pty.close_job(job)
            job = None
            # KILL_ON_JOB_CLOSE may report exit code zero. Completion within
            # five seconds (rather than the 60s sleep) proves termination.
            process.wait(5)
            self.assertEqual(win32event.WaitForSingleObject(child, 5000), win32event.WAIT_OBJECT_0)
            out.wait(5)
            err.wait(5)
        finally:
            if job:
                pty.close_job(job)
            process.wait(5)
            if child:
                child.Close()
            process.close()

    def test_drain_start_failure_reaps_process(self):
        processes = []
        spawn = sandbox.spawn_suspended
        def capture(*args):
            process = spawn(*args)
            processes.append(process)
            return process
        with patch.object(sandbox, "spawn_suspended", side_effect=capture), \
             patch.object(pty, "PipeDrain", side_effect=RuntimeError("thread unavailable")):
            with self.assertRaises(pty.PtyDaemonError):
                pty.create_contained_process([str(self.python), "-I", "-c", "import time;time.sleep(60)"],
                                             cwd=self.root, env={}, restricted_worker=True)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)
        self.assertIsNone(processes[0]._handle)
        self.assertIsNone(processes[0]._desktop)

    def test_restriction_failure_never_falls_back_to_popen(self):
        with patch.object(sandbox, "restricted_token", side_effect=sandbox.SandboxError("denied")), \
             patch.object(pty.subprocess, "Popen") as fallback:
            with self.assertRaises(pty.PtyDaemonError):
                pty.create_contained_process([str(self.python)], env={}, restricted_worker=True)
            fallback.assert_not_called()

    def test_assignment_failure_never_resumes(self):
        with patch.object(pty, "_assign_process_to_job", side_effect=OSError("denied")), \
             patch.object(pty, "_resume_process") as resume:
            with self.assertRaises(pty.PtyDaemonError):
                pty.create_contained_process([str(self.python), "-I", "-c", "raise RuntimeError()"],
                                             cwd=self.root, env={}, restricted_worker=True)
            resume.assert_not_called()

    def test_environment_drops_ambient_secrets_and_loader_overrides(self):
        base = dict(os.environ, HARNESS_WORKER_HOME=str(self.root),
                    ANTHROPIC_API_KEY="must-not-cross", HARNESS_AUDIT_SIGNER_CONFIG="private",
                    HARNESS_EGRESS_ATTESTATION="private", PYTHONPATH="controller-imports",
                    UNKNOWN_SECRET="must-not-cross")
        env = sandbox.worker_environment(base, {"ARK_API_KEY": "declared-worker-key"})
        self.assertEqual(env["ARK_API_KEY"], "declared-worker-key")
        for key in ("ANTHROPIC_API_KEY", "HARNESS_AUDIT_SIGNER_CONFIG", "HARNESS_EGRESS_ATTESTATION",
                    "PYTHONPATH", "UNKNOWN_SECRET"):
            self.assertNotIn(key, env)
        self.assertEqual(env["USERPROFILE"], str(self.root))
        with self.assertRaises(sandbox.SandboxError):
            sandbox.worker_environment({}, {})
        with self.assertRaises(sandbox.SandboxError):
            sandbox.worker_environment(base, {"PYTHONPATH": "injected"})

    def test_native_launch_preserves_model_free_guard(self):
        with patch.dict(os.environ, {"AGI_LIVE_EXECUTION_ALLOWED": "0"}), \
             patch.object(sandbox, "restricted_token") as token:
            with self.assertRaises(sandbox.SandboxError):
                sandbox.spawn_suspended([str(self.python), "controlled_hermes.py"], self.root, {})
            token.assert_not_called()

    def test_malformed_environment_fails_before_token_creation(self):
        with patch.object(sandbox, "restricted_token") as token:
            with self.assertRaises(sandbox.SandboxError):
                sandbox.spawn_suspended([str(self.python)], self.root, {"KEY": "value\0INJECTED=1"})
            token.assert_not_called()

    def test_restricted_worker_close_stdin_signals_immediate_eof(self):
        code = "import sys; data = sys.stdin.read(); print(f'EOF:{len(data)}', flush=True)"
        process, job, out, err = pty.create_contained_process(
            [str(self.python), "-I", "-B", "-c", code],
            cwd=self.root, env={"SystemRoot": os.environ["SystemRoot"]},
            restricted_worker=True, close_stdin=True)
        try:
            process.wait(timeout=5)
            out.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertIn("EOF:0", out.text)
        finally:
            pty.close_job(job)
            process.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

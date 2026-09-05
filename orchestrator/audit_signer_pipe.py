"""Bounded local Windows named-pipe transport. No TCP or pickle protocol."""
from __future__ import annotations

import json

from audit_signer_protocol import CLIENT_ACCESS, MAX_MESSAGE, SignerError, canonical

IO_TIMEOUT_MS = 5000


class PeerIdentityError(RuntimeError):
    """Fatal identity restoration failure; never handled as a request error."""


def _windows():
    import os
    if os.name != "nt":
        raise SignerError("windows_named_pipe_required")
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32file
    import win32pipe
    import win32security
    return pywintypes, win32api, win32con, win32event, win32file, win32pipe, win32security


def current_sid() -> str:
    _, api, con, _, _, _, security = _windows()
    token = security.OpenProcessToken(api.GetCurrentProcess(), con.TOKEN_QUERY)
    try:
        return security.ConvertSidToStringSid(security.GetTokenInformation(token, security.TokenUser)[0])
    finally:
        token.Close()


def pipe_sddl(config) -> str:
    # Generic write includes CREATE_PIPE_INSTANCE; grant individual rights instead.
    return (f"D:P(D;;GA;;;{config.worker_sid})(A;;GA;;;{config.signer_sid})"
            f"(A;;0x{CLIENT_ACCESS:x};;;{config.controller_sid})")


def _io(handle, operation, data=None, timeout_ms=IO_TIMEOUT_MS):
    types, _, _, event, file, pipe, _ = _windows()
    overlapped = types.OVERLAPPED()
    overlapped.hEvent = event.CreateEvent(None, True, False, None)
    buffer = file.AllocateReadBuffer(MAX_MESSAGE) if operation == "read" else data
    try:
        try:
            if operation == "connect":
                result = pipe.ConnectNamedPipe(handle, overlapped)
                # pywin32 RETURNS 535 when the client won the connect race.
                # No I/O is pending, so waiting/cancelling this OVERLAPPED can
                # hang forever. Zero is synchronous success as well.
                if result in (0, 535):
                    return None
            elif operation == "read":
                file.ReadFile(handle, buffer, overlapped)
            else:
                file.WriteFile(handle, buffer, overlapped)
        except types.error as exc:
            if operation == "connect" and exc.winerror == 535:
                return None  # Client already connected.
            if exc.winerror != 997:
                raise
        if event.WaitForSingleObject(overlapped.hEvent, timeout_ms) != event.WAIT_OBJECT_0:
            # Same-thread cancellation. Keep buffers alive until cancellation
            # completes; CancelIo alone does not mean completion.
            file.CancelIo(handle)
            try:
                file.GetOverlappedResult(handle, overlapped, True)
            except types.error:
                pass
            raise SignerError("audit_signer_io_timeout")
        count = file.GetOverlappedResult(handle, overlapped, False)
        return bytes(buffer[:count]) if operation == "read" else None
    finally:
        overlapped.hEvent.Close()


def request(config, payload: dict) -> dict:
    _, _, con, _, file, pipe, _ = _windows()
    encoded = canonical(payload)
    if len(encoded) > MAX_MESSAGE:
        raise SignerError("audit_signer_request_too_large")
    pipe.WaitNamedPipe(config.pipe, IO_TIMEOUT_MS)
    handle = file.CreateFile(
        config.pipe, CLIENT_ACCESS, 0, None, con.OPEN_EXISTING,
        con.FILE_FLAG_OVERLAPPED | 0x100000 | 0x10000, None,
    )  # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION: no delegation.
    try:
        pipe.SetNamedPipeHandleState(handle, pipe.PIPE_READMODE_MESSAGE, None, None)
        _io(handle, "write", encoded)
        raw = _io(handle, "read")
        _io(handle, "write", b"{}")  # Do not disconnect before reply is read.
        response = json.loads(raw)
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise SignerError("audit_signer_request_denied")
        return response
    finally:
        handle.Close()


def _peer(handle) -> tuple[str, bool]:
    _, api, con, _, _, pipe, security = _windows()
    security.ImpersonateNamedPipeClient(handle)
    try:
        token = security.OpenThreadToken(api.GetCurrentThread(), con.TOKEN_QUERY, True)
        try:
            sid = security.ConvertSidToStringSid(security.GetTokenInformation(token, security.TokenUser)[0])
            restricted = bool(security.IsTokenRestricted(token))
            return sid, restricted
        finally:
            token.Close()
    finally:
        try:
            security.RevertToSelf()
        except Exception as exc:
            raise PeerIdentityError("signer_impersonation_revert_failed") from exc


def serve_pipe(config, handler, stop):
    """Single server instance; caller supplies a stop Event and request handler."""
    types, _, con, _, _, pipe, security = _windows()
    attributes = types.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        pipe_sddl(config), 1)
    attributes.bInheritHandle = False
    handle = pipe.CreateNamedPipe(
        config.pipe, pipe.PIPE_ACCESS_DUPLEX | con.FILE_FLAG_OVERLAPPED | 0x80000,
        pipe.PIPE_TYPE_MESSAGE | pipe.PIPE_READMODE_MESSAGE | 0x8,
        1, MAX_MESSAGE, MAX_MESSAGE, IO_TIMEOUT_MS, attributes,
    )  # FIRST_PIPE_INSTANCE and REJECT_REMOTE_CLIENTS.
    try:
        while not stop.is_set():
            try:
                _io(handle, "connect", timeout_ms=250)
                raw = _io(handle, "read")
                sid, restricted = _peer(handle)
                try:
                    response = handler(json.loads(raw), sid, restricted)
                except Exception:
                    response = {"ok": False, "error": "request_denied"}
                encoded = canonical(response)
                if len(encoded) > MAX_MESSAGE:
                    raise SignerError("audit_signer_response_too_large")
                _io(handle, "write", encoded)
                _io(handle, "read")
            except (OSError, SignerError, ValueError, types.error):
                pass  # Never log request bodies, tokens, or private data.
            finally:
                try:
                    pipe.DisconnectNamedPipe(handle)
                except types.error:
                    pass
    finally:
        handle.Close()

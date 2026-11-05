"""Fail-closed Linux filesystem and TCP-port boundaries for executable candidates.

The caller prepares credentials before entering. These restrictions do not identify
remote hosts: TCP destinations are restricted by port, not by hostname.
"""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import platform
import sys
import sysconfig


def landlock_abi():
    if sys.platform != "linux" or platform.machine() != "x86_64":
        return -1
    return ctypes.CDLL(None, use_errno=True).syscall(444, 0, 0, 1)


def runtime_read_paths():
    import contracts
    candidates = [
        Path(__file__).resolve().parents[2], Path(contracts.__file__).resolve().parent,
        Path(sys.executable).resolve(), Path(sysconfig.get_path("stdlib")),
        Path(sysconfig.get_path("platstdlib")), Path(sysconfig.get_path("purelib")),
        Path(sysconfig.get_path("platlib")), Path("/usr/lib"), Path("/usr/local/lib"),
        Path("/lib"), Path("/lib64"), Path("/etc/ssl/certs"),
        Path("/usr/share/ca-certificates"), Path("/etc/resolv.conf"),
        Path("/etc/hosts"), Path("/etc/nsswitch.conf"), Path("/etc/gai.conf"),
        Path("/dev/urandom"), Path("/dev/random"), Path("/dev/null"),
    ]
    return list(dict.fromkeys(path.resolve() for path in candidates if path.exists()))


def _check(result, operation):
    if result < 0:
        code = ctypes.get_errno()
        raise RuntimeError(f"候选隔离失败（{operation}，errno={code}）；拒绝执行候选")
    return result


def _seccomp(libc, allow_exec):
    """Deny alternate socket families, cross-process access and FD-reopening APIs."""
    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    instructions, labels, jumps = [], {}, []

    def statement(code, value):
        instructions.append([code, 0, 0, value])

    def jump(value, yes, no):
        jumps.append((len(instructions), yes, no))
        instructions.append([0x15, 0, 0, value])

    def label(name):
        labels[name] = len(instructions)

    statement(0x20, 4)  # seccomp_data.arch
    jump(0xC000003E, "arch_ok", "deny")
    label("arch_ok")
    statement(0x20, 0)  # seccomp_data.nr
    # Reject the x32 ABI as well as privileged process and alternative I/O APIs.
    forbidden = [101, 310, 311, 438, 304, 303, 165, 166, 272, 308, 321, 298,
                 425, 426, 427, 62, 200, 234]
    if not allow_exec:
        forbidden += [59, 322]
    for index, number in enumerate(forbidden):
        jump(number, "deny", f"next_{index}")
        label(f"next_{index}")
    # A native number with the x32 syscall bit must never fall through to allow.
    statement(0x54, 0x40000000)
    jump(0x40000000, "deny", "native")
    label("native")
    statement(0x20, 0)
    jump(41, "socket", "socketpair_check")
    label("socketpair_check")
    jump(53, "socketpair", "allow")
    label("socketpair")
    statement(0x20, 16)
    jump(1, "socket_type", "deny")
    label("socket")
    statement(0x20, 16)  # args[0]: family
    jump(2, "socket_type", "ipv6")
    label("ipv6")
    jump(10, "socket_type", "deny")
    label("socket_type")
    statement(0x20, 24)  # args[1]: socket type, excluding CLOEXEC/NONBLOCK flags
    statement(0x54, 0xF)
    jump(1, "allow", "deny")
    label("deny")
    statement(0x06, 0x00050000 | errno.EPERM)
    label("allow")
    statement(0x06, 0x7FFF0000)
    for index, yes, no in jumps:
        instructions[index][1] = labels[yes] - index - 1
        instructions[index][2] = labels[no] - index - 1
        if not all(0 <= offset <= 255 for offset in instructions[index][1:3]):
            raise RuntimeError("候选隔离过滤器超出安全范围")
    filters = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
    program = Program(len(instructions), filters)
    _check(libc.syscall(317, 1, 1, ctypes.byref(program)), "seccomp TSYNC")


def restrict_process(*, read_paths, write_paths, connect_ports=(), keep_fds=(),
                     allow_exec=False, clean_environment=True):
    """Restrict this disposable process. Never call in a serving API process."""
    abi = landlock_abi()
    if abi < 4:
        raise RuntimeError("需要 Landlock ABI 4；当前环境不支持，拒绝执行候选")
    if len(list(Path("/proc/self/task").iterdir())) != 1:
        raise RuntimeError("候选隔离前已有其他线程，拒绝执行候选")
    ports = sorted(set(connect_ports))
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
        raise ValueError("候选网络端口无效")

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]

    class PortRule(ctypes.Structure):
        _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]

    libc = ctypes.CDLL(None, use_errno=True)
    filesystem_mask = (1 << 15) - 1  # ABI 3: includes REFER and TRUNCATE
    ruleset = Ruleset(filesystem_mask, 3)  # handle TCP BIND and CONNECT, default deny
    ruleset_fd = _check(libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0), "create_ruleset")
    try:
        read_access = (1 << 0) | (1 << 2) | (1 << 3)
        write_access = filesystem_mask & ~((1 << 6) | (1 << 11))
        for paths, access in ((read_paths, read_access), (write_paths, write_access)):
            for value in paths:
                path = Path(value).resolve(strict=True)
                allowed = access if path.is_dir() else access & ((1 << 0) | (1 << 1) | (1 << 2) | (1 << 14))
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = PathRule(allowed, fd)
                    _check(libc.syscall(445, ruleset_fd, 1, ctypes.byref(rule), 0), f"path:{path.name}")
                finally:
                    os.close(fd)
        for port in ports:
            rule = PortRule(2, port)  # TCP CONNECT only, never BIND
            _check(libc.syscall(445, ruleset_fd, 2, ctypes.byref(rule), 0), "network rule")
        _check(libc.prctl(38, 1, 0, 0, 0), "no_new_privs")
        _check(libc.syscall(446, ruleset_fd, 0), "restrict_self")
    finally:
        os.close(ruleset_fd)
    # Existing descriptors escape pathname checks; only explicitly owned FDs survive.
    preserved = {0, 1, 2, *keep_fds}
    for fd in range(3, 65536):
        if fd not in preserved:
            try:
                os.close(fd)
            except OSError:
                pass
    if clean_environment:
        os.environ.clear()
        os.environ.update({
            "PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0",
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
        })
    sys.dont_write_bytecode = True
    _seccomp(libc, allow_exec)
    return abi

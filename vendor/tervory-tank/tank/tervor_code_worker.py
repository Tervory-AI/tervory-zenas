"""Sandbox bootstrap; trusted only before candidate import.

Candidate code shares this interpreter, including json and worker frames. Its
stdout after import is an untrusted claim, never authenticated grading evidence.
"""
from __future__ import annotations

import ctypes
import errno
import importlib
import json
import os
from pathlib import Path
import resource
import sys

MEMORY = 128 * 1024 * 1024
OUTPUT = 16 * 1024
# Everything else, including socket, clone, exec, io_uring, mount, ptrace, IPC,
# user namespaces, memfd and resource-limit changes, fails with EPERM.
SYSCALLS = '''read write close lseek fstat newfstatat stat lstat statx openat
access faccessat faccessat2 readlink readlinkat getcwd getdents64 readv writev
pread64 pwrite64 dup dup2 dup3 fcntl unlinkat unlink rename renameat renameat2
mkdir mkdirat rmdir ftruncate fchmod fchmodat chmod truncate mmap mprotect
munmap mremap brk madvise getpid getppid gettid getuid getgid geteuid getegid
getresuid getresgid clock_gettime gettimeofday time nanosleep clock_nanosleep
rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack futex futex_time64 getrandom
getrusage uname exit exit_group'''.split()


def lockdown() -> None:
    os.closerange(3, 1024 * 1024)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError('no_new_privs unavailable')
    for kind, limits in ((resource.RLIMIT_AS, (MEMORY, MEMORY)),
                         (resource.RLIMIT_CPU, (1, 2)),
                         (resource.RLIMIT_NPROC, (1, 1)),
                         (resource.RLIMIT_FSIZE, (OUTPUT, OUTPUT)),
                         (resource.RLIMIT_NOFILE, (32, 32)),
                         (resource.RLIMIT_CORE, (0, 0))):
        resource.setrlimit(kind, limits)
    lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x00050000 | errno.EPERM)  # default errno, native arch only
    if not context:
        raise RuntimeError('seccomp context unavailable')
    try:
        for name in SYSCALLS:
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and lib.seccomp_rule_add(context, 0x7fff0000, number, 0) != 0:
                raise RuntimeError('seccomp rule unavailable')
        if lib.seccomp_load(context) != 0:
            raise RuntimeError('seccomp load failed')
    finally:
        lib.seccomp_release(context)


def probe() -> dict:
    import socket
    checks = {}
    for name, action in (
        ('network', lambda: socket.socket()),
        ('process', os.fork),
        ('exec', lambda: os.execv('/usr/bin/python3', ['python3', '-V'])),
        ('test_write', lambda: Path('/worker.py').write_text('tampered')),
        ('workspace_write', lambda: Path('/work/new.py').write_text('tampered')),
        ('host_repo', lambda: Path('/workspace/tervory-tank/AGENTS.md').read_text()),
        ('host_secret_path', lambda: Path('/home/agent/.gitconfig').read_text()),
        ('limit_raise', lambda: resource.setrlimit(resource.RLIMIT_AS, (MEMORY * 2, MEMORY * 2))),
    ):
        try:
            action()
        except (OSError, ValueError):
            checks[name] = True
        else:
            checks[name] = False
    descriptors = []
    for fd in range(3, 32):
        try:
            os.fstat(fd)
            descriptors.append(fd)
        except OSError:
            pass
    return dict(checks=checks, open_descriptors=descriptors, python=sys.version.split()[0], environment=dict(os.environ),
                uid=os.getuid(), memory_bytes=MEMORY, output_bytes=OUTPUT)


def main() -> int:
    # Refuse accidental direct invocation before reading candidate-controlled bytes.
    if (os.getpid() != 1 or os.getuid() != 65534 or os.getcwd() != '/work' or not Path('/worker.py').is_file()
            or Path('/proc').exists() or not os.statvfs('/').f_flag & os.ST_RDONLY
            or not os.statvfs('/work').f_flag & os.ST_RDONLY):
        print('{"bootstrap_error":"namespace_boundary_required"}')
        return 125
    # No workspace imports or candidate-controlled bytes are used before lockdown.
    request = json.loads(sys.stdin.read(65537))
    try:
        lockdown()
    except (OSError, RuntimeError, ValueError):
        print('{"bootstrap_error":"isolation_unavailable"}')
        return 125
    if request.get('probe') is True:
        print(json.dumps(probe(), sort_keys=True))
        return 0
    sys.path.insert(0, '/work')
    try:
        target = importlib.import_module(request['module'])
        function = getattr(target, request['function'])
        value = function if request.get('operation') == 'attribute' else function(*request['args'])
        result = dict(kind='return', value=value)
    except BaseException as error:
        result = dict(kind='exception', type=type(error).__name__)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())

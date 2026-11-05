"""CPython/WASI scratch worker: only the run directory is writable or visible.

No host shell, subprocess, socket, environment inheritance or native Python FFI
is exposed to model code. Wasmtime supplies the capability boundary without
privileged containers, namespaces, Landlock or a Docker daemon.
"""
from __future__ import annotations

import platform
import re
import shlex
import sys
from pathlib import Path


ROOT = Path("/opt/python-wasi")


def engine():
    import wasmtime
    config = wasmtime.Config()
    config.consume_fuel = True
    # Use baseline CPU features, so build-host instruction extensions are not
    # required on Boston. AOT data is generated only from the pinned WASM asset.
    config.target = platform.machine() + "-unknown-linux-gnu"
    return wasmtime.Engine(config)


def precompile():
    import wasmtime
    compiled = wasmtime.Module.from_file(engine(), str(ROOT / "python.wasm"))
    (ROOT / "python.cwasm").write_bytes(compiled.serialize())


def python_source(command: str) -> str:
    heredoc = re.fullmatch(r"python(?:3)?\s+-\s+<<['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*\n(.*)\n\1\s*", command, re.S)
    if heredoc:
        return heredoc.group(2)
    argv = shlex.split(command)
    if len(argv) == 3 and argv[0] in {"python", "python3"} and argv[1] == "-c":
        return argv[2]
    raise ValueError("Use Python -c or a Python heredoc; host shell commands are unavailable")


def main():
    import wasmtime
    work = Path(sys.argv[1]).resolve(strict=True)
    command = sys.stdin.buffer.read(64001)
    if len(command) > 64000:
        raise ValueError("Command too large")
    source = python_source(command.decode())
    runtime = engine()
    module = wasmtime.Module.deserialize_file(runtime, str(ROOT / "python.cwasm"))
    store = wasmtime.Store(runtime)
    store.set_limits(memory_size=128 * 1024 * 1024, instances=1, memories=1)
    store.set_fuel(2_000_000_000)
    wasi = wasmtime.WasiConfig()
    wasi.argv = ["python", "-P", "-B", "-c", "import os; os.chdir('/workspace');\n" + source]
    wasi.env = [("PYTHONHOME", "/python"), ("HOME", "/workspace"), ("TMPDIR", "/workspace/.tmp")]
    wasi.preopen_dir(str(ROOT / "lib"), "/python/lib", fs_mutable=False)
    wasi.preopen_dir(str(work), "/workspace", fs_mutable=True)
    wasi.inherit_stdout()
    wasi.inherit_stderr()
    store.set_wasi(wasi)
    linker = wasmtime.Linker(runtime)
    linker.define_wasi()
    instance = linker.instantiate(store, module)
    try:
        instance.exports(store)["_start"](store)
    except wasmtime.ExitTrap as exited:
        raise SystemExit(exited.code) from None


if __name__ == "__main__":
    main()

"""
WolframLanguageSession 单例。

进程级共享一个 kernel，避免每次请求都付 ~3s 启动开销。
首次调用 get_session() 触发 start()；atexit 注册 terminate()。
Kernel 路径优先用 WOLFRAM_KERNEL 环境变量；未设置时按当前系统自动查找常见安装位置。
"""

from __future__ import annotations

import atexit
import os
import platform
import shutil
import threading
from pathlib import Path

from wolframclient.evaluation import WolframLanguageSession

_session: WolframLanguageSession | None = None
_lock = threading.Lock()
_eval_lock = threading.RLock()


def _candidate_kernel_paths() -> list[str]:
    system = platform.system()
    if system == "Darwin":
        return [
            "/Applications/Wolfram.app/Contents/MacOS/WolframKernel",
            "/Applications/Mathematica.app/Contents/MacOS/WolframKernel",
        ]
    if system == "Windows":
        roots = [
            os.environ.get("ProgramW6432"),
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ]
        names = ("Wolfram", "Wolfram Research")
        products = ("Wolfram", "Mathematica", "Wolfram Engine")
        candidates: list[str] = []
        for root in [r for r in roots if r]:
            for name in names:
                for product in products:
                    base = Path(root) / name / product
                    candidates.extend(str(p) for p in base.glob("*/WolframKernel.exe"))
                    candidates.append(str(base / "WolframKernel.exe"))
        return candidates
    return [
        "/usr/local/Wolfram/WolframEngine/Executables/WolframKernel",
        "/usr/local/Wolfram/Mathematica/Executables/WolframKernel",
        "/opt/Wolfram/WolframEngine/Executables/WolframKernel",
        "/opt/Wolfram/Mathematica/Executables/WolframKernel",
    ]


def find_kernel() -> str:
    env_kernel = os.environ.get("WOLFRAM_KERNEL")
    if env_kernel:
        return env_kernel

    for executable in ("WolframKernel", "WolframKernel.exe"):
        found = shutil.which(executable)
        if found:
            return found

    for path in _candidate_kernel_paths():
        if os.path.exists(path):
            return path

    searched = "\n  - ".join(_candidate_kernel_paths())
    raise FileNotFoundError(
        "未找到 WolframKernel。请安装 Mathematica/Wolfram Engine，"
        "或设置环境变量 WOLFRAM_KERNEL 指向 WolframKernel 可执行文件。"
        f"\n当前系统：{platform.system() or 'Unknown'}"
        f"\n已尝试：\n  - {searched}"
    )


def get_session() -> WolframLanguageSession:
    global _session
    if _session is not None:
        return _session
    with _lock:
        if _session is None:
            kernel = find_kernel()
            sess = WolframLanguageSession(kernel)
            sess.start()
            atexit.register(sess.terminate)
            _session = sess
    return _session


def get_eval_lock() -> threading.RLock:
    return _eval_lock


if __name__ == "__main__":
    s = get_session()
    try:
        print("kernel ok, 1+1 =", s.evaluate("1+1"))
        print("DSolve smoke test:", s.evaluate("DSolve[y'[x]==y[x], y[x], x]"))
    finally:
        s.terminate()

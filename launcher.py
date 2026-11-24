"""tomato 一键启动：准备虚拟环境 → 安装依赖 → 启动机器人 → 打开 WebUI。

只用标准库，兼容 Windows / macOS / Linux。

用法：
    python launcher.py                # 正常启动
    python launcher.py --no-browser   # 不自动打开浏览器
    python launcher.py --reinstall    # 强制重装依赖
    python launcher.py --port 9000    # 临时换端口（也会传给 nonebot）
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MARKER = VENV_DIR / ".tomato-requirements"
ENV_FILE = ROOT / ".env"
BOOT_TIMEOUT = 90.0


def say(message: str = "") -> None:
    print(message, flush=True)


def fail(message: str) -> None:
    say(f"\n[启动失败] {message}")
    sys.exit(1)


# ------------------------------- 环境准备 -------------------------------


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def inside_project_venv() -> bool:
    try:
        return Path(sys.executable).resolve().parent.parent == VENV_DIR.resolve()
    except OSError:
        return False


def ensure_venv() -> Path:
    python = venv_python()
    if python.exists():
        return python

    if inside_project_venv():
        fail("虚拟环境不完整，请删除 .venv 目录后重新运行。")

    say(f"[1/4] 创建虚拟环境 .venv （使用 {sys.executable}）…")
    result = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if result.returncode != 0 or not venv_python().exists():
        fail("创建虚拟环境失败，请确认 Python 3.10+ 已正确安装。")
    return venv_python()


def requirements_digest() -> str:
    if not REQUIREMENTS.exists():
        return ""
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def marker_digest() -> str:
    try:
        return MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def install_dependencies(python: Path, force: bool = False) -> None:
    if not REQUIREMENTS.exists():
        fail("找不到 requirements.txt。")

    digest = requirements_digest()
    if not force and digest and digest == marker_digest():
        say("[2/4] 依赖已是最新，跳过安装。")
        return

    say("[2/4] 安装依赖（第一次可能需要几分钟）…")
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)]
    result = subprocess.run(command)
    if result.returncode != 0:
        fail("依赖安装失败，请检查网络后重试（可先换国内 pip 镜像）。")
    MARKER.write_text(digest, encoding="utf-8")


# ------------------------------- 启动与就绪 -------------------------------


def read_env_port() -> int:
    port = 8765
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().upper() == "PORT":
                port = int(value.strip().strip("\"'"))
    except (OSError, ValueError):
        pass
    return port


def health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/api/health"


def wait_until_ready(port: int, process: subprocess.Popen) -> bool:
    url = health_url(port)
    deadline = time.monotonic() + BOOT_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
    return False


def run_server(python: Path, port: int, no_browser: bool = False) -> int:
    say("[3/4] 启动机器人和 WebUI…\n")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    command = [str(python), "main.py"]
    if port != 8765:
        env["PORT"] = str(port)

    process = subprocess.Popen(command, cwd=str(ROOT), env=env)
    say(f"[4/4] 等待服务就绪（{BOOT_TIMEOUT:.0f} 秒超时）…")

    if not wait_until_ready(port, process):
        if process.poll() is None:
            say("\n服务没有在预期时间内就绪，请看上面的日志；下面继续等待进程（Ctrl+C 退出）。")
        else:
            say(f"\n进程已退出（退出码 {process.returncode}）。")

    if not no_browser:
        webbrowser.open(f"http://127.0.0.1:{port}/webui/")
    say(f"WebUI: http://127.0.0.1:{port}/webui/    （按 Ctrl+C 停止）\n")

    try:
        return process.wait()
    except KeyboardInterrupt:
        say("\n正在停止…")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


# ------------------------------- 入口 -------------------------------


def main() -> None:
    arguments = sys.argv[1:]
    if "--help" in arguments or "-h" in arguments:
        say(__doc__)
        return

    no_browser = "--no-browser" in arguments
    force = "--reinstall" in arguments
    port = read_env_port()
    for index, argument in enumerate(arguments):
        if argument == "--port" and index + 1 < len(arguments):
            port = int(arguments[index + 1])

    if not (ROOT / "main.py").exists():
        fail(f"没找到 main.py，请把启动器放在项目根目录（当前：{ROOT}）。")

    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    python = ensure_venv()
    install_dependencies(python, force=force)

    # 已经开着的话，直接打开页面，避免端口冲突。
    try:
        with urllib.request.urlopen(health_url(port), timeout=1.0) as response:
            if response.status == 200:
                say(f"[提示] WebUI 已经在运行：http://127.0.0.1:{port}/webui/")
                if not no_browser:
                    webbrowser.open(f"http://127.0.0.1:{port}/webui/")
                return
    except (urllib.error.URLError, OSError, ValueError):
        pass

    say(f"[3/4] 准备在端口 {port} 启动服务。")

    # 重新进入项目解释器运行，避免依赖装好后当前进程却是系统 Python。
    if not inside_project_venv():
        say("切换到项目虚拟环境重新执行…\n")
        subprocess.run([str(python), str(Path(__file__).resolve()), *arguments], cwd=str(ROOT))
        return

    code = run_server(python, port, no_browser=no_browser)
    if code:
        say(f"\n机器人退出，退出码 {code}。")


if __name__ == "__main__":
    main()

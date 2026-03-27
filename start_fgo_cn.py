import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_MUMU_DIR = r"C:\Program Files\Netease\MuMu Player 12\nx_main"
DEFAULT_PORT = 16384
DEFAULT_PACKAGE = "com.bilibili.fatego"


def run_cmd(cmd, timeout=15, check=False):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    output = (result.stdout or "").strip()
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{output}")
    return result.returncode, output


def start_mumu(mumu_dir: Path, index: int):
    mumu_exe = mumu_dir / "MuMuNxMain.exe"
    if not mumu_exe.exists():
        raise FileNotFoundError(f"未找到模拟器可执行文件: {mumu_exe}")

    cmd = [str(mumu_exe), "-v", str(index)]
    subprocess.Popen(
        cmd,
        cwd=str(mumu_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    print(f"[1/4] 已发送启动命令: {' '.join(cmd)}")


def wait_adb_online(adb_path: Path, serial: str, timeout_sec: int):
    deadline = time.time() + timeout_sec
    last_output = ""

    while time.time() < deadline:
        _, output = run_cmd([str(adb_path), "connect", serial], timeout=10)
        last_output = output
        if output:
            print(f"[2/4] adb connect: {output}")

        _, devices = run_cmd([str(adb_path), "devices"], timeout=10)
        for line in devices.splitlines():
            if line.startswith(serial) and "\tdevice" in line:
                print(f"[2/4] 设备已上线: {serial}")
                return True

        time.sleep(2)

    print(f"[2/4] 等待设备上线超时({timeout_sec}s)，最后输出: {last_output}")
    return False


def start_fgo(adb_path: Path, serial: str, package_name: str):
    # 先唤醒屏幕，减少首次启动失败概率
    run_cmd([str(adb_path), "-s", serial, "shell", "input", "keyevent", "224"], timeout=10)

    # monkey 启动不依赖具体 activity 名称，兼容性更好
    code, output = run_cmd(
        [str(adb_path), "-s", serial, "shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"],
        timeout=20,
    )

    if code != 0 or ("Events injected: 1" not in output and "Events injected: 1" not in output.replace("\r", "")):
        raise RuntimeError(f"FGO 启动失败，monkey 输出:\n{output}")

    print(f"[4/4] 已启动应用: {package_name}")


def main():
    parser = argparse.ArgumentParser(description="启动 MuMu12 并启动国服 FGO")
    parser.add_argument("--mumu-dir", default=DEFAULT_MUMU_DIR, help="MuMu nx_main 目录")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="ADB 端口，默认 16384")
    parser.add_argument("--index", type=int, default=0, help="MuMu 实例索引，默认 0")
    parser.add_argument("--package", default=DEFAULT_PACKAGE, help="FGO 包名，默认 com.bilibili.fatego")
    parser.add_argument("--timeout", type=int, default=120, help="等待设备上线超时秒数，默认 120")
    args = parser.parse_args()

    mumu_dir = Path(args.mumu_dir)
    adb_path = mumu_dir / "adb.exe"
    serial = f"127.0.0.1:{args.port}"

    if not mumu_dir.exists():
        raise FileNotFoundError(f"MuMu 目录不存在: {mumu_dir}")
    if not adb_path.exists():
        raise FileNotFoundError(f"未找到 adb.exe: {adb_path}")

    print("=== MuMu + FGO 启动器 ===")
    print(f"MuMu目录 : {mumu_dir}")
    print(f"ADB路径  : {adb_path}")
    print(f"设备串号 : {serial}")
    print(f"实例索引 : {args.index}")
    print(f"包名     : {args.package}")

    start_mumu(mumu_dir, args.index)
    print("[3/4] 等待模拟器与 ADB 就绪...")
    if not wait_adb_online(adb_path, serial, args.timeout):
        sys.exit(1)

    start_fgo(adb_path, serial, args.package)
    print("完成。")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)

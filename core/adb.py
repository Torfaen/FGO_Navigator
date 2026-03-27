import subprocess
from dataclasses import dataclass


@dataclass
class CmdResult:
    code: int
    output: str


class AdbClient:
    def __init__(self, adb_path: str, serial: str):
        self.adb_path = adb_path
        self.serial = serial

    def run(self, args: list[str], timeout: int = 15) -> CmdResult:
        result = subprocess.run(
            [self.adb_path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        return CmdResult(code=result.returncode, output=(result.stdout or "").strip())

    def run_raw(self, args: list[str], timeout: int = 20) -> tuple[int, bytes]:
        result = subprocess.run(
            [self.adb_path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout or b""

    def run_shell(self, shell_args: list[str], timeout: int = 15) -> CmdResult:
        return self.run(["-s", self.serial, "shell", *shell_args], timeout=timeout)

    def connect(self) -> CmdResult:
        return self.run(["connect", self.serial], timeout=10)

    def is_online(self) -> bool:
        result = self.run(["devices"], timeout=10)
        for line in result.output.splitlines():
            if line.startswith(self.serial) and "\tdevice" in line:
                return True
        return False

    def tap(self, x: int, y: int) -> CmdResult:
        return self.run_shell(["input", "tap", str(x), str(y)], timeout=10)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> CmdResult:
        return self.run_shell(
            ["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
            timeout=10,
        )

    def start_package(self, package_name: str) -> CmdResult:
        return self.run_shell(
            ["monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"],
            timeout=20,
        )

    def is_process_running(self, package_name: str) -> bool:
        # 优先使用 pidof，兼容大部分 Android 9+ 系统
        pidof_res = self.run_shell(["pidof", package_name], timeout=10)
        if pidof_res.code == 0 and pidof_res.output.strip():
            return True

        # 兜底用 ps 文本匹配
        ps_res = self.run_shell(["ps"], timeout=15)
        if ps_res.code != 0:
            return False
        for line in ps_res.output.splitlines():
            if package_name in line:
                return True
        return False

    def screencap_png(self) -> bytes:
        code, data = self.run_raw(["-s", self.serial, "exec-out", "screencap", "-p"], timeout=20)
        if code != 0 or not data:
            return b""
        return data

    def current_package(self) -> str:
        result = self.run_shell(["dumpsys", "window", "windows"], timeout=15)
        text = result.output
        marker = "mCurrentFocus="
        idx = text.find(marker)
        if idx == -1:
            return ""
        line = text[idx:].splitlines()[0]
        if "/" not in line:
            return ""
        left = line.split("/", 1)[0]
        pkg = left.split()[-1] if left.split() else ""
        return pkg

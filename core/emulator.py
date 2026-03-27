import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from core.adb import AdbClient


def _read_simple_yaml(path: str) -> dict[str, str]:
    data: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            data[key] = value
    return data


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str, default: bool = False) -> bool:
    text = (value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass
class EmulatorConfig:
    mumu_dir: str
    mumu_index: int
    adb_path: str
    adb_port: int
    package_name: str
    adb_connect_timeout_sec: int
    home_wait_timeout_sec: int
    chapter_templates: list[str]
    target_stage_template: str
    target_stage_threshold: float
    bbchannel_enabled: bool
    bbchannel_debug_cmd: str
    bbchannel_workdir: str
    bbchannel_monitor_sec: int


class EmulatorLauncher:
    def __init__(self, config: EmulatorConfig):
        self.config = config
        self.mumu_dir = Path(config.mumu_dir)
        self.adb_path = Path(config.adb_path) if config.adb_path else (self.mumu_dir / "adb.exe")
        self.serial = f"127.0.0.1:{config.adb_port}"
        self.adb = AdbClient(str(self.adb_path), self.serial)

    @classmethod
    def from_config_file(cls, path: str) -> "EmulatorLauncher":
        data = _read_simple_yaml(path)
        config = EmulatorConfig(
            mumu_dir=data.get("mumu_dir", r"C:/Program Files/Netease/MuMu Player 12/nx_main"),
            mumu_index=int(data.get("mumu_index", 0)),
            adb_path=data.get("adb_path", ""),
            adb_port=int(data.get("adb_port", 16384)),
            package_name=data.get("package_name", "com.bilibili.fatego"),
            adb_connect_timeout_sec=int(data.get("adb_connect_timeout_sec", 120)),
            home_wait_timeout_sec=int(data.get("home_wait_timeout_sec", 180)),
            chapter_templates=_parse_csv_list(
                data.get(
                    "chapter_templates",
                    "assets/templates/chapter/ordeal_call.png,assets/templates/chapter/ordeal_call_I.png",
                )
            ),
            target_stage_template=data.get("target_stage_template", "assets/templates/chapter/ordeal_call.png"),
            target_stage_threshold=float(data.get("target_stage_threshold", 0.78)),
            bbchannel_enabled=_parse_bool(data.get("bbchannel_enabled", "false"), default=False),
            bbchannel_debug_cmd=data.get("bbchannel_debug_cmd", r"C:/Users/ASUS/Desktop/auto/BBchannel/启动_debug.cmd"),
            bbchannel_workdir=data.get("bbchannel_workdir", r"C:/Users/ASUS/Desktop/auto/BBchannel"),
            bbchannel_monitor_sec=int(data.get("bbchannel_monitor_sec", 20)),
        )
        return cls(config)

    def start_emulator(self) -> None:
        mumu_exe = self.mumu_dir / "MuMuNxMain.exe"
        if not mumu_exe.exists():
            raise FileNotFoundError(f"未找到模拟器可执行文件: {mumu_exe}")
        if not self.adb_path.exists():
            raise FileNotFoundError(f"未找到 adb.exe: {self.adb_path}")

        subprocess.Popen(
            [str(mumu_exe), "-v", str(self.config.mumu_index)],
            cwd=str(self.mumu_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        print(f"[启动] MuMu 已启动: {mumu_exe} -v {self.config.mumu_index}")

    def wait_adb_online(self) -> bool:
        deadline = time.time() + self.config.adb_connect_timeout_sec
        while time.time() < deadline:
            message = self.adb.connect().output
            if message:
                print(f"[ADB] {message}")
            if self.adb.is_online():
                print(f"[ADB] 设备在线: {self.serial}")
                return True
            time.sleep(2)
        print("[ADB] 设备连接超时")
        return False

    def start_and_connect(self) -> bool:
        self.start_emulator()
        return self.wait_adb_online()

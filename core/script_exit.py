"""脚本结束时统一行为：关游戏 / 关模拟器 / 不操作。"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.emulator import EmulatorLauncher


def _read_simple_yaml(path: str) -> dict[str, str]:
    data: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return data
    with open(p, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip("'").strip('"')
    return data


def _adb_candidates(cfg: dict[str, str]) -> list[Path]:
    out: list[Path] = []
    ap = (cfg.get("adb_path") or "").strip()
    if ap:
        out.append(Path(ap))
    md = (cfg.get("mumu_dir") or "").strip()
    if md:
        out.append(Path(md) / "adb.exe")
    out.extend(
        [
            Path(r"C:/Program Files/Netease/MuMu Player 12/nx_main/adb.exe"),
            Path(r"C:/Users/ASUS/Desktop/auto/BBchannel/adb/adb.exe"),
        ]
    )
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        s = str(p.resolve()) if p.exists() else str(p)
        if s not in seen:
            seen.add(s)
            uniq.append(p)
    return uniq


def force_stop_game(cfg: dict[str, str]) -> None:
    """仅 force-stop 游戏包体。"""
    package = cfg.get("package_name", "com.bilibili.fatego")
    serial = f"127.0.0.1:{int(cfg.get('adb_port', '16384'))}"
    commands: list[list[str]] = []
    for adb in _adb_candidates(cfg):
        if adb.exists():
            commands.append([str(adb), "-s", serial, "shell", "am", "force-stop", package])
            commands.append([str(adb), "shell", "am", "force-stop", package])
    if not commands:
        print("[Exit] 未找到可用 adb.exe，无法关闭游戏进程")
        return
    for cmd in commands:
        try:
            r = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10,
                check=False,
            )
            print(f"[Exit] 执行: {' '.join(cmd)}")
            if r.stdout:
                print(r.stdout.strip())
            if r.returncode == 0:
                print("[Exit] 已关闭游戏进程（模拟器可保留）")
                return
        except Exception as exc:
            print(f"[Exit] 关闭游戏异常: {exc}")


def close_emulator_mumu(cfg: dict[str, str]) -> None:
    """尝试结束 MuMu 主进程（Windows）。"""
    mumu_dir = (cfg.get("mumu_dir") or "").strip()
    # 常见进程名：MuMuNxMain.exe
    names = ["MuMuNxMain.exe", "MuMuNxDevice.exe"]
    for name in names:
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/IM", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=15,
                check=False,
            )
            print(f"[Exit] taskkill {name}: {r.stdout or r.stderr or r.returncode}")
            if r.returncode == 0:
                print("[Exit] 已请求结束模拟器相关进程")
                return
        except Exception as exc:
            print(f"[Exit] taskkill {name} 异常: {exc}")
    if mumu_dir:
        print(f"[Exit] 若模拟器仍在运行，请检查进程名或手动关闭（mumu_dir={mumu_dir}）")


def apply_script_end_action(
    action: str | None,
    config_path: str = "config/settings.yaml",
    cfg: dict[str, str] | None = None,
) -> None:
    """
    script_end_action:
      - none / maintain / keep — 不操作
      - close_game — adb force-stop 包体
      - close_emulator — taskkill MuMu 主进程（再关游戏可另配）
    """
    data = cfg if cfg is not None else _read_simple_yaml(config_path)
    raw = (action if action is not None else data.get("script_end_action", "none")).strip().lower()
    if raw in ("", "none", "maintain", "keep", "原样"):
        print("[Exit] script_end_action=维持原样，不关闭游戏/模拟器")
        return
    if raw in ("close_game", "game", "stop_game", "关游戏"):
        print("[Exit] script_end_action=关闭游戏")
        force_stop_game(data)
        return
    if raw in ("close_emulator", "emulator", "mumu", "关模拟器"):
        print("[Exit] script_end_action=关闭模拟器")
        close_emulator_mumu(data)
        return
    print(f"[Exit] 未知 script_end_action={raw!r}，忽略")


def apply_script_end_action_for_launcher(launcher: "EmulatorLauncher") -> None:
    """从 EmulatorLauncher 读配置并执行（main 用）。"""
    cfg = {
        "mumu_dir": launcher.config.mumu_dir,
        "adb_path": str(launcher.adb_path),
        "adb_port": str(launcher.config.adb_port),
        "package_name": launcher.config.package_name,
    }
    apply_script_end_action(launcher.config.script_end_action, cfg=cfg)

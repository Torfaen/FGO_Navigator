import sys
from datetime import datetime
from pathlib import Path

from core.action import wait_until
from core.bbchannel import launch_debug_and_monitor, run_forever
from core.emulator import EmulatorLauncher
from core.script_exit import apply_script_end_action_for_launcher
from flows.startup_flow import StartupFlow

# 与 BAAH 类工具一致：`X.X.X - MM:SS - LEVEL : 正文`（1-based 第 9～13 列为 MM:SS）
# 版本号须保持 5 字符（如 1.0.0），否则外部「时间戳列号」需重算
SCRIPT_LOG_VERSION = "1.0.0"


class _Tee:
    def __init__(self, *streams, level: str = "INFO"):
        self.streams = streams
        self._line_start = True
        self.level = level

    def write(self, data):
        if not data:
            return
        chunks = data.splitlines(keepends=True)
        out = ""
        for chunk in chunks:
            if self._line_start and chunk not in ("\n", "\r\n"):
                out += (
                    f"{SCRIPT_LOG_VERSION} - {datetime.now():%M:%S} - {self.level} : "
                )
            out += chunk
            self._line_start = chunk.endswith("\n")
        for s in self.streams:
            s.write(out)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def main() -> int:
    logs_dir = Path("DATA") / "LOGS"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts_log_path = logs_dir / f"log_{datetime.now():%Y-%m-%d-%H-%M-%S}.log"
    legacy_log_path = Path("fgo_runtime.log")
    with open(ts_log_path, "w", encoding="utf-8") as ts_log_file, open(
        legacy_log_path, "w", encoding="utf-8"
    ) as legacy_log_file:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = _Tee(old_stdout, ts_log_file, legacy_log_file, level="INFO")
        sys.stderr = _Tee(old_stderr, ts_log_file, legacy_log_file, level="ERROR")
        try:
            code = _run_main()
            if code == 0:
                print("所有任务结束")
            else:
                print(f"保存全异常日志到文件: {ts_log_path}")
            return code
        except Exception:
            print(f"保存全异常日志到文件: {ts_log_path}")
            raise
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr


def _run_main() -> int:
    launcher = EmulatorLauncher.from_config_file("config/settings.yaml")
    if not launcher.start_and_connect():
        return 1

    flow = StartupFlow(
        launcher.adb,
        package_name=launcher.config.package_name,
        chapter_templates=launcher.config.chapter_templates,
        target_stage_template=launcher.config.target_stage_template,
        target_stage_threshold=launcher.config.target_stage_threshold,
    )
    ok = wait_until(
        flow.run_until_home,
        timeout_sec=launcher.config.home_wait_timeout_sec,
        interval_sec=0.5,
    )
    if flow.ordeal_call_handler.fatal_error:
        print(flow.ordeal_call_handler.fatal_error)
        apply_script_end_action_for_launcher(launcher)
        return 3
    if not ok:
        apply_script_end_action_for_launcher(launcher)
        return 2
    if launcher.config.bbchannel_enabled:
        code = launch_debug_and_monitor(
            cmd_path=launcher.config.bbchannel_debug_cmd,
            workdir=launcher.config.bbchannel_workdir,
            monitor_sec=launcher.config.bbchannel_monitor_sec,
        )
        if code == 0:
            run_forever()
    apply_script_end_action_for_launcher(launcher)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

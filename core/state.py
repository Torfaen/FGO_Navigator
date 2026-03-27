from enum import Enum

from core.adb import AdbClient


class GameState(str, Enum):
    UNKNOWN = "unknown"
    APP_NOT_RUNNING = "app_not_running"
    IN_FGO_NOT_HOME = "in_fgo_not_home"
    HOME = "home"


class StateDetector:
    def __init__(self, adb: AdbClient, package_name: str):
        self.adb = adb
        self.package_name = package_name

    def detect(self) -> GameState:
        pkg = self.adb.current_package()
        if not pkg:
            return GameState.UNKNOWN
        if self.package_name not in pkg:
            return GameState.APP_NOT_RUNNING

        # 当前是占位逻辑：进入FGO进程即判定为“未到主界面”
        # 后续可接入截图模板/OCR后再精确区分 HOME 与其他页面。
        return GameState.IN_FGO_NOT_HOME

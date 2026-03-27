from pathlib import Path
import time
from typing import List, Optional

from core.adb import AdbClient
from core.vision import MatchResult, TemplateMatcher

# 与 StartupFlow.close_matcher 相同：assets/templates/icon/close.png
_CLOSE_TEMPLATE_SCALES: List[float] = [
    round(2.0 - i * (1.7 / 9), 4) for i in range(10)
]


class OrdealCallHandler:
    """Ordeal Call 专用处理：先点“上一次”，再执行滑动搜索。"""

    def __init__(self) -> None:
        self.free_ordeal_matcher = TemplateMatcher(
            str(Path("assets/templates/icon/free_ordeal.png")),
            threshold=0.75,
            use_gray=True,
            scales=[1.0, 0.62, 0.64, 0.66, 0.67, 0.68, 0.7],
        )
        _last_time_scales = [1.0, 0.8, 0.7, 0.62, 0.6, 0.55, 0.5, 0.45, 0.4, 0.35, 0.3]
        self.last_time_matcher = TemplateMatcher(
            str(Path("assets/templates/chapter/mission/last_time.png")),
            threshold=0.75,
            use_gray=True,
            scales=_last_time_scales,
        )
        self.mission_start_matcher = TemplateMatcher(
            str(Path("assets/templates/icon/mission_start.png")),
            threshold=0.75,
            use_gray=True,
            scales=[1.0, 0.8, 0.7, 0.62, 0.6, 0.55, 0.5, 0.45, 0.4, 0.35, 0.3],
        )
        self.mission_fail_close_matcher = TemplateMatcher(
            str(Path("assets/templates/icon/close.png")),
            threshold=0.78,
            use_gray=True,
            scales=_CLOSE_TEMPLATE_SCALES,
        )
        self.last_time_clicked = False
        self.free_ordeal_checked_once = False
        self.last_time_search_down_swipe_done = 0
        self.last_time_search_right_swipe_done = 0
        self.mission_search_down_swipe_done = 0
        self.mission_clicked = False
        self.fatal_error: Optional[str] = None  # 进入关卡失败等，置位后由 StartupFlow 结束 wait 并由 main 收尾

    def reset(self) -> None:
        self.last_time_clicked = False
        self.free_ordeal_checked_once = False
        self.last_time_search_down_swipe_done = 0
        self.last_time_search_right_swipe_done = 0
        self.mission_search_down_swipe_done = 0
        self.mission_clicked = False
        self.fatal_error = None

    def handle(
        self,
        adb: AdbClient,
        target_matcher: TemplateMatcher,
        shot: bytes,
        stage_prefix: str,
    ) -> tuple[bool, bool, MatchResult]:
        """返回 (handled, done, match_result)。"""
        if not shot:
            return True, False, MatchResult(False, 0.0, 0, 0)

        # 第一步：进入地球仪后优先匹配并点击一次“上一次”
        if not self.last_time_clicked:
            # 节点2前置：free_ordeal 只检测一次（后续滑动阶段不再检测）
            if not self.free_ordeal_checked_once:
                self.free_ordeal_checked_once = True
                time.sleep(1.0)
                for i in range(2):
                    free_shot = shot if i == 0 else adb.screencap_png()
                    match_free = self.free_ordeal_matcher.match_png_bytes(free_shot)
                    print(
                        f"{stage_prefix} [Match] free_ordeal try={i + 1}/2 score={match_free.score:.4f} ({match_free.score * 100:.2f}%)"
                    )
                    if match_free.score >= self.free_ordeal_matcher.threshold:
                        self.last_time_clicked = True
                        print(f"{stage_prefix} [Flow] 命中 free_ordeal，跳过 last_time，直接进入节点3")
                        return True, False, MatchResult(False, 0.0, 0, 0)

            match_last = self.last_time_matcher.match_png_bytes(shot)
            print(
                f"{stage_prefix} [Match] last_time score={match_last.score:.4f} ({match_last.score * 100:.2f}%)"
            )
            if match_last.score >= self.last_time_matcher.threshold:
                # 匹配中心偏上一点，更接近「上一次」可点区域
                tap_x = match_last.center_x
                tap_y = max(0, match_last.center_y - 20)
                adb.tap(tap_x, tap_y)
                self.last_time_clicked = True
                print(
                    f"{stage_prefix} [Flow] 命中 last_time 并点击({tap_x},{tap_y})（中心上移20px），开始找目标小关卡"
                )
                return True, False, MatchResult(False, 0.0, 0, 0)

            # 第二节点：只负责找 last_time（找到并点击前，不进入第三节点）
            if self.last_time_search_down_swipe_done < 2:
                adb.swipe(640, 360, 640, 680, duration_ms=450)
                self.last_time_search_down_swipe_done += 1
                print(
                    f"{stage_prefix} [Flow] 未命中 last_time，执行向下大划({self.last_time_search_down_swipe_done}/2)"
                )
                time.sleep(1.0)
                return True, False, MatchResult(False, 0.0, 0, 0)

            adb.swipe(540, 360, 740, 360, duration_ms=420)
            self.last_time_search_right_swipe_done += 1
            print(f"{stage_prefix} [Flow] 未命中 last_time，执行向右慢划继续搜索")
            if self.last_time_search_right_swipe_done % 5 == 0:
                adb.swipe(640, 360, 640, 320, duration_ms=220)
                print(
                    f"{stage_prefix} [Flow] last_time 向右慢划累计{self.last_time_search_right_swipe_done}次，追加一次向上小划"
                )
            time.sleep(1.0)
            return True, False, MatchResult(False, 0.0, 0, 0)

        # 第三节点：匹配并点击目标小关卡
        if not self.mission_clicked:
            match_target = target_matcher.match_png_bytes(shot)
            print(
                f"{stage_prefix} [Match] mission:{target_matcher.template_path.stem} score={match_target.score:.4f} ({match_target.score * 100:.2f}%)"
            )
            if match_target.score >= target_matcher.threshold:
                print(
                    f"{stage_prefix} [Flow] 命中小关卡 {target_matcher.template_path.stem}，点击({match_target.center_x},{match_target.center_y})，进入节点4"
                )
                adb.tap(match_target.center_x, match_target.center_y)
                self.mission_clicked = True
                time.sleep(1.0)
                return True, False, match_target

            # 找不到：先从屏幕中心向下大划 2 下
            if self.mission_search_down_swipe_done < 2:
                adb.swipe(640, 360, 640, 680, duration_ms=450)
                self.mission_search_down_swipe_done += 1
                print(f"{stage_prefix} [Flow] 未命中目标，执行向下大划({self.mission_search_down_swipe_done}/2)")
                time.sleep(1.0)
                return True, False, match_target

            # 节点2通过后不再执行向右慢划，仅继续等待下一轮截图匹配
            print(f"{stage_prefix} [Flow] 未命中目标，保持当前位置等待下一轮匹配")
            time.sleep(1.0)
            return True, False, match_target

        # 第四节点：无法开战弹窗上的「关闭」→ 致命错误并结束脚本（由 main 执行 script_end_action）
        match_fail = self.mission_fail_close_matcher.match_png_bytes(shot)
        print(
            f"{stage_prefix} [Match] close (无法开战) score={match_fail.score:.4f} ({match_fail.score * 100:.2f}%)"
        )
        if match_fail.score >= self.mission_fail_close_matcher.threshold:
            adb.tap(match_fail.center_x, match_fail.center_y)
            self.fatal_error = "[异常] 进入关卡失败（无法开战弹窗，已尝试点「关闭」）"
            print(f"{stage_prefix} [Flow] {self.fatal_error}")
            time.sleep(0.3)
            return True, False, match_fail

        match_start = self.mission_start_matcher.match_png_bytes(shot)
        print(
            f"{stage_prefix} [Match] mission_start score={match_start.score:.4f} ({match_start.score * 100:.2f}%)"
        )
        if match_start.score >= self.mission_start_matcher.threshold:
            adb.tap(match_start.center_x, match_start.center_y)
            print(
                f"{stage_prefix} [Flow] 命中 mission_start 并点击({match_start.center_x},{match_start.center_y})，Ordeal 流程完成"
            )
            return True, True, match_start

        time.sleep(1.0)
        return True, False, match_start


import time
from pathlib import Path
from typing import List, Optional

from core.adb import AdbClient
from core.state import GameState, StateDetector
from core.vision import TemplateMatcher
from flows.ordeal_call_handler import OrdealCallHandler

# close「关闭」：倍率 2.0～0.3（含），均匀 10 档
CLOSE_TEMPLATE_SCALES: List[float] = [
    round(2.0 - i * (1.7 / 9), 4) for i in range(10)
]

# login_0 / login_1 / update（开始更新资料）共用倍率
_LOGIN_SCALES: List[float] = [1.0, 0.62, 0.64, 0.66, 0.67, 0.68, 0.7]


class StartupFlow:
    def __init__(
        self,
        adb: AdbClient,
        package_name: str = "com.bilibili.fatego",
        chapter_templates: Optional[List[str]] = None,
        target_stage_template: str = "assets/templates/chapter/ordeal_call.png",
        target_stage_threshold: float = 0.78,
    ):
        self.adb = adb
        self.package_name = package_name
        self.detector = StateDetector(adb, package_name)
        self.last_action_ts = 0.0
        self.in_fgo_since = 0.0
        self.latest_screen_path = Path("latest_screen.png")
        self.templates_dir = Path("assets/templates")
        self.login_prompt_matcher = TemplateMatcher(
            str(self.templates_dir / "login_0.png"),
            threshold=0.85,
            scales=_LOGIN_SCALES,
        )
        self.login_confirm_matcher = TemplateMatcher(
            str(self.templates_dir / "login_1.png"),
            threshold=0.85,
            scales=_LOGIN_SCALES,
        )
        self.update_matcher = TemplateMatcher(
            str(self.templates_dir / "icon" / "update.png"),
            threshold=0.85,
            scales=_LOGIN_SCALES,
        )
        self.close_notice_matcher = TemplateMatcher(
            str(self.templates_dir / "icon" / "close_ann.png"),
            threshold=0.72,
            use_gray=True,
            scales=[1.0, 0.62, 0.64, 0.66, 0.67, 0.68, 0.7],
        )
        self.close_matcher = TemplateMatcher(
            str(self.templates_dir / "icon" / "close.png"),
            threshold=0.78,
            use_gray=True,
            scales=CLOSE_TEMPLATE_SCALES,
        )
        # 大章节模板组：按配置文件给定顺序逐个匹配
        self.chapter_matchers: list[tuple[str, TemplateMatcher]] = []
        chapter_templates = chapter_templates or []
        for chapter_template in chapter_templates:
            path = Path(chapter_template)
            matcher = TemplateMatcher(
                str(path),
                threshold=0.78,
                use_gray=True,
                scales=[1.0, 0.62, 0.64, 0.66, 0.67, 0.68, 0.7],
            )
            self.chapter_matchers.append((path.stem, matcher))

        self.target_stage_template = Path(target_stage_template)
        self.target_stage_name = self.target_stage_template.stem
        self.special_ordeal_call_name = Path("assets/templates/chapter/ordeal_call.png").stem
        # 小关卡模板：命中大章节后开始匹配
        self.target_stage_matcher = TemplateMatcher(
            str(self.target_stage_template),
            threshold=target_stage_threshold,
            use_gray=True,
            scales=[1.0, 0.62, 0.64, 0.66, 0.67, 0.68, 0.7],
        )
        self.event_reward_matcher = TemplateMatcher(
            str(self.templates_dir / "icon" / "event_reward.png"),
            threshold=0.78,
            use_gray=True,
            scales=[1.0, 0.62, 0.64, 0.66, 0.67, 0.68, 0.7],
        )
        self.ordeal_call_handler = OrdealCallHandler()
        # close_ann 点击后：先 post_ann_close 阶段 5 秒（仅连点 close），结束后再进入 main_screen
        self.post_ann_close_deadline: float = 0.0  # >0 且 now<deadline 表示在连点窗口内；到点则本帧切入 main_screen
        self.last_close_burst_tap_ts: float = 0.0
        self.close_burst_min_interval_sec: float = 0.15
        self.close_burst_duration_sec: float = 5.0
        self.login1_mode = False
        self._skip_update_after_login_seen = False  # 已识别 login 界面后不再检测 update
        self.main_pre_mode = False
        self.main_screen_mode = False
        self.chapter_selected = False
        self.selected_chapter_name = ""
        self.running_entry_checked = False
        self.last_score_log_ts = {}
        self.last_state_value = ""
        self.last_stage = ""

    def _stage_prefix(self) -> str:
        stage = self.last_stage if self.last_stage else "init"
        return f"[Phase : {stage}]"

    def _throttle(self, sec: float = 1.0) -> bool:
        now = time.time()
        if now - self.last_action_ts < sec:
            return False
        self.last_action_ts = now
        return True

    def _dump_latest_screen(self, shot: bytes) -> None:
        if not shot:
            return
        self.latest_screen_path.write_bytes(shot)

    def _log_match_score(self, name: str, score: float) -> None:
        now = time.time()
        last_ts = self.last_score_log_ts.get(name, 0.0)
        if now - last_ts > 1.0:
            self.last_score_log_ts[name] = now
            print(f"{self._stage_prefix()} [Match] {name} score={score:.4f} ({score * 100:.2f}%)")

    def _log_state_if_changed(self, state_value: str) -> None:
        if state_value != self.last_state_value:
            self.last_state_value = state_value
            print(f"{self._stage_prefix()} [Flow] 当前状态: {state_value}")

    def _log_stage_if_changed(self, stage: str) -> None:
        if stage != self.last_stage:
            self.last_stage = stage
            print(f"{self._stage_prefix()} [Flow] 阶段切换: {stage}")

    def _handle_pre_home(self, shot: bytes) -> bool:
        now = time.time()
        # close_ann 之后：未满 5 秒前不进入 main_screen，只识别并连点 close
        if self.post_ann_close_deadline > 0:
            if now < self.post_ann_close_deadline:
                self._log_stage_if_changed("main_pre")
                self._dump_latest_screen(shot)
                match_burst = self.close_matcher.match_png_bytes(shot)
                self._log_match_score("close_post_ann", match_burst.score)
                if match_burst.score >= self.close_matcher.threshold:
                    if now - self.last_close_burst_tap_ts >= self.close_burst_min_interval_sec:
                        self.last_close_burst_tap_ts = now
                        self.adb.tap(match_burst.center_x, match_burst.center_y)
                        remain = max(0.0, self.post_ann_close_deadline - now)
                        print(
                            f"{self._stage_prefix()} [Flow] main_pre 连点 close 模板 "
                            f"score={match_burst.score:.3f}, 点击({match_burst.center_x},{match_burst.center_y}), "
                            f"剩余约 {remain:.1f}s"
                        )
                return False
            self.post_ann_close_deadline = 0.0
            self.main_screen_mode = True
            self.chapter_selected = False
            self.selected_chapter_name = ""
            self.ordeal_call_handler.reset()
            self._log_stage_if_changed("main_screen")
            print(f"{self._stage_prefix()} [Flow] main_pre 5s 连点结束，进入 main_screen")

        if self.main_screen_mode:
            self._log_stage_if_changed("main_screen")
            self._dump_latest_screen(shot)
            # close「关闭」仅在 main_pre（close_ann 后 5s 连点窗口）内匹配，main_screen 不再检测

            if not self.chapter_selected:
                best_chapter_name = ""
                best_chapter_score = 0.0
                best_matcher = None
                best_match = None
                for chapter_name, chapter_matcher in self.chapter_matchers:
                    chapter_match = chapter_matcher.match_png_bytes(shot)
                    self._log_match_score(f"chapter:{chapter_name}", chapter_match.score)
                    if chapter_match.score > best_chapter_score:
                        best_chapter_score = chapter_match.score
                        best_chapter_name = chapter_name
                        best_matcher = chapter_matcher
                        best_match = chapter_match

                if best_matcher is not None and best_chapter_score >= best_matcher.threshold:
                    self.chapter_selected = True
                    self.selected_chapter_name = best_chapter_name
                    if self.selected_chapter_name == self.special_ordeal_call_name:
                        self.ordeal_call_handler.reset()
                    if best_match is not None and self._throttle(0.8):
                        self.adb.tap(best_match.center_x, best_match.center_y)
                        if self.selected_chapter_name == self.special_ordeal_call_name:
                            print(
                                f"{self._stage_prefix()} [Flow] 命中大章节 {best_chapter_name}，点击({best_match.center_x},{best_match.center_y})后进入 Ordeal Call 专用处理"
                            )
                        else:
                            print(
                                f"{self._stage_prefix()} [Flow] 命中大章节 {best_chapter_name}，点击({best_match.center_x},{best_match.center_y})后开始识别小关卡"
                            )
                    else:
                        if self.selected_chapter_name == self.special_ordeal_call_name:
                            print(f"{self._stage_prefix()} [Flow] 命中大章节 {best_chapter_name}，进入 Ordeal Call 专用处理")
                        else:
                            print(f"{self._stage_prefix()} [Flow] 命中大章节 {best_chapter_name}，开始识别小关卡")
                    return False

                if self._throttle(0.9):
                    self.adb.swipe(740, 390, 740, 220, duration_ms=300)
                    print(f"{self._stage_prefix()} [Flow] 未命中任何大章节，执行上滑(740,390)->(740,220)")
                return False

            if self.selected_chapter_name == self.special_ordeal_call_name:
                handled, done, match_target = self.ordeal_call_handler.handle(
                    adb=self.adb,
                    target_matcher=self.target_stage_matcher,
                    shot=shot,
                    stage_prefix=self._stage_prefix(),
                )
                if self.ordeal_call_handler.fatal_error:
                    return True
                if handled:
                    return done
            else:
                match_target = self.target_stage_matcher.match_png_bytes(shot)

            self._log_match_score(f"mission:{self.target_stage_name}", match_target.score)
            if match_target.score >= self.target_stage_matcher.threshold:
                print(
                    f"{self._stage_prefix()} [Flow] 命中小关卡 {self.target_stage_name}（章节:{self.selected_chapter_name}），停止滑动"
                )
                return True
            if self._throttle(0.9):
                self.adb.swipe(740, 390, 740, 220, duration_ms=300)
                print(
                    f"{self._stage_prefix()} [Flow] 未命中小关卡 {self.target_stage_name}，执行上滑(740,390)->(740,220)"
                )
            return False

        if self.main_pre_mode:
            self._log_stage_if_changed("main_pre")
            self._dump_latest_screen(shot)
            match_close = self.close_notice_matcher.match_png_bytes(shot)
            self._log_match_score("close_ann", match_close.score)
            if match_close.score >= self.close_notice_matcher.threshold and self._throttle(0.8):
                self.adb.tap(match_close.center_x, match_close.center_y)
                print(
                    f"{self._stage_prefix()} [Flow] 命中 close_ann 模板 score={match_close.score:.3f}, 点击({match_close.center_x},{match_close.center_y})"
                )
                self.post_ann_close_deadline = time.time() + self.close_burst_duration_sec
                self.last_close_burst_tap_ts = 0.0
                self.main_pre_mode = False
                self.main_screen_mode = False
                self._log_stage_if_changed("main_pre")
                return False
            return False

        self._log_stage_if_changed("logining")
        if self.in_fgo_since == 0.0:
            self.in_fgo_since = time.time()

        self._dump_latest_screen(shot)
        login_th = 0.85
        match1 = self.login_confirm_matcher.match_png_bytes(shot)
        match0 = self.login_prompt_matcher.match_png_bytes(shot)
        if match1.score >= login_th or match0.score >= login_th:
            self._skip_update_after_login_seen = True

        if not self._skip_update_after_login_seen:
            match_update = self.update_matcher.match_png_bytes(shot)
            self._log_match_score("update", match_update.score)
            if match_update.score >= login_th and self._throttle(0.8):
                self.adb.tap(match_update.center_x, match_update.center_y)
                print(
                    f"{self._stage_prefix()} [Flow] 命中 update 模板 score={match_update.score:.3f}, "
                    f"点击({match_update.center_x},{match_update.center_y})"
                )
                return False

        # 一旦检测到 login_1，停止 login_0 检测
        if match1.score >= login_th and not self.login1_mode:
            self.login1_mode = True
            print(f"{self._stage_prefix()} [Flow] 检测到 login_1，停止 login_0 检测")

        if self.login1_mode:
            self._log_match_score("login_1", match1.score)
            if match1.score >= login_th and self._throttle(0.8):
                self.adb.tap(match1.center_x, match1.center_y)
                print(
                    f"{self._stage_prefix()} [Flow] 命中 login_1 模板 score={match1.score:.3f}, 点击({match1.center_x},{match1.center_y})，进入 main_pre"
                )
                self.main_pre_mode = True
                return False
            return False

        # 同时检测下的 login_0 路径（仅在未进入 login_1 模式时）
        self._log_match_score("login_0", match0.score)
        if match0.score >= login_th and self._throttle(0.8):
            self.adb.tap(match0.center_x, match0.center_y)
            print(
                f"{self._stage_prefix()} [Flow] 命中 login_0 模板 score={match0.score:.3f}, 点击({match0.center_x},{match0.center_y})"
            )

        self._log_match_score("login_1", match1.score)
        return False

    def _check_event_reward_on_running_entry(self) -> bool:
        for i in range(5):
            shot = self.adb.screencap_png()
            if not shot:
                continue
            self._dump_latest_screen(shot)
            match_reward = self.event_reward_matcher.match_png_bytes(shot)
            self._log_match_score("event_reward", match_reward.score)
            if match_reward.score >= self.event_reward_matcher.threshold:
                self.login1_mode = False
                self._skip_update_after_login_seen = False
                self.main_pre_mode = False
                self.main_screen_mode = True
                self.chapter_selected = False
                self.selected_chapter_name = ""
                self.ordeal_call_handler.reset()
                self._log_stage_if_changed("main_screen")
                print(f"{self._stage_prefix()} [Flow] 命中 event_reward（第{i + 1}次），直接进入 main_screen")
                return True
            time.sleep(0.2)
        return False

    def run_until_home(self) -> bool:
        # 先按进程判断：有进程就继续流程；无进程就先启动包体
        running = self.adb.is_process_running(self.package_name)
        if running:
            if not self.running_entry_checked:
                self.running_entry_checked = True
                self._check_event_reward_on_running_entry()
            shot = self.adb.screencap_png()
            return self._handle_pre_home(shot)

        self._log_stage_if_changed("app_not_running")
        if self._throttle(2.0):
            self.login1_mode = False
            self._skip_update_after_login_seen = False
            self.main_pre_mode = False
            self.main_screen_mode = False
            self.post_ann_close_deadline = 0.0
            self.last_close_burst_tap_ts = 0.0
            self.chapter_selected = False
            self.selected_chapter_name = ""
            self.ordeal_call_handler.reset()
            self.running_entry_checked = False
            self.in_fgo_since = 0.0
            result = self.adb.start_package(self.package_name)
            print(f"{self._stage_prefix()} [Flow] 未检测到FGO进程，启动包体: {result.output}")
        return False

        # 以下分支保留但不再走到（作为兼容兜底）
        state = self.detector.detect()
        self._log_state_if_changed(state.value)

        if state == GameState.APP_NOT_RUNNING:
            self._log_stage_if_changed("app_not_running")
            self.login1_mode = False
            self._skip_update_after_login_seen = False
            self.main_pre_mode = False
            self.main_screen_mode = False
            self.post_ann_close_deadline = 0.0
            self.last_close_burst_tap_ts = 0.0
            self.chapter_selected = False
            self.selected_chapter_name = ""
            self.ordeal_call_handler.reset()
            self.running_entry_checked = False
            self.in_fgo_since = 0.0
            result = self.adb.start_package(self.package_name)
            print(f"{self._stage_prefix()} [Flow] 启动FGO: {result.output}")
            return False

        if state == GameState.UNKNOWN:
            self._log_stage_if_changed("app_not_running")
            if self._throttle(2.0):
                self.login1_mode = False
                self._skip_update_after_login_seen = False
                self.main_pre_mode = False
                self.main_screen_mode = False
                self.post_ann_close_deadline = 0.0
                self.last_close_burst_tap_ts = 0.0
                self.chapter_selected = False
                self.selected_chapter_name = ""
                self.ordeal_call_handler.reset()
                self.running_entry_checked = False
                self.in_fgo_since = 0.0
                result = self.adb.start_package(self.package_name)
                print(f"{self._stage_prefix()} [Flow] 启动FGO(unknown兜底): {result.output}")
            return False

        if state == GameState.IN_FGO_NOT_HOME:
            shot = self.adb.screencap_png()
            return self._handle_pre_home(shot)

        if state == GameState.HOME:
            self._log_stage_if_changed("home")
            print(f"{self._stage_prefix()} [Flow] 已到主界面")
            return True

        return False

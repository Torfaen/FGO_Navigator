import subprocess
import argparse
import ctypes
import time
import builtins
from pathlib import Path

try:
    import cv2
    import numpy as np
    from PIL import ImageGrab
except Exception:  # pragma: no cover
    cv2 = None
    np = None
    ImageGrab = None

_FOUND_WINDOW_LOGGED = False
_START_NODE2_LOGGED = False
_CONFIRM_CLICKED_ONCE = False
_DPI_AWARE_SET = False
_LOG_FILE_HANDLE = None
_LOG_PATH = Path("bbchannel_runtime.log")


def _read_simple_yaml(path: str) -> dict[str, str]:
    data: dict[str, str] = {}
    cfg = Path(path)
    if not cfg.exists():
        return data
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip().strip("'").strip('"')
    except Exception:
        return {}
    return data


def _init_log_file(overwrite: bool = False) -> None:
    global _LOG_FILE_HANDLE
    mode = "w" if overwrite else "a"
    if _LOG_FILE_HANDLE:
        try:
            _LOG_FILE_HANDLE.close()
        except Exception:
            pass
    _LOG_FILE_HANDLE = open(_LOG_PATH, mode, encoding="utf-8")


def print(*args, **kwargs):  # type: ignore[override]
    builtins.print(*args, **kwargs)
    global _LOG_FILE_HANDLE
    if _LOG_FILE_HANDLE is None:
        return
    text = " ".join(str(a) for a in args)
    end = kwargs.get("end", "\n")
    try:
        _LOG_FILE_HANDLE.write(text + end)
        _LOG_FILE_HANDLE.flush()
    except Exception:
        pass


def _ensure_dpi_awareness() -> None:
    """统一 Windows DPI 坐标系，避免匹配坐标与鼠标坐标不一致。"""
    global _DPI_AWARE_SET
    if _DPI_AWARE_SET:
        return

    user32 = ctypes.windll.user32
    # 优先 Per-Monitor V2，失败再回退旧接口
    try:
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
        user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    _DPI_AWARE_SET = True


def _get_window_text(user32, hwnd) -> str:
    WM_GETTEXT = 0x000D
    WM_GETTEXTLENGTH = 0x000E
    length = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.byref(buffer))
    return buffer.value.strip()


def _find_window_by_title_fuzzy(keyword: str) -> int:
    user32 = ctypes.windll.user32
    found_hwnd = 0
    key = (keyword or "").strip().lower()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_windows_proc(hwnd, _lparam):
        nonlocal found_hwnd
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _get_window_text(user32, hwnd).lower()
        if key and key in title:
            found_hwnd = int(hwnd)
            return False
        return True

    user32.EnumWindows(enum_windows_proc, 0)
    return found_hwnd


def _find_window_by_title_contains(keyword: str) -> int:
    user32 = ctypes.windll.user32
    found_hwnd = 0
    key = (keyword or "").strip().lower()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_windows_proc(hwnd, _lparam):
        nonlocal found_hwnd
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _get_window_text(user32, hwnd).lower()
        if key and key in title:
            found_hwnd = int(hwnd)
            return False
        return True

    user32.EnumWindows(enum_windows_proc, 0)
    return found_hwnd


def _log_stop_window_content(stop_hwnd: int) -> None:
    user32 = ctypes.windll.user32
    title = _get_window_text(user32, stop_hwnd)
    print(f"[BBchannel] 检测到停止窗口 hwnd={stop_hwnd}, title='{title}'")
    print("[BBchannel] 停止窗口文本开始")
    if title:
        print(title)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_child_proc(child_hwnd, _lparam):
        text = _get_window_text(user32, child_hwnd).strip()
        if text:
            print(text)
        return True

    user32.EnumChildWindows(stop_hwnd, enum_child_proc, 0)
    print("[BBchannel] 停止窗口文本结束")


def _check_stop_dialog_and_log(template_path: str = "assets/templates/bbchannel/stop.png", threshold: float = 0.78) -> bool:
    # 优先用窗口句柄检测并提取文本内容
    stop_hwnd = _find_window_by_title_contains("脚本停止")
    if stop_hwnd:
        _log_stop_window_content(stop_hwnd)
        return True

    # 兜底：模板匹配检测到“脚本停止”图样
    if cv2 is None or np is None or ImageGrab is None:
        return False
    path = Path(template_path)
    if not path.exists():
        return False
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        return False

    screen_pil = ImageGrab.grab()
    screen = cv2.cvtColor(np.array(screen_pil), cv2.COLOR_RGB2GRAY)
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val >= threshold:
        print(f"[BBchannel] 命中 stop 模板 score={max_val:.4f}, loc={max_loc}，停止运行")
        return True
    return False


def _close_fgo_game_only() -> None:
    """只关闭 FGO 进程，保留模拟器。"""
    cfg = _read_simple_yaml("config/settings.yaml")
    package = cfg.get("package_name", "com.bilibili.fatego")
    serial = f"127.0.0.1:{int(cfg.get('adb_port', '16384'))}"
    adb_candidates = []
    adb_path_cfg = cfg.get("adb_path", "").strip()
    if adb_path_cfg:
        adb_candidates.append(Path(adb_path_cfg))
    mumu_dir_cfg = cfg.get("mumu_dir", "").strip()
    if mumu_dir_cfg:
        adb_candidates.append(Path(mumu_dir_cfg) / "adb.exe")
    adb_candidates.extend(
        [
            Path(r"C:/Program Files/Netease/MuMu Player 12/nx_main/adb.exe"),
            Path(r"C:/Users/ASUS/Desktop/auto/BBchannel/adb/adb.exe"),
        ]
    )
    commands = []
    for adb in adb_candidates:
        if adb.exists():
            commands.append([str(adb), "-s", serial, "shell", "am", "force-stop", package])
            commands.append([str(adb), "shell", "am", "force-stop", package])

    if not commands:
        print("[BBchannel] 未找到可用 adb.exe，无法关闭 FGO 进程")
        return

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10,
                check=False,
            )
            print(f"[BBchannel] 执行关闭FGO命令: {' '.join(cmd)}")
            if result.stdout:
                print(result.stdout.strip())
            if result.returncode == 0:
                print("[BBchannel] 已执行 FGO 进程关闭（模拟器保留运行）")
                return
        except Exception as exc:
            print(f"[BBchannel] 关闭FGO命令异常: {exc}")


def _click_confirm_button_background(keyword: str = "bbchannel64位") -> bool:
    """后台尝试点击“确定/OK”按钮，不要求窗口在前台。"""
    user32 = ctypes.windll.user32
    BM_CLICK = 0x00F5
    clicked = False
    key = (keyword or "").strip().lower()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_windows_proc(hwnd, _lparam):
        nonlocal clicked
        if clicked or not user32.IsWindowVisible(hwnd):
            return not clicked

        title = _get_window_text(user32, hwnd).lower()
        if key not in title and "免责声明" not in title:
            return True

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_child_proc(child_hwnd, _lparam2):
            nonlocal clicked
            text = _get_window_text(user32, child_hwnd).strip()
            if text == "确定" or text.upper() == "OK":
                user32.SendMessageW(child_hwnd, BM_CLICK, 0, 0)
                clicked = True
                return False
            return True

        user32.EnumChildWindows(hwnd, enum_child_proc, 0)
        return not clicked

    user32.EnumWindows(enum_windows_proc, 0)
    global _CONFIRM_CLICKED_ONCE
    if clicked:
        _CONFIRM_CLICKED_ONCE = True
        print("[BBchannel] 已后台点击“确定/OK”按钮")
    return clicked


def _detect_window_only(
    delay_sec: float = 2.0,
    keyword: str = "bbchannel64位",
    search_timeout_sec: int = 20,
    verbose: bool = True,
) -> bool:
    """等待后按窗口标题模糊匹配，仅检测不聚焦。"""
    global _FOUND_WINDOW_LOGGED
    time.sleep(delay_sec)

    hwnd = 0
    deadline = time.time() + max(1, search_timeout_sec)
    last_progress_log_ts = 0.0
    should_log_search = verbose and (not _FOUND_WINDOW_LOGGED)
    if should_log_search:
        print(f"[BBchannel] 开始查找窗口，关键字: '{keyword}'，超时: {search_timeout_sec}s")
    while time.time() < deadline and not hwnd:
        hwnd = _find_window_by_title_fuzzy(keyword)
        if not hwnd:
            now = time.time()
            if should_log_search and now - last_progress_log_ts >= 1.0:
                remaining = max(0, int(deadline - now))
                print(f"[BBchannel] 正在查找窗口... 剩余约 {remaining}s")
                last_progress_log_ts = now
            time.sleep(0.2)

    if hwnd:
        user32 = ctypes.windll.user32
        if not _FOUND_WINDOW_LOGGED:
            found_title = _get_window_text(user32, hwnd)
            print(f"[BBchannel] 已找到窗口 hwnd={hwnd}, title='{found_title}'")
            _FOUND_WINDOW_LOGGED = True
        return _click_confirm_button_background(keyword=keyword)
    else:
        if verbose:
            print(f"[BBchannel] {search_timeout_sec}s 内未找到包含“{keyword}”的窗体")
        _FOUND_WINDOW_LOGGED = False
    return False


def _focus_window(keyword: str = "bbchannel64位") -> bool:
    """将匹配到的窗口置前激活。"""
    user32 = ctypes.windll.user32
    hwnd = _find_window_by_title_fuzzy(keyword)
    if not hwnd:
        print(f"[BBchannel] 聚焦失败，未找到包含“{keyword}”的窗体")
        return False

    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    user32.SetActiveWindow(hwnd)
    print(f"[BBchannel] 已聚焦窗口，等待2s后点击开始")
    return True


def _click_start_button_by_template(
    template_path: str = "assets/templates/bbchannel/start_BBC.png",
    threshold: float = 0.78,
) -> bool:
    _ensure_dpi_awareness()
    if cv2 is None or np is None or ImageGrab is None:
        print("[BBchannel] 未安装图像依赖(cv2/PIL)，无法执行开始按钮模板匹配")
        return False

    path = Path(template_path)
    if not path.exists():
        print(f"[BBchannel] 未找到开始按钮模板: {path}")
        return False

    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        print(f"[BBchannel] 模板读取失败: {path}")
        return False

    # 回退为全屏匹配与全屏坐标点击
    screen_pil = ImageGrab.grab()
    screen = cv2.cvtColor(np.array(screen_pil), cv2.COLOR_RGB2GRAY)

    # start_BBC 做多尺度匹配：0.3 到 1.8，间隔 0.2
    scales: list[float] = []
    s = 0.3
    while s <= 1.8 + 1e-9:
        scales.append(round(s, 2))
        s += 0.2
    if scales and scales[-1] != round(1.8, 2):
        scales.append(round(1.8, 2))

    best_val = -1.0
    best_loc = (0, 0)
    best_tw = template.shape[1]
    best_th = template.shape[0]
    best_scale = 1.0

    for scale in scales:
        if scale == 1.0:
            tpl = template
        else:
            tw = max(1, int(template.shape[1] * scale))
            th = max(1, int(template.shape[0] * scale))
            tpl = cv2.resize(template, (tw, th), interpolation=cv2.INTER_LINEAR)

        th, tw = tpl.shape[:2]
        if screen.shape[0] < th or screen.shape[1] < tw:
            continue

        result = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val > best_val:
            best_val = float(max_val)
            best_loc = max_loc
            best_tw = tw
            best_th = th
            best_scale = scale

    print(f"[BBchannel] start_BBC 多尺度匹配(best) scale={best_scale:.2f} 分数: {best_val:.4f}")
    cx = int(best_loc[0] + best_tw // 2)
    cy = int(best_loc[1] + best_th // 2)

    # 每次运行都输出一次点击预览图（黄色框+红圈）
    preview = cv2.cvtColor(screen, cv2.COLOR_GRAY2BGR)
    cv2.rectangle(
        preview,
        (best_loc[0], best_loc[1]),
        (best_loc[0] + best_tw, best_loc[1] + best_th),
        (0, 255, 255),
        2,
    )
    cv2.circle(preview, (cx, cy), max(12, min(best_tw, best_th) // 4), (0, 0, 255), 3)
    cv2.putText(
        preview,
        f"scale={best_scale:.2f} score={best_val:.4f} click=({cx},{cy})",
        (max(10, best_loc[0] - 20), max(25, best_loc[1] - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 0, 255),
        2,
    )
    cv2.imwrite("bbchannel_click_preview.png", preview)
    print("[BBchannel] 已输出点击预览图: bbchannel_click_preview.png")

    if best_val < threshold:
        return False

    user32 = ctypes.windll.user32
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    user32.SetCursorPos(cx, cy)
    print(f"[BBchannel] 鼠标已移动到匹配点({cx},{cy})，准备点击")
    time.sleep(0.2)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    print(f"[BBchannel] 已点击 start_BBC 按钮一次，屏幕坐标({cx},{cy})")
    return True


def launch_debug_and_monitor(cmd_path: str, workdir: str, monitor_sec: int = 20) -> int:
    _init_log_file(overwrite=True)
    print(f"[BBchannel] 启动: {cmd_path}")
    subprocess.Popen(["cmd", "/c", cmd_path], cwd=workdir)
    _detect_window_only(delay_sec=2.0, keyword="bbchannel64位", search_timeout_sec=monitor_sec, verbose=True)
    print("[BBchannel] 已完成窗口模糊搜索")
    return 0


def run_forever(keyword: str = "bbchannel64位", interval_sec: float = 2.0) -> int:
    """持续运行：循环检测窗口，直到用户中断。"""
    global _START_NODE2_LOGGED
    print(f"[BBchannel] 持续运行模式已开启，关键字='{keyword}'，间隔={interval_sec}s")
    global _CONFIRM_CLICKED_ONCE
    node2_enabled = _CONFIRM_CLICKED_ONCE
    start_clicked = False
    try:
        while True:
            clicked_confirm = _detect_window_only(
                delay_sec=0.0,
                keyword=keyword,
                search_timeout_sec=1,
                verbose=True,
            )
            if clicked_confirm:
                node2_enabled = True
                if not _START_NODE2_LOGGED:
                    print("[BBchannel] 已进入节点二：开始按钮匹配与点击")
                    _START_NODE2_LOGGED = True
                print("[BBchannel] 点掉确定后等待3s")
                time.sleep(3.0)

            if node2_enabled and (not _START_NODE2_LOGGED):
                print("[BBchannel] 已进入节点二：开始按钮匹配与点击")
                _START_NODE2_LOGGED = True

            if node2_enabled and not start_clicked:
                _focus_window(keyword=keyword)
                time.sleep(2.0)
                start_clicked = _click_start_button_by_template(
                    template_path="assets/templates/bbchannel/start_BBC.png",
                    threshold=0.78,
                )
                if start_clicked:
                    print("[BBchannel] start_BBC 已点击一次，后续不再重复点击")

            if start_clicked and _check_stop_dialog_and_log(
                template_path="assets/templates/bbchannel/stop.png",
                threshold=0.78,
            ):
                _close_fgo_game_only()
                print("[BBchannel] 检测到“脚本停止”，结束运行")
                return 0
            time.sleep(max(0.2, interval_sec))
    except KeyboardInterrupt:
        print("[BBchannel] 收到中断，结束持续运行模式")
        return 0


def _main() -> int:
    _ensure_dpi_awareness()
    parser = argparse.ArgumentParser(description="单独调试 BBchannel 启动与日志监控")
    parser.add_argument(
        "--cmd",
        default=r"C:/Users/ASUS/Desktop/auto/BBchannel/启动.cmd",
        help="BBchannel 启动脚本路径",
    )
    parser.add_argument(
        "--workdir",
        default=r"C:/Users/ASUS/Desktop/auto/BBchannel",
        help="BBchannel 工作目录",
    )
    parser.add_argument(
        "--monitor-sec",
        type=int,
        default=20,
        help="日志监控秒数",
    )
    parser.add_argument(
        "--keyword",
        default="bbchannel64位",
        help="窗口标题模糊匹配关键字",
    )
    parser.add_argument(
        "--loop-interval-sec",
        type=float,
        default=2.0,
        help="持续运行模式的循环间隔秒数",
    )
    args = parser.parse_args()
    code = launch_debug_and_monitor(
        cmd_path=args.cmd,
        workdir=args.workdir,
        monitor_sec=args.monitor_sec,
    )
    if code != 0:
        return code
    # IDE 直接调试时默认进入持续循环，不需要额外参数
    return run_forever(keyword=args.keyword, interval_sec=args.loop_interval_sec)


if __name__ == "__main__":
    raise SystemExit(_main())

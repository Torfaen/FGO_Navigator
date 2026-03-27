from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None


@dataclass
class MatchResult:
    ok: bool
    score: float
    center_x: int
    center_y: int


class TemplateMatcher:
    def __init__(
        self,
        template_path: str,
        threshold: float = 0.88,
        use_gray: bool = False,
        scales: Optional[List[float]] = None,
    ):
        self.template_path = Path(template_path)
        self.threshold = threshold
        self.use_gray = use_gray
        self.scales = scales or [1.0]
        self._template = None
        self._w = 0
        self._h = 0
        self.available = cv2 is not None and np is not None and self.template_path.exists()
        if self.available:
            read_mode = cv2.IMREAD_GRAYSCALE if self.use_gray else cv2.IMREAD_COLOR
            self._template = cv2.imread(str(self.template_path), read_mode)
            if self._template is None:
                self.available = False
            else:
                self._h, self._w = self._template.shape[:2]

    def match_png_bytes(self, png_bytes: bytes) -> MatchResult:
        if not self.available or not png_bytes:
            return MatchResult(False, 0.0, 0, 0)

        screen_array = np.frombuffer(png_bytes, dtype=np.uint8)
        read_mode = cv2.IMREAD_GRAYSCALE if self.use_gray else cv2.IMREAD_COLOR
        screen = cv2.imdecode(screen_array, read_mode)
        if screen is None:
            return MatchResult(False, 0.0, 0, 0)

        best_score = 0.0
        best_cx = 0
        best_cy = 0
        for scale in self.scales:
            if scale <= 0:
                continue

            if scale == 1.0:
                tpl = self._template
            else:
                tw = max(1, int(self._w * scale))
                th = max(1, int(self._h * scale))
                tpl = cv2.resize(self._template, (tw, th), interpolation=cv2.INTER_LINEAR)

            th, tw = tpl.shape[:2]
            if screen.shape[0] < th or screen.shape[1] < tw:
                continue

            result = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_score:
                x, y = max_loc
                best_score = float(max_val)
                best_cx = int(x + tw // 2)
                best_cy = int(y + th // 2)

        return MatchResult(best_score >= self.threshold, best_score, best_cx, best_cy)

# FGO 自动化脚本 — 项目说明

> 本文档供开发与维护时快速回顾架构；配置与行为以代码及 `config/settings.yaml` 为准。

## 1. 项目概述

- **目标**：在 Windows + MuMu 模拟器上，通过 ADB 截图与 OpenCV 模板匹配，自动完成登录/更新、进入指定章节与关卡；可选衔接 **BBchannel** 调试启动与长期监控。
- **技术栈**：Python 3、`opencv-python-headless`、`numpy`、`Pillow`；ADB `exec-out screencap` + 模板匹配（`core/vision.py`）。
- **非目标**：不实现游戏内战斗 AI（战斗由 BBchannel 等外部工具负责）。

---

## 2. 目录结构（概要）

| 路径 | 说明 |
|------|------|
| `main.py` | 入口：模拟器启动、ADB 连接、`StartupFlow` 轮询、BBchannel、`script_end_action` 收尾 |
| `start_fgo_cn.py` | 独立小工具：仅启动 MuMu + 连接 ADB + monkey 启动 FGO（无模板流程） |
| `config/settings.yaml` | 主配置（模拟器、ADB、章节/关卡模板、BBchannel、`script_end_action`） |
| `core/adb.py` | `AdbClient`：connect、tap、swipe、screencap、进程检测等 |
| `core/emulator.py` | `EmulatorLauncher` / `EmulatorConfig`：读 YAML、启动 MuMu、等待在线 |
| `core/vision.py` | `TemplateMatcher`：多尺度 `matchTemplate` |
| `core/state.py` | `GameState` / `StateDetector`（当前 **HOME 未真正用于分支**，见 §7） |
| `core/action.py` | `wait_until` 通用轮询 |
| `core/script_exit.py` | 脚本结束：关游戏 / 关模拟器 / 不操作 |
| `core/bbchannel.py` | 启动 BBchannel 调试、窗口监控、`run_forever` 轮询停止按钮等（**不再**在此关游戏，收尾统一走 `main`） |
| `flows/startup_flow.py` | 登录 → 更新 → main_pre/main_screen、大章节/小关卡、Ordeal 委托 |
| `flows/ordeal_call_handler.py` | Ordeal Call 专用：上一次、滑动找关、`close` 无法开战致命错误 |
| `assets/templates/` | PNG 模板资源 |
| `assets/regions.yaml` | 预留 ROI/OCR，**当前未接入代码** |
| `DATA/LOGS/` | 运行日志（由 `main` 创建） |

---

## 3. 入口流程与退出码

`main._run_main()` 大致顺序：

1. `EmulatorLauncher.from_config_file` → `start_and_connect()`（失败 **return 1**，当前**不**执行 `script_end_action`）。
2. `wait_until(flow.run_until_home, timeout=home_wait_timeout_sec)`。
3. 若 `ordeal_call_handler.fatal_error`（如无法开战已点关闭）→ 打印 → `apply_script_end_action_for_launcher` → **return 3**。
4. 若超时 **return 2**，并执行收尾。
5. 若启用 BBchannel：`launch_debug_and_monitor` → 成功则 `run_forever()`。
6. 正常路径最后 **return 0**，并执行 `apply_script_end_action_for_launcher`。

| 退出码 | 含义 |
|--------|------|
| 0 | 流程按设计结束（含收尾动作） |
| 1 | 模拟器/ADB 连接失败 |
| 2 | `wait_until` 超时（未在时限内认为「到关」） |
| 3 | 致命业务错误（如进入关卡失败） |

标准输出经 `_Tee` 写入带时间戳的日志与 `fgo_runtime.log`。

---

## 4. 配置项要点（`config/settings.yaml`）

- **MuMu / ADB**：`mumu_dir`、`mumu_index`、`adb_path`、`adb_port`、`adb_connect_timeout_sec`。
- **流程**：`home_wait_timeout_sec`、`chapter_templates`（CSV）、`target_stage_template`、`target_stage_threshold`。
- **BBchannel**：`bbchannel_enabled`、`bbchannel_debug_cmd`、`bbchannel_workdir`、`bbchannel_monitor_sec`。
- **收尾**：`script_end_action`：`none` / `close_game` / `close_emulator`（见 `core/script_exit.py` 别名）。

配置解析使用 **`core/emulator.py` 与 `core/script_exit.py` 内的简易 YAML 行解析**（非 PyYAML），仅支持 `key: value` 风格。

---

## 5. 状态机与阶段（`StartupFlow`）

逻辑集中在 `_handle_pre_home` 与 `run_until_home`：

- **logining**：`login_0` / `login_1`、`update`。
- **main_pre**：`close_ann` → 进入 5 秒窗口，仅连点 `close.png`。
- **main_screen**：按 `chapter_templates` 匹配大章节；若章节为 **ordeal_call** 则交给 `OrdealCallHandler`，否则上滑找 `target_stage_matcher`。
- **Ordeal**：节点含 free ordeal、上一次、mission_start；若匹配到 **close（无法开战）** 则设置 `fatal_error` 并结束等待。

`run_until_home` 在「进程已运行」分支走截图流程；**未运行**则 `start_package`，且文件末尾有一段 **不可达的** `detector.detect()` 分支（历史兼容，见 §7）。

---

## 6. 模板匹配说明（`TemplateMatcher`）

- 支持多 **scale** 列表，代价为每帧多次 `matchTemplate`。
- 模板文件不存在时 `available=False`，匹配分数为 0，对应逻辑不触发。
- **坐标**：部分流程写死为 **1280×720** 类逻辑分辨率下的 tap/swipe（如 `740,390`），换分辨率需统一调整或 ROI。

---

## 7. 已知限制与技术债

1. **`GameState.HOME` 与 `StateDetector`**：`detect()` 对 FGO 进程一律返回 `IN_FGO_NOT_HOME`，`run_until_home` 实际以 **模板命中「小关卡/Ordeal 完成条件」** 为成功，而非真实 HOME 检测。
2. **`startup_flow.py` 尾部死代码**：`return False` 之后仍有 `GameState` 分支，运行时不会执行，可整理删除或改为清晰注释。
3. **`regions.yaml`**：占位，未与 `vision`/`startup_flow` 联动。
4. **YAML 解析重复**：`emulator.py`、`script_exit.py`、历史 `bbchannel` 删除前等处均为手写解析，可合并为单模块并支持类型（int/list/bool）。
5. **硬编码路径**：`script_exit.py` 中 adb 候选路径含本机示例路径；`EmulatorConfig` 默认 BBchannel 路径为用户目录。
6. **退出码 1**：连接失败时不执行 `script_end_action`，若希望「任何结束都收尾」需产品决策。

---

## 8. 优化空间（建议优先级）

### 8.1 结构与可维护性

- **抽取共享模板常量**：如 `CLOSE_TEMPLATE_SCALES`、`close.png` 参数，避免 `startup_flow` 与 `ordeal_call_handler` 重复定义。
- **收敛 YAML**：使用 `PyYAML` 或 `tomllib`+单一格式，支持列表与注释，减少解析错误。
- **拆分 `StartupFlow`**：按阶段拆成子模块或策略类，降低单文件体积与分支复杂度。

### 8.2 性能

- **截图频率**：`wait_until` 间隔 0.5s + 每帧多模板、多尺度，CPU 占用可观；可按阶段降低匹配次数（例如非 main_screen 降低 scales 数量）。
- **模板缓存**：`TemplateMatcher` 已缓存读盘模板；避免同一帧对同一图重复 `imdecode`，可考虑在单轮 `handle` 内复用解码后的 `screen`（需改 API）。
- **ROI**：在 `regions.yaml` 定义章节/关卡列表区域，匹配前裁切，可显著减小 `matchTemplate` 成本（需校准分辨率）。

### 8.3 稳定性

- **分辨率适配**：将 tap/swipe 改为相对坐标或配置化锚点。
- **致命弹窗扩展**：除 `close` 外可叠加 OCR/多模板，降低误判与漏判（需测试集截图）。
- **ADB 失败重试**：`screencap` 偶发空数据时可重试 1～2 次再进入下一轮。

### 8.4 工程化

- **版本化依赖**：`requirements.txt` 已存在；可锁定次要版本或引入 `uv`/`pip-tools`。
- **最小集成测试**：对 `_read_simple_yaml`、`TemplateMatcher` 无文件/坏图、以及 `wait_until` 做纯逻辑测试（无需真机）。
- **日志**：结构化日志（JSON 或固定字段）便于外部监控脚本解析。

### 8.5 与 BBchannel 的边界

- 收尾已统一在 `main`；后续若增加「仅 BBchannel 失败时重试」等，应避免在 `bbchannel.py` 再次引入关游戏逻辑，保持单一出口。

---

## 9. 相关文件速查

| 需求 | 先看 |
|------|------|
| 改等待首页超时 | `config/settings.yaml` → `home_wait_timeout_sec` |
| 改章节/关卡图 | `chapter_templates`、`target_stage_template` |
| 改脚本结束行为 | `script_end_action` + `core/script_exit.py` |
| Ordeal 无法开战 | `flows/ordeal_call_handler.py` + `fatal_error` |
| 连接不上模拟器 | `core/emulator.py`、`adb_connect_timeout_sec` |

---

*文档生成自仓库当前结构；若重构目录或入口，请同步更新本节。*

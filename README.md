# 英雄没有闪——绝境深渊纯视觉自动爬塔工具

Windows 桌面程序：通过截图 + OpenCV 模板匹配 + 颜色分析，自动循环挑战微信小程序《英雄没有闪》中的「绝境深渊」。

## 1. 项目功能

- 定位标题包含「英雄没有闪」的微信小程序独立窗口
- 识别难度选择 / 确认弹窗 / 战斗 / 结算界面
- 只刷列表最后一关（默认难度 50），到底前不会挑战其他难度
- 当前已在底部时用一次短滑确认，随后选中难度 50 并点「挑战深渊」
- 确认弹窗只点「开始挑战」
- 结算出现「点击关闭」后点击关闭
- 提供 GUI、全局热键、dry-run、调试标注与离线回放

## 2. 纯视觉方案定义

本项目的「纯视觉」指：

1. 游戏状态只通过截图、模板匹配、HSV/灰度分析判断
2. 禁止读取游戏内存
3. 禁止读取或篡改微信 / 小程序 / 游戏进程数据
4. 禁止分析或调用游戏网络接口
5. 禁止把 OCR 作为核心控制依据
6. 禁止绕过反作弊、注入 DLL、Hook、修改游戏文件
7. 允许用 Windows API 查找窗口矩形
8. 允许正常前台鼠标点击
9. 不实现后台无焦点点击；点击前必须确认目标窗口在前台

## 3. 支持平台

- Windows 10 / 11
- Python 3.11+（推荐）

## 4. Python 安装要求

请先安装 Python 3.11 或更高版本，并确保命令行可执行 `python`。

## 5. 虚拟环境创建

在项目根目录 PowerShell：

```powershell
cd F:\PersonalDev\yxmys
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 6. 依赖安装

```powershell
python -m pip install -r requirements.txt
```

## 7. 放置参考截图

将 550×1020 的小程序窗口截图放到：

- `assets/reference/select_screen.png`（难度选择）
- `assets/reference/confirm_screen.png`（开始挑战弹窗）
- `assets/reference/result_screen.png`（结算，含「点击关闭」）

仓库已内置基于 starter 模板合成的参考图，便于离线测试。**正式对局前请替换为你自己的真实截图**，然后重新生成模板。

也可运行：

```powershell
python tools\synthesize_reference.py --force
```

## 8. 生成模板

```powershell
python tools\build_templates.py --force
```

会从参考图按 `config/default.yaml` 中的 `template_crops` 裁剪：

- `assets/templates/select_anchor.png`
- `assets/templates/challenge_button.png`
- `assets/templates/start_button.png`
- `assets/templates/close_prompt.png`

并立刻在参考图上做匹配验证。

## 9. dry-run

```powershell
python -m tower_bot --dry-run
```

会正常截图、识别、跑状态机，并在日志中输出「本应点击」的坐标与动作，但**绝不移动鼠标、绝不点击**。

## 10. 正式模式

### 双击启动（推荐）

打开项目根目录，双击：

```text
启动.bat
```

会使用项目内 `.venv` 启动 GUI，无需手动输入命令。

### 命令行启动

```powershell
cd F:\PersonalDev\yxmys
.\.venv\Scripts\python.exe -m tower_bot
```

打开微信小程序独立窗口（标题含「英雄没有闪」），窗口尽量不被遮挡，在 GUI 中点「开始」或按 `F8`。

## 11. GUI 与热键

GUI 标题：`yxmys自动爬绝境`

按钮：开始 / 暂停 / 停止 / 单步识别 / 打开调试目录

显示：程序状态、窗口状态、视觉状态、最高可用难度行、选中行、各模板分数、挑战次数、结算关闭次数、最近动作、最近错误

热键：

| 热键 | 作用 |
|------|------|
| F8 | 开始 / 暂停切换 |
| F9 | 停止并退出 |
| F10 | 切换调试模式 |
| Esc | 紧急暂停（不杀进程） |
| 鼠标移到屏幕角落 | PyAutoGUI FailSafe 紧急停止 |

## 12. 参数调整

编辑 `config/default.yaml`：

- `window.*`：标题关键字、参考尺寸、宽高比容差
- `matching.*`：模板阈值
- `rois.*`：搜索 ROI
- `difficulty.*`：行中心、饱和度阈值、黄色 HSV
- `actions.*`：冷却、超时、防抖
- `capture.*`：识别间隔

修改后重启程序。非法配置会给出明确错误。

## 13. 查看 debug/latest.png

开启调试（F10 或 `--debug`）后，每次识别会写：

- `debug/latest.png`：最新标注图（ROI、匹配框、分数、五行指标、动作）

异常情况还会写入带时间戳的文件，例如：

```text
debug/2026-07-29_21-30-15_unknown.png
```

## 14. DPI 缩放

程序启动时尝试：

`SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`

失败则回退 `SetProcessDPIAware`。

若点击偏移：

1. 确认系统显示缩放后重启程序
2. 保持小程序窗口接近 550:1020 比例
3. 不要跨 DPI 监视器拖动窗口后再立刻点击

## 15. 窗口标题不同

修改配置：

```yaml
window:
  title_contains: "英雄没有闪"
```

改成你窗口标题中的稳定子串。标题匹配后仍需通过视觉锚点验证才会点击。

## 16. 调整模板阈值

```yaml
matching:
  select_anchor_threshold: 0.78
  challenge_threshold: 0.80
  start_threshold: 0.82
  close_threshold: 0.80
```

可先用离线回放看分数：

```powershell
python tools\replay_detector.py assets\reference\select_screen.png
```

分数长期略低于阈值时，小幅下调；误识别过多时上调。

## 17. 调整难度行坐标

```yaml
difficulty:
  row_centers: [360, 450, 555, 655, 760]
  click_x: 275
  row_half_height: 28
  saturation_threshold: 75.0
```

若列表滚动或 UI 改版，先截新图，用 `tools/analyze_reference.py` 看各行 `meanS` / `gray`，再改坐标与阈值。

## 18. 重新裁剪模板

1. 更新三张参考图
2. 如需改裁剪框，编辑 `config/default.yaml` 的 `template_crops`
3. 执行：

```powershell
python tools\build_templates.py --force
```

## 19. 常见误识别排查

| 现象 | 排查 |
|------|------|
| 找不到窗口 | 标题是否包含配置关键字；是否独立窗口；是否最小化 |
| UNKNOWN 不点 | 正常保护；看 `debug/latest.png` 与模板分数 |
| 弹窗时点到底层挑战 | 确认 start 模板有效；优先级应为 RESULT > CONFIRM > SELECT |
| 结算不点关闭 | 「点击关闭」可能闪烁；降低 `capture.battle_interval`；更新 close 模板 |
| 选错难度 | 看五行饱和度；灰色行应 locked；调 `saturation_threshold` |
| 已选中仍点难度 | 检查黄色边框 HSV 与 `yellow_border_ratio_threshold` |
| 点击偏移 | DPI、窗口比例、是否前台 |

分析参考图：

```powershell
python tools\analyze_reference.py
```

## 20. 安全停止

1. Esc：立即暂停
2. F8：暂停
3. 鼠标移到屏幕任意角落：FailSafe 紧急停止
4. GUI「停止」或 F9：停止并退出

紧急停止后不会自动恢复，需手动再点「开始」。

## 21. 风险声明

自动化可能违反游戏或平台规则，也可能导致账号处罚。请自行确认规则，并仅在你有权操作的设备与账号上使用。使用本工具的风险由使用者自行承担。

---

## 开发与测试命令

```powershell
# 单元测试
python -m pytest -v

# 离线回放
python tools\replay_detector.py assets\reference\select_screen.png
python tools\replay_detector.py assets\reference\confirm_screen.png
python tools\replay_detector.py assets\reference\result_screen.png

# dry-run
python -m tower_bot --dry-run

# 无 GUI
python -m tower_bot --no-gui --dry-run
```

## 目录结构

```text
tower_bot/           # 主程序包
assets/reference/    # 参考截图
assets/templates/    # 模板
config/default.yaml  # 配置
tools/               # 模板裁剪 / 分析 / 回放
tests/               # pytest
debug/               # 调试输出
logs/                # 日志
tower_bot_starter/   # 原始起步代码（保留供对照）
```

## 状态机优先级

1. RESULT（点击关闭）
2. CONFIRM_CHALLENGE（开始挑战）
3. SELECT_DIFFICULTY（难度选择）
4. IN_BATTLE / UNKNOWN（不点击）

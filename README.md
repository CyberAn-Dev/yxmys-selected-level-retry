# 英雄没有闪——当前难度重复挑战工具

Windows 桌面程序：通过截图、OpenCV 模板匹配和颜色分析，反复挑战微信小程序《英雄没有闪》中的当前难度，直到成功。

当前版本：`v1.0.0`

## 唯一工作流程

1. 打开标题包含「英雄没有闪」的微信小程序独立窗口。
2. 在难度列表中手动选中要刷的当前难度。
3. 运行程序并点击“开始”或按 `F8`，程序自动发起第一次挑战。
4. 程序点击“挑战深渊”和“开始挑战”，战斗中不操作。
5. 失败结算或中途异常返回选择列表时，程序只向下滑动找回刚才的当前难度，重新选中后继续挑战。
6. 命中成功结算模板后停止。

程序不会自动寻找、选择或切换到最高难度，也不会执行战斗操作。

## 安装

Windows 10/11，Python 3.11+：

```powershell
cd F:\PersonalDev\yxmys-selected-level-retry
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 配置成功结算模板

选定关卡模式必须有一张成功结算截图中的稳定标题或徽章裁剪图。保存为例如：

```text
selected_level_retry/templates/success.png
```

然后编辑 `selected_level_retry/default.yaml`：

```yaml
result:
  success_template: "selected_level_retry/templates/success.png"
  success_roi: [0, 0, 550, 1020]
  success_threshold: 0.80
```

默认 `result.max_attempts: 0`，表示失败后持续重试；成功模板缺失时程序不会启动，避免成功后无限重试。

## 启动

双击根目录的 `启动.bat`，也可以直接运行根目录的 `yxmys_selected_level_retry.exe`（不需要 Python）。源码启动命令：

```powershell
.\.venv\Scripts\python.exe -m selected_level_retry
```

启动后必须先在游戏中手动选中目标难度，再点“开始”。也可使用：

```powershell
.\.venv\Scripts\python.exe -m selected_level_retry --dry-run
```

`--dry-run` 只识别并记录动作，不移动鼠标。

## 控制与安全

- `F8`：开始/暂停
- `F9`：停止并退出
- `Esc`：紧急暂停
- 鼠标移到屏幕角落：触发 PyAutoGUI FailSafe

只使用截图、模板匹配、HSV/灰度分析和前台鼠标操作，不读取游戏内存、不调用游戏网络接口，也不执行后台无焦点点击。

## 模板

通用模板位于 `assets/templates/`，参考截图位于 `assets/reference/`。如需从自己的参考截图重新裁剪模板，可运行：

```powershell
python tools\build_templates.py --force
```

日志写入 `logs/`。

打包版会把运行所需的配置和模板内置到 EXE；运行时日志写入 EXE 同目录的 `logs/`。

## 目录结构

```text
selected_level_retry/   # 唯一自动化功能
tower_bot/              # 当前功能依赖的视觉、窗口、输入底层
assets/                 # 模板和参考截图
config/default.yaml     # 窗口、识别和输入配置
tools/build_templates.py # 重新裁剪模板的辅助工具
logs/                   # 日志
```

自动化可能违反游戏或平台规则，也可能导致账号处罚。请确认你有权操作相关设备和账号，并自行承担使用风险。

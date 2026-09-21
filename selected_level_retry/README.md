# 选定关卡重复挑战

这是项目唯一保留的自动化模式：重复挑战用户启动时手动选中的当前难度，直到成功。

使用方式：

1. 打开微信小程序独立窗口。
2. 手动选中想反复挑战的关卡，并保持窗口可见。
3. 准备一张成功结算截图，裁剪其中稳定的成功标题/徽章区域，保存为例如 `selected_level_retry/templates/success.png`。
4. 编辑 `default.yaml`：

   ```yaml
   result:
     success_template: "selected_level_retry/templates/success.png"
     success_roi: [0, 0, 550, 1020]
     success_threshold: 0.80
   ```

   `success_roi` 必须覆盖成功模板可能出现的区域。如果模板是从固定位置裁剪的，建议把 ROI 收窄到该位置。

5. 启动：

   ```powershell
   .\.venv\Scripts\python.exe -m selected_level_retry
   ```

   也可以直接传成功模板：

   ```powershell
   .\.venv\Scripts\python.exe -m selected_level_retry --success-template selected_level_retry/templates/success.png
   ```

点击 GUI 的“开始”或按 `F8` 后，程序会先记录当前黄色选中行，然后自动发起第一次挑战；不会替你选择初始目标。

## 工作流程

```text
记录手动选中的关卡
→ 点击挑战/开始
→ 战斗中不操作
→ 结算命中成功模板：停止
→ 结算未命中成功模板：关闭结算
→ 或中途异常回到选择列表：只向下滑动找回当前关卡并重新选中
→ 再次挑战
```

找回关卡不依赖 OCR：程序保存选中行的内部图像，并用 SIFT 特征匹配在滚动后的五行中寻找同一关卡；目标暂时不可见时只按配置向下滑动。无论是失败结算后回到初始列表，还是中途异常回到选择列表，都会走同一套找回流程。找回后还会验证黄色选中框，验证失败不会直接挑战。

## 成功/失败识别

当前主项目只有“点击关闭”结算模板，无法区分成功和失败。因此本功能要求配置成功模板。默认策略是：结算出现但没有命中成功模板，就按失败关闭并重试；如果希望不确定时暂停，可设置：

```yaml
result:
  assume_non_success_is_failure: false
  unknown_policy: pause
```

成功模板缺失时，程序不会启动自动化，避免成功后无限重试。

## 安全边界

- UNKNOWN、战斗中、无法找回目标时不盲点。
- 点击前确认窗口存在、比例正常且位于前台。
- 只重复挑战启动时手动选中的当前关卡，不自动寻找或切换到最高难度。
- 失败重试次数可通过 `result.max_attempts` 限制，`0` 表示无限。
- F8 暂停/继续，F9 停止退出；鼠标移到屏幕角落可触发 PyAutoGUI FailSafe。

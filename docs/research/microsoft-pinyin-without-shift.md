# 微软拼音无 Shift 切换方案

研究日期：2026-08-27  
适用范围：MacStyleIME v1.3.0

## 结论

可以在不发送 Shift 的情况下尝试切换微软拼音内部中英文模式：取得前台窗口的默认 IME 窗口后，发送 `WM_IME_CONTROL`，使用兼容命令 `IMC_GETCONVERSIONMODE` / `IMC_SETCONVERSIONMODE` 读取和修改转换模式。

这条路径已被 [AIMSwitcher](https://github.com/A-23187/AIMSwitcher/blob/01ad49efcfda83b6911e66a82b46fbff5bed7e85/AIMSwitcher/main.cpp) 等真实工具采用，也符合 MacStyleIME 现有 IMM32 兼容代码的方向。它不合成 Shift，因此不会占用或破坏 `Shift+1`、`Shift+A` 等正常组合键。

但它不是适用于所有 Windows 输入框的正式通用接口。微软公开了 [`ImmGetDefaultIMEWnd`](https://learn.microsoft.com/en-us/windows/win32/api/imm/nf-imm-immgetdefaultimewnd) 和 [`WM_IME_CONTROL`](https://learn.microsoft.com/en-us/windows/win32/intl/wm-ime-control)，当前文档却没有把 `IMC_GETCONVERSIONMODE` / `IMC_SETCONVERSIONMODE` 列为受支持命令。因此，这只能标为传统 IMM32 兼容方案，不能承诺覆盖所有现代 TSF、WinUI、UWP 或浏览器输入框。

微软公开的 [`ImmSetConversionStatus`](https://learn.microsoft.com/en-us/windows/win32/api/imm/nf-imm-immsetconversionstatus) 也能设置转换状态，但要求调用方取得有效的 `HIMC`。MacStyleIME 是外部托盘进程，在现代 TSF 输入控件中经常不能取得代表目标输入框完整状态的 `HIMC`，所以它也不是跨应用通用解。

TSF compartment 是正式接口，但 compartment 隶属于相应的 TSF thread manager、document manager 或 context。MacStyleIME 在自己的进程中创建 TSF 对象，并不能据此写入前台应用线程的状态。若要严格按照 TSF 模型覆盖现代应用，需要实现并注册真正的 TSF Text Service/TIP，这已超出 v1.3.0 便携托盘工具的合理范围。参见微软的 [TSF Compartments](https://learn.microsoft.com/en-us/windows/win32/tsf/compartments) 与 [Text Services Framework](https://learn.microsoft.com/en-us/windows/win32/tsf/text-services-framework) 文档。

## 对 Shift 的处理

不要在 MacStyleIME 的低级键盘钩子中拦截、延迟再重放物理 Shift。正确分工是：

1. 在“微软拼音设置 > 按键”中取消 Shift 作为“模式切换”键；微软文档确认微软拼音支持管理模式切换快捷键，微软问答给出的操作也是取消 Shift。参见 [Microsoft Simplified Chinese IME](https://support.microsoft.com/en-au/windows/microsoft-simplified-chinese-ime-9b962a3b-2fa4-4f37-811c-b1886320dd72) 和 [Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/5575365/microsoft-pinyin-problem)。
2. 让物理 Shift 完全交给 Windows 和目标应用处理，从而保留 `Shift+1 -> !`、`Shift+A -> A`。
3. MacStyleIME 的“微软拼音内部模式”选项使用 `WM_IME_CONTROL` 兼容路径，不发送 Shift，也不设置 Shift fallback。

## v1.3.0 建议

实现时保留现有两种用户选项：

- “切换键盘输入法”：沿用现有 HKL/键盘布局切换。
- “切换微软拼音内部中英文”：只使用 `WM_IME_CONTROL + IMC_GET/SETCONVERSIONMODE`；失败时记录并保持当前状态，不回退为模拟 Shift，也不偷偷切换键盘布局。

管理员模式只能解决完整性级别/UIPI 的一部分限制，不能让不提供 IMM 状态的现代 TSF 控件突然支持该接口。因此交付时必须把它描述为兼容模式，并手动覆盖记事本、传统 CMD、Windows Terminal、浏览器和管理员窗口。

## 最终判断

- 找到了不发送 Shift 的可实施接口路径。
- 没有找到微软承诺能让外部托盘程序统一控制所有现代输入框的通用接口。
- 不建议把 Ctrl+Space 或其他模拟按键包装成“接口方案”；如果采用它，只能作为用户自行配置的快捷键方案。

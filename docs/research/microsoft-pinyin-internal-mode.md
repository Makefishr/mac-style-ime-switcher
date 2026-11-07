# Windows 10/11 微软拼音内部中英文模式切换研究

> 状态：此早期研究中的 Shift 注入建议已被后续决策取代。当前方案见[《微软拼音无 Shift 切换方案》](microsoft-pinyin-without-shift.md)。

## 范围与结论

本文面向 MacStyleIME `v1.3.0`（基线提交 `bfbfa75`），研究日期为 2026-08-27。目标是在不切换“中文（简体，中国）- 微软拼音”键盘布局的前提下，切换微软拼音内部的“中/英”模式，并与原有“英文键盘 ↔ 微软拼音键盘”切换方式同时保留。本文没有引用或审查 `v1.4.0`。

结论：可以实现，但没有一个适合外部托盘程序、同时又能对所有 Win32、现代 TSF 和管理员窗口进行确定性读写的“微软拼音内部模式 API”。对 MacStyleIME v1.3.0，推荐把两种用户可见方式明确分开：

1. **切换键盘输入法**：保留现有键盘布局切换逻辑。
2. **切换微软拼音内部中/英文模式**：微软拼音处于活动状态时，通过公开的 `SendInput` 模拟一次独立的 Shift 按下/抬起；不要切换键盘布局，也不要在同一次操作里继续调用 IMM 写模式。

这个推荐不是因为 Shift 是专用编程接口，而是因为微软明确把 Shift 记录为微软拼音中/英文模式的用户操作，而 `SendInput` 是公开的系统输入注入接口。它沿着真实键盘输入路径进入当前焦点应用，因而比外部进程直接修改另一个 TSF 线程的内部状态更适合现代应用。限制是：它是“切换”而不是“设置为指定状态”，依赖用户在微软拼音按键设置中启用 Shift，并且必须正确处理焦点、修饰键和权限。

## 证据分级

- **公开受支持接口**：Microsoft Learn 或当前 Windows SDK 明确声明的 API/接口及其使用范围。
- **兼容性推断**：由 SDK 定义、消息行为和现有 Windows 行为推导，但微软没有承诺其可从任意外部进程控制微软拼音。
- **项目经验**：真实项目源码证明某种做法曾被采用；不等价于微软的兼容性承诺，也不代表已覆盖所有 Windows 10/11 版本与应用框架。

## 方案比较

| 方案 | 接口地位 | 外部工具控制焦点应用 | 现代 TSF 应用 | 管理员窗口 | 结论 |
| --- | --- | --- | --- | --- | --- |
| `WM_IME_CONTROL` + `IMC_SETCONVERSIONMODE` / `IMC_SETOPENSTATUS` | `WM_IME_CONTROL` 与 `ImmGetDefaultIMEWnd` 公开；这四个 GET/SET 命令未列入当前桌面文档和当前 SDK 的公开命令列表 | 经典窗口中常可用，且便于向目标线程的默认 IME 窗口发消息 | 可能取不到有效 IME 窗口或状态，不能作为普遍保证 | 普通进程不应假设能控制更高完整性目标；提升后仍受目标实现影响 | 保留为经典 IMM/控制台兼容路径，不作为新内部模式的主方案 |
| `ImmSetConversionStatus` + `ImmSetOpenStatus` | 公开、受支持的桌面 IMM API | 需要目标窗口的有效 `HIMC`；官方契约主要描述 IME-aware 应用操作关联输入上下文 | 现代 TSF 控件可能没有可从外部工具取得的有效 `HIMC`，或仅有兼容桥 | 提升只解决完整性边界，不会凭空创建 `HIMC` | 适合同一应用/线程拥有的经典输入上下文；不适合作为跨应用全局方案 |
| `SendInput` 模拟 Shift | `SendInput` 公开；微软拼音使用 Shift 切换中/英有官方产品文档 | 作用于当前前台焦点，和用户真实按键路径一致 | 四种方案中最适合现代 TSF 的外部工具方案 | 只允许注入到相同或更低完整性目标；MacStyleIME 要控制管理员窗口必须自身提升 | 推荐作为“微软拼音内部模式”方式 |
| TSF `ITfCompartmentMgr` + `GUID_COMPARTMENT_KEYBOARD_OPENCLOSE` | TSF 接口、GUID 与 `SetValue` 都公开 | compartment 有 global、thread manager、document manager、context 等不同作用域；键盘开关在官方样例中属于当前 TSF thread manager | 在输入法/Text Service 所在目标线程内是正统做法 | 权限不是主要问题，关键是外部托盘进程拿到的是自己的 TSF 对象，不是目标应用线程的对象 | 不直接用于 v1.3.0；除非把产品改造成安装到各应用进程的正式 TSF Text Service |

## 1. `WM_IME_CONTROL` 与转换模式命令

### 公开部分

Microsoft Learn 说明：系统为每个线程创建默认 IME 窗口，`ImmGetDefaultIMEWnd` 返回该窗口，应用可以向它发送 `WM_IME_CONTROL`。`WM_IME_CONTROL` 本身也是公开消息。

- [ImmGetDefaultIMEWnd](https://learn.microsoft.com/en-us/windows/win32/api/imm/nf-imm-immgetdefaultimewnd)
- [WM_IME_CONTROL](https://learn.microsoft.com/en-us/windows/win32/intl/wm-ime-control)

### 未公开/兼容性部分

当前 Microsoft Learn 的桌面 `WM_IME_CONTROL` 页面只列出候选窗、组合窗、状态窗相关命令，没有列出：

```text
IMC_GETCONVERSIONMODE = 0x0001
IMC_SETCONVERSIONMODE = 0x0002
IMC_GETOPENSTATUS     = 0x0005
IMC_SETOPENSTATUS     = 0x0006
```

同样，微软维护的当前 SDK 重编译头文件 `immdev.h` 在 `// wParam for WM_IME_CONTROL` 下也只公开候选窗、组合窗和状态窗命令，却仍公开 `ImmGet/SetConversionStatus` 与 `ImmGet/SetOpenStatus` 函数。因此，“把上述数值作为 `WM_IME_CONTROL` 命令发给任意焦点窗口”不能标成当前桌面 SDK 的公开受支持契约，只能标成传统 IMM 兼容行为。

- [当前 Windows SDK `immdev.h`](https://github.com/microsoft/win32metadata/blob/1b30c323bdf80e8b6ab28e94602af477b9b3ac3d/generation/WinSDK/RecompiledIdlHeaders/um/immdev.h)

### 项目经验

[AIMSwitcher 的 `main.cpp`](https://github.com/A-23187/AIMSwitcher/blob/01ad49efcfda83b6911e66a82b46fbff5bed7e85/AIMSwitcher/main.cpp) 获取前台窗口的默认 IME 窗口，然后用 `IMC_GET/SETCONVERSIONMODE` 查询或设置模式。其 [README](https://github.com/A-23187/AIMSwitcher/blob/01ad49efcfda83b6911e66a82b46fbff5bed7e85/README.md) 明确说内部模式功能只在微软拼音上测试过。这能证明该兼容路径在真实工具中被使用过，但不能把它提升为微软对 Windows 10/11 现代 TSF 应用的保证。

MacStyleIME 当前键盘布局方式还需要这条兼容路径处理经典 conhost 和在切回中文布局后开启转换；建议继续用 `SendMessageTimeoutW(..., SMTO_ABORTIFHUNG, ...)`，避免目标 IME 窗口挂起时拖死全局热键线程。

## 2. `ImmSetConversionStatus` / `ImmSetOpenStatus`

这些是公开的 IMM 桌面 API：

- [ImmGetContext](https://learn.microsoft.com/en-us/windows/win32/api/imm/nf-imm-immgetcontext) 返回与指定窗口关联的输入上下文，并要求使用后调用 `ImmReleaseContext`。
- [ImmGetConversionStatus](https://learn.microsoft.com/en-us/windows/win32/api/imm/nf-imm-immgetconversionstatus) 只在 IME 支持相应模式时返回转换/句子模式。
- [ImmSetConversionStatus](https://learn.microsoft.com/en-us/windows/win32/api/imm/nf-imm-immsetconversionstatus) 设置当前转换状态。微软还特别注明，从 Windows 8 开始默认输入切换是按用户而不是按线程管理，并指出 Microsoft Japanese IME 的全局模式会使该调用在获得焦点时失败。此说明针对日文 IME，不能直接当作微软拼音结论，但足以表明“调用成功一次”不等于焦点变化后仍由输入法保留该状态。
- [ImmSetOpenStatus](https://learn.microsoft.com/en-us/windows/win32/api/imm/nf-imm-immsetopenstatus) 打开或关闭 IME。
- [Input Context](https://learn.microsoft.com/en-us/windows/win32/intl/input-context) 说明默认输入上下文是线程资源，状态影响与该上下文关联的窗口。

微软的 [IME Apps `Mode.c` 样例](https://github.com/microsoft/Windows-classic-samples/blob/d59e5f1dc9c768615e4e1ab1f0f009e6a3ed747c/Samples/Win7Samples/winui/input/ime/imeapps/Mode.c) 展示了规范顺序：对应用自己的窗口调用 `ImmGetContext`，读取状态，保留无关位，只修改目标位，调用 `ImmSetConversionStatus`，最后 `ImmReleaseContext`。

问题在于 MacStyleIME 是外部托盘程序，不拥有前台应用的输入控件。现代 TSF 应用可能返回空 `HIMC`，也可能仅通过 IMM/TSF 兼容层暴露不完整状态。因此这组 API 是“公开受支持的 IMM API”，但“用它从一个外部进程控制任意前台应用”不是已经证实的通用模型。它不能替代 Shift 作为现代 TSF 路径。

## 3. 模拟 Shift

微软的简体中文 IME 文档明确区分了两层状态：Windows 徽标键 + Space 切换已安装的键盘布局；在微软拼音布局内部，Shift 或输入模式图标在“中/英”之间切换。文档还说明按键配置可以改变模式切换快捷键。

- [MicrosoftDocs：Simplified Chinese IMEs](https://github.com/MicrosoftDocs/globalization/blob/main/globalization/input/simplified-chinese-ime.md)
- [Microsoft Simplified Chinese IME 支持页](https://support.microsoft.com/en-US/Windows/Hardware/Input-Devices/microsoft-simplified-chinese-ime)

`SendInput` 是公开 API，用于把键盘事件串行插入系统输入流。它受 UIPI 约束，只能向相同或更低完整性级别的应用注入；返回 0 时，Windows 也不保证把 UIPI 阻止明确报告为错误。它不会重置当前键盘状态，因此其他正在按下的键会干扰结果。

- [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)

因此，内部模式实现必须至少满足：

1. 只在焦点线程当前语言为简体中文时尝试；最好进一步识别当前活动 profile 确实是微软拼音，因为 `LANGID 0x0804` 也可能对应微软五笔或其他简体中文输入法。公开的 [ITfInputProcessorProfileMgr::GetActiveProfile](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfinputprocessorprofilemgr-getactiveprofile) 可返回当前活动键盘 profile，适合作为后续增强的识别入口。
2. 在发送前确认 Shift、Ctrl、Alt、Win 均未被用户按住，避免把 Shift 合并进另一快捷键。
3. 一次提交 Shift down + Shift up；检查返回的已插入事件数。
4. 确保自身低级键盘钩子忽略 `LLKHF_INJECTED`，避免注入事件再次触发本工具。
5. 不在同一次操作后无条件再发 `WM_IME_CONTROL`；否则经典应用可能先被 API 修改、再被 Shift 反向切回，形成双切换。
6. 不在“内部模式”失败时自动退回键盘布局切换，因为这会违背用户选择的语义。应记录失败，并保持当前键盘布局不变。

这条路径仍是 toggle，不是 `setChinese(true/false)`。当 IMM 状态可读时，可以仅用于前后诊断；当现代 TSF 应用不暴露可读状态时，程序只能确认事件成功插入，不能声称已读取到最终中/英状态。

## 4. TSF compartment

TSF compartment 是公开机制。Microsoft Learn 明确区分 global、thread manager、document manager 和 context compartment；`ITfCompartment::SetValue` 使用一个 TSF client ID 写入 `VARIANT` 值。

- [TSF Compartments](https://learn.microsoft.com/en-us/windows/win32/tsf/compartments)
- [ITfCompartment::SetValue](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfcompartment-setvalue)
- [Windows SDK `msctf.idl`](https://github.com/microsoft/win32metadata/blob/1b30c323bdf80e8b6ab28e94602af477b9b3ac3d/generation/WinSDK/RecompiledIdlHeaders/um/msctf.idl)

官方 [UILess TSF 样例 `Compartment.cpp`](https://github.com/microsoft/Windows-classic-samples/blob/d59e5f1dc9c768615e4e1ab1f0f009e6a3ed747c/Samples/Win7Samples/winui/input/tsf/textservice/uilessmode/Compartment.cpp) 明确把 `GUID_COMPARTMENT_KEYBOARD_OPENCLOSE` 放在当前 `ITfThreadMgr` 的 compartment manager 上。微软的 [SampleIME compartment 封装](https://github.com/microsoft/Windows-classic-samples/blob/d59e5f1dc9c768615e4e1ab1f0f009e6a3ed747c/Samples/IME/cpp/SampleIME/Compartment.cpp) 也在输入法自身的 `_pThreadMgr` 和 `_tfClientId` 上读写。

真实 TSF 输入法项目 [小狼毫 `WeaselTSF/Compartment.cpp`](https://github.com/rime/weasel/blob/d73f6295e8252ed2f7b9c12bae32e9001b1afdaa/WeaselTSF/Compartment.cpp) 使用同一模式，并监听 `GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION`。这属于“输入法/Text Service 已经被 TSF 加载到目标应用上下文”的项目经验。

MacStyleIME 若在自己的 Python 进程创建 `ITfThreadMgr`，获得的是自己的线程管理器；把 `GUID_COMPARTMENT_KEYBOARD_OPENCLOSE` 写到它上面，并不等于取得前台应用线程的 compartment。`GetGlobalCompartment` 也不能把一个 thread-manager compartment 自动变成全局远程控制通道。因此直接增加 Python COM 调用不会解决跨应用问题。要严格按 TSF 模型控制每个现代应用，需要实现、注册、签名并让系统加载一个正式 TSF Text Service/TIP；这远超 v1.3.0 便携托盘工具的范围与风险预算，不推荐。

## 现代 TSF、普通/管理员窗口的边界

微软要求 Windows 应用中的现代 IME 使用 TSF，系统会把所选 IME DLL 加载到应用进程，TSF 位于应用和 IME 之间。这解释了为什么外部工具在新记事本、Windows Terminal、WinUI/UWP 等环境中不能依赖一个经典 `HIMC` 或默认 IME 窗口来代表完整状态。

- [IME requirements](https://learn.microsoft.com/en-us/windows/apps/develop/input/input-method-editor-requirements)
- [Text Services Framework](https://learn.microsoft.com/en-us/windows/win32/tsf/text-services-framework)

权限方面应分别看待“捕获 CapsLock”和“执行切换”：

- 普通 MacStyleIME 不应被当作可控制管理员窗口的完整方案。微软 PowerToys 的官方说明也明确指出，键盘重映射若要作用于管理员窗口，PowerToys 自身必须以管理员身份运行：[PowerToys Keyboard Manager](https://learn.microsoft.com/en-us/windows/powertoys/keyboard-manager#troubleshooting-keyboard-remapping-issues)。
- 提升 MacStyleIME 后，`SendInput` 可以面向相同或更低完整性应用，管理员 CMD/Terminal 与普通应用都在该方向内；但这仍不覆盖其他桌面、锁屏、登录界面或 UAC Secure Desktop。
- `WM_IME_CONTROL` 是窗口消息。UIPI 防止低完整性发送者向更高完整性窗口发送受限消息；微软并未在消息过滤文档中承诺 `WM_IME_CONTROL` 一定能跨完整性工作，因此不能把它当作绕过提升的方案：[ChangeWindowMessageFilterEx](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changewindowmessagefilterex)。
- 提升只解决权限方向，不解决目标应用是否提供 IMM 状态、是否采用现代 TSF、是否允许 Shift 作为模式键等兼容性问题。

## 对 v1.3.0 的推荐实现

设置中只保留两个互斥选项：

### 选项 A：切换键盘输入法

- 保留英文美国键盘与简体中文键盘之间的现有切换。
- 保留经典 conhost/IMM 的 `WM_IME_CONTROL` 兼容处理，但文档与日志不要把消息发送成功表述成“微软拼音最终模式已确认”。
- 适合希望 CapsLock 明确在两个键盘布局之间切换的用户。

### 选项 B：切换微软拼音内部中/英文模式

- 不更换 HKL，不发送 `WM_INPUTLANGCHANGEREQUEST`。
- 仅在简体中文布局且当前活动 profile 可判定为微软拼音时执行；profile 暂时无法判定时，至少保证语言为 `0x0804`，并把兼容性限制写进设置说明。
- 使用 `SendInput` 发送单次 Shift down/up，保留当前已有的修饰键保护与注入事件忽略逻辑。
- 不把 `ImmSetConversionStatus`、`WM_IME_CONTROL` 或 TSF compartment 串成自动 fallback，避免双切换和跨框架状态分裂。
- 设置说明应明确：“需要在微软拼音的按键设置中启用 Shift 切换；仅在微软拼音为当前键盘时生效。”

如果未来产品要求“不论初始状态，强制设为中文或英文”，当前便携外部进程架构没有覆盖所有现代 TSF 应用的公开通用接口。届时应重新评估正式 TSF Text Service，而不是继续叠加未公开消息或固定延时。

## 本次只读检查对应的当前代码

当前工作区已经具备两个设置枚举和两个单选项；内部模式当前由 `SendInput` 模拟 Shift，键盘布局模式仍使用布局消息和 IMM 兼容路径。这个方向与上述推荐一致。本研究任务未修改实现代码，也未运行程序或兼容性测试。

- [`ime_switcher/toggle.py`](../../ime_switcher/toggle.py)
- [`ime_switcher/settings_store.py`](../../ime_switcher/settings_store.py)
- [`ime_switcher/settings_ui.py`](../../ime_switcher/settings_ui.py)

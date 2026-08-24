# Mac-style IME Switcher

在 Windows 10/11 上模仿 macOS 的 CapsLock 手势：短按控制中文/英文，长按进入大写。程序在后台运行，常驻系统托盘。

## 下载与快速使用

1. 从 [GitHub Releases 的 latest 页面](https://github.com/Makefishr/mac-style-ime-switcher/releases/latest) 下载正式的 `MacStyleIME.exe`。
2. 将 EXE 放在稳定、可写的目录中并运行。程序没有主窗口，右键托盘图标可打开“设置”或退出。
3. 在“设置”中选择切换模式，并按需启用开机自启或管理员启动。

程序只允许一个逻辑实例运行。普通使用不需要管理员权限。

## 按键行为

### CapsLock

- **短按（不足 1 秒）**：在当前上下文属于所选模式的支持范围时，执行一次中文/英文切换。
- **长按（达到 1 秒）**：在受支持上下文中进入原生大写锁定，CapsLock LED 亮起；再次按 CapsLock 退出大写。
- **不支持、无法识别或切换失败**：原物理按键不会直接交给下游；程序会在松开后回放一次完整的原生 CapsLock 按下和抬起，因此不会吞掉 CapsLock 的原生切换语义。
- 自动重复的 CapsLock keydown 仍属于同一次按压，不会产生多个动作。

CapsLock 的上下文判断和切换在单个后台 worker 中按顺序完成；键盘 hook 本身只记录手势并快速返回。

### Shift 与系统切换快捷键

- 单独轻按通用 Shift、左 Shift 或右 Shift（包括自动重复）不会传给微软拼音，因此不会触发其 Shift 中英切换。
- Shift 与字母或其他普通键组合时（也可同时按住 Ctrl/Alt），程序会先重放对应的左/右 Shift，再重放目标键，保留键盘大写和快捷键修饰语义。
- `Win+Space`、`Ctrl+Space`、仅修饰键的 `Ctrl+Shift` 与 `Alt+Shift` 会被拦截，避免绕过 CapsLock 切换逻辑。

## 两种切换模式

### 中文键盘布局 / 英文键盘布局（默认）

只在当前活动输入上下文明确为简体中文（zh-CN）或美国英语（en-US）时工作：

- zh-CN → en-US
- en-US → zh-CN

布局模式只切换键盘布局，不读取或修改微软拼音内部的 open/conversion 状态。其他语言、目标布局不可用或布局请求失败时，程序不会假装成功，而是回放原生 CapsLock。

### 微软拼音内部中文/英文模式

此模式不更换键盘布局。它只在当前活动上下文同时满足以下条件时工作：

- 当前语言明确为 zh-CN；
- 输入法描述经规范化后明确识别为“微软拼音”或“Microsoft Pinyin”。

程序读取微软拼音的 open/conversion 状态，写入目标状态后再读回确认。描述为空、未知输入法、窗口或 IME 查询失败、写入失败、超时或读回不一致时均按不支持处理，并回放原生 CapsLock。

## 设置、配置与日志

托盘菜单只包含“设置”和“退出”。设置窗口提供：

- CapsLock 切换模式；
- 当前用户开机自启；
- 可选的管理员启动。

设置成功后窗口直接关闭；保存失败时窗口保持打开并显示错误。配置文件和当前用户的开机自启项会作为一次保存操作处理，无法完整回滚时会提示重新检查开机自启。

- 配置：EXE 同目录下的 `ime_switcher.json`
- 日志：`%LOCALAPPDATA%\MacStyleIME\ime_switcher.log`
- 日志回退位置：`%TEMP%\MacStyleIME\ime_switcher.log`

退出正在运行的托盘程序后，也可直接维护当前用户的开机自启项：

```bat
MacStyleIME.exe --install
MacStyleIME.exe --uninstall
```

这两个命令只修改开机自启项，不改写 `ime_switcher.json`。

## 权限与安全边界

管理员启动默认关闭，保存后在下次普通启动时生效。自动请求管理员权限必须同时满足：

- 运行的是打包后的 EXE；
- EXE 位于 Windows Known Folder API 返回的真实 `Program Files` 或 `Program Files (x86)` 目录内；
- 当前进程尚未具有管理员权限。

程序不信任可由进程修改的 `ProgramFiles` 环境变量，也不会从普通便携目录自动提升。受保护目录无法确认、路径不在上述目录内、UAC 被取消或提升失败时，程序会记录日志并退出，不会假装已获得权限。

## 从源码构建

构建要求以 [`build_ime.bat`](build_ime.bat) 和 [`requirements-build.lock`](requirements-build.lock) 为准。脚本会验证 Windows x64 的 CPython 3.12、官方 Python launcher 与 Windows PowerShell，并在项目目录创建隔离的 `.venv-build`。

```bat
build_ime.bat
```

依赖由 `requirements-build.lock` 固定版本和 SHA-256 hash，并通过 `--require-hashes` 安装；不要用全局 `pip` 或 `pyinstaller` 替代构建脚本。默认产物位于 `dist\MacStyleIME.exe`。

## 实现与项目结构

程序通过 `WH_KEYBOARD_LL` 处理物理键盘事件。CapsLock 的上下文查询与副作用在有界单 worker 中执行；Shift 键盘组合通过单批 `SendInput` 保持修饰键与目标键的顺序。注入的回放事件会直接绕过本程序的 hook，避免递归。

```text
ime_switcher/
├── __main__.py      # 单实例、命令行和托盘主循环
├── config.py        # 常量、路径与日志
├── hook.py          # 低级键盘 hook 和 SendInput 边界
├── shift_guard.py   # Shift tap 抑制与键盘组合重放
├── caps_ime.py      # CapsLock 手势、worker 与 LED 状态
├── toggle.py        # 布局和微软拼音内部模式切换
├── settings.py      # 设置、开机自启和管理员重启
├── tray.py          # 托盘菜单
└── winapi.py        # Win32 API 声明与封装
tests/               # 行为与构建契约测试
build_ime.bat        # 锁定依赖的 Windows 构建入口
requirements-build.lock
```

## 许可证

本项目采用 [MIT License](LICENSE)。

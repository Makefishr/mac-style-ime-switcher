# Mac-style IME Switcher

适用于 Windows 10/11 的 CapsLock 中英文切换工具。程序常驻系统托盘，短按 CapsLock 切换输入法，长按进入或退出大写锁定。

## 下载与快速开始

1. 从 [Releases](https://github.com/Makefishr/mac-style-ime-switcher/releases/latest) 下载 `MacStyleIME.exe`。
2. 直接运行，无需安装。
3. 右键托盘图标打开“设置”。
4. 如果需要在管理员 CMD、Windows Terminal 或其他管理员应用中切换，请启用“以管理员身份运行”。

启用管理员模式后，Windows 会显示 UAC 请求；取消或启动失败时，MacStyleIME 会回到普通模式继续运行。

## CapsLock 行为

- **短按**：执行设置中选择的输入法切换方式。
- **长按 1 秒后松开**：进入或退出大写锁定，CapsLock 指示灯会同步变化。
- **普通 Shift 组合不受影响**：例如 `Shift+1` 仍可正常输入 `!`。

程序会拦截 Windows 默认的输入法切换组合 `Win+Space`、`Ctrl+Space`、`Ctrl+Shift` 和 `Alt+Shift`，避免它们改变 MacStyleIME 管理的输入法状态。

## 两种切换方式

### 切换键盘输入法

在英文键盘和微软拼音键盘之间切换。这是默认方式，需要先在 Windows 中添加中文和英文键盘布局。

### 仅切换微软拼音内部中英文

保持微软拼音键盘不变，只切换其内部中文/英文模式。使用前请先切到微软拼音。

该方式使用 Windows IMM 兼容接口，不发送 Shift，也不切换键盘布局。它适用于已经支持该接口的输入框，但不能保证覆盖所有现代 TSF、WinUI、UWP 或浏览器输入框。

## 设置

- **以管理员身份运行**：允许 MacStyleIME 处理相同或更低权限应用中的按键，包括管理员应用。
- **输入法切换方式**：在两种 CapsLock 切换行为之间选择。
- **开机时自动运行**：为当前 Windows 用户写入启动项。

设置和诊断日志保存在 `%LOCALAPPDATA%\MacStyleIME`。

## 命令行

```text
MacStyleIME.exe              运行并驻留系统托盘
MacStyleIME.exe --install    添加当前用户开机自启
MacStyleIME.exe --uninstall  移除当前用户开机自启
MacStyleIME.exe --help       显示帮助
```

## 兼容边界

- 管理员模式不会绕过 Windows 安全桌面，也不能作用于登录界面、锁屏或 UAC 确认窗口。
- 微软拼音内部模式依赖目标输入框对 IMM 兼容消息的支持；不支持时会保持当前状态。
- 程序只处理当前交互桌面的键盘输入。

## 隐私

- 不包含遥测、联网、上传或自动更新逻辑。
- 键盘钩子只瞬时处理完成切换所需的虚拟键状态，不记录输入内容、剪贴板或窗口标题。
- 诊断日志包含启动、切换结果、错误和本地路径，不包含键入文本。

## 从源码构建

需要 64 位 Python 3.12。在仓库根目录运行：

```bat
build_ime.bat
```

构建脚本会创建全新的隔离环境，通过 `requirements-build.txt` 校验所有直接和间接依赖的版本与 SHA-256，并在成功后生成根目录下的 `MacStyleIME.exe`。

更新构建依赖时，先修改 `requirements-build.in`，再在受控的 Windows x64 / Python 3.12 环境中重新生成并审查锁文件。

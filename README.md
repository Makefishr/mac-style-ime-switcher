# Mac-style IME Switcher

在 Windows 上用 CapsLock 一键切换输入法，让习惯Mac系统的人在Windows系统上也能顺手拈来

## 这是什么？

macOS 用户习惯按 **CapsLock** 在中英文之间切换，但 Windows 上
默认只能用 Win+Space / Ctrl+Shift 这些组合键，无法修改成CapsLock切换。

这个工具就是给习惯Mac系统切换输入法的人让他在 **Windows 10/11** 系统上也能顺畅使用。
—— 短按 CapsLock 切中英文，长按照样开启大写锁定。

后台静默运行，托盘常驻，占用几乎为零。重启不丢，开机自启。

## 功能

- **CapsLock 切换输入法** — 短按 CapsLock 在中/英文输入法之间切换，无弹窗、无延迟
- **微软拼音内部模式感知** — 检测中文布局下微软拼音的内置中/英文模式，不会出现"切到中文布局却还是英文模式"
- **屏蔽 Windows 默认快捷键** — 自动拦截 Win+Space、Ctrl+Space、Ctrl+Shift、Alt+Shift，防止误触打乱切换逻辑
- **系统托盘图标** — 显示当前输入法状态（中/En），右键菜单可开关开机自启
- **长按开启大写** — 按住 CapsLock 1 秒以上进入大写模式，再按关闭。短按依旧切输入法
- **CapsLock LED 始终关闭** — 输入法模式下大写锁定 LED 自动熄灭；长按进入大写模式后 LED 正常亮起

## 系统要求

- **Windows 10 / Windows 11**（不支持 macOS，macOS 原生已有此功能）
- 系统中已添加至少一个中文键盘布局和一个英文键盘布局

## 下载

从 [Releases](https://github.com/Makefishr/mac-style-ime-switcher/releases/latest) 下载最新版 `MacStyleIME.exe`。

## 安装 & 运行

```
# 直接运行（无窗口，后台常驻托盘）
MacStyleIME.exe

# 设为开机自启
MacStyleIME.exe --install

# 取消开机自启
MacStyleIME.exe --uninstall
```

## 从源码构建

```bash
pip install pystray pillow pyinstaller
build_ime.bat
```

## 原理

通过 `WH_KEYBOARD_LL` 低级键盘钩子拦截 CapsLock 的按下和释放事件：

- **短按（< 1 秒）** → 触发 IME 切换：`GetKeyboardLayout` 检测当前输入法，`LoadKeyboardLayoutW` + `PostMessage(WM_INPUTLANGCHANGEREQUEST)` 静默切换
- **长按（≥ 1 秒）** → 大写模式：释放时发送 `keybd_event(VK_CAPITAL)` 切换系统大写锁定

所有 I/O 操作在独立线程中执行，避免阻塞 hook 线程导致 Windows 移除钩子。

## 项目结构

```
ime_switcher/
├── __init__.py
├── __main__.py      # 入口：单例检查、CLI 解析、主循环
├── caps_ime.py      # CapsLock IME 引擎（短按/长按状态机、LED 管理）
├── config.py        # 常量、日志、全局状态
├── hook.py          # 低级键盘钩子 (WH_KEYBOARD_LL)
├── toggle.py        # 输入法切换核心逻辑
├── tray.py          # 系统托盘和注册表自启
├── winapi.py        # Win32 API 声明和封装
tests/
└── test_caps_ime.py
```

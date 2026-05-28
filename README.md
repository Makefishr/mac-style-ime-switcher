# Mac-style IME Switcher

Mac 风格的中英输入法切换器 —— 用 CapsLock 一键切换输入法。

## 功能

- **CapsLock 切换输入法** — 像 macOS 一样，按 CapsLock 在中/英文输入法之间切换，无弹窗、无延迟
- **微软拼音内部模式感知** — 检测中文布局下微软拼音的内置中/英文模式，不会出现"切到中文布局却还是英文模式"的问题
- **屏蔽 Windows 默认快捷键** — 自动拦截 Win+Space、Ctrl+Space、Ctrl+Shift、Alt+Shift，防止误触回退
- **系统托盘图标** — 显示当前输入法状态（中/En），右键菜单可查看状态、开关开机自启
- **长按 CapsLock 开启大写** — 按住 CapsLock 1 秒以上松开即可进入大写模式，再次按下关闭。短按依旧切换输入法
- **CapsLock LED 始终关闭** — 输入法模式下大写锁定 LED 自动熄灭；长按进入大写模式后 LED 正常亮起

## 下载

从 [Releases](https://github.com/Makefishr/mac-style-ime-switcher/releases/latest) 页面下载最新版 `MacStyleIME.exe`。

## 安装/运行

```
# 直接运行（无窗口，常驻托盘）
MacStyleIME.exe

# 添加开机自启
MacStyleIME.exe --install

# 移除开机自启
MacStyleIME.exe --uninstall
```

## 从源码构建

```bash
# 安装依赖
pip install pystray pillow pyinstaller

# 构建
python -m PyInstaller --onefile --noconsole --name "MacStyleIME" \
    --hidden-import pystray --hidden-import PIL --hidden-import PIL.Image --hidden-import PIL.ImageDraw \
    --hidden-import ime_switcher.config --hidden-import ime_switcher.winapi \
    --hidden-import ime_switcher.toggle --hidden-import ime_switcher.hook \
    --hidden-import ime_switcher.tray \
    --hidden-import ime_switcher.caps_handler \
    --add-data "app.ico;." --icon "app.ico" \
    --distpath "." ime_switcher/__main__.py

# 或直接运行构建脚本
build_ime.bat
```

## 项目结构

```
ime_switcher/
├── __init__.py      # 包标记
├── __main__.py      # 入口：单例检查、CLI 解析、主循环
├── config.py        # 常量、日志、全局状态
├── winapi.py        # Win32 API 声明和封装
├── toggle.py        # 输入法切换核心逻辑
├── hook.py          # 低级键盘钩子 (WH_KEYBOARD_LL)
├── caps_handler.py  # CapsLock 双动作状态机（短按/长按）
├── tray.py          # 系统托盘和注册表自启
tests/
└── test_caps_handler.py  # CapsLock 状态机单元测试
```

## 原理

通过 `WH_KEYBOARD_LL` 键盘钩子拦截 CapsLock 的按下和释放事件。短按（< 1 秒）触发 IME 切换：使用 `GetKeyboardLayout` 检测当前输入法状态，`LoadKeyboardLayoutW` + `PostMessage(WM_INPUTLANGCHANGEREQUEST)` 实现静默切换。长按（≥ 1 秒）触发大写模式：在按键释放时发送 `keybd_event(VK_CAPITAL)` 切换系统大写锁定状态。所有 I/O 操作在独立线程中执行，避免阻塞 hook 线程导致 Windows 移除钩子。

## 系统要求

- Windows 10/11
- 已添加中文和英文键盘布局（至少各一个）

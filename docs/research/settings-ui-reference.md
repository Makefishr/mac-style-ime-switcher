# Settings-window references for MacStyleIME v1.3.0

## Scope

This note evaluates how to add a native settings window to MacStyleIME `v1.3.0` (`bfbfa75`). It covers the GUI toolkit, tray/GUI event-loop integration, single-window behavior, setting ownership, user-visible state, and PyInstaller impact. Elevation mechanics are covered separately in [windows-elevation-reference.md](windows-elevation-reference.md).

The recommendation does not use code from MacStyleIME `v1.4.0` or `main`.

## v1.3.0 baseline

- `ime_switcher.__main__.run_background` starts the keyboard-hook thread and then blocks in `pystray.Icon.run()`.
- `ime_switcher.tray.build_menu` builds a tray-only interface. Auto-start is changed immediately from the menu and is stored in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- There is no GUI event loop, settings controller, or per-user settings file.
- The local v1.3.0 environment already includes Python's Tk/Tcl 8.6. PySide6, PyQt6, and CustomTkinter are not installed.

## Source findings

### 1. Keep Tk on the main thread and move pystray to a worker thread

The official [pystray usage guide](https://github.com/moses-palmer/pystray/blob/cf38ef6aa20f5c799829502993892827c7574146/docs/usage.rst#L35-L48) says `Icon.run()` is blocking and normally belongs on the main thread, but explicitly states that running it on another thread is safe for Windows-only applications. The same guide describes [`run_detached()` for GUI-framework integration](https://github.com/moses-palmer/pystray/blob/cf38ef6aa20f5c799829502993892827c7574146/docs/usage.rst#L237-L247).

CPython's [Tkinter threading model](https://github.com/python/cpython/blob/f54fd2ab6e1f3e3e81a88be3d94ff9d5867b3b3a/Doc/library/tkinter.rst#L442-L491) says each `Tk` object owns a Tcl interpreter associated with its creating thread, relies on a running event loop for cross-thread calls, and recommends only one `Tk` instance in practice.

For this Windows-only application, the simplest ownership model is therefore:

- the process main thread creates one hidden `tk.Tk` root and runs `root.mainloop()`;
- a daemon worker thread runs `pystray.Icon.run()`;
- the existing keyboard hook remains on its own worker thread;
- tray callbacks enqueue commands for the Tk thread instead of creating or manipulating widgets directly.

A small `queue.SimpleQueue` polled by `root.after(...)` is preferable to direct cross-thread Tk calls. It makes the ownership rule explicit and avoids relying on the subset of Tkinter calls that happen to marshal successfully between threads.

### 2. Reuse one `Toplevel` settings window

[PyAutoClicker](https://github.com/GoblinRules/PyAutoClicker/blob/fbe66ac129b381149a306c5af29e608e51d1a9cf/pyautoclicker.py#L413-L426) is a concrete pystray/Tkinter application: it runs pystray on a daemon thread and routes tray actions back to Tk with `root.after(0, ...)`. Its [settings-window implementation](https://github.com/GoblinRules/PyAutoClicker/blob/fbe66ac129b381149a306c5af29e608e51d1a9cf/pyautoclicker.py#L764-L858) keeps one `Toplevel`, raises the existing window instead of creating duplicates, and maps `WM_DELETE_WINDOW` to `withdraw`.

That project is useful as an implementation example, not as an authority. The reusable behavior is:

- keep a single settings-window reference;
- if it exists, refresh values, `deiconify`, `lift`, and focus it;
- otherwise create one `Toplevel` owned by the hidden root;
- close/X hides the window without terminating the tray application;
- only the application's explicit Quit action destroys the root.

MacStyleIME has only two user settings in this feature, so a tabbed notebook would add navigation without adding clarity. A compact, single-page `ttk` dialog is sufficient.

### 3. Display effective state, especially after a failed write or cancelled UAC

[ActivityWatch's tray implementation](https://github.com/ActivityWatch/aw-qt/blob/455ad3efd258b6b735eda8dc89f99110a98a6556/aw_qt/trayicon.py) is Qt-based, but its state handling is toolkit-independent: before showing the menu it reads the actual OS auto-start state, and after an attempted change it refreshes the check mark from the OS even when the operation failed.

MacStyleIME should use the same product rule in the settings window:

- auto-start status comes from the registry, not merely from the checkbox's last value;
- privilege status comes from the current process token, not merely from the saved preference;
- after Save, each control is refreshed from its effective source;
- UAC cancellation leaves the ordinary process alive and shows `当前运行：普通模式`, even if the saved preference still requests administrator mode on the next launch.

This distinction avoids claiming that the application is elevated just because the user selected the option.

### 4. Use the domain-adjacent project selectively

[KoreanInputKeeper](https://github.com/songyoungsoo/KoreanInputKeeper/blob/75728744ebfc14649a6f3f6ea4ce6426e4971396/korean_input_keeper.py) is a Windows IME/keyboard tray application using Tkinter and pystray. Its [OSD thread](https://github.com/songyoungsoo/KoreanInputKeeper/blob/75728744ebfc14649a6f3f6ea4ce6426e4971396/korean_input_keeper.py#L153-L209) owns one Tk event loop, and external events are posted with `after(...)`. This supports the event-loop separation above.

Two details should not be copied:

- it creates Tk on a background thread; MacStyleIME can give Tk the main thread and use pystray's documented Windows worker-thread support;
- it stores `settings.json` beside the executable and writes it directly. That is fragile for an installed or read-only executable directory and can leave a truncated file if the process stops during a write.

## Toolkit decision

Use standard-library `tkinter` with themed `ttk` widgets.

Reasons specific to v1.3.0:

- Tk/Tcl is already present in the supported Python environment;
- no new pip dependency or GUI framework migration is required;
- `ttk` uses Windows-themed controls and is enough for two checkboxes, status text, explanatory text, and Save/Cancel buttons;
- the existing pystray/Pillow stack remains unchanged;
- PyInstaller already has Tkinter collection support, although the one-file artifact must still be built once to verify Tcl/Tk data collection.

Do not add PySide6/PyQt6 for this window. ActivityWatch demonstrates a clean single-event-loop Qt architecture, but adopting Qt here would replace the tray architecture and add a large dependency surface for a small settings form. Do not add CustomTkinter solely for styling; it would introduce another dependency and a custom theme without improving the feature contract.

## Recommended settings window

Use the tray item `设置...` to open a fixed-minimum-size, resizable `ttk` window with two sections:

### Run mode

- Checkbox: `以管理员身份运行`
- Helper text: `启用后会请求 Windows 管理员权限，以便在管理员应用中切换输入法。若取消或启动失败，将继续以普通模式运行。`
- Effective status: `当前运行：管理员模式` or `当前运行：普通模式`
- A changed run-mode preference takes effect through an orderly application restart after Save.

### Startup

- Checkbox: `开机时自动运行`
- Its initial and post-save values are always read from the actual HKCU Run entry.

### Actions

- `保存`: validate, persist/apply, refresh effective state, and initiate the required privilege-mode restart.
- `取消`: discard unsaved control values and hide the window.
- Window close/X: same behavior as Cancel.

Do not show a success dialog for an ordinary save. Use a short tray notification only for outcomes that need attention: UAC cancelled, elevation launch failed, or an auto-start registry operation failed. Each failure should state that MacStyleIME is still running in ordinary mode when that is the effective result.

## Settings ownership and persistence

Separate desired configuration from effective operating-system state:

- `%LOCALAPPDATA%\MacStyleIME\settings.json` owns `run_as_administrator`.
- The HKCU Run registry value remains the source of truth for auto-start; do not duplicate it into JSON.
- The current process token remains the source of truth for `当前运行`.

The JSON loader should merge known fields with defaults and ignore a missing file. A malformed file should be logged with its path and the application should use defaults. Save through a temporary file in the same directory followed by `os.replace`, so the final replacement is atomic on the same volume. Preserve a small module-level API rather than exposing Tk variables to startup code.

Suggested boundary:

```text
settings_store.py
  load_preferences() -> Preferences
  save_preferences(preferences) -> None

settings_ui.py
  SettingsWindow.show()
  SettingsWindow.hide()
  SettingsWindow.refresh_effective_state()

tray.py
  build_menu(on_open_settings, on_quit)
```

The elevation module should not import Tkinter. It returns `launched`, `cancelled`, or `failed(error)`; the settings controller decides which user-visible message and lifecycle action follow.

## Integration contract for v1.3.0

1. Parse internal restart/elevation arguments and load the preference before starting long-lived UI components.
2. Acquire and retain the single-instance mutex according to the elevation handoff contract.
3. Start the existing keyboard-hook thread without changing CapsLock, physical Shift interception, IME selection, or fallback behavior.
4. Create one hidden Tk root on the main thread.
5. Start pystray on a Windows worker thread.
6. Route `设置...` and `退出` through the GUI command queue.
7. Run `root.mainloop()` until Quit or a successful restart handoff.
8. On shutdown, set `config.running = False`, stop the tray icon, destroy Tk, and release retained Win32 handles deterministically.

## Minimal verification

The appropriate automated boundary is intentionally narrow:

- compile/import validation for the new modules;
- one PyInstaller build using the existing `--onefile --noconsole` command;
- no broad test-suite expansion for a small Windows UI integration.

Manual checks remain necessary:

1. open Settings repeatedly from the tray and confirm only one window exists;
2. close with X and reopen, confirming unsaved changes were discarded;
3. toggle auto-start, reopen Settings, and confirm the value matches the registry;
4. enable administrator mode, accept UAC, and confirm the replacement instance reports administrator mode;
5. enable administrator mode, cancel UAC, and confirm the ordinary instance and tray remain alive;
6. verify input switching in both a normal editor and an elevated application's input field;
7. run the packaged one-file executable and confirm the settings window opens without missing Tcl/Tk resources.

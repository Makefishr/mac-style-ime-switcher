# Windows elevation references for MacStyleIME v1.3.0

## Scope

This note evaluates reusable source patterns for an optional “administrator compatibility mode” in MacStyleIME `v1.3.0` (`bfbfa75`). The required behavior is: request elevation only when the user enables the option, keep the normal process alive if UAC is cancelled or launch fails, preserve single-instance behavior during a restart, and remain compatible with the existing PyInstaller `--onefile --noconsole` build.

The recommendation deliberately does not use code from MacStyleIME `v1.4.0` or `main`.

## Primary-source findings

### 1. Use `ShellExecuteExW` with the `runas` verb

- [Wox `shell_windows.go`](https://github.com/Wox-launcher/Wox/blob/0d13a0981c29f2ea931482a7d130567d82f82e78/wox.core/util/shell/shell_windows.go) uses `ShellExecuteExW`, UTF-16 strings, `SEE_MASK_NOCLOSEPROCESS`, and an owned process handle. It distinguishes Windows `ERROR_CANCELLED` from other launch errors and closes the returned handle after waiting. This is the strongest directly reusable contract.
- [Conda menuinst `win_elevate.py`](https://github.com/conda/menuinst/blob/9f91ff55c06f6b0af5f779f773d470268678b4a5/menuinst/platforms/win_utils/win_elevate.py) demonstrates the same flow in Python/ctypes: `IsUserAnAdmin`, `ShellExecuteEx`, `SEE_MASK_NOCLOSEPROCESS`, `WaitForSingleObject`, `GetExitCodeProcess`, and `subprocess.list2cmdline` for Windows argument quoting. Its implementation is old and ANSI-based, so its structure is useful but its declarations should not be copied verbatim.
- [ctwin32 `shell.py`](https://github.com/RoccoMatano/ctwin32/blob/468082ca912f08f5e24585df52bb4062e243b3db/ctwin32/shell.py) provides a current Python implementation of `SHELLEXECUTEINFOW`, process-handle waiting, argument-to-command-line conversion, and both elevation and de-elevation primitives.
- [EventGhost `PipedProcess.py`](https://github.com/EventGhost/EventGhost/blob/48b913d4925545784f95eda49993c46b577a75f9/eg/WinApi/PipedProcess.py) shows a mature elevated-child design with `ShellExecuteExW`, a retained process handle, bounded startup coordination, and named-pipe communication. The IPC layer is unnecessary for MacStyleIME because the whole application can restart elevated.
- Microsoft documents `runas` as the Shell verb that launches an application as administrator and presents the UAC consent or credential prompt: [ShellExecute documentation](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shellexecutea).

### 2. Treat UAC cancellation as an ordinary product outcome

[Wox](https://github.com/Wox-launcher/Wox/blob/0d13a0981c29f2ea931482a7d130567d82f82e78/wox.core/util/shell/shell_windows.go) maps `ERROR_CANCELLED` to a dedicated elevation-cancelled result. MacStyleIME should do the same: cancellation is not a crash and must not terminate the existing normal process.

A useful counterexample is [GoodbyeDPI UI `run.py`](https://github.com/Storik4pro/goodbyeDPI-UI/blob/72ce058f147ebb7795ecb641d285ba5bd022a020/run.py): it calls `ShellExecuteW(..., "runas", ...)` and immediately exits without checking the return value. If UAC is cancelled, no replacement instance exists. Do not copy this pattern.

Recommended result handling:

- elevation launched: stop the old tray/hook instance in an orderly handoff;
- UAC cancelled (`ERROR_CANCELLED` / 1223): keep the old instance running normally and show one clear notification;
- other launch error: keep the old instance running normally, log the Win32 error, and show one failure notification.

### 3. Coordinate elevation with the single-instance mutex

MacStyleIME `v1.3.0` acquires its global mutex before command processing and does not retain the handle for an explicit restart. An elevated child started while the old instance is alive would therefore be rejected as a duplicate.

[ColorControl `Program.cs`](https://github.com/Maassoft/ColorControl/blob/d0d3bb4682c483b0b2c14a794958abf2d142100e/ColorControl/Program.cs) keeps its mutex as process state and explicitly closes it before `Application.Restart()`. This validates the important ownership rule, but closing MacStyleIME's mutex before the UAC result is known would create a race and weaken single-instance protection.

Recommended handoff:

1. Keep the old process and mutex alive while `ShellExecuteExW` presents UAC.
2. Launch the child with an internal argument containing the trusted parent PID, for example `--elevated-restart <pid>`.
3. Only after `ShellExecuteExW` succeeds, stop the old tray and hook loop and close its retained mutex handle.
4. The elevated child waits for that exact parent process to exit (or for a short bounded timeout), then acquires the normal versioned global mutex and starts.
5. The internal restart argument is not exposed as a user setting and does not bypass the normal mutex for unrelated launches.

For a fresh application launch with the preference already enabled, elevation should happen before mutex acquisition. That path has no old application instance to hand off.

### 4. Preserve the PyInstaller `asInvoker` manifest

PyInstaller's default Windows manifest is `asInvoker`; its `--uac-admin` option changes the execution level to `requireAdministrator`: [PyInstaller manifest source](https://github.com/pyinstaller/pyinstaller/blob/a564c48bee108fccf39e34711b7953112c4e67a7/PyInstaller/utils/win32/winmanifest.py) and [option source](https://github.com/pyinstaller/pyinstaller/blob/a564c48bee108fccf39e34711b7953112c4e67a7/PyInstaller/building/makespec.py).

Do not add `--uac-admin`. A `requireAdministrator` manifest invokes UAC before Python code runs, so cancellation cannot fall back to the ordinary MacStyleIME process. Optional elevation requires an `asInvoker` executable that self-relaunches with `runas` only after the setting is enabled.

In a frozen application, `sys.executable` is the bundled executable path, so it is the correct `lpFile` for self-relaunch: [PyInstaller run-time information](https://pyinstaller.org/en/stable/runtime-information.html). For an independent one-file restart, current PyInstaller requires `PYINSTALLER_RESET_ENVIRONMENT=1`; otherwise a new process may attempt to reuse the old extraction environment: [PyInstaller restart guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart). Adaptation for `ShellExecuteExW` (inference): because this API has no environment parameter, set the variable temporarily in the parent environment around the launch and restore it immediately afterward so the child inherits it.

The local build environment currently has PyInstaller `6.20.0`, so the reset requirement applies.

### 5. Reject unsafe or disproportionate alternatives

- **`uiAccess=true`: reject.** UIAccess is designed for accessibility software and is gated by Windows security policy, trusted installation location, and signing expectations. MacStyleIME `v1.3.0` is an unsigned portable executable. See Microsoft's [UIAccess secure-location policy](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/user-account-control-only-elevate-uiaccess-applications-that-are-installed-in-secure-locations).
- **`--uac-admin` / `requireAdministrator`: reject.** It eliminates the requested normal-mode fallback.
- **Highest-privilege scheduled task pointing to the portable EXE: reject.** [ColorControl's elevation service](https://github.com/Maassoft/ColorControl/blob/d0d3bb4682c483b0b2c14a794958abf2d142100e/ColorControl/Services/Common/ElevationService.cs) demonstrates the scheduled-task mechanism. Security inference for this codebase: because MacStyleIME `v1.3.0` is a portable executable that may live in a user-writable directory, a highest-privilege task targeting that path would turn replacement of the EXE into an elevation path. It also adds task creation, repair, and uninstall state that `v1.3.0` does not otherwise own.
- **Windows service or permanent elevated helper: reject for this feature.** [ColorControl's elevated-process manager](https://github.com/Maassoft/ColorControl/blob/d0d3bb4682c483b0b2c14a794958abf2d142100e/Shared/Services/WinElevatedProcessManager.cs) uses a separate elevated process and RPC because that application has privileged operations to isolate. MacStyleIME needs its keyboard hook and IME switching in the elevated interactive process; a service/helper split adds IPC without reducing the required UI-process privilege.
- **Blind `ShellExecuteW` followed by `exit`: reject.** It loses the app on UAC cancellation and provides no child handle for handoff.

## Recommended architecture for v1.3.0

Add one focused Windows-elevation module behind a small interface:

- `is_elevated() -> bool`
- `launch_elevated(args) -> launched | cancelled | failed(error)`
- `wait_for_parent(pid, timeout_ms) -> bool`

Use `ShellExecuteExW`, `SEE_MASK_NOCLOSEPROCESS`, Unicode declarations, `subprocess.list2cmdline`, explicit Win32 error capture, and deterministic handle closing. Keep the executable manifest as `asInvoker`.

Startup ordering should be:

1. parse internal restart/elevation arguments;
2. load the per-user preference;
3. if elevation is requested and the process is not elevated, attempt `runas` before taking the mutex;
4. on cancellation/failure, continue in ordinary mode;
5. on elevated handoff, wait for the exact parent PID;
6. acquire the normal versioned mutex;
7. start the existing hook and tray code unchanged.

When the setting is enabled from a running normal instance, use the parent-PID handoff above. Do not change CapsLock, Shift, IME switching, or hook semantics as part of this feature.

## Minimal verification appropriate to this change

Automated verification should be narrow: compile/import validation for the new module and one build of the packaged executable. The behavior that cannot be established by compilation must be checked manually:

1. enable the option, accept UAC, and confirm the replacement instance is elevated and unique;
2. enable the option, cancel UAC, and confirm the ordinary instance remains alive;
3. switch IME in one normal editor and one elevated editor;
4. relaunch the one-file executable and confirm no stale `_MEI` extraction or duplicate-instance failure;
5. confirm the UAC secure desktop itself is not claimed as supported.

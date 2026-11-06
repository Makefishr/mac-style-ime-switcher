"""Current-user startup registration via HKCU Run or an interactive logon task."""

from __future__ import annotations

import csv
import io
import logging
import os
import subprocess
import sys
import tempfile
import winreg
from pathlib import Path
from xml.etree import ElementTree as ET

from . import config, elevation

log = logging.getLogger(__name__)


def launch_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return sys.executable, []
    return config.source_executable(), [str(Path(__file__).with_name("__main__.py").resolve())]


def run_command() -> str:
    executable, arguments = launch_command()
    return subprocess.list2cmdline([executable, *arguments])


def startup_method(enabled: bool, administrator: bool) -> str:
    return "task" if enabled and administrator else "run" if enabled else "disabled"


def task_xml(username: str, sid: str, executable: str, arguments: list[str]) -> str:
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", namespace)

    def element(parent, name, text=None, **attributes):
        node = ET.SubElement(parent, f"{{{namespace}}}{name}", attributes)
        node.text = text
        return node

    root = ET.Element(f"{{{namespace}}}Task", {"version": "1.3"})
    trigger = element(element(root, "Triggers"), "LogonTrigger")
    element(trigger, "Enabled", "true")
    element(trigger, "UserId", sid)
    principal = element(element(root, "Principals"), "Principal", id="Author")
    element(principal, "UserId", username)
    element(principal, "LogonType", "InteractiveToken")
    element(principal, "RunLevel", "HighestAvailable")
    settings = element(root, "Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("StartWhenAvailable", "true"),
        ("ExecutionTimeLimit", "PT0S"),
    ):
        element(settings, name, value)
    action = element(element(root, "Actions", Context="Author"), "Exec")
    element(action, "Command", executable)
    if arguments:
        element(action, "Arguments", subprocess.list2cmdline(arguments))
    element(action, "WorkingDirectory", str(config.APP_DIR))
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


class WindowsStartup:
    def __init__(self, registry=winreg, runner=None, elevated=None):
        self._registry = registry
        self._runner = runner or subprocess.run
        self._elevated = elevated or elevation.is_elevated
        self._identity = None

    def _run(self, executable: str, *arguments: str):
        path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / executable
        try:
            result = self._runner(
                [str(path), *arguments], capture_output=True, text=True,
                errors="replace", timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.SubprocessError as exc:
            raise OSError(f"{executable}: {exc}") from exc
        if result.returncode:
            raise OSError(f"{executable}: {(result.stderr or result.stdout).strip()}")
        return result.stdout

    def _user(self) -> tuple[str, str]:
        if self._identity is None:
            output = self._run("whoami.exe", "/USER", "/FO", "CSV", "/NH")
            try:
                username, sid = next(csv.reader(io.StringIO(output.strip())))
            except (ValueError, StopIteration, csv.Error) as exc:
                raise OSError("无法读取当前 Windows 用户，请查看日志后重试") from exc
            if not sid.startswith("S-1-"):
                raise OSError("无法确认当前 Windows 用户的 SID")
            self._identity = username, sid
        return self._identity

    def _task_name(self) -> str:
        return f"{config.APP_NAME}-Autostart-{self._user()[1]}"

    def task_exists(self) -> bool:
        # Querying the task list avoids treating access denied or service failure
        # as "not found", whose error text is localized by schtasks.
        expected = "\\" + self._task_name()
        output = self._run("schtasks.exe", "/Query", "/FO", "CSV", "/NH")
        return any(row and row[0].casefold() == expected.casefold()
                   for row in csv.reader(io.StringIO(output)))

    def run_exists(self, *, matching_command: bool = True) -> bool:
        registry = self._registry
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, registry.KEY_READ) as key:
                value, kind = registry.QueryValueEx(key, config.APP_NAME)
                if not matching_command:
                    return True
                executable, arguments = launch_command()
                legacy_command = " ".join(f'"{item}"' for item in [executable, *arguments])
                return kind == registry.REG_SZ and value in (run_command(), legacy_command)
        except FileNotFoundError:
            return False

    def is_enabled(self) -> bool:
        return self.run_exists() or self.task_exists()

    def is_configured(self, enabled: bool, administrator: bool) -> bool:
        """Check the requested registration without changing or recreating it."""
        exists = self.task_exists()
        method = startup_method(enabled, administrator)
        if method != "task":
            return not exists and (
                self.run_exists() if enabled else not self.run_exists(matching_command=False)
            )
        if not exists or self.run_exists(matching_command=False):
            return False
        output = self._run("schtasks.exe", "/Query", "/TN", self._task_name(), "/XML")
        try:
            actual = ET.fromstring(output)
        except ET.ParseError as exc:
            raise OSError("无法读取登录任务的 XML，请查看日志后重试") from exc
        username, sid = self._user()
        executable, arguments = launch_command()
        expected = ET.fromstring(task_xml(username, sid, executable, arguments))
        namespace = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
        principal_user_path = f"{namespace}Principals/{namespace}Principal/{namespace}UserId"
        actual_user = actual.findtext(principal_user_path, "")
        if actual_user.casefold() in (username.casefold(), sid.casefold()):
            # Task Scheduler may normalize the account name to its SID on export.
            expected.find(principal_user_path).text = actual_user

        def matches(wanted, found, allow_extra=False):
            if found is None or wanted.tag != found.tag:
                return False
            if (wanted.text or "").strip() != (found.text or "").strip():
                return False
            if any(found.get(key) != value for key, value in wanted.attrib.items()):
                return False
            if not allow_extra and len(wanted) != len(found):
                return False
            return all(matches(child, found.find(child.tag)) for child in wanted)

        # Scheduler may add default settings and registration metadata. Triggers,
        # principals and actions must still describe exactly the requested task.
        return (actual.tag == expected.tag
                and actual.findtext(namespace + "Settings/" + namespace + "Enabled", "true") != "false"
                and all(matches(child, actual.find(child.tag), child.tag == namespace + "Settings")
                        for child in expected))

    def needs_elevation(self, enabled: bool, administrator: bool) -> bool:
        return not self._elevated() and (
            startup_method(enabled, administrator) == "task" or self.task_exists()
        )

    def _remove_run(self) -> None:
        registry = self._registry
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
                registry.DeleteValue(key, config.APP_NAME)
        except FileNotFoundError:
            log.debug("Run entry %s was already absent", config.APP_NAME)
            return
        log.info("Removed Run entry %s", config.APP_NAME)

    def _write_run(self) -> None:
        registry = self._registry
        command = run_command()
        log.info("Writing Run entry %s with command: %s", config.APP_NAME, command)
        with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, config.REG_RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
            registry.SetValueEx(key, config.APP_NAME, 0, registry.REG_SZ, command)

    def _create_task(self) -> None:
        username, sid = self._user()
        executable, arguments = launch_command()
        name = self._task_name()
        xml = task_xml(username, sid, executable, arguments)
        config.USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        log.info("Creating logon task %s with command: %s", name, run_command())
        # The XML holds no credentials; InteractiveToken reuses the logged-in user.
        with tempfile.TemporaryDirectory(prefix="startup-", dir=config.USER_DATA_DIR) as folder:
            path = Path(folder) / "task.xml"
            path.write_text('<?xml version="1.0" encoding="UTF-16"?>\n' + xml, encoding="utf-16")
            self._run("schtasks.exe", "/Create", "/TN", name, "/XML", str(path), "/F")

    def apply(self, enabled: bool, administrator: bool) -> None:
        if self.is_configured(enabled, administrator):
            log.debug(
                "Startup registration already matches enabled=%s administrator=%s; nothing to change",
                enabled, administrator,
            )
            return
        method = startup_method(enabled, administrator)
        exists = self.task_exists()
        if not self._elevated() and (method == "task" or exists):
            raise PermissionError("需要管理员权限才能更新登录计划任务")
        if method == "task":
            self._create_task()
            self._remove_run()
        else:
            if method == "run":
                self._write_run()
            if exists:
                log.info("Deleting logon task %s", self._task_name())
                self._run("schtasks.exe", "/Delete", "/TN", self._task_name(), "/F")
            if method == "disabled":
                self._remove_run()

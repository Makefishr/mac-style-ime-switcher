"""In-memory Windows adapters; tests never touch the user's system settings."""

from contextlib import contextmanager
import csv
import io
from pathlib import Path
from subprocess import CompletedProcess


class Registry:
    HKEY_CURRENT_USER = 1
    KEY_READ = 2
    KEY_SET_VALUE = 4
    REG_SZ = 1
    REG_BINARY = 3
    REG_DWORD = 4

    def __init__(self, entries=None):
        self.entries = entries or {}
        self.writes = []
        self.before_write = lambda: None

    @contextmanager
    def OpenKey(self, hive, path, reserved, access):
        if path not in self.entries:
            raise FileNotFoundError(path)
        yield path

    @contextmanager
    def CreateKeyEx(self, hive, path, reserved, access):
        self.entries.setdefault(path, {})
        yield path

    def QueryValueEx(self, key, name):
        try:
            return self.entries[key][name]
        except KeyError:
            raise FileNotFoundError(name) from None

    def SetValueEx(self, key, name, reserved, kind, value):
        self.before_write()
        self.writes.append((key, name, value, kind))
        self.entries[key][name] = (value, kind)

    def DeleteValue(self, key, name):
        self.before_write()
        try:
            del self.entries[key][name]
        except KeyError:
            raise FileNotFoundError(name) from None


class Scheduler:
    username = r"EXAMPLE\用户 & User"
    sid = "S-1-5-21-123-456-789-1001"

    def __init__(self):
        self.tasks = set()
        self.calls = []
        self.xml = None
        self.fail = None

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        output = io.StringIO()
        if Path(command[0]).name == "whoami.exe":
            csv.writer(output).writerow([self.username, self.sid])
        elif "/Query" in command:
            if "/XML" in command:
                return CompletedProcess(command, 0, self.xml or "<Task/>", "")
            for name in self.tasks:
                csv.writer(output).writerow(["\\" + name, "N/A", "Ready"])
        elif "/Create" in command:
            if self.fail == "create":
                return CompletedProcess(command, 1, "", "access denied")
            self.xml = Path(command[command.index("/XML") + 1]).read_text(encoding="utf-16")
            self.tasks.add(command[command.index("/TN") + 1])
        elif "/Delete" in command:
            if self.fail == "delete":
                return CompletedProcess(command, 1, "", "access denied")
            self.tasks.remove(command[command.index("/TN") + 1])
        return CompletedProcess(command, 0, output.getvalue(), "")

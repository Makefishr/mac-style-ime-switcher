"""Release-script contract tests run through a clean command environment."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from ime_switcher import config


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class TestBuildContract(unittest.TestCase):
    def test_dry_run_rejects_unsafe_build_path_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            script = sandbox / "build_ime.bat"
            shutil.copy2(REPOSITORY_ROOT / "build_ime.bat", script)
            (sandbox / "requirements-build.lock").write_text(
                "# dry-run fixture\n",
                encoding="ascii",
            )
            base_environment = os.environ.copy()
            base_environment.update({
                "MACSTYLEIME_BUILD_DRY_RUN": "1",
                "PATH": str(Path(os.environ["SystemRoot"]) / "System32"),
            })
            cases = {
                "relative Python": {"MACSTYLEIME_PYTHON": "python.exe"},
                "missing Python": {
                    "MACSTYLEIME_PYTHON": str(sandbox / "missing-python.exe"),
                },
                "relative build root": {"MACSTYLEIME_BUILD_ROOT": "candidate"},
                "quoted dist path": {
                    "MACSTYLEIME_DISTPATH": f'{sandbox / "dist"}\"suffix',
                },
                "newline build root": {
                    "MACSTYLEIME_BUILD_ROOT": f'{sandbox / "candidate"}\nsecond-line',
                },
            }

            for label, overrides in cases.items():
                with self.subTest(label=label):
                    environment = base_environment.copy()
                    environment.update(overrides)
                    completed = subprocess.run(
                        ["cmd.exe", "/d", "/c", str(script)],
                        cwd=sandbox,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    )

                    output = (
                        (completed.stdout or "") + (completed.stderr or "")
                    )
                    self.assertNotEqual(completed.returncode, 0, msg=output)
                    self.assertIn(
                        "Build path and Python overrides must be safe absolute values.",
                        output,
                    )

    def test_dry_run_rejects_ambient_path_pollution(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            pollution_dir = sandbox / "outside-project-dlls"
            pollution_dir.mkdir()
            ambient_log = sandbox / "ambient-launcher.log"
            (pollution_dir / "py.cmd").write_text(
                "@echo off\r\n"
                ">>\"%BUILD_STUB_LOG%\" echo AMBIENT_PY PATH=%PATH%\r\n"
                "exit /b 0\r\n",
                encoding="ascii",
            )

            script = sandbox / "build_ime.bat"
            shutil.copy2(REPOSITORY_ROOT / "build_ime.bat", script)
            (sandbox / "requirements-build.lock").write_text(
                "# dry-run fixture\n",
                encoding="ascii",
            )
            candidate = sandbox / "candidate"
            system_root = Path(os.environ["SystemRoot"])
            clean_path = ";".join((
                str(candidate / "venv" / "Scripts"),
                str(system_root / "System32"),
                str(system_root),
            ))
            environment = os.environ.copy()
            environment.update({
                "BUILD_STUB_LOG": str(ambient_log),
                "CONDA_EXE": str(pollution_dir / "conda.exe"),
                "CONDA_PREFIX": str(pollution_dir),
                "MACSTYLEIME_BUILD_DRY_RUN": "1",
                "MACSTYLEIME_BUILD_ROOT": str(candidate),
                "MACSTYLEIME_PYTHON": sys.executable,
                "PATH": f"{pollution_dir};{system_root / 'System32'}",
                "PYTHONHOME": str(pollution_dir),
                "PYTHONPATH": str(pollution_dir),
                "VIRTUAL_ENV": str(pollution_dir),
            })

            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", str(script)],
                cwd=sandbox,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            output = (completed.stdout or "") + (completed.stderr or "")
            ambient_calls = (
                ambient_log.read_text(encoding="ascii")
                if ambient_log.exists()
                else ""
            )
            self.assertEqual(completed.returncode, 0, msg=output)
            self.assertEqual(ambient_calls, "", msg=ambient_calls)
            self.assertIn(f'[DRY RUN] Base Python: "{sys.executable}"', output)
            self.assertIn(f'[DRY RUN] Build PATH: "{clean_path}"', output)
            self.assertNotIn(str(pollution_dir), output)

    def test_dry_run_honors_isolated_build_root(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            bin_dir = sandbox / "bin"
            bin_dir.mkdir()
            log_file = sandbox / "commands.log"
            (bin_dir / "py.cmd").write_text(
                "@echo off\r\n"
                ">>\"%BUILD_STUB_LOG%\" echo py %*\r\n"
                "exit /b 0\r\n",
                encoding="ascii",
            )
            script = sandbox / "build_ime.bat"
            shutil.copy2(REPOSITORY_ROOT / "build_ime.bat", script)
            (sandbox / "requirements-build.lock").write_text(
                "# dry-run fixture\n",
                encoding="ascii",
            )
            candidate = sandbox / "candidate"
            environment = os.environ.copy()
            environment.update({
                "BUILD_STUB_LOG": str(log_file),
                "MACSTYLEIME_BUILD_DRY_RUN": "1",
                "MACSTYLEIME_BUILD_ROOT": str(candidate),
                "MACSTYLEIME_PYTHON": sys.executable,
                "PATH": f"{bin_dir};{Path(os.environ['SystemRoot']) / 'System32'}",
                "PYTHONNOUSERSITE": "1",
            })

            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", str(script)],
                cwd=sandbox,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            output = (completed.stdout or "") + (completed.stderr or "")
            self.assertEqual(completed.returncode, 0, msg=output)
            self.assertIn(
                str(candidate / "venv" / "Scripts" / "python.exe"),
                output,
            )
            self.assertIn(f'--distpath "{candidate / "dist"}"', output)
            self.assertIn(f'--workpath "{candidate / "work"}"', output)
            self.assertIn(f'--specpath "{candidate / "spec"}"', output)
            self.assertNotIn(str(sandbox / ".venv-build"), output)

    def test_dry_run_does_not_use_ambient_build_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            bin_dir = sandbox / "bin"
            bin_dir.mkdir()
            log_file = sandbox / "ambient-commands.log"

            for command in ("pip", "pyinstaller", "py"):
                (bin_dir / f"{command}.cmd").write_text(
                    "@echo off\r\n"
                    f">>\"%BUILD_STUB_LOG%\" echo {command} %*\r\n"
                    "exit /b 0\r\n",
                    encoding="ascii",
                )

            script = sandbox / "build_ime.bat"
            shutil.copy2(REPOSITORY_ROOT / "build_ime.bat", script)
            (sandbox / "requirements-build.lock").write_text(
                "# dry-run fixture\n", encoding="ascii",
            )

            environment = os.environ.copy()
            environment.update({
                "BUILD_STUB_LOG": str(log_file),
                "MACSTYLEIME_BUILD_DRY_RUN": "1",
                "PATH": f"{bin_dir};{Path(os.environ['SystemRoot']) / 'System32'}",
                "PYTHONNOUSERSITE": "1",
            })
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", str(script)],
                cwd=sandbox,
                env=environment,
                input="\r\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            output = (completed.stdout or "") + (completed.stderr or "")
            self.assertEqual(
                completed.returncode,
                0,
                msg=output,
            )
            observed_commands = (
                log_file.read_text(encoding="utf-8")
                if log_file.exists()
                else ""
            )
            self.assertNotIn("pip ", observed_commands.lower())
            self.assertNotIn("pyinstaller ", observed_commands.lower())
            self.assertEqual(observed_commands, "")
            self.assertIn(
                "--require-hashes",
                output,
                msg=f"returncode={completed.returncode} log={observed_commands!r}",
            )
            self.assertIn("-m PyInstaller", output)
            self.assertIn(".venv-build", output)
            self.assertIn(" -3.12", output)
            self.assertIn(
                f"Mac-style IME Switcher v{config.VERSION} - secure build",
                output,
            )

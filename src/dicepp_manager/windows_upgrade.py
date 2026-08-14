"""Simple Windows Velopack hand-off with an explicit manual recovery path."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable

from dicepp_data import InstanceLayout
from dicepp_meta import get_version
from packaging.version import Version

from ._file_utils import _atomic_copy, _atomic_json
from ._path_security import (
    UnsafePathError,
    assert_contained_no_reparse,
    assert_directory_no_reparse,
    is_reparse_point,
)
from .velopack_bundle import (
    VELOPACK_BUNDLE_NAME,
    ValidatedVelopackBundle,
    VelopackBundleError,
    validate_velopack_bundle,
)

_TRANSACTION_RE = re.compile(r"[0-9a-f]{32}")
_RECOVERY_FORMAT = 1
_RECOVERY_SCRIPT_NAME = "DicePP-Recover.cmd"
_RECOVERY_REQUEST_NAME = "manual-restore.requested"
_RECOVERY_DESCRIPTION_NAME = "recover.json"
_RECOVERY_CURRENT_NAME = "current"
_FAILED_CURRENT_NAME = "failed-current"


class SimpleWindowsVelopackUpgradeAdapter:
    """Back up ``current/`` and let Velopack perform a normal update.

    There is deliberately no background watchdog and no automatic program
    rollback.  A failed target is restored only when the user runs the stable
    root recovery script prepared by the source Manager.
    """

    platform = "windows"
    protocol = "windows-simple-v1"
    supported = True

    def __init__(
        self,
        *,
        layout: InstanceLayout,
        install_command: list[str],
        version_loader: Callable[[], str] = get_version,
        launcher_path: Path | None = None,
    ) -> None:
        if not install_command:
            raise ValueError("A Velopack apply command is required")
        if not any("{wait_pid}" in value for value in install_command):
            raise ValueError("Velopack apply command must wait for {wait_pid}")
        self.layout = layout
        self.install_command = list(install_command)
        self.version_loader = version_loader
        self.launcher_path = launcher_path or layout.root / "DicePP.exe"

    async def preflight(self, package: Any) -> dict[str, Any]:
        self._validate_target_package(package)
        source_version = self._current_version()
        if Version(source_version) == Version(package.version):
            raise self._compatibility_error(
                "Windows automatic update target is already installed"
            )
        await asyncio.to_thread(self._validate_stable_layout)
        return {
            "status": "ok",
            "source_version": source_version,
            "recovery": "manual",
        }

    async def capture_current(self, package: Any) -> dict[str, Any]:
        self._validate_target_package(package)
        return {"source_version": self._current_version()}

    async def stage(self, package: Any, transaction_id: str) -> dict[str, Any]:
        self._validate_target_package(package)
        if not _TRANSACTION_RE.fullmatch(transaction_id):
            raise self._compatibility_error("Upgrade transaction identity is invalid")
        return await asyncio.to_thread(
            self._stage_program_backup,
            transaction_id,
        )

    async def prepare_recovery(
        self,
        staged: dict[str, Any],
        *,
        transaction_id: str,
        source_version: str,
        target_version: str,
        pre_upgrade_filename: str,
        original_running: list[str],
    ) -> dict[str, Any]:
        """Persist the immutable source-owned recovery snapshot and script."""

        recovery_dir = self._trusted_recovery_dir(staged, transaction_id)
        payload = {
            "format_version": _RECOVERY_FORMAT,
            "transaction_id": transaction_id,
            "source_version": source_version,
            "target_version": target_version,
            "pre_upgrade_filename": pre_upgrade_filename,
            "original_running": list(original_running),
        }
        await asyncio.to_thread(
            self._write_recovery_material,
            recovery_dir,
            payload,
        )
        return {
            **staged,
            "recover_json": str(recovery_dir / _RECOVERY_DESCRIPTION_NAME),
            "manual_restore_request": str(
                recovery_dir / _RECOVERY_REQUEST_NAME
            ),
            "root_recovery_script": str(
                self.layout.root / _RECOVERY_SCRIPT_NAME
            ),
        }

    async def switch(
        self,
        package: Any,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del current
        recovery_dir = self._trusted_recovery_dir(staged, transaction_id)
        required = (
            recovery_dir / _RECOVERY_CURRENT_NAME,
            recovery_dir / _RECOVERY_DESCRIPTION_NAME,
            recovery_dir / _RECOVERY_SCRIPT_NAME,
            self.layout.root / _RECOVERY_SCRIPT_NAME,
        )
        if any(not path.is_file() for path in required[1:]) or not required[0].is_dir():
            raise self._compatibility_error(
                "Windows recovery material is incomplete before Velopack apply"
            )
        command = [
            value.replace("{package}", str(package.path))
            .replace("{package_dir}", str(package.path.parent))
            .replace("{wait_pid}", str(os.getpid()))
            for value in self.install_command
        ]
        log_path = recovery_dir / "velopack-output.log"
        updater_pid = await asyncio.to_thread(
            self._start_velopack,
            command,
            log_path,
        )
        return {
            "handoff_required": True,
            "updater_pid": updater_pid,
            "wait_pid": os.getpid(),
            "recovery_dir": str(recovery_dir),
            "recover_json": str(recovery_dir / _RECOVERY_DESCRIPTION_NAME),
        }

    async def commit(
        self,
        package: Any,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del current
        maintenance_error = await asyncio.to_thread(
            self._maintain_packages_dir, package
        )
        cleanup_error = await asyncio.to_thread(
            self._cleanup_recovery,
            staged,
            transaction_id,
        )
        result: dict[str, Any] = {
            "status": "committed",
            "recovery_material_removed": cleanup_error is None,
        }
        warnings = [
            message
            for message in (maintenance_error, cleanup_error)
            if message is not None
        ]
        if warnings:
            result["warnings"] = warnings
        return result

    async def rollback(
        self,
        package: Any,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del package, current
        cleanup_error = await asyncio.to_thread(
            self._cleanup_recovery,
            staged,
            transaction_id,
        )
        if cleanup_error is not None:
            raise self._upgrade_error(cleanup_error)
        return {
            "status": "aborted_before_apply",
            "program_restored": False,
        }

    async def cleanup(self, staged: dict[str, Any]) -> None:
        if not staged:
            return
        transaction_id = str(staged.get("transaction_id") or "")
        error = await asyncio.to_thread(
            self._cleanup_recovery,
            staged,
            transaction_id,
        )
        if error is not None:
            raise self._upgrade_error(error)

    async def cleanup_transaction(self, transaction_id: str) -> None:
        """Remove staging left before ``stage()`` could return durable metadata.

        The coordinator already journals the transaction identity before staging.
        That identity is therefore the only safe recovery key when the process dies
        during the backup copy or immediately after its atomic rename.
        """

        error = await asyncio.to_thread(
            self._cleanup_incomplete_transaction,
            transaction_id,
        )
        if error is not None:
            raise self._upgrade_error(error)

    def load_manual_restore_request(
        self,
        detail: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the source snapshot only after the user-created flag exists."""

        transaction_id = str(detail.get("transaction_id") or "")
        staged = detail.get("platform_staged")
        if not isinstance(staged, dict) or not _TRANSACTION_RE.fullmatch(transaction_id):
            return None
        try:
            recovery_dir = self._trusted_recovery_dir(staged, transaction_id)
        except Exception:
            return None
        marker = recovery_dir / _RECOVERY_REQUEST_NAME
        description = recovery_dir / _RECOVERY_DESCRIPTION_NAME
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size != 0:
            return None
        try:
            from ._file_utils import _read_json_object

            payload = _read_json_object(description)
        except (OSError, ValueError):
            return None
        expected = {
            "format_version": _RECOVERY_FORMAT,
            "transaction_id": transaction_id,
            "source_version": staged.get("source_version"),
            "target_version": detail.get("target_version"),
            "pre_upgrade_filename": detail.get("pre_upgrade_filename"),
            "original_running": detail.get("original_running"),
        }
        if payload != expected:
            return None
        return payload

    async def finish_manual_restore(
        self,
        staged: dict[str, Any],
        transaction_id: str,
    ) -> str | None:
        return await asyncio.to_thread(
            self._cleanup_recovery,
            staged,
            transaction_id,
        )

    def _stage_program_backup(self, transaction_id: str) -> dict[str, Any]:
        self._validate_stable_layout()
        recovery_root = self.layout.manager_recovery_dir
        recovery_root.mkdir(parents=True, exist_ok=True)
        assert_contained_no_reparse(
            recovery_root,
            root=self.layout.root,
            allow_missing=False,
        )
        assert_directory_no_reparse(recovery_root)
        existing = list(recovery_root.iterdir())
        root_script = self.layout.root / _RECOVERY_SCRIPT_NAME
        if existing or os.path.lexists(root_script):
            raise self._compatibility_error(
                "Existing Windows recovery material must be resolved before upgrading"
            )
        transaction_dir = recovery_root / transaction_id
        temporary = recovery_root / f".{transaction_id}.tmp"
        current = self.layout.root / _RECOVERY_CURRENT_NAME
        try:
            temporary.mkdir(parents=False, exist_ok=False)
            self._validate_regular_tree(current)
            shutil.copytree(
                current,
                temporary / _RECOVERY_CURRENT_NAME,
                copy_function=shutil.copy2,
            )
            self._validate_regular_tree(temporary / _RECOVERY_CURRENT_NAME)
            os.replace(temporary, transaction_dir)
        except Exception as exc:
            if temporary.exists() and not temporary.is_symlink():
                shutil.rmtree(temporary, ignore_errors=True)
            if transaction_dir.exists() and not transaction_dir.is_symlink():
                shutil.rmtree(transaction_dir, ignore_errors=True)
            if isinstance(exc, (UnsafePathError, OSError)):
                raise self._compatibility_error(
                    f"Could not back up the Windows current directory: {exc}"
                ) from exc
            raise
        return {
            "transaction_id": transaction_id,
            "source_version": self._current_version(),
            "recovery_dir": str(transaction_dir),
            "current_backup": str(transaction_dir / _RECOVERY_CURRENT_NAME),
        }

    def _write_recovery_material(
        self,
        recovery_dir: Path,
        payload: dict[str, Any],
    ) -> None:
        description = recovery_dir / _RECOVERY_DESCRIPTION_NAME
        transaction_script = recovery_dir / _RECOVERY_SCRIPT_NAME
        root_script = self.layout.root / _RECOVERY_SCRIPT_NAME
        if any(os.path.lexists(path) for path in (description, transaction_script, root_script)):
            raise self._compatibility_error(
                "Windows recovery description or entry already exists"
            )
        script = self._recovery_script(payload["transaction_id"])
        try:
            _atomic_json(description, payload)
            description.chmod(stat.S_IREAD)
            transaction_script.write_text(script, encoding="utf-8", newline="\r\n")
            _atomic_copy(transaction_script, root_script)
        except Exception as exc:
            root_script.unlink(missing_ok=True)
            transaction_script.unlink(missing_ok=True)
            try:
                description.chmod(stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
            description.unlink(missing_ok=True)
            raise self._compatibility_error(
                f"Could not prepare the Windows recovery entry: {exc}"
            ) from exc

    def _recovery_script(self, transaction_id: str) -> str:
        return f"""@echo off
setlocal
set "DICEPP_ROOT=%~dp0"
set "RECOVERY=%~dp0manager\\recovery\\{transaction_id}"
set "BACKUP=%RECOVERY%\\current"
set "FAILED=%RECOVERY%\\failed-current"

if not exist "%DICEPP_ROOT%DicePP.exe" goto missing_launcher
if not exist "%BACKUP%\\" goto missing_backup
if exist "%FAILED%\\" goto failed_exists
if exist "%RECOVERY%\\manual-restore.requested" goto marker_exists
if not exist "%DICEPP_ROOT%current\\" goto install_backup

set /A MOVE_ATTEMPTS=0
:move_current
move "%DICEPP_ROOT%current" "%FAILED%" >NUL 2>&1
if not errorlevel 1 goto install_backup
set /A MOVE_ATTEMPTS+=1
if %MOVE_ATTEMPTS% GEQ 15 goto current_in_use
"%SystemRoot%\\System32\\ping.exe" -n 2 127.0.0.1 >NUL
goto move_current

:install_backup
move "%BACKUP%" "%DICEPP_ROOT%current" >NUL
if errorlevel 1 goto install_failed
type NUL > "%RECOVERY%\\manual-restore.requested"
if errorlevel 1 goto marker_failed
start "" /B "%DICEPP_ROOT%DicePP.exe" --background
if errorlevel 1 goto launcher_failed
exit /b 0

:install_failed
if exist "%FAILED%\\" move "%FAILED%" "%DICEPP_ROOT%current" >NUL
echo Could not restore the previous DicePP program. Recovery files were kept.
pause
exit /b 1

:current_in_use
echo DicePP is still using the current directory. Close DicePP and try again.
pause
exit /b 1

:missing_backup
echo The DicePP recovery backup is missing. Nothing was changed.
pause
exit /b 1

:failed_exists
echo A previous recovery attempt already isolated a current directory.
echo Recovery files were kept; resolve them manually before retrying.
pause
exit /b 1

:missing_launcher
echo The stable DicePP launcher is missing. Nothing was changed.
pause
exit /b 1

:marker_failed
move "%DICEPP_ROOT%current" "%BACKUP%" >NUL
if errorlevel 1 goto marker_failed_manual
if not exist "%FAILED%\\" goto marker_reverted
move "%FAILED%" "%DICEPP_ROOT%current" >NUL
if errorlevel 1 goto marker_failed_manual
:marker_reverted
echo The recovery marker could not be written. The directory swap was reversed.
echo Recovery files were kept and DicePP was not started.
pause
exit /b 1

:marker_failed_manual
echo The recovery marker and automatic directory reversal both failed.
echo Recovery files were kept; resolve the current and failed-current folders manually.
pause
exit /b 1

:launcher_failed
echo The previous program was restored, but DicePP could not be started.
echo Recovery files were kept; start DicePP manually after resolving the error.
pause
exit /b 1

:marker_exists
echo A manual recovery marker already exists. Nothing was changed.
echo Resolve the existing recovery attempt before retrying.
pause
exit /b 1
"""

    def _cleanup_recovery(
        self,
        staged: dict[str, Any],
        transaction_id: str,
    ) -> str | None:
        try:
            if not _TRANSACTION_RE.fullmatch(transaction_id):
                raise self._compatibility_error(
                    "Upgrade transaction identity is invalid"
                )
            expected = self.layout.manager_recovery_dir / transaction_id
            root_script = self.layout.root / _RECOVERY_SCRIPT_NAME
            if not os.path.lexists(expected):
                if not os.path.lexists(root_script):
                    return None
                raise self._compatibility_error(
                    "Root recovery script remains without its transaction directory"
                )
            recovery_dir = self._trusted_recovery_dir(staged, transaction_id)
            transaction_script = recovery_dir / _RECOVERY_SCRIPT_NAME
            if os.path.lexists(root_script):
                if (
                    root_script.is_symlink()
                    or is_reparse_point(root_script)
                    or not root_script.is_file()
                    or transaction_script.is_symlink()
                    or is_reparse_point(transaction_script)
                    or not transaction_script.is_file()
                ):
                    raise UnsafePathError(
                        "Root recovery entry cannot be safely matched to its transaction"
                    )
                if root_script.read_bytes() != transaction_script.read_bytes():
                    raise self._compatibility_error(
                        "Root recovery script differs from its transaction copy"
                    )
                root_script.unlink()
            description = recovery_dir / _RECOVERY_DESCRIPTION_NAME
            if description.exists():
                description.chmod(stat.S_IWRITE | stat.S_IREAD)
            shutil.rmtree(recovery_dir)
            return None
        except Exception as exc:
            return (
                "Windows recovery material cleanup failed: "
                f"{str(exc) or type(exc).__name__}"
            )

    def _cleanup_incomplete_transaction(self, transaction_id: str) -> str | None:
        try:
            if not _TRANSACTION_RE.fullmatch(transaction_id):
                raise self._compatibility_error(
                    "Upgrade transaction identity is invalid"
                )
            recovery_root = self.layout.manager_recovery_dir
            if not recovery_root.exists():
                return None
            assert_contained_no_reparse(
                recovery_root,
                root=self.layout.root,
                allow_missing=False,
            )
            assert_directory_no_reparse(recovery_root)
            for candidate in (
                recovery_root / f".{transaction_id}.tmp",
                recovery_root / transaction_id,
            ):
                if not os.path.lexists(candidate):
                    continue
                if candidate.is_symlink() or is_reparse_point(candidate):
                    raise UnsafePathError(
                        f"Incomplete Windows recovery path is unsafe: {candidate}"
                    )
                assert_contained_no_reparse(
                    candidate,
                    root=recovery_root,
                    allow_missing=False,
                )
                assert_directory_no_reparse(candidate)
                self._validate_regular_tree(candidate)
                shutil.rmtree(candidate)
            return None
        except Exception as exc:
            return (
                "Incomplete Windows recovery staging cleanup failed: "
                f"{str(exc) or type(exc).__name__}"
            )

    def _trusted_recovery_dir(
        self,
        staged: dict[str, Any],
        transaction_id: str,
    ) -> Path:
        if not _TRANSACTION_RE.fullmatch(transaction_id):
            raise self._compatibility_error("Upgrade transaction identity is invalid")
        expected = self.layout.manager_recovery_dir / transaction_id
        supplied = Path(str(staged.get("recovery_dir") or ""))
        if supplied != expected or supplied.is_symlink():
            raise self._compatibility_error("Windows recovery path is invalid")
        assert_contained_no_reparse(
            supplied,
            root=self.layout.root,
            allow_missing=False,
        )
        assert_directory_no_reparse(supplied)
        return supplied

    def _validate_stable_layout(self) -> None:
        update_exe = Path(self.install_command[0])
        current = self.layout.root / _RECOVERY_CURRENT_NAME
        launcher = self.launcher_path
        if (
            not update_exe.is_absolute()
            or update_exe.is_symlink()
            or not update_exe.is_file()
            or current.is_symlink()
            or not current.is_dir()
            or launcher.is_symlink()
            or not launcher.is_file()
        ):
            raise self._compatibility_error(
                "Velopack stable root is incomplete (Update.exe/current/root launcher)"
            )
        assert_contained_no_reparse(current, root=self.layout.root, allow_missing=False)
        assert_contained_no_reparse(launcher, root=self.layout.root, allow_missing=False)

    def _validate_regular_tree(self, root: Path) -> None:
        assert_directory_no_reparse(root)
        for directory, names, files in os.walk(root, followlinks=False):
            base = Path(directory)
            if base != root and (base.is_symlink() or is_reparse_point(base)):
                raise UnsafePathError(f"Program backup contains a reparse point: {base}")
            for name in [*names, *files]:
                path = base / name
                if path.is_symlink() or is_reparse_point(path):
                    raise UnsafePathError(
                        f"Program backup contains a link or reparse point: {path}"
                    )
                metadata = path.stat()
                if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                    raise UnsafePathError(
                        f"Program backup contains a special entry: {path}"
                    )

    def _validate_target_package(self, package: Any) -> ValidatedVelopackBundle:
        from .upgrade import UpgradeCompatibilityError

        if (
            package.platform != "windows"
            or package.arch != "amd64"
            or package.artifact.get("platform") != "windows"
            or package.artifact.get("arch") != "amd64"
            or package.artifact.get("purpose") != "velopack-bundle"
            or package.artifact.get("filename") != VELOPACK_BUNDLE_NAME
            or package.bundle_path is None
            or package.bundle_manifest is None
        ):
            raise UpgradeCompatibilityError(
                "Windows automatic updates require the verified Velopack bundle"
            )
        try:
            packages_root = assert_contained_no_reparse(
                self.layout.manager_packages_dir,
                root=self.layout.root,
                allow_missing=False,
            )
            assert_directory_no_reparse(packages_root)
            assert_contained_no_reparse(
                package.bundle_path,
                root=packages_root,
                allow_missing=False,
            )
            assert_contained_no_reparse(
                package.path,
                root=packages_root,
                allow_missing=False,
            )
            validated = validate_velopack_bundle(
                package.bundle_path,
                expected_dicepp_version=package.version,
                expected_channel=package.release.get("channel"),
                expected_size=package.artifact.get("size"),
                expected_sha256=package.artifact.get("sha256"),
            )
        except (OSError, UnsafePathError, VelopackBundleError) as exc:
            raise UpgradeCompatibilityError(
                f"Windows Velopack bundle is invalid: {exc}"
            ) from exc
        if (
            validated.manifest != package.bundle_manifest
            or package.path.is_symlink()
            or not package.path.is_file()
            or package.path.stat().st_size != validated.nupkg_size
            or self._sha256_file(package.path) != validated.nupkg_sha256
        ):
            raise UpgradeCompatibilityError(
                "Verified Velopack payload no longer matches its bundle"
            )
        return validated

    def _maintain_packages_dir(self, package: Any) -> str | None:
        try:
            validated = self._validate_target_package(package)
            expected = package.artifact.get("sha256")
            if not isinstance(expected, str) or not expected:
                return "Committed bundle digest is unavailable"
            packages_dir = self.layout.root / "packages"
            packages_dir.mkdir(parents=True, exist_ok=True)
            assert_contained_no_reparse(
                packages_dir,
                root=self.layout.root,
                allow_missing=False,
            )
            assert package.bundle_path is not None
            target = packages_dir / VELOPACK_BUNDLE_NAME
            if (
                target.is_symlink()
                or not target.is_file()
                or self._sha256_file(target) != expected
            ):
                _atomic_copy(package.bundle_path, target)
            _atomic_json(
                packages_dir / "velopack.win-x64.verified.json",
                {
                    "format_version": 1,
                    "dicepp_version": package.version,
                    "channel": validated.manifest["channel"],
                    "platform": "windows",
                    "arch": "amd64",
                    "filename": VELOPACK_BUNDLE_NAME,
                    "size": package.artifact["size"],
                    "sha256": expected,
                },
            )
            return None
        except Exception as exc:
            return f"Windows package cache refresh failed: {str(exc) or type(exc).__name__}"

    def _start_velopack(self, command: list[str], log_path: Path) -> int:
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        with log_path.open("ab", buffering=0) as output:
            environment = os.environ.copy()
            # Update.exe outlives this frozen process and launches the next
            # DicePP.exe instance (including Velopack lifecycle hooks).  Since
            # PyInstaller 6.9, a same-executable child otherwise reuses its
            # parent's bootloader state and can hang after the source exits.
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            process = subprocess.Popen(
                command,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=creationflags,
            )
        if process.pid <= 0:
            raise self._upgrade_error("Velopack apply process did not start")
        return process.pid

    def _current_version(self) -> str:
        version = self.version_loader()
        if not isinstance(version, str) or version == "unknown":
            raise self._compatibility_error(
                "Current Windows program version is unavailable"
            )
        return version

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _compatibility_error(message: str) -> Exception:
        from .upgrade import UpgradeCompatibilityError

        return UpgradeCompatibilityError(message)

    @staticmethod
    def _upgrade_error(message: str) -> Exception:
        from .upgrade import UpgradeError

        return UpgradeError(message)


__all__ = ["SimpleWindowsVelopackUpgradeAdapter"]

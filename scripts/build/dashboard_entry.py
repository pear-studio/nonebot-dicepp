"""PyInstaller bootstrap for the Windows DicePP single-entry executable."""

import os
import stat
import sys
import tempfile
import traceback


def _configure_launcher_environment(
    app_dir: str,
) -> dict[str, str]:
    program_dir = os.path.abspath(app_dir)
    install_root = (
        os.path.dirname(program_dir)
        if os.path.basename(program_dir).lower() == "current"
        else program_dir
    )
    _sync_version_owned_config(program_dir, install_root)
    defaults = {
        "DICEPP_APP_DIR": program_dir,
        "DICEPP_PROJECT_ROOT": install_root,
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": "4090",
        "DICEPP_MANAGER_HOST": "127.0.0.1",
        "DICEPP_MANAGER_PORT": "4091",
        "DICEPP_MANAGER_URL": "http://127.0.0.1:4091",
        "DICEPP_MANAGER_TOKEN_FILE": os.path.join(install_root, "manager", "state", "api-token"),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    return {key: os.environ[key] for key in defaults}


def _sync_version_owned_config(program_dir: str, install_root: str) -> None:
    if os.path.normcase(program_dir) == os.path.normcase(install_root):
        return
    for relative in (os.path.join("config", "bots", "_template.json"),):
        source = os.path.join(program_dir, relative)
        if not os.path.isfile(source) or os.path.islink(source):
            continue
        destination = os.path.join(install_root, relative)
        parent = _ensure_safe_seed_parent(
            install_root,
            os.path.dirname(relative),
        )
        if _existing_safe_config(destination):
            continue
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{os.path.basename(destination)}.",
            suffix=".tmp",
            dir=parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                with open(source, "rb") as input_file:
                    output.write(input_file.read())
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                _existing_safe_config(destination)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        else:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _existing_safe_config(path: str) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        os.path.islink(path)
        or (reparse and attributes & reparse)
        or not stat.S_ISREG(info.st_mode)
    ):
        raise RuntimeError(
            f"Refusing unsafe instance config destination: {path}"
        )
    return True


def _ensure_safe_seed_parent(
    install_root: str,
    relative_parent: str,
) -> str:
    root_info = _validate_seed_directory(install_root, root=install_root)
    root_identity = (root_info.st_dev, root_info.st_ino)
    current = install_root
    ancestors = [install_root]
    components = relative_parent.replace("\\", "/").split("/")
    for component in components:
        if not component:
            continue
        _validate_seed_directory(
            install_root,
            root=install_root,
            identity=root_identity,
        )
        current = os.path.join(current, component)
        try:
            os.mkdir(current)
        except FileExistsError:
            pass
        _validate_seed_directory(current, root=install_root)
        ancestors.append(current)
    for ancestor in ancestors:
        _validate_seed_directory(
            ancestor,
            root=install_root,
            identity=root_identity if ancestor == install_root else None,
        )
    return current


def _validate_seed_directory(
    path: str,
    *,
    root: str,
    identity: tuple[int, int] | None = None,
):
    info = os.lstat(path)
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        os.path.islink(path)
        or (reparse and attributes & reparse)
        or not stat.S_ISDIR(info.st_mode)
        or (
            identity is not None
            and (info.st_dev, info.st_ino) != identity
        )
    ):
        raise RuntimeError(
            f"Refusing unsafe instance config directory: {path}"
        )
    root_resolved = os.path.normcase(os.path.realpath(root))
    resolved = os.path.normcase(os.path.realpath(path))
    try:
        inside = os.path.commonpath([resolved, root_resolved]) == root_resolved
    except ValueError:
        inside = False
    if not inside:
        raise RuntimeError(
            f"Instance config directory escapes stable root: {path}"
        )
    return info


def _is_background_launch(argv: list[str] | None = None) -> bool:
    arguments = argv if argv is not None else sys.argv[1:]
    return any(argument in {"--background", "--manager-tray"} for argument in arguments)


def _is_velopack_hook(argv: list[str] | None = None) -> bool:
    """Detect Velopack installer lifecycle hooks (``--veloapp-*``).

    The installer invokes the main executable with these arguments during
    install/update/uninstall and expects a fast exit.  Every hook is a no-op
    for DicePP (shortcuts and registry entries belong to the installer), so
    the process must return immediately instead of starting the resident
    launcher — otherwise the installer times out and force-kills the exe.
    """
    arguments = argv if argv is not None else sys.argv[1:]
    hooks = {
        "--veloapp-install",
        "--veloapp-updated",
        "--veloapp-obsolete",
        "--veloapp-uninstall",
    }
    return any(argument in hooks for argument in arguments)


def _ensure_windowed_standard_streams() -> None:
    """Give libraries a harmless sink when a GUI process has no console."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(
                sys,
                name,
                open(os.devnull, "w", encoding="utf-8", buffering=1),
            )


def _append_bootstrap_failure(app_dir: str, exc: BaseException) -> None:
    """Best-effort logging before the regular launcher logger exists."""
    try:
        program_dir = os.path.abspath(app_dir)
        install_root = (
            os.path.dirname(program_dir)
            if os.path.basename(program_dir).lower() == "current"
            else program_dir
        )
        log_dir = os.path.join(install_root, "data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "dicepp-runtime.log")
        with open(log_path, "a", encoding="utf-8") as output:
            output.write(
                "launcher | bootstrap fatal error: "
                f"{type(exc).__name__}: {exc}\n"
            )
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=output)
    except BaseException:
        # The original bootstrap failure remains the authoritative result.
        # Logging cannot be allowed to mask it during an unattended launch.
        pass


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if getattr(sys, "frozen", False):
    app_dir = os.path.dirname(sys.executable)
else:
    app_dir = project_root


# Velopack lifecycle hooks must return before any environment setup or heavy
# import: the installer holds file locks and enforces a short timeout.
if _is_velopack_hook():
    raise SystemExit(0)


_ensure_windowed_standard_streams()


try:
    _launcher_environment = _configure_launcher_environment(app_dir)
    os.chdir(_launcher_environment["DICEPP_PROJECT_ROOT"])

    from dashboard.src.launcher import main
except Exception as exc:
    if _is_background_launch():
        _append_bootstrap_failure(app_dir, exc)
        raise SystemExit(1) from None
    raise


if __name__ == "__main__":
    main()

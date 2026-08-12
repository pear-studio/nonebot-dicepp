import os
from pathlib import Path

from dicepp_data import InstanceLayout
from plugins.DicePP.utils.logger import logger
from plugins.DicePP.frozen import get_project_root


_legacy_warning_roots: set[Path] = set()


def _contains_non_hidden_file(directory: Path) -> bool:
    try:
        for path in directory.rglob("*"):
            relative = path.relative_to(directory)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.is_file():
                return True
    except OSError:
        return False
    return False


def _legacy_content_locations(layout: InstanceLayout) -> list[Path]:
    locations: list[Path] = []
    excel_dir = layout.content_dir / "excel"
    if _contains_non_hidden_file(excel_dir):
        locations.append(excel_dir)

    try:
        homebrew_dirs = sorted(layout.data_bots_dir.glob("*/QueryHomebrew"))
        for homebrew_dir in homebrew_dirs:
            try:
                if any(path.is_file() for path in homebrew_dir.glob("HB*.db")):
                    locations.append(homebrew_dir)
            except OSError:
                continue
    except OSError:
        pass
    return locations


def _warn_legacy_content_once(layout: InstanceLayout) -> None:
    """Report retired homebrew assets once without reading or changing them."""
    root = layout.root.resolve()
    if root in _legacy_warning_roots:
        return
    _legacy_warning_roots.add(root)

    legacy_locations = _legacy_content_locations(layout)
    if legacy_locations:
        locations = "、".join(str(path) for path in legacy_locations)
        logger.warning(
            f"[Config] [Legacy] 检测到已停用的群私设/XLSX 数据: {locations}。"
            "这些数据会保留在磁盘，但 DicePP 不再读取、导入或迁移它们。"
        )


def _derive_paths(layout: InstanceLayout) -> dict[str, Path]:
    """Compatibility attribute map backed by the shared instance layout."""
    return {
        "PROJECT_ROOT": layout.root,
        "CONFIG_DIR": layout.config_dir,
        "CONFIG_GLOBAL": layout.config_global,
        "CONFIG_USER": layout.config_user,
        "CONFIG_BOTS_DIR": layout.config_bots_dir,
        "DATA_DIR": layout.data_root,
        "DATA_BOTS_DIR": layout.data_bots_dir,
        "LOCAL_IMG_DIR": layout.local_images_dir,
        "CONTENT_DIR": layout.content_dir,
        "CONTENT_CHARACTERS_DIR": layout.content_characters_dir,
        "CONTENT_QUERIES_DIR": layout.content_queries_dir,
        "CONTENT_DECKS_DIR": layout.content_decks_dir,
        "CONTENT_RANDOM_DIR": layout.content_random_dir,
    }


class Paths:
    # Declared for static visibility; the values are populated by _apply_root
    # at import time and rebound by configure_project_root. _derive_paths is the
    # single source of truth for what each path resolves to.
    PROJECT_ROOT: Path
    CONFIG_DIR: Path
    CONFIG_GLOBAL: Path
    CONFIG_USER: Path
    CONFIG_BOTS_DIR: Path
    DATA_DIR: Path
    DATA_BOTS_DIR: Path
    LOCAL_IMG_DIR: Path
    CONTENT_DIR: Path
    CONTENT_CHARACTERS_DIR: Path
    CONTENT_QUERIES_DIR: Path
    CONTENT_DECKS_DIR: Path
    CONTENT_RANDOM_DIR: Path
    _layout: InstanceLayout

    @classmethod
    def _apply_layout(cls, layout: InstanceLayout) -> None:
        cls._layout = layout
        for name, path in _derive_paths(layout).items():
            setattr(cls, name, path)

    @classmethod
    def configure_project_root(cls, project_root: str | os.PathLike[str]) -> None:
        """Rebind all project paths for a process-scoped runtime.

        DicePP normally resolves these paths once at import time. Development
        runtimes such as ``dicepp-shell`` need to point an already-imported Bot
        at an isolated workspace. This remains deliberately process-global:
        callers must not run Bots from different roots concurrently.
        """
        cls._apply_layout(
            InstanceLayout.from_root(
                project_root,
                data_root=os.environ.get("DICEPP_DATA_DIR"),
            )
        )

    @classmethod
    def instance_layout(cls) -> InstanceLayout:
        return InstanceLayout.from_legacy_paths(cls)

    @classmethod
    def bot_data_dir(cls, bot_id: str) -> Path:
        return cls.instance_layout().bot_data_dir(bot_id)

    @classmethod
    def ensure_dirs(cls) -> None:
        _warn_legacy_content_once(cls._layout)
        for d in [
            cls.CONFIG_DIR, cls.CONFIG_BOTS_DIR,
            cls.DATA_DIR, cls.DATA_BOTS_DIR, cls.LOCAL_IMG_DIR,
            cls.CONTENT_DIR, cls.CONTENT_CHARACTERS_DIR, cls.CONTENT_QUERIES_DIR, cls.CONTENT_DECKS_DIR,
            cls.CONTENT_RANDOM_DIR,
        ]:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                logger.info("[Config] [Init] 创建文件夹: " + str(d))

    @classmethod
    def safe_content_path(cls, base_dir: Path, name: str, suffix: str = "") -> Path:
        """
        将用户输入的文件名安全地拼接到 base_dir 下。

        拒绝含路径分隔符或绝对路径的输入，并在 resolve 后验证结果
        仍在 base_dir 之内，防止路径遍历攻击（../、绝对路径等）。

        Args:
            base_dir: 允许访问的根目录（如 Paths.CONTENT_DECKS_DIR）
            name:     用户输入的文件/目录名（不含后缀，不允许含路径分隔符）
            suffix:   要附加的后缀（如 ".xlsx"）

        Returns:
            已验证的安全路径

        Raises:
            ValueError: 输入包含路径分隔符、绝对路径，或解析后越界
        """
        if "/" in name or "\\" in name:
            raise ValueError(f"文件名不允许包含路径分隔符: {name!r}")
        candidate = Path(name + suffix)
        if candidate.is_absolute():
            raise ValueError(f"不允许绝对路径: {name!r}")
        resolved = (base_dir / candidate).resolve()
        try:
            resolved.relative_to(base_dir.resolve())
        except ValueError:
            raise ValueError(f"路径越界: {name!r}")
        return resolved

    @classmethod
    def safe_content_subpath(cls, base_dir: Path, rel: str) -> Path:
        """
        将用户输入的相对路径安全地拼接到 base_dir 下，允许含子目录。

        不拒绝路径分隔符（支持 'folder/file.xlsx' 形式），但在
        resolve 后验证结果仍在 base_dir 之内，防止 ../ 越界。

        Args:
            base_dir: 允许访问的根目录
            rel:      用户输入的相对路径（可含子目录，不可为绝对路径）

        Raises:
            ValueError: 输入为绝对路径或解析后越界
        """
        candidate = Path(rel)
        if candidate.is_absolute():
            raise ValueError(f"不允许绝对路径: {rel!r}")
        resolved = (base_dir / candidate).resolve()
        try:
            resolved.relative_to(base_dir.resolve())
        except ValueError:
            raise ValueError(f"路径越界: {rel!r}")
        return resolved


# Populate all derived paths from the project root at import time. Kept out of
# the class body so the derivation logic lives only in _derive_paths.
Paths._apply_layout(InstanceLayout.from_env(Path(get_project_root())))

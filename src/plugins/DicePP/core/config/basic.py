import os
from pathlib import Path

from utils.logger import dice_log
from utils.frozen import get_project_root


class Paths:
    PROJECT_ROOT: Path = Path(get_project_root())

    CONFIG_DIR:          Path = PROJECT_ROOT / "config"
    CONFIG_GLOBAL:       Path = CONFIG_DIR / "global.json"
    CONFIG_SECRETS:      Path = CONFIG_DIR / "secrets.json"
    CONFIG_BOTS_DIR:     Path = CONFIG_DIR / "bots"
    CONFIG_PERSONAS_DIR: Path = CONFIG_DIR / "personas"

    DATA_DIR:      Path = PROJECT_ROOT / "data"
    DATA_BOTS_DIR: Path = DATA_DIR / "bots"
    LOCAL_IMG_DIR: Path = DATA_DIR / "local_images"

    CONTENT_DIR:         Path = PROJECT_ROOT / "content"
    CONTENT_QUERIES_DIR: Path = CONTENT_DIR / "queries"
    CONTENT_DECKS_DIR:   Path = CONTENT_DIR / "decks"
    CONTENT_RANDOM_DIR:  Path = CONTENT_DIR / "random"
    CONTENT_EXCEL_DIR:   Path = CONTENT_DIR / "excel"

    @classmethod
    def bot_data_dir(cls, bot_id: str) -> Path:
        return cls.DATA_BOTS_DIR / bot_id

    @classmethod
    def ensure_dirs(cls) -> None:
        for d in [
            cls.CONFIG_DIR, cls.CONFIG_BOTS_DIR, cls.CONFIG_PERSONAS_DIR,
            cls.DATA_DIR, cls.DATA_BOTS_DIR, cls.LOCAL_IMG_DIR,
            cls.CONTENT_DIR, cls.CONTENT_QUERIES_DIR, cls.CONTENT_DECKS_DIR,
            cls.CONTENT_RANDOM_DIR, cls.CONTENT_EXCEL_DIR,
        ]:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                dice_log("[Config] [Init] 创建文件夹: " + str(d))

    @classmethod
    def safe_content_path(cls, base_dir: Path, name: str, suffix: str = "") -> Path:
        """
        将用户输入的文件名安全地拼接到 base_dir 下。

        拒绝含路径分隔符或绝对路径的输入，并在 resolve 后验证结果
        仍在 base_dir 之内，防止路径遍历攻击（../、绝对路径等）。

        Args:
            base_dir: 允许访问的根目录（如 Paths.CONTENT_EXCEL_DIR）
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

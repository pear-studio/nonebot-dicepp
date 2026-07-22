"""Shared user-facing project metadata for DicePP runtimes."""

from importlib import metadata as importlib_metadata

PACKAGE_NAME = "dicepp"
PROJECT_NAME = "DicePP"
PROJECT_AUTHOR = "梨子"
PROJECT_CONTRIBUTORS = ("调零", "云朵松饼糖")
PROJECT_CONTRIBUTOR_GITHUB = {
    "调零": "zeroxilo",
    "云朵松饼糖": "nubeslove",
}
PROJECT_DOCS_URL = "https://docs.qq.com/doc/DV3hFWUx6VG1MUnhp"
PROJECT_SOURCE_URL = "https://github.com/pear-studio/nonebot-dicepp"
PROJECT_CONTRIBUTORS_URL = f"{PROJECT_SOURCE_URL}/blob/master/docs/contributors.md"


def get_version() -> str:
    """Return the installed DicePP package version without a ``v`` prefix."""
    try:
        return importlib_metadata.version(PACKAGE_NAME)
    except Exception:
        return "unknown"


def get_display_version() -> str:
    """Return the version label used by user-facing surfaces."""
    version = get_version()
    if version == "unknown":
        return version
    return version if version.startswith("v") else f"v{version}"


def get_project_info() -> dict[str, object]:
    """Return serializable metadata shared by the Bot and Dashboard."""
    return {
        "name": PROJECT_NAME,
        "version": get_version(),
        "display_version": get_display_version(),
        "author": PROJECT_AUTHOR,
        "contributors": [
            {
                "name": name,
                "github": PROJECT_CONTRIBUTOR_GITHUB[name],
                "url": f"https://github.com/{PROJECT_CONTRIBUTOR_GITHUB[name]}",
            }
            for name in PROJECT_CONTRIBUTORS
        ],
        "docs_url": PROJECT_DOCS_URL,
        "source_url": PROJECT_SOURCE_URL,
        "contributors_url": PROJECT_CONTRIBUTORS_URL,
    }

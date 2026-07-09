#!/usr/bin/env python3
"""Build DicePP full offline bundles for group distribution."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import platform
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


REPO = "pear-studio/nonebot-dicepp"
LLONEBOT_REPO = "LLOneBot/LuckyLilliaBot"
SKILL_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = SKILL_DIR / "assets"
OUT_DIR = SKILL_DIR / "out"
DEFAULT_LLONEBOT_VERSION = "7.12.15"
DEFAULT_PMHQ_VERSION = "7.3.2"


def run(args: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def normalize_v(version: str) -> str:
    version = version.strip()
    return version if version.startswith("v") else f"v{version}"


def strip_v(version: str) -> str:
    return version[1:] if version.startswith("v") else version


def version_key(path_or_version: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", path_or_version)
    return tuple(int(part) for part in numbers) if numbers else (0,)


def latest_dicepp_release() -> str:
    if shutil.which("gh"):
        try:
            result = run(
                ["gh", "release", "view", "--repo", REPO, "--json", "tagName", "-q", ".tagName"],
                capture=True,
            )
            tag = result.stdout.strip()
            if tag:
                return tag
        except subprocess.CalledProcessError:
            pass
        try:
            result = run(
                [
                    "gh",
                    "release",
                    "list",
                    "--repo",
                    REPO,
                    "--limit",
                    "1",
                    "--json",
                    "tagName",
                    "-q",
                    ".[0].tagName",
                ],
                capture=True,
            )
            tag = result.stdout.strip()
            if tag:
                return tag
        except subprocess.CalledProcessError:
            pass

    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    request = urllib.request.Request(url, headers={"User-Agent": "DicePP-full-offline-bundle"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["tag_name"]
    except urllib.error.HTTPError:
        pass

    url = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
    request = urllib.request.Request(url, headers={"User-Agent": "DicePP-full-offline-bundle"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            releases = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            "无法读取最新 DicePP Release。请安装 gh 并登录，或使用 --dicepp-version 明确指定版本。"
        ) from exc
    for release in releases:
        if not release.get("draft"):
            return release["tag_name"]
    raise SystemExit("未找到可用的 DicePP Release。请使用 --dicepp-version 明确指定版本。")


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    request = urllib.request.Request(url, headers={"User-Agent": "DicePP-full-offline-bundle"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SystemExit(f"Release asset 不存在: {url}") from exc
        raise
    tmp.replace(destination)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_single_sha(path: Path, sha_path: Path) -> None:
    sha_path.write_text(f"{sha256_file(path)}  {path.name}\n", encoding="utf-8")


def ensure_dicepp_release_asset(version: str, filename: str) -> Path:
    asset_dir = ASSETS_DIR / "dicepp"
    destination = asset_dir / filename
    if destination.exists():
        print(f"[reuse] DicePP asset: {destination}")
        return destination

    url = f"https://github.com/{REPO}/releases/download/{version}/{filename}"
    print(f"[download] {url}")
    download_file(url, destination)
    return destination


def ensure_dicepp_offline_zip(version: str) -> Path:
    return ensure_dicepp_release_asset(version, f"DicePP-{version}-linux-amd64-offline.zip")


def ensure_dicepp_windows_zip(version: str) -> Path:
    return ensure_dicepp_release_asset(version, f"DicePP-{version}-win64.zip")


def cached_linux_llonebot_assets() -> list[Path]:
    return sorted(
        (ASSETS_DIR / "llonebot").glob("llonebot-v*-pmhq-v*-docker-images.tar.zst"),
        key=lambda item: (version_key(item.name), item.stat().st_mtime),
        reverse=True,
    )


def cached_windows_llonebot_assets() -> list[Path]:
    return sorted(
        (ASSETS_DIR / "llonebot" / "windows").glob("LLBot-Desktop-win-x64-v*.zip"),
        key=lambda item: (version_key(item.name), item.stat().st_mtime),
        reverse=True,
    )


def linux_llonebot_asset_name(llonebot_version: str, pmhq_version: str) -> str:
    return f"llonebot-v{strip_v(llonebot_version)}-pmhq-v{strip_v(pmhq_version)}-docker-images.tar.zst"


def windows_llonebot_asset_name(llonebot_version: str) -> str:
    return f"LLBot-Desktop-win-x64-v{strip_v(llonebot_version)}.zip"


def wsl_available() -> bool:
    if platform.system() != "Windows" or not shutil.which("wsl.exe"):
        return False
    try:
        run(
            ["wsl.exe", "-e", "sh", "-lc", "command -v docker >/dev/null && command -v zstd >/dev/null"],
            capture=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def wsl_path(path: Path) -> str:
    result = run(["wsl.exe", "-e", "wslpath", "-a", str(path)], capture=True)
    return result.stdout.strip()


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def build_linux_llonebot_asset(llonebot_version: str, pmhq_version: str) -> Path:
    asset_dir = ASSETS_DIR / "llonebot"
    asset_dir.mkdir(parents=True, exist_ok=True)
    output = asset_dir / linux_llonebot_asset_name(llonebot_version, pmhq_version)
    if output.exists():
        print(f"[reuse] LLOneBot Docker images: {output}")
        return output

    llbot_image = f"linyuchen/llbot:{strip_v(llonebot_version)}"
    pmhq_image = f"linyuchen/pmhq:{strip_v(pmhq_version)}"
    tar_name = output.name.removesuffix(".zst")

    def docker_script(output_dir: str) -> str:
        tar_path = f"{output_dir}/{tar_name}"
        zst_path = f"{tar_path}.zst"
        return "\n".join(
            [
                "set -eu",
                f"mkdir -p {shell_quote(output_dir)}",
                f"docker image inspect {shell_quote(llbot_image)} >/dev/null 2>&1 || docker pull {shell_quote(llbot_image)}",
                f"docker image inspect {shell_quote(pmhq_image)} >/dev/null 2>&1 || docker pull {shell_quote(pmhq_image)}",
                f"docker save {shell_quote(llbot_image)} {shell_quote(pmhq_image)} -o {shell_quote(tar_path)}",
                f"zstd -19 -T0 --rm -f {shell_quote(tar_path)} -o {shell_quote(zst_path)}",
                f"zstd -t {shell_quote(zst_path)}",
            ]
        )

    if platform.system() == "Windows":
        if not wsl_available():
            raise SystemExit(
                "当前是 Windows，且未检测到可用的 WSL2 docker/zstd。"
                "请先在 WSL2 中安装并确认 docker、zstd 可用，或使用 --llonebot-asset 指定已有镜像包。"
            )
        print("[build] 使用 WSL2 生成 LLOneBot Docker 离线镜像")
        run(["wsl.exe", "-e", "sh", "-lc", docker_script(wsl_path(asset_dir))])
    else:
        if not shutil.which("docker") or not shutil.which("zstd"):
            raise SystemExit("缺少 docker 或 zstd。请先安装依赖，或使用 --llonebot-asset 指定已有镜像包。")
        print("[build] 生成 LLOneBot Docker 离线镜像")
        run(["sh", "-lc", docker_script(str(asset_dir))])

    write_single_sha(output, output.with_name(f"{output.name}.sha256"))
    return output


def resolve_linux_llonebot_asset(args: argparse.Namespace) -> tuple[Path, str, str]:
    if args.llonebot_asset:
        asset = Path(args.llonebot_asset).resolve()
        if not asset.exists():
            raise SystemExit(f"LLOneBot Docker asset 不存在: {asset}")
        match = re.search(r"llonebot-v(.+)-pmhq-v(.+)-docker-images\.tar\.zst$", asset.name)
        llonebot_version = args.llonebot_version or (match.group(1) if match else "unknown")
        pmhq_version = args.pmhq_version or (match.group(2) if match else "unknown")
        normalized = normalize_v(llonebot_version) if llonebot_version != "unknown" else llonebot_version
        return asset, normalized, pmhq_version

    if args.llonebot_version:
        llonebot_version = strip_v(args.llonebot_version)
        pmhq_version = strip_v(args.pmhq_version or DEFAULT_PMHQ_VERSION)
        asset = ASSETS_DIR / "llonebot" / linux_llonebot_asset_name(llonebot_version, pmhq_version)
        if asset.exists():
            return asset, normalize_v(llonebot_version), pmhq_version
        return build_linux_llonebot_asset(llonebot_version, pmhq_version), normalize_v(llonebot_version), pmhq_version

    cached = cached_linux_llonebot_assets()
    if cached:
        asset = cached[0]
        match = re.search(r"llonebot-v(.+)-pmhq-v(.+)-docker-images\.tar\.zst$", asset.name)
        llonebot_version = normalize_v(match.group(1)) if match else "unknown"
        pmhq_version = match.group(2) if match else "unknown"
        print(f"[reuse] 默认使用已有 Linux LLOneBot 资产: {asset.name}")
        return asset, llonebot_version, pmhq_version

    llonebot_version = DEFAULT_LLONEBOT_VERSION
    pmhq_version = strip_v(args.pmhq_version or DEFAULT_PMHQ_VERSION)
    return build_linux_llonebot_asset(llonebot_version, pmhq_version), normalize_v(llonebot_version), pmhq_version


def ensure_windows_llonebot_asset(llonebot_version: str) -> Path:
    version = strip_v(llonebot_version)
    asset_dir = ASSETS_DIR / "llonebot" / "windows"
    destination = asset_dir / windows_llonebot_asset_name(version)
    if destination.exists():
        print(f"[reuse] LLOneBot Windows Desktop: {destination}")
        return destination

    url = f"https://github.com/{LLONEBOT_REPO}/releases/download/v{version}/LLBot-Desktop-win-x64.zip"
    print(f"[download] {url}")
    download_file(url, destination)
    write_single_sha(destination, destination.with_name(f"{destination.name}.sha256"))
    return destination


def resolve_windows_llonebot_asset(args: argparse.Namespace) -> tuple[Path, str]:
    if args.llonebot_windows_asset:
        asset = Path(args.llonebot_windows_asset).resolve()
        if not asset.exists():
            raise SystemExit(f"LLOneBot Windows asset 不存在: {asset}")
        match = re.search(r"LLBot-Desktop-win-x64-v(.+)\.zip$", asset.name)
        version = args.llonebot_version or (match.group(1) if match else "unknown")
        normalized = normalize_v(version) if version != "unknown" else version
        return asset, normalized

    if args.llonebot_version:
        version = strip_v(args.llonebot_version)
        return ensure_windows_llonebot_asset(version), normalize_v(version)

    cached = cached_windows_llonebot_assets()
    if cached:
        asset = cached[0]
        match = re.search(r"LLBot-Desktop-win-x64-v(.+)\.zip$", asset.name)
        version = normalize_v(match.group(1)) if match else "unknown"
        print(f"[reuse] 默认使用已有 Windows LLOneBot 资产: {asset.name}")
        return asset, version

    return ensure_windows_llonebot_asset(DEFAULT_LLONEBOT_VERSION), normalize_v(DEFAULT_LLONEBOT_VERSION)


def write_linux_readme(path: Path, dicepp_zip_name: str, llonebot_asset_name_value: str) -> None:
    path.write_text(
        f"""# DicePP Linux 整合包使用说明

本整合包包含：

- DicePP 官方 Linux 离线包：dicepp/{dicepp_zip_name}
- LLOneBot Docker 离线镜像：llonebot/{llonebot_asset_name_value}

请先解压 DicePP 官方 Linux 离线包，并阅读其中的 使用说明.md 和 docs/linux.md。

导入 LLOneBot 镜像示例：

```bash
cd llonebot
zstd -d -f {llonebot_asset_name_value}
docker load -i {llonebot_asset_name_value.removesuffix('.zst')}
```

版本、来源和校验见 manifest.json 与 checksums.sha256。
""",
        encoding="utf-8",
    )


def write_windows_readme(path: Path, dicepp_zip_name: str, llonebot_asset_name_value: str) -> None:
    path.write_text(
        f"""# DicePP Windows 整合包使用说明

本整合包包含：

- DicePP Windows 发布包：dicepp/{dicepp_zip_name}
- LLOneBot Windows 桌面包：llonebot/{llonebot_asset_name_value}

请先解压 DicePP Windows 发布包，并阅读其中的 使用说明.md 和 docs/windows.md。

LLOneBot 的安装和连接方式以 DicePP Windows 文档为准。

版本、来源和校验见 manifest.json 与 checksums.sha256。
""",
        encoding="utf-8",
    )


def add_tree_to_zip(zip_path: Path, root: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root.parent))


def write_checksums(root: Path) -> None:
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "checksums.sha256":
            rel = path.relative_to(root).as_posix()
            entries.append(f"{sha256_file(path)}  {rel}")
    (root / "checksums.sha256").write_text("\n".join(entries) + "\n", encoding="utf-8")


def finalize_bundle(bundle_name: str, root: Path) -> Path:
    write_checksums(root)
    output_zip = OUT_DIR / f"{bundle_name}.zip"
    if output_zip.exists():
        output_zip.unlink()
    add_tree_to_zip(output_zip, root)
    write_single_sha(output_zip, output_zip.with_name(f"{output_zip.name}.sha256"))
    print(f"[ok] 整合包: {output_zip}")
    print(f"[ok] 校验文件: {output_zip}.sha256")
    return output_zip


def build_linux_bundle(dicepp_version: str, args: argparse.Namespace) -> Path:
    dicepp_zip = ensure_dicepp_offline_zip(dicepp_version)
    llonebot_asset, llonebot_version, pmhq_version = resolve_linux_llonebot_asset(args)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bundle_name = f"DicePP-{dicepp_version}-linux-amd64-with-llonebot"

    with tempfile.TemporaryDirectory(prefix="dicepp-full-offline-linux-") as tmp_dir:
        root = Path(tmp_dir) / bundle_name
        (root / "dicepp").mkdir(parents=True)
        (root / "llonebot").mkdir(parents=True)

        shutil.copy2(dicepp_zip, root / "dicepp" / dicepp_zip.name)
        shutil.copy2(llonebot_asset, root / "llonebot" / llonebot_asset.name)

        llonebot_sha = llonebot_asset.with_name(f"{llonebot_asset.name}.sha256")
        if llonebot_sha.exists():
            shutil.copy2(llonebot_sha, root / "llonebot" / llonebot_sha.name)
        else:
            write_single_sha(root / "llonebot" / llonebot_asset.name, root / "llonebot" / f"{llonebot_asset.name}.sha256")

        (root / "llonebot" / "source.txt").write_text(
            "\n".join(
                [
                    f"LLOneBot image: linyuchen/llbot:{strip_v(llonebot_version)}",
                    f"PMHQ image: linyuchen/pmhq:{strip_v(pmhq_version)}",
                    "Source: Docker images pulled by the DicePP maintainer and exported with docker save.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        manifest = {
            "name": "DicePP Linux offline bundle with LLOneBot",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "platform": "linux-amd64",
            "dicepp": {
                "version": dicepp_version,
                "asset": f"dicepp/{dicepp_zip.name}",
                "release_url": f"https://github.com/{REPO}/releases/tag/{dicepp_version}",
            },
            "llonebot": {
                "version": llonebot_version,
                "pmhq_version": pmhq_version,
                "asset": f"llonebot/{llonebot_asset.name}",
                "images": [
                    f"linyuchen/llbot:{strip_v(llonebot_version)}",
                    f"linyuchen/pmhq:{strip_v(pmhq_version)}",
                ],
            },
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_linux_readme(root / "使用说明.md", dicepp_zip.name, llonebot_asset.name)
        return finalize_bundle(bundle_name, root)


def build_windows_bundle(dicepp_version: str, args: argparse.Namespace) -> Path:
    dicepp_zip = ensure_dicepp_windows_zip(dicepp_version)
    llonebot_asset, llonebot_version = resolve_windows_llonebot_asset(args)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bundle_name = f"DicePP-{dicepp_version}-win64-with-llonebot"

    with tempfile.TemporaryDirectory(prefix="dicepp-full-offline-windows-") as tmp_dir:
        root = Path(tmp_dir) / bundle_name
        (root / "dicepp").mkdir(parents=True)
        (root / "llonebot").mkdir(parents=True)

        shutil.copy2(dicepp_zip, root / "dicepp" / dicepp_zip.name)
        shutil.copy2(llonebot_asset, root / "llonebot" / llonebot_asset.name)

        llonebot_sha = llonebot_asset.with_name(f"{llonebot_asset.name}.sha256")
        if llonebot_sha.exists():
            shutil.copy2(llonebot_sha, root / "llonebot" / llonebot_sha.name)
        else:
            write_single_sha(root / "llonebot" / llonebot_asset.name, root / "llonebot" / f"{llonebot_asset.name}.sha256")

        llonebot_release_url = f"https://github.com/{LLONEBOT_REPO}/releases/tag/{llonebot_version}"
        (root / "llonebot" / "source.txt").write_text(
            "\n".join(
                [
                    f"LLOneBot Desktop: LLBot-Desktop-win-x64.zip",
                    f"Release: {llonebot_release_url}",
                    f"Downloaded asset: {llonebot_asset.name}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        manifest = {
            "name": "DicePP Windows offline bundle with LLOneBot",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "platform": "windows-x64",
            "dicepp": {
                "version": dicepp_version,
                "asset": f"dicepp/{dicepp_zip.name}",
                "release_url": f"https://github.com/{REPO}/releases/tag/{dicepp_version}",
            },
            "llonebot": {
                "version": llonebot_version,
                "asset": f"llonebot/{llonebot_asset.name}",
                "release_url": llonebot_release_url,
                "upstream_asset": "LLBot-Desktop-win-x64.zip",
            },
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_windows_readme(root / "使用说明.md", dicepp_zip.name, llonebot_asset.name)
        return finalize_bundle(bundle_name, root)


def build_bundles(args: argparse.Namespace) -> None:
    dicepp_version = normalize_v(args.dicepp_version) if args.dicepp_version else latest_dicepp_release()
    platforms = ["linux", "windows"] if args.platform == "all" else [args.platform]
    failures: list[tuple[str, str]] = []
    for selected_platform in platforms:
        try:
            if selected_platform == "linux":
                build_linux_bundle(dicepp_version, args)
            elif selected_platform == "windows":
                build_windows_bundle(dicepp_version, args)
            else:
                raise SystemExit(f"未知平台: {selected_platform}")
        except SystemExit as exc:
            if args.platform != "all":
                raise
            failures.append((selected_platform, str(exc)))
            print(f"[warn] {selected_platform} bundle failed: {exc}")

    if failures:
        summary = "; ".join(f"{name}: {message}" for name, message in failures)
        raise SystemExit(f"部分整合包生成失败: {summary}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=["linux", "windows", "all"],
        default="all",
        help="Bundle platform to build, defaults to all",
    )
    parser.add_argument("--dicepp-version", help="DicePP release tag, defaults to latest GitHub Release")
    parser.add_argument("--llonebot-version", help="LLOneBot version, defaults to existing cached asset")
    parser.add_argument("--pmhq-version", help=f"PMHQ image version for Linux Docker bundle, defaults to {DEFAULT_PMHQ_VERSION}")
    parser.add_argument("--llonebot-asset", help="Path to an existing Linux LLOneBot docker images .tar.zst asset")
    parser.add_argument("--llonebot-windows-asset", help="Path to an existing Windows LLBot-Desktop zip asset")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        build_bundles(parse_args())
    except KeyboardInterrupt:
        sys.exit(130)

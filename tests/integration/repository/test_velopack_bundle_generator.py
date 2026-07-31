from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.build.generate_velopack_bundle import build_bundle


def test_velopack_bundle_generator_publishes_only_manifest_and_full_nupkg(
    tmp_path: Path,
) -> None:
    nupkg = tmp_path / "DicePP-3.1.0-full.nupkg"
    with zipfile.ZipFile(nupkg, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            "<package><metadata><version>3.1.0</version></metadata></package>",
        )
    output = tmp_path / "velopack.win-x64.zip"

    manifest = build_bundle(
        dicepp_version="3.1.0",
        velopack_version="3.1.0",
        channel="stable",
        nupkg_path=nupkg,
        output=output,
    )

    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["manifest.json", nupkg.name]
        assert json.loads(archive.read("manifest.json")) == manifest
        assert archive.read(nupkg.name) == nupkg.read_bytes()


def test_velopack_bundle_generator_canonicalises_channel_suffixed_nupkg(
    tmp_path: Path,
) -> None:
    # Real `vpk pack --channel` output names the nupkg
    # {id}-{version}-{channel}-full.nupkg; the bundle member must use the
    # canonical {id}-{version}-full.nupkg form.
    nupkg = tmp_path / "DicePP-3.0.0-rc.17-win-x64-prerelease-full.nupkg"
    with zipfile.ZipFile(nupkg, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            "<package><metadata><version>3.0.0-rc.17</version></metadata></package>",
        )
    output = tmp_path / "velopack.win-x64.zip"

    manifest = build_bundle(
        dicepp_version="3.0.0rc17",
        velopack_version="3.0.0-rc.17",
        channel="prerelease",
        nupkg_path=nupkg,
        output=output,
    )

    canonical = "DicePP-3.0.0-rc.17-full.nupkg"
    assert manifest["nupkg"]["filename"] == canonical
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["manifest.json", canonical]


def test_velopack_bundle_generator_rejects_version_mismatched_nupkg(
    tmp_path: Path,
) -> None:
    nupkg = tmp_path / "DicePP-3.1.0-full.nupkg"
    nupkg.write_bytes(b"not-a-real-nupkg")
    output = tmp_path / "velopack.win-x64.zip"

    with pytest.raises(ValueError, match="does not contain version"):
        build_bundle(
            dicepp_version="3.2.0",
            velopack_version="3.2.0",
            channel="stable",
            nupkg_path=nupkg,
            output=output,
        )
    assert not output.exists()

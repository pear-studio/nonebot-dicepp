from __future__ import annotations

import json
import zipfile
from pathlib import Path

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

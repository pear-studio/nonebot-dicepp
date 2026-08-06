from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from scripts.build.windows_upgrade_matrix_harness import main


def test_windows_matrix_fails_closed_until_a_simplified_source_is_published(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.zip"
    target = tmp_path / "target.zip"
    source.write_bytes(b"source")
    target.write_bytes(b"target")

    def asset(path: Path, *, target_asset: bool) -> dict:
        record = {
            "purpose": "portable",
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }
        if target_asset:
            record.update(
                filename=path.name,
                platform="windows",
                arch="amd64",
            )
        else:
            record["name"] = path.name
        return record

    context = {
        "contract_version": 1,
        "platform": "windows",
        "arch": "amd64",
        "source_version": "3.0.0rc20",
        "scenario": "healthy_commit",
        "source_assets": [asset(source, target_asset=False)],
        "target_version": "3.0.0rc21",
        "target_commit_sha": "1" * 40,
        "target_assets": [asset(target, target_asset=True)],
    }
    context_path = tmp_path / "context.json"
    output_path = tmp_path / "result.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "windows_upgrade_matrix_harness.py",
            "--context",
            str(context_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 2
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "unavailable"
    assert result["assertions"] == {}
    assert "pinned published source release" in result["observations"]["reason"]

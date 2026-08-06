"""Unit tests for the Linux upgrade matrix harness — no Docker required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build.linux_upgrade_matrix_harness import _validate_context
from scripts.build.upgrade_matrix_platform_harness import _sha256, run_unavailable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _src_asset(path: Path, payload: bytes = b"src") -> dict:
    path.write_bytes(payload)
    return {
        "purpose": "linux-bundle",
        "name": path.name,
        "path": str(path),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _tgt_asset(path: Path, payload: bytes = b"tgt") -> dict:
    path.write_bytes(payload)
    return {
        "platform": "linux",
        "arch": "amd64",
        "purpose": "linux-bundle",
        "filename": path.name,
        "path": str(path),
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _valid_context(**overrides: object) -> dict:
    ctx = {
        "contract_version": 1,
        "platform": "linux",
        "arch": "amd64",
        "source_version": "3.0.0rc19",
        "scenario": "healthy_commit",
        "source_assets": [],
        "target_version": "3.1.0",
        "target_commit_sha": "b" * 40,
        "target_assets": [],
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------------------
# _validate_context
# ---------------------------------------------------------------------------


def test_validate_context_accepts_valid_linux_context() -> None:
    ctx = _valid_context(
        source_assets=[{"name": "s", "path": "/a", "sha256": "a" * 64, "size": 0}],
        target_assets=[{
            "platform": "linux", "arch": "amd64", "purpose": "linux-bundle",
            "filename": "t", "path": "/b", "sha256": "b" * 64, "size": 0,
        }],
    )
    _validate_context(ctx, platform="linux")


def test_validate_context_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="do not match contract"):
        _validate_context("not a dict", platform="linux")


def test_validate_context_rejects_missing_fields() -> None:
    ctx = _valid_context()
    del ctx["scenario"]
    with pytest.raises(ValueError, match="do not match contract"):
        _validate_context(ctx, platform="linux")


def test_validate_context_rejects_extra_fields() -> None:
    ctx = _valid_context()
    ctx["extra"] = True
    with pytest.raises(ValueError, match="do not match contract"):
        _validate_context(ctx, platform="linux")


def test_validate_context_rejects_wrong_contract_version() -> None:
    ctx = _valid_context(contract_version=2)
    with pytest.raises(ValueError, match="unsupported context contract version"):
        _validate_context(ctx, platform="linux")


def test_validate_context_rejects_wrong_platform() -> None:
    ctx = _valid_context(platform="windows")
    with pytest.raises(ValueError, match="differs from linux"):
        _validate_context(ctx, platform="linux")


def test_validate_context_rejects_missing_source_assets() -> None:
    ctx = _valid_context(source_assets=[])
    with pytest.raises(ValueError, match="source assets are missing"):
        _validate_context(ctx, platform="linux")


def test_validate_context_rejects_non_list_source_assets() -> None:
    ctx = _valid_context(source_assets="not-a-list")
    with pytest.raises(ValueError, match="source assets are missing"):
        _validate_context(ctx, platform="linux")


def test_validate_context_rejects_missing_target_assets() -> None:
    ctx = _valid_context(
        source_assets=[{"name": "s", "path": "/a", "sha256": "a" * 64, "size": 0}],
        target_assets=[],
    )
    with pytest.raises(ValueError, match="target assets are missing"):
        _validate_context(ctx, platform="linux")


def test_validate_context_rejects_non_list_target_assets() -> None:
    ctx = _valid_context(
        source_assets=[{"name": "s", "path": "/a", "sha256": "a" * 64, "size": 0}],
        target_assets=None,
    )
    with pytest.raises(ValueError, match="target assets are missing"):
        _validate_context(ctx, platform="linux")


# ---------------------------------------------------------------------------
# run_unavailable
# ---------------------------------------------------------------------------


def test_run_unavailable_produces_valid_contract(tmp_path: Path) -> None:
    ctx = _valid_context(
        source_assets=[_src_asset(tmp_path / "src.zip")],
        target_assets=[_tgt_asset(tmp_path / "tgt.zip")],
    )
    result = run_unavailable("linux", ctx)

    assert result["contract_version"] == 1
    assert result["platform"] == "linux"
    assert result["arch"] == "amd64"
    assert result["source_version"] == "3.0.0rc19"
    assert result["target_version"] == "3.1.0"
    assert result["scenario"] == "healthy_commit"
    assert result["status"] == "unavailable"
    assert result["assertions"] == {}
    assert isinstance(result["observations"]["reason"], str)
    assert len(result["observations"]["reason"]) > 0


def test_run_unavailable_rejects_wrong_platform_in_context(
    tmp_path: Path,
) -> None:
    ctx = _valid_context(
        platform="windows",
        source_assets=[_src_asset(tmp_path / "src.zip")],
        target_assets=[_tgt_asset(tmp_path / "tgt.zip")],
    )
    with pytest.raises(ValueError, match="differs from entrypoint"):
        run_unavailable("linux", ctx)


def test_run_unavailable_rejects_missing_contract_version(
    tmp_path: Path,
) -> None:
    ctx = _valid_context(
        source_assets=[_src_asset(tmp_path / "src.zip")],
        target_assets=[_tgt_asset(tmp_path / "tgt.zip")],
    )
    del ctx["contract_version"]
    with pytest.raises(ValueError, match="do not match contract"):
        run_unavailable("linux", ctx)


def test_run_unavailable_rejects_empty_source_assets() -> None:
    ctx = _valid_context(source_assets=[])
    with pytest.raises(ValueError, match="source assets are missing"):
        run_unavailable("linux", ctx)


def test_run_unavailable_rejects_empty_target_assets(
    tmp_path: Path,
) -> None:
    ctx = _valid_context(
        source_assets=[_src_asset(tmp_path / "src.zip")],
    )
    with pytest.raises(ValueError, match="target assets are missing"):
        run_unavailable("linux", ctx)


# ---------------------------------------------------------------------------
# Harness CLI integration (exit code contract)
# ---------------------------------------------------------------------------


def test_harness_main_returns_2_when_orchestrator_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling main() directly returns 2 when orchestrator is unavailable."""
    from scripts.build.linux_upgrade_matrix_harness import main

    context_path = tmp_path / "context.json"
    output_path = tmp_path / "result.json"
    src = tmp_path / "src.zip"
    tgt = tmp_path / "tgt.zip"
    ctx = _valid_context(
        source_assets=[_src_asset(src)],
        target_assets=[_tgt_asset(tgt)],
    )
    context_path.write_text(json.dumps(ctx), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "linux_upgrade_matrix_harness.py",
            "--context", str(context_path),
            "--output", str(output_path),
        ],
    )

    exit_code = main()
    assert exit_code == 2
    assert output_path.is_file()
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "unavailable"

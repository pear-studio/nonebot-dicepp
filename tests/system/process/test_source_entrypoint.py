"""Source entrypoint smoke checks."""

import subprocess
import sys
import textwrap
from pathlib import Path


def test_source_entrypoint_loads_shared_metadata_without_installed_project(tmp_path):
    """The Docker-style source layout must load the complete DicePP plugin."""
    project_root = Path.cwd().resolve()
    bootstrap = textwrap.dedent(
        """
        import runpy
        import sys
        from pathlib import Path

        project_root = Path(sys.argv[1]).resolve()
        source_root = (project_root / "src").resolve()
        sys.path[:] = [
            entry
            for entry in sys.path
            if not entry or Path(entry).resolve() != source_root
        ]
        sys.argv = ["bot.py", "--smoke-check"]
        runpy.run_path(str(project_root / "bot.py"), run_name="__main__")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap, str(project_root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "SMOKE CHECK PASSED" in output

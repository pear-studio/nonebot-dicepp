import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BACKLOG_PY = Path(__file__).resolve().parents[2] / "scripts" / "tools" / "backlog.py"


def run(*args, cwd=None, input_text="") -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(BACKLOG_PY), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        input=input_text,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestAdd:
    def test_add_single(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        rc, out, err = run(
            "--file", str(backlog),
            "add",
            "--module", "roll",
            "--title", "Dice balance check",
            "--source", "review-260506-0000-roll.md / R1",
            "--problem", "Missing edge case for d0",
            "--trigger", "When refactoring dice engine",
            "--reason", "Out of current PR scope",
        )
        assert rc == 0
        assert out.startswith("B-")
        text = backlog.read_text()
        assert "Dice balance check" in text
        assert "roll" in text

    def test_add_missing_required_field(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        rc, out, err = run(
            "--file", str(backlog),
            "add",
            "--module", "roll",
            "--title", "X",
            "--trigger", "T",
            "--reason", "",
        )
        assert rc != 0
        assert "校验失败" in err

    def test_add_duplicate_id_rejected(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        # First add
        run(
            "--file", str(backlog),
            "add",
            "--module", "roll",
            "--title", "Dice balance check",
            "--source", "review-260506-0000-roll.md / R1",
            "--problem", "Missing edge case for d0",
            "--trigger", "When refactoring dice engine",
            "--reason", "Out of current PR scope",
        )
        # Second identical add
        rc, out, err = run(
            "--file", str(backlog),
            "add",
            "--module", "roll",
            "--title", "Dice balance check",
            "--source", "review-260506-0000-roll.md / R1",
            "--problem", "Missing edge case for d0",
            "--trigger", "When refactoring dice engine",
            "--reason", "Out of current PR scope",
        )
        assert rc != 0
        assert "已存在" in err


class TestBatchAdd:
    def test_batch_add(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        payload = """Module: roll
Title: Batch A
Source: review-260506-0000-roll.md / R2
Problem: p1
Trigger: t1
Reason: r1
<<<<END>>>
Module: persona
Title: Batch B
Source: chat
Problem: p2
Trigger: t2
Reason: r2
<<<<END>>>
"""
        rc, out, err = run(
            "--file", str(backlog),
            "batch-add",
            "--payload-file", "-",
            input_text=payload,
        )
        # Note: subprocess input only works when stdin is explicitly wired,
        # our run() helper supports input_text.
        # But argparse nargs='?' for payload may read stdin directly,
        # so let's use a temp file instead to be safe.

    def test_batch_add_via_file(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        payload_file = tmp_dir / "payload.txt"
        payload_file.write_text(
            "Module: roll\n"
            "Title: Batch A\n"
            "Source: review-260506-0000-roll.md / R2\n"
            "Problem: p1\n"
            "Trigger: t1\n"
            "Reason: r1\n"
            "<<<END>>>\n"
            "Module: persona\n"
            "Title: Batch B\n"
            "Source: chat\n"
            "Problem: p2\n"
            "Trigger: t2\n"
            "Reason: r2\n"
            "<<<END>>>\n",
            encoding="utf-8",
        )
        rc, out, err = run(
            "--file", str(backlog),
            "batch-add",
            "--payload-file", str(payload_file),
        )
        assert rc == 0
        lines = out.splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("B-")
        assert lines[1].startswith("B-")
        text = backlog.read_text()
        assert "Batch A" in text
        assert "Batch B" in text


class TestListShow:
    def test_list_and_show(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "Item1",
            "--source", "s", "--problem", "p", "--trigger", "t", "--reason", "r",
        )
        rc, out, _ = run("--file", str(backlog), "list")
        assert rc == 0
        assert "Item1" in out

        rc2, out2, _ = run("--file", str(backlog), "show", out.split()[0].strip("[]"))
        assert rc2 == 0
        assert "Item1" in out2


class TestClosePrune:
    def test_close(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        rc, bid, _ = run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "ToClose",
            "--source", "s", "--problem", "p", "--trigger", "t", "--reason", "r",
        )
        assert rc == 0
        bid = bid.strip()

        rc2, _, _ = run("--file", str(backlog), "close", bid)
        assert rc2 == 0

        rc3, out3, _ = run("--file", str(backlog), "show", bid)
        assert rc3 != 0

    def test_prune(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        rc1, b1, _ = run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "A",
            "--source", "s", "--problem", "p", "--trigger", "t", "--reason", "r",
        )
        rc2, b2, _ = run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "B",
            "--source", "s", "--problem", "p", "--trigger", "t", "--reason", "r",
        )
        b1 = b1.strip()
        b2 = b2.strip()

        rc, out, _ = run("--file", str(backlog), "prune", b1, b2)
        assert rc == 0
        assert "移除" in out

        rc3, out3, _ = run("--file", str(backlog), "list")
        assert rc3 == 0
        assert out3 == "无 backlog 项"


class TestSortValidate:
    def test_sort(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        # Add two items out of order (simulate by direct write)
        # Actually add normally; order should be insertion order.
        run(
            "--file", str(backlog),
            "add", "--module", "zmod", "--title", "Z",
            "--source", "s", "--problem", "p", "--trigger", "t", "--reason", "r",
        )
        run(
            "--file", str(backlog),
            "add", "--module", "amod", "--title", "A",
            "--source", "s", "--problem", "p", "--trigger", "t", "--reason", "r",
        )

        rc, _, _ = run("--file", str(backlog), "sort")
        assert rc == 0
        text = backlog.read_text()
        # amod should come before zmod after sort
        assert text.index("amod") < text.index("zmod")

    def test_validate_pass(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "V",
            "--source", "s", "--problem", "p", "--trigger", "t", "--reason", "r",
        )
        rc, out, _ = run("--file", str(backlog), "validate")
        assert rc == 0
        assert "校验通过" in out

    def test_validate_fail_missing_trigger(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        # Manually craft invalid file
        backlog.write_text(
            "# Backlog\n\n---\n\n## roll\n\n"
            "### [B-260506-000000] Bad\n"
            "- 来源: s\n"
            "- 创建: 2026-05-06\n"
            "- 原始问题: p\n"
            "- 暂缓原因: r\n"
            "\n",
            encoding="utf-8",
        )
        rc, _, err = run("--file", str(backlog), "validate")
        assert rc != 0
        assert "缺少触发条件" in err

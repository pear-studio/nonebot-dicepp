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
            "--priority", "P1",
            "--type", "feature",
            "--effort", "M",
            "--symptom", "d0 边界未覆盖, 输出概率分布偏移",
            "--plan", "增补 d0 单测, 评估随机分布拦截",
        )
        assert rc == 0
        assert out.startswith("B-")
        text = backlog.read_text()
        assert "Dice balance check" in text
        assert "roll" in text

    def test_add_missing_required_field(self, tmp_dir):
        # 必填字段传空值, 走 validate 路径
        backlog = tmp_dir / "backlog.md"
        rc, out, err = run(
            "--file", str(backlog),
            "add",
            "--module", "roll",
            "--title", "X",
            "--priority", "P1",
            "--type", "feature",
            "--effort", "M",
            "--symptom", "",
            "--plan", "p",
        )
        assert rc != 0
        assert "校验失败" in err

    def test_add_duplicate_id_rejected(self, tmp_dir):
        # 同秒内同 module/title/symptom 会生成相同 ID, 应被拒绝
        backlog = tmp_dir / "backlog.md"
        args = [
            "--file", str(backlog),
            "add",
            "--module", "roll",
            "--title", "Dice balance check",
            "--priority", "P1",
            "--type", "feature",
            "--effort", "M",
            "--symptom", "d0 边界未覆盖",
            "--plan", "增补单测",
        ]
        run(*args)
        rc, out, err = run(*args)
        assert rc != 0
        assert "已存在" in err


class TestBatchAdd:
    def test_batch_add_via_file_single_line(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        payload_file = tmp_dir / "payload.txt"
        payload_file.write_text(
            "Module: roll\n"
            "Title: Batch A\n"
            "Priority: P1\n"
            "Type: feature\n"
            "Effort: M\n"
            "Symptom: 单行问题表现\n"
            "Plan: 单行工作计划\n"
            "<<<END>>>\n"
            "Module: persona\n"
            "Title: Batch B\n"
            "Priority: P2\n"
            "Type: bug\n"
            "Effort: S\n"
            "Symptom: 另一条单行\n"
            "Plan: 另一条计划\n"
            "<<<END>>>\n",
            encoding="utf-8",
        )
        rc, out, err = run(
            "--file", str(backlog),
            "batch-add",
            "--payload-file", str(payload_file),
        )
        assert rc == 0, err
        lines = out.splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("B-")
        assert lines[1].startswith("B-")
        text = backlog.read_text()
        assert "Batch A" in text
        assert "Batch B" in text

    def test_batch_add_multiline_bullets(self, tmp_dir):
        # 验证多行 bullet 写法可以被解析、保存和读出
        backlog = tmp_dir / "backlog.md"
        payload_file = tmp_dir / "payload.txt"
        payload_file.write_text(
            "Module: persona\n"
            "Title: 多行测试\n"
            "Priority: P1\n"
            "Type: bug\n"
            "Effort: L\n"
            "Symptom:\n"
            "  - 现象 1\n"
            "  - 现象 2\n"
            "  - 现象 3\n"
            "Plan:\n"
            "  - 修复方向 A\n"
            "  - 风险点 B\n"
            "<<<END>>>\n",
            encoding="utf-8",
        )
        rc, bid, err = run(
            "--file", str(backlog),
            "batch-add",
            "--payload-file", str(payload_file),
        )
        assert rc == 0, err
        bid = bid.strip()

        rc2, out2, _ = run("--file", str(backlog), "show", bid)
        assert rc2 == 0
        assert "现象 1" in out2
        assert "现象 2" in out2
        assert "现象 3" in out2
        assert "修复方向 A" in out2
        assert "风险点 B" in out2


class TestListShow:
    def test_list_and_show(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "Item1",
            "--priority", "P1", "--type", "feature", "--effort", "M",
            "--symptom", "s", "--plan", "p",
        )
        rc, out, _ = run("--file", str(backlog), "list")
        assert rc == 0
        assert "Item1" in out

        bid = out.split()[0].strip("[]")
        rc2, out2, _ = run("--file", str(backlog), "show", bid)
        assert rc2 == 0
        assert "Item1" in out2


class TestClosePrune:
    def test_close(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        rc, bid, _ = run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "ToClose",
            "--priority", "P1", "--type", "feature", "--effort", "M",
            "--symptom", "s", "--plan", "p",
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
            "--priority", "P1", "--type", "feature", "--effort", "M",
            "--symptom", "s", "--plan", "p",
        )
        rc2, b2, _ = run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "B",
            "--priority", "P1", "--type", "feature", "--effort", "M",
            "--symptom", "s", "--plan", "p",
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
        # 模块以字典序排序写出, 不论插入顺序
        run(
            "--file", str(backlog),
            "add", "--module", "zmod", "--title", "Z",
            "--priority", "P1", "--type", "feature", "--effort", "M",
            "--symptom", "s", "--plan", "p",
        )
        run(
            "--file", str(backlog),
            "add", "--module", "amod", "--title", "A",
            "--priority", "P1", "--type", "feature", "--effort", "M",
            "--symptom", "s", "--plan", "p",
        )

        rc, _, _ = run("--file", str(backlog), "sort")
        assert rc == 0
        text = backlog.read_text()
        assert text.index("amod") < text.index("zmod")

    def test_validate_pass(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        run(
            "--file", str(backlog),
            "add", "--module", "roll", "--title", "V",
            "--priority", "P1", "--type", "feature", "--effort", "M",
            "--symptom", "s", "--plan", "p",
        )
        rc, out, _ = run("--file", str(backlog), "validate")
        assert rc == 0
        assert "校验通过" in out

    def test_validate_fail_missing_symptom_and_plan(self, tmp_dir):
        backlog = tmp_dir / "backlog.md"
        # 手工写入一个缺 问题表现 / 工作计划 的非法条目
        backlog.write_text(
            "# Backlog\n\n---\n\n## roll\n\n"
            "### [B-260506-000000] Bad\n"
            "- 创建: 2026-05-06\n"
            "- 优先级: P0\n"
            "- 类型: bug\n"
            "- 改动量: S\n"
            "\n",
            encoding="utf-8",
        )
        rc, _, err = run("--file", str(backlog), "validate")
        assert rc != 0
        assert "缺少问题表现" in err
        assert "缺少工作计划" in err

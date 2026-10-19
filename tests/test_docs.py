import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.check_docs import check_docs


class DocumentationTests(unittest.TestCase):
    def test_repository_examples_and_skills(self):
        self.assertGreater(check_docs(), 100)

    def test_rejects_drift_and_invalid_examples_without_executing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / relative for relative in (".agents/skills/telegram-cli/SKILL.md",
                     ".claude/skills/telegram-cli/SKILL.md", "tcli_skills/SKILL.md")]
            for path in paths:
                path.parent.mkdir(parents=True)
                path.write_text("skill\n", encoding="utf-8")
            (root / "README.md").write_text('```console\ntcli send me "never sent"\n```\n', encoding="utf-8")
            (root / "README.ru.md").write_text('```console\ntcli read me --not-a-real-flag\n```\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid tcli example"):
                check_docs(root)
            paths[0].write_text("different\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "byte-identical"):
                check_docs(root)

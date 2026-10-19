"""Check bundled skills and parse fenced tcli examples without running commands."""
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tg


def check_docs(root=ROOT):
    skills = [root / path for path in (".agents/skills/telegram-cli/SKILL.md",
                                       ".claude/skills/telegram-cli/SKILL.md", "tcli_skills/SKILL.md")]
    if len({path.read_bytes() for path in skills}) != 1:
        raise ValueError("The three Telegram CLI skills must be byte-identical.")
    count = 0
    cli = tg.parser()
    for path in [root / "README.md", root / "README.ru.md", *skills]:
        fenced = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("```"):
                fenced = not fenced
                continue
            if not fenced or not line.strip().startswith("tcli "):
                continue
            arguments = shlex.split(line.strip(), comments=True)[1:]
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    cli.parse_args(arguments)
            except SystemExit as error:
                if error.code != 0:
                    raise ValueError(f"{path.relative_to(root)}:{number}: invalid tcli example: {line}") from None
            count += 1
    if count == 0:
        raise ValueError("No tcli examples found.")
    return count


if __name__ == "__main__":
    print(f"Documentation OK: {check_docs()} CLI examples parsed; three skills identical.")

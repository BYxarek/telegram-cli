"""Install a wheel in a clean environment and check the CLI outside the checkout."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv


def main():
    wheel = Path(sys.argv[1]).resolve()
    with TemporaryDirectory(prefix="tcli-wheel-") as directory:
        root = Path(directory)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True, cwd=root)
        settings = os.environ.copy()
        settings.pop("TG_API_ID", None)
        settings.pop("TG_API_HASH", None)
        settings.pop("PYTHONPATH", None)
        home = root / "home"
        home.mkdir()
        settings.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(root / "data"))
        executable = environment / ("Scripts/tcli.exe" if os.name == "nt" else "bin/tcli")
        settings["PATH"] = str(executable.parent)
        for args in (["--help"], ["--version"], ["skills", "install", "--agent", "all"],
                     ["skills", "install", "--agent", "all"]):
            subprocess.run([str(executable), *args], check=True, cwd=root, env=settings)
        codex = home / ".agents" / "skills" / "telegram-cli" / "SKILL.md"
        claude = home / ".claude" / "skills" / "telegram-cli" / "SKILL.md"
        if not codex.read_bytes() == claude.read_bytes():
            raise RuntimeError("Installed skills differ")
        if list(home.rglob("*.bak")) or (root / "data").exists():
            raise RuntimeError("Offline/idempotent skill installation created unexpected files")
        print("Clean wheel installation and offline skill installation passed.")


if __name__ == "__main__":
    main()

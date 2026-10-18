# Publishing telegram-cli-local

The initial PyPI release is version 0.1.0. No PyPI upload is performed
on a normal push or a manual workflow run. Only publishing a GitHub Release triggers the publish job.

## One-time PyPI setup

1. Create a PyPI account, verify its email, and configure the authentication required by PyPI.
2. Open https://pypi.org/manage/account/publishing/ and add a pending GitHub publisher:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `telegram-cli-local` |
   | Owner | `BYxarek` |
   | Repository | `telegram-cli` |
   | Workflow filename | `release.yml` |
   | Environment name | `pypi` |

3. Check that the project name is still available. A pending publisher does not reserve the name.
4. In GitHub repository settings, create the `pypi` environment if needed. Optional required reviewers
   can gate the final upload; the workflow already uses this environment.

The publisher grants this repository's release workflow permission to upload this package using
short-lived OIDC credentials. No PyPI password or long-lived API token belongs in GitHub secrets.
See the [PyPI pending-publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Release checks

From the repository root:

```console
python -m pip install -e . build twine
python -X utf8 -m unittest discover -s tests -v
python -m build
python -m twine check --strict dist/*
python scripts/check_distribution.py dist/telegram_cli_local-0.1.0-py3-none-any.whl
```

Use a clean `dist/` directory for each release to avoid uploading stale versions.
The smoke check installs the wheel into a temporary virtual environment, runs the CLI outside the
checkout, and installs both skills into a temporary home directory without Telegram configuration.
It needs access to PyPI to install the wheel's dependencies; it never accesses Telegram.

## Publish a release

1. Update `__version__` in `tcli_skills/__init__.py` for each new release. PyPI versions are immutable.
2. Keep all three skill copies identical and update both READMEs. Maintain `CHANGELOG.md`
   under `Unreleased` for every change; move those entries into a versioned section for the release.
3. Commit and push to `main`. Wait for the `Test, build, and publish` workflow to pass on Windows
   and Linux with Python 3.10 and 3.14, including the built-wheel installation check.
4. Create a GitHub Release targeting that commit with a matching tag, initially `v0.1.0`, and publish it.
   Draft releases do not upload. The workflow rejects tags that differ from the package version.
5. Check that the publish job succeeded and that https://pypi.org/project/telegram-cli-local/
   lists the expected version. Test `pipx install telegram-cli-local` on a clean machine.

For users, updates are explicit:

```console
pipx upgrade telegram-cli-local
tcli skills install
```

By default, skill installation only targets detected agents and creates nothing if none are found.
Use --agent codex, --agent claude, or --agent all for an explicit installation.
Updating the CLI alone does not replace user skill copies. Changed skills are backed up before
replacement, and custom instructions need to be merged manually.

# slopwatch

Catch hallucinated, typosquatted and brand-new packages before `pip` or `npm` installs them.

```
$ slopwatch check pypi requests reqeusts flask-jwt-simple-auth
  ok   pypi  requests               score   0
block  pypi  reqeusts               score 100
         +100  doesn't exist on the registry. Likely a
               hallucinated name, and anyone could register it
               did you mean 'requests'? (1 character away)
block  pypi  flask-jwt-simple-auth  score 100
         +100  doesn't exist on the registry. Likely a
               hallucinated name, and anyone could register it

$ slopwatch check npm axios axois
  ok   npm   axios  score   5
block  npm   axois  score 100
          +60  npm security placeholder: a malicious package
               with this name was removed
          +40  looks like 'axios' (1 character away)
          +10  only one release
```

## Why

LLMs make up package names. Not occasionally: a 2024 study of code-generating models
(Spracklen et al., *"We Have a Package for You!"*) found that a large share of
recommended packages didn't exist, and that many of the fake names came back again and
again across runs. A name that an AI reliably invents is a name an attacker can register
and wait on. This has picked up the name **slopsquatting**.

That used to need a human to copy the bad name into a terminal. Coding agents now run
`pip install` and `npm install` themselves, often with nobody reading the command first.
Existing scanners mostly answer "is this package known to be malicious?" The gap here is
different: "does this package even exist, and if it does, did it show up last Tuesday
with one release and a postinstall script?"

slopwatch sits in front of the install and answers that question.

## Install

```
pip install git+https://github.com/viehgroup/slopwatch
```

Python 3.9+, no dependencies.

## Usage

**Check names directly**

```
slopwatch check pypi fastapi uvicorn pydantic-settings
slopwatch check npm zod @tanstack/react-query
```

**Scan a project**

```
slopwatch scan requirements.txt
slopwatch scan pyproject.toml package.json
```

Follows `-r` includes in requirements files, skips local paths, git URLs and `file:` deps.

**Guard an install**

```
slopwatch run pip install some-package
slopwatch run npm i some-package
```

Checks what the command would fetch, then runs it if nothing is blocked.
Understands `pip`, `pip3`, `python -m pip`, `uv pip`, `uv add`, `poetry add`, `pipx install`,
`npm`, `yarn`, `pnpm`, `bun` and `npx`. Use `--force` to override a block.

To make it the default for your shell, source [`examples/shell-aliases.sh`](examples/shell-aliases.sh).

**In CI**

```
slopwatch scan requirements.txt --strict
```

Exit codes: `0` clean, `1` warnings (only with `--strict`), `2` blocked.
`--json` gives machine-readable output.

## Using it with Claude Code

slopwatch ships a `PreToolUse` hook. Add this to `.claude/settings.json`
(also in [`examples/`](examples/claude-code-settings.json)):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "slopwatch hook" }
        ]
      }
    ]
  }
}
```

When the agent tries to install something suspicious, the call is blocked and the
reason is fed back to the model, which usually corrects the name on its own. Compound
commands like `cd app && npm i foo` are handled. Because nobody is watching the agent's
terminal, the hook blocks on warnings too; pass `--lenient` to only block on hard fails.

Check the Claude Code hooks documentation for the current config format if this
doesn't load. The other agent tools that support pre-command hooks can call
`slopwatch run` or `slopwatch hook` the same way.

## What it looks at

| Signal | Points |
|---|---|
| Package doesn't exist on the registry | 100 |
| npm security placeholder (a malicious package was removed) | 60 |
| Name imitates a popular package (typo, separator, affix, foreign scope) | 40, or 5 if the package is long-established |
| First published under 14 days ago | 40 |
| First published under 90 days ago | 20 |
| Install hooks (`preinstall`, `install`, `postinstall`) | 25, or 5 for popular packages |
| Latest release yanked | 15 |
| No release dates at all | 15 |
| Only one release | 10 |
| No repository or homepage | 10 |
| PyPI source dist only (runs `setup.py` at install) | 10 |
| Deprecated | 10 |

30+ is a warning, 60+ is a block. Packages in the bundled top-5000 PyPI list or the
popular npm list are never blocked.

Lookalike detection compares against those lists using edit distance with adjacent
transpositions (`reqeusts`), separator collapsing (`crossenv` vs `cross-env`), common
affixes (`python-requests`, `node-lodash`) and unfamiliar scopes (`@someone/lodash`).

Registry responses are cached for six hours in `~/.cache/slopwatch`.
Set `SLOPWATCH_NO_CACHE=1` or pass `--no-cache` to skip it.

## Limits

Be clear about what this is and isn't.

- It's a heuristic. A patient attacker who registers a name and waits months, with a
  repo link and a few releases, will score lower. Pair it with lockfiles and hash
  pinning, not instead of them.
- It doesn't inspect package contents. Pair it with a malware scanner if you need that.
- On network errors it fails open with a warning, so a flaky connection doesn't brick
  your installs. Use `--strict` if you'd rather fail closed.
- Private registries and mirrors aren't supported yet.

Tested against a random sample of 60 real packages ranked 5,000–15,000 on PyPI: no
blocks, one warning (a young package one letter away from an established one).

## Development

```
pip install -e .
python -m unittest discover -s tests -v
```

Tests run offline. The popular-package list comes from
[hugovk/top-pypi-packages](https://github.com/hugovk/top-pypi-packages).

## Related

From the same team:
[agentscan](https://github.com/viehgroup/agentscan) finds leaked keys and risky settings
in AI agent configs, and [extwatch](https://github.com/viehgroup/extwatch) catches
backdoored VS Code, Cursor and Windsurf extensions.

## License

MIT. Built and maintained by the **VIEH Security Team**.

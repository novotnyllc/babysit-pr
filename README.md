# PR Babysitter

This repository packages the `babysit-pr` skill as a standalone Codex and Claude plugin.

The skill watches a GitHub pull request for CI status, review comments, review submissions, and mergeability changes. It is designed for agents that should keep monitoring after a PR is green so later review feedback is not missed.

## Source

This work is derived from `openai/codex`:

- Source path: `.codex/skills/babysit-pr`
- Upstream repository: https://github.com/openai/codex
- Copied from upstream commit: `70ac0f123c4b1869c9069d5b34e367b96c28bfad`
- Upstream license: Apache-2.0

Local changes include packaging the skill as a standalone marketplace plugin and fixing GitHub issue #19148 so reviews from `copilot-pull-request-reviewer` are surfaced even though that account does not use a `[bot]` suffix.

## Upstream updates

The weekly `Upstream Sync` GitHub Actions workflow checks `openai/codex` for changes under `.codex/skills/babysit-pr`. When upstream changes are available, it applies the upstream diff onto `plugins/babysit-pr/skills/babysit-pr`, updates the recorded upstream commit, bumps the plugin patch version, runs the watcher tests, opens a pull request, and enables auto-merge.

You can run the same check locally:

```bash
python3 scripts/sync_upstream.py --check
```

## Install

Codex can add this repository as a plugin marketplace:

```bash
codex plugin marketplace add novotnyllc/babysit-pr
```

Claude Code can add the same repository as a marketplace and install the plugin:

```bash
claude plugin marketplace add novotnyllc/babysit-pr
claude plugin install babysit-pr@babysit-pr
```

For local testing before publishing:

```bash
claude plugin marketplace add .
claude plugin install babysit-pr@babysit-pr
```

## Validate

```bash
python3 -m pytest plugins/babysit-pr/skills/babysit-pr/scripts
claude plugin validate plugins/babysit-pr
```

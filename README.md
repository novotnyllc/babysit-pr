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
claude plugin marketplace add /Volumes/Data/Users/claire/dev/babysit-pr
claude plugin install babysit-pr@babysit-pr
```

## Validate

```bash
python3 -m pytest plugins/babysit-pr/skills/babysit-pr/scripts
claude plugin validate plugins/babysit-pr
```

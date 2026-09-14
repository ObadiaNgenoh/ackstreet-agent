---
name: recon-before-acting
description: Survey a working directory before changing anything — list files, read the README, check git status, and identify the build/test command. Use at the start of any task in an unfamiliar repository or project folder.
version: 1.0.0
author: ackstreet-agent
created: seeds
tags: [workflow, recon, safety]
source: seed
---

# Skill: Recon Before Acting

## When to Use
At the start of any task that touches an existing project directory — especially
when you have not worked in it before. Recon is cheap; undoing a wrong edit is not.

## Steps
1. List the directory tree, ignoring noisy build folders:
   `ls -la` then `find . -maxdepth 2 -not -path '*/.git/*' -not -path '*/node_modules/*'`
2. Read the project's own documentation first — it encodes the maintainers' intent:
   `cat README.md` (then `CONTRIBUTING.md`, `docs/` if present)
3. Identify the language and dependency manifest:
   `ls package.json pyproject.toml requirements.txt go.mod Cargo.toml Gemfile 2>/dev/null`
4. Check version-control state so you know what is already modified:
   `git status --short && git log --oneline -5`
5. Find the canonical build/test command rather than inventing one:
   `ls Makefile justfile tox.ini .github/workflows 2>/dev/null`
6. Only then start editing.

## Pitfalls
- Editing before reading the README — the project often already documents the
  exact procedure you were about to improvise.
- Running a test command you guessed instead of the one the project defines.
- Ignoring a dirty `git status` and losing track of which changes are yours.
- Recursing into `.git`, `node_modules` or `.venv` and drowning in output.

## Verification
- You can state the language, the test command, and the current git state
  without running another command.

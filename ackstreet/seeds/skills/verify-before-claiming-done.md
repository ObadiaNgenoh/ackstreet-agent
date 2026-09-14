---
name: verify-before-claiming-done
description: Confirm a change actually works before reporting success — re-read written files, re-run the failing command, and check exit codes. Use before declaring any task complete or telling a user it is finished.
version: 1.0.0
author: ackstreet-agent
created: seeds
tags: [workflow, verification, quality]
source: seed
---

# Skill: Verify Before Claiming Done

## When to Use
Immediately before reporting that a task succeeded — after any file write, patch,
install or configuration change. The most common agent failure is claiming success
without checking.

## Steps
1. **A write is not a success until it is read back.** After writing a file:
   `read_file` it (or `cat` it) and confirm the content is what you intended.
2. **Re-run the exact command that was failing.** Do not substitute a simpler
   command that you expect to pass.
3. **Check the exit code, not just the output.** A test runner can print
   confident-looking output and still exit non-zero:
   `pytest -q; echo "exit=$?"`
4. **For a server or service**, prove it responds rather than assuming it started:
   `curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:PORT/health`
5. **For generated output**, validate the artifact itself — parse the JSON,
   open the file, count the lines.
6. Report the *actual observed* result, quoting the real output. If something
   is still failing, say so plainly instead of describing it as done.

## Pitfalls
- Treating "the command ran" as "the command succeeded".
- Trusting cached or stale output from an earlier run.
- Verifying a different path than the one you wrote.
- Reporting "should work" instead of "I ran it and here is the output".

## Verification
- For every claim you make, you can point to the command that proved it and
  quote its output.

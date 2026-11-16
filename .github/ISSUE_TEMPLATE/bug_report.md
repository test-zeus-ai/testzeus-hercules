---
name: Bug report
about: Something in Hercules does not work as documented
title: ''
labels: bug
assignees: ''

---

**What happened**
A short description of the problem.

**What you expected**
What you expected to happen instead.

**How to reproduce**
1. The command you ran (for example `hercules run .hercules/cases/sign-in.json --mode pure`), or what you did in the tester UI or coding agent.
2. The test case, or a smaller one that shows the problem. Remove anything private.
3. What you saw (output, error message).

**Evidence**
If a run went wrong, attach what helps from `.hercules/runs/<run_id>/`: `run.json`, and for the step that failed
`steps/<n>/observation.txt`, `decisions.jsonl`, `plan.jsonl` and `plain.jpg`. Check them for anything private before you attach them.

**Environment**
- `hercules version`:
- OS and memory:
- Python version:
- Model endpoint (provider and model, for example Ollama with `gemma4:26b-a4b-it-qat`):
- Mode (`pure` or `hybrid`) and egress policy:
- `hercules doctor` output (it never prints keys):

**Anything else**

# Running real coding agents

Everything in CI runs the scripted mock agent. To evaluate a real one:

1. Install the agent CLI and sign in (Claude Code, Codex CLI or Muse Code).
2. Check its non-interactive command in `agents.json` against `<cli> --help`: flags change between releases.
   Keep the permissions narrow: edits in the workspace and `python -m pytest`, nothing else.
3. Run a few repeats, because coding agents are not deterministic:

```bash
python -m caq run --agent claude-code --repeats 3 --update-baseline   # first run: record the baseline
python -m caq run --agent claude-code --repeats 3                     # later runs: gated against it
```

Each run works in a throwaway git repo created from `tasks/<id>/repo`; hidden tests are copied in only after
the agent exits. Reports land in `reports/latest/` with one escalation packet per failed run.

Cost: 8 tasks × 3 repeats is 24 short agent sessions. Watch the first run's usage before raising repeats.

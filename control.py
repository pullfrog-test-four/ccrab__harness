"""c-CRAB control arm: the paper's own Claude Code adapter (run_batch_baselines.run_claude_code).

Three deliberate deviations from the published run, each so the arm matches the Pullfrog arm:
- model: `--model sonnet` -> the Pullfrog arm's model, plus the Pullfrog arm's `--effort`
- workdir: a clone of the same squashed snapshot repo Pullfrog reviewed, so the prompt's
  base/head SHAs are that repo's `main` (merge-base tree) and `pr` (head tree)
- timeout: the adapter's 300s default is raised (the published arm ran to 689s)
"""

import argparse
import json
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, "ccrab")
# only pipeline.dataset_utils imports `datasets`, and nothing here calls it
sys.modules["datasets"] = types.SimpleNamespace(load_dataset=None)
import run_batch_baselines as rbb  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--instance", required=True)
parser.add_argument("--workdir", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--effort", required=True)
parser.add_argument("--timeout", type=int, required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

rows = (json.loads(line) for line in open("ccrab/results_pipeline_funnel/stage4_agent_resolved.jsonl"))
instance = next(row for row in rows if row["instance_id"] == args.instance)


def git(*cmd: str) -> str:
    return subprocess.run(["git", *cmd], cwd=args.workdir, check=True, capture_output=True, text=True).stdout.strip()


instance["base_commit"] = git("rev-parse", "origin/main")
instance["commit_to_review"]["head_commit"] = git("rev-parse", "origin/pr")

real_run = subprocess.run


def run_with_model(cmd, *a, **kw):
    if cmd[:1] == ["claude"]:
        cmd = list(cmd)
        cmd[cmd.index("--model") + 1] = args.model
        cmd[1:1] = ["--effort", args.effort]
    return real_run(cmd, *a, **kw)


rbb.subprocess.run = run_with_model
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
result = rbb.run_claude_code(instance, Path(args.workdir), out, args.timeout)
(out / "result.json").write_text(
    json.dumps({"instance_id": args.instance, "tools": {"claude-code": result}}, indent=2, default=str)
)
print(f"{args.instance}: success={result['success']} findings={result['num_findings']} elapsed={result['elapsed_seconds']}s")

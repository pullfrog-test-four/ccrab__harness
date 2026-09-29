"""c-CRAB evaluation of one arm's findings for one instance: run_tool_eval.process_tool_instance.

Two deviations from the published run, neither touching the prompt, model or tests:
- auth: an API key via Claude Code's apiKeyHelper instead of a mounted ~/.claude/.credentials.json
- the resolver's Claude Code is pinned to one version so every arm and instance runs the same binary
"""

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "ccrab")
import run_tool_eval as rte  # noqa: E402

CLAUDE_VERSION = "2.1.284"
RESOLVER_MODEL = "claude-sonnet-4-6"

parser = argparse.ArgumentParser()
parser.add_argument("--instance", required=True)
parser.add_argument("--tool", required=True)
parser.add_argument("--findings-dir", required=True)
parser.add_argument("--testgen-dir", required=True)
parser.add_argument("--key-file", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

# keep per-test verdicts out of the public job log
logging.getLogger().setLevel(logging.WARNING)


def setup_claude_in_container(session):
    session.run_command("useradd -m agent", timeout=30)
    result = session.run_command(
        f"su - agent -c 'curl -fsSL https://claude.ai/install.sh | bash -s {CLAUDE_VERSION}'", timeout=300
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to install Claude Code: {result.stderr[-2000:]}")
    session.run_command("mkdir -p /home/agent/.claude", timeout=10)
    session.copy_to(args.key_file, "/home/agent/.anthropic_key")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"apiKeyHelper": "cat /home/agent/.anthropic_key"}, f)
    session.copy_to(f.name, "/home/agent/.claude/settings.json")
    session.run_command("chown -R agent:agent /home/agent && chmod 600 /home/agent/.anthropic_key", timeout=10)
    result = session.run_command(
        "chown -R agent:agent /workspace && git config --global --add safe.directory /workspace", timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to chown /workspace: {result.stderr[-1000:]}")


rte.setup_claude_in_container = setup_claude_in_container
real_invoke = rte.invoke_claude_in_container
resolver_output: list[str] = []


def invoke_with_usage(session, prompt, model):
    """the paper's invocation plus `--output-format json`, which only changes what -p prints, so cost is recorded."""
    real_run = session.run_command

    def run(cmd, **kw):
        if isinstance(cmd, list) and "--dangerously-skip-permissions" in cmd[-1]:
            cmd = [*cmd[:-1], cmd[-1] + " --output-format json"]
        return real_run(cmd, **kw)

    session.run_command = run
    try:
        stdout, rc = real_invoke(session, prompt, model)
    finally:
        session.run_command = real_run
    resolver_output.append(stdout)
    return stdout, rc


rte.invoke_claude_in_container = invoke_with_usage

instance = rte.load_stage3_instance(Path("ccrab/results_pipeline_funnel/stage3_testgen_verified.jsonl"), args.instance)
testgen = rte.load_testgen_results(Path(args.testgen_dir), args.instance)
tool_data = rte.load_tool_findings(Path(args.findings_dir), args.instance, args.tool)
assert instance and testgen and tool_data and tool_data["findings"], "missing instance, testgen or findings"

result = rte.process_tool_instance(
    instance=instance,
    tool_data=tool_data,
    testgen_results=testgen,
    output_dir=Path(args.out),
    model=RESOLVER_MODEL,
    tool=args.tool,
    docker_image=rte.get_docker_image_name(args.instance),
    credentials_path=Path("/nonexistent"),
)
(Path(args.out) / args.instance.replace("/", "__") / "resolver.json").write_text("\n".join(resolver_output))
print(f"{args.instance}: done (error={result.get('error')})")

import json

from harness.t2 import run


async def test_real_pytest_verdicts_reach_the_batch_report(tmp_path):
    directory, passed = await run(tmp_path)
    assert passed
    summary = json.loads((directory / "evaluation.json").read_bytes())["summary"]
    assert (summary["goals_passed"], summary["goals_total"]) == (1, 4)
    assert (summary["subtasks_passed"], summary["subtasks_total"]) == (1, 4)
    evidence = json.loads((directory / "control.json").read_bytes())["pytest_calls"]
    node, goal = evidence["correct"]
    assert node["root"] == "workspaces/writer"
    assert goal["root"].startswith("artifacts/") and goal["root"].endswith("/implement")
    assert evidence["empty"][0]["summary"] == "no tests collected"
    assert evidence["timeout"][0]["exit_code"] is None

"""Tests for scripts/alert_formatter (alert email wording) and scripts/setup_alert_formatter.py."""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


formatter = _load("alert_formatter_lambda", SCRIPTS / "alert_formatter" / "lambda_function.py")


def _alarm(name="backend-rag-models-unhealthy", new="ALARM", old="OK", time="2026-10-29T15:02:33.123+0000"):
    return {"AlarmName": name, "NewStateValue": new, "OldStateValue": old, "StateChangeTime": time}


def test_problem_email_is_short_and_plain() -> None:
    subject, body = formatter.build_email(_alarm(), "multiragapi.snaic.net")

    assert subject == "\U0001F534 SNAIC LLM backend: chatbot AI is not responding"
    assert body.splitlines() == [
        "SNAIC LLM backend - problem detected",
        "",
        "What: The AI models behind the website chatbot are not responding.",
        "Impact: SNAIC Bot on www.snaic.net cannot answer questions.",
        "When: 29 Oct 2026, 11:02 PM (Singapore time)",
        "",
        "Details: https://multiragapi.snaic.net/health/models",
        "Next step: open the link to see what is failing. Short outages often clear by themselves; "
        "you will get a recovery email when it is back.",
    ]


def test_recovery_email_and_core_alarm() -> None:
    subject, body = formatter.build_email(
        _alarm("backend-rag-core-unhealthy", new="OK", old="ALARM", time="2026-10-29T01:05:00.000+0000"),
        "multiragapi.snaic.net",
    )

    assert subject == "✅ SNAIC LLM backend: API is healthy again"
    assert "The backend API and its databases are healthy again. No action needed." in body
    assert "When: 29 Oct 2026, 9:05 AM (Singapore time)" in body


def test_no_email_for_insufficient_data_or_first_ok() -> None:
    assert formatter.build_email(_alarm(new="INSUFFICIENT_DATA"), "x") is None
    assert formatter.build_email(_alarm(new="OK", old="INSUFFICIENT_DATA"), "x") is None


def test_unknown_alarm_uses_its_description() -> None:
    alarm = _alarm("other-alarm") | {"AlarmDescription": "Disk is full."}

    subject, body = formatter.build_email(alarm, "multiragapi.snaic.net")

    assert subject.endswith("SNAIC LLM backend: problem detected (other-alarm)")
    assert "What: Disk is full." in body
    assert len(formatter.build_email(_alarm("x" * 200), "d")[0]) < 100


def test_handler_publishes_formatted_email(monkeypatch) -> None:
    published = []
    fake_boto3 = types.SimpleNamespace(
        client=lambda service: types.SimpleNamespace(publish=lambda **kwargs: published.append(kwargs))
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setenv("EMAIL_TOPIC_ARN", "arn:aws:sns:us-east-1:1:backend-rag-health-alerts")
    event = {
        "Records": [
            {"Sns": {"Message": json.dumps(_alarm())}},
            {"Sns": {"Message": json.dumps(_alarm(new="INSUFFICIENT_DATA"))}},
        ]
    }

    assert formatter.lambda_handler(event, None) == {"sent": 1}
    assert published[0]["TopicArn"] == "arn:aws:sns:us-east-1:1:backend-rag-health-alerts"
    assert published[0]["Subject"].endswith("chatbot AI is not responding")


# A stand-in for the aws CLI: logs each call and answers from simple rules.
FAKE_AWS = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["FAKE_AWS_LOG"], "a") as log:
        log.write(" ".join(args) + "\\n")
    command = " ".join(args[:2])
    if os.environ.get("FAKE_AWS_DENY") and command == "sns create-topic":
        sys.stderr.write("An error occurred (AuthorizationError) when calling the CreateTopic operation")
        sys.exit(254)
    if command == "sns create-topic":
        name = args[args.index("--name") + 1]
        print(json.dumps({"TopicArn": f"arn:aws:sns:us-east-1:123456789012:{name}"}))
    elif command == "iam get-role":
        sys.stderr.write("An error occurred (NoSuchEntity) when calling the GetRole operation")
        sys.exit(254)
    elif command == "iam create-role":
        print(json.dumps({"Role": {"Arn": "arn:aws:iam::123456789012:role/backend-rag-alert-formatter"}}))
    elif command == "lambda get-function":
        sys.stderr.write("An error occurred (ResourceNotFoundException) when calling the GetFunction operation")
        sys.exit(254)
    elif command == "lambda create-function":
        print(json.dumps({"FunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:backend-rag-alert-formatter"}))
    elif command == "sns subscribe":
        print(json.dumps({"SubscriptionArn": "arn:aws:sns:us-east-1:123456789012:backend-rag-alarm-events:1"}))
    """
)


def _run_setup(tmp_path: Path, deny: bool = False) -> tuple[subprocess.CompletedProcess, list[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "aws"
    fake.write_text(FAKE_AWS)
    fake.chmod(0o755)
    log = tmp_path / "aws.log"
    output = tmp_path / "github_output"
    env = os.environ | {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_AWS_LOG": str(log),
        "GITHUB_OUTPUT": str(output),
        "RUNNER_TEMP": str(tmp_path),
    }
    if deny:
        env["FAKE_AWS_DENY"] = "1"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup_alert_formatter.py")], capture_output=True, text=True, env=env
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls, output.read_text() if output.exists() else ""


def test_setup_creates_formatter_and_reports_topic(tmp_path) -> None:
    result, calls, github_output = _run_setup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert github_output == "alarm_topic_name=backend-rag-alarm-events\n"
    commands = [" ".join(call.split()[:2]) for call in calls]
    assert commands == [
        "sns create-topic",
        "sns create-topic",
        "iam get-role",
        "iam create-role",
        "iam put-role-policy",
        "lambda get-function",
        "lambda create-function",
        "lambda add-permission",
        "sns subscribe",
    ]
    create_function = next(call for call in calls if call.startswith("lambda create-function"))
    assert "EMAIL_TOPIC_ARN" in create_function and "backend-rag-health-alerts" in create_function
    assert "--protocol lambda" in calls[-1] and "backend-rag-alarm-events" in calls[-1]


def test_setup_without_permissions_warns_and_keeps_direct_emails(tmp_path) -> None:
    result, _, github_output = _run_setup(tmp_path, deny=True)

    assert result.returncode == 0
    assert "::warning::Alert formatter not set up" in result.stdout
    assert github_output == ""

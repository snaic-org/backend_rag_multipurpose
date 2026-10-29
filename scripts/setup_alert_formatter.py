"""Set up the alert formatter: short, plain-language health alert emails.

  CloudWatch alarm -> SNS <prefix>-alarm-events -> Lambda <prefix>-alert-formatter
                   -> SNS <prefix>-health-alerts (existing email subscription)

Idempotent: reuses whatever already exists and updates the function code.
Uses the aws CLI, like setup-health-alerts.ps1, so it needs no Python packages.

On success it prints the events topic name, and also writes it to
$GITHUB_OUTPUT as alarm_topic_name when run in GitHub Actions; pass it to
setup-health-alerts.ps1 -AlarmActionTopicName so the alarms publish there.
On any failure (for example the deploy user lacks the IAM permissions) it
prints a warning and exits 0 without that output, so the alarms keep emailing
directly in the AWS format instead of going silent.

Usage: python3 scripts/setup_alert_formatter.py [--prefix backend-rag] [--domain multiragapi.snaic.net]
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REGION = "us-east-1"  # Route 53 health check alarms only exist in us-east-1.
LAMBDA_SOURCE = Path(__file__).resolve().parent / "alert_formatter" / "lambda_function.py"
LAMBDA_RUNTIME = "python3.12"
PERMISSION_STATEMENT_ID = "sns-alarm-events"


class AwsError(RuntimeError):
    pass


def aws(*args: str, region: str | None = REGION) -> dict:
    command = ["aws", *args, "--output", "json"]
    if region:
        command += ["--region", region]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise AwsError(result.stderr.strip() or f"aws {args[0]} {args[1]} failed")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def write_temp(name: str, content: bytes) -> str:
    path = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / name
    path.write_bytes(content)
    return str(path)


def lambda_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(LAMBDA_SOURCE, "lambda_function.py")
    return buffer.getvalue()


def ensure_role(role_name: str, function_name: str, email_topic_arn: str) -> str:
    try:
        role_arn = aws("iam", "get-role", "--role-name", role_name, region=None)["Role"]["Arn"]
    except AwsError as exc:
        if "NoSuchEntity" not in str(exc):
            raise
        trust = {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
        }
        role_arn = aws(
            "iam", "create-role", "--role-name", role_name,
            "--assume-role-policy-document", json.dumps(trust),
            "--description", "Formats SNAIC LLM backend health alerts",
            region=None,
        )["Role"]["Arn"]
        print(f"Created IAM role {role_name}")

    account = email_topic_arn.split(":")[4]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "sns:Publish", "Resource": email_topic_arn},
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{REGION}:{account}:log-group:/aws/lambda/{function_name}*",
            },
        ],
    }
    aws(
        "iam", "put-role-policy", "--role-name", role_name,
        "--policy-name", "publish-alert-emails", "--policy-document", json.dumps(policy),
        region=None,
    )
    return role_arn


def ensure_function(function_name: str, role_arn: str, email_topic_arn: str, domain: str) -> str:
    zip_path = write_temp(f"{function_name}.zip", lambda_zip())
    environment = json.dumps({"Variables": {"EMAIL_TOPIC_ARN": email_topic_arn, "DOMAIN_NAME": domain}})
    try:
        aws("lambda", "get-function", "--function-name", function_name)
    except AwsError as exc:
        if "ResourceNotFoundException" not in str(exc):
            raise
        # A new IAM role takes a few seconds before Lambda can assume it.
        for attempt in range(1, 13):
            try:
                created = aws(
                    "lambda", "create-function", "--function-name", function_name,
                    "--runtime", LAMBDA_RUNTIME, "--handler", "lambda_function.lambda_handler",
                    "--role", role_arn, "--zip-file", f"fileb://{zip_path}",
                    "--timeout", "15", "--memory-size", "128", "--environment", environment,
                    "--description", "Formats SNAIC LLM backend health alerts",
                )
                print(f"Created Lambda function {function_name}")
                return created["FunctionArn"]
            except AwsError as create_exc:
                if "cannot be assumed" not in str(create_exc) or attempt == 12:
                    raise
                time.sleep(5)

    aws("lambda", "update-function-code", "--function-name", function_name, "--zip-file", f"fileb://{zip_path}")
    aws("lambda", "wait", "function-updated", "--function-name", function_name)
    updated = aws(
        "lambda", "update-function-configuration", "--function-name", function_name,
        "--role", role_arn, "--environment", environment,
    )
    print(f"Updated Lambda function {function_name}")
    return updated["FunctionArn"]


def ensure_subscription(events_topic_arn: str, function_name: str, function_arn: str) -> None:
    try:
        aws(
            "lambda", "add-permission", "--function-name", function_name,
            "--statement-id", PERMISSION_STATEMENT_ID, "--action", "lambda:InvokeFunction",
            "--principal", "sns.amazonaws.com", "--source-arn", events_topic_arn,
        )
    except AwsError as exc:
        if "ResourceConflictException" not in str(exc):
            raise  # Anything but "this permission already exists".
    # Subscribing the same endpoint again returns the existing subscription.
    aws("sns", "subscribe", "--topic-arn", events_topic_arn, "--protocol", "lambda", "--notification-endpoint", function_arn)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prefix", default="backend-rag")
    parser.add_argument("--domain", default="multiragapi.snaic.net")
    args = parser.parse_args()

    events_topic_name = f"{args.prefix}-alarm-events"
    function_name = f"{args.prefix}-alert-formatter"
    try:
        email_topic_arn = aws("sns", "create-topic", "--name", f"{args.prefix}-health-alerts")["TopicArn"]
        events_topic_arn = aws("sns", "create-topic", "--name", events_topic_name)["TopicArn"]
        role_arn = ensure_role(function_name, function_name, email_topic_arn)
        function_arn = ensure_function(function_name, role_arn, email_topic_arn, args.domain)
        ensure_subscription(events_topic_arn, function_name, function_arn)
    except AwsError as exc:
        print(
            "::warning::Alert formatter not set up, so health alerts keep the AWS email format. "
            "Check that the deploy user has the permissions in deploy/ecs/github-actions-deploy-policy.json. "
            f"Error: {exc}"
        )
        return 0

    print(f"Alert formatter ready: alarms publish to {events_topic_name}, {function_name} emails via {email_topic_arn}")
    print(events_topic_name)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output:
            output.write(f"alarm_topic_name={events_topic_name}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

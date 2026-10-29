"""Turn CloudWatch health alarms into short, plain-language alert emails.

CloudWatch alarm emails are a fixed AWS template full of metric details. The
alarms publish to an events topic instead; this AWS Lambda function receives
each state change and publishes a short message to the email topic, which
keeps the existing email subscription.

Environment variables:
  EMAIL_TOPIC_ARN  SNS topic with the email subscription
  DOMAIN_NAME      public API host, for the status links (multiragapi.snaic.net)
"""

import json
import os
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "SNAIC LLM backend"
SINGAPORE_TIME = timezone(timedelta(hours=8))

# Keyed by the end of the alarm name (setup-health-alerts.ps1 names them
# "<prefix>-models-unhealthy" and "<prefix>-core-unhealthy").
ALERTS = {
    "models-unhealthy": {
        "path": "/health/models",
        "down_subject": "chatbot AI is not responding",
        "down_what": "The AI models behind the website chatbot are not responding.",
        "up_subject": "chatbot AI is working again",
        "up_what": "The AI models behind the website chatbot are responding again.",
    },
    "core-unhealthy": {
        "path": "/health",
        "down_subject": "API is down",
        "down_what": "The backend API or one of its databases (PostgreSQL, Redis, Qdrant) is unhealthy.",
        "up_subject": "API is healthy again",
        "up_what": "The backend API and its databases are healthy again.",
    },
}


def build_email(alarm: dict, domain: str) -> tuple[str, str] | None:
    """Return (subject, body) for an alarm state change, or None to send nothing."""
    new_state = alarm.get("NewStateValue")
    old_state = alarm.get("OldStateValue")
    # Only real problems and recoveries from a problem: skip INSUFFICIENT_DATA
    # and the OK an alarm reports when it is first created.
    if new_state == "ALARM":
        problem = True
    elif new_state == "OK" and old_state == "ALARM":
        problem = False
    else:
        return None

    name = alarm.get("AlarmName", "")
    alert = next((value for suffix, value in ALERTS.items() if name.endswith(suffix)), None)
    when = _singapore_time(alarm.get("StateChangeTime", ""))

    if alert is None:
        what = alarm.get("AlarmDescription") or name
        subject = f"{SERVICE_NAME}: {'problem detected' if problem else 'recovered'} ({name})"
        link = f"https://{domain}/health"
    else:
        what = alert["down_what"] if problem else alert["up_what"]
        subject = f"{SERVICE_NAME}: {alert['down_subject'] if problem else alert['up_subject']}"
        link = f"https://{domain}{alert['path']}"

    if problem:
        lines = [
            f"{SERVICE_NAME} - problem detected",
            "",
            f"What: {what}",
            "Impact: SNAIC Bot on www.snaic.net cannot answer questions.",
            f"When: {when}",
            "",
            f"Details: {link}",
            "Next step: open the link to see what is failing. Short outages often clear "
            "by themselves; you will get a recovery email when it is back.",
        ]
        subject = f"\U0001F534 {subject}"
    else:
        lines = [
            f"{SERVICE_NAME} - recovered",
            "",
            f"{what} No action needed.",
            f"When: {when}",
        ]
        subject = f"✅ {subject}"

    # SNS email subjects must be under 100 characters.
    return subject[:99], "\n".join(lines)


def _singapore_time(value: str) -> str:
    try:
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        moment = datetime.now(timezone.utc)
    local = moment.astimezone(SINGAPORE_TIME)
    hour = local.hour % 12 or 12
    return f"{local.day} {local:%b %Y}, {hour}:{local:%M %p} (Singapore time)"


def lambda_handler(event, context):
    import boto3  # Provided by the AWS Lambda Python runtime.

    sns = boto3.client("sns")
    topic_arn = os.environ["EMAIL_TOPIC_ARN"]
    domain = os.environ.get("DOMAIN_NAME", "multiragapi.snaic.net")
    sent = 0
    for record in event.get("Records", []):
        alarm = json.loads(record["Sns"]["Message"])
        email = build_email(alarm, domain)
        if email is None:
            continue
        subject, body = email
        sns.publish(TopicArn=topic_arn, Subject=subject, Message=body)
        sent += 1
    return {"sent": sent}

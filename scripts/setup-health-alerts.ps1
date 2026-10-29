param(
    [string]$DomainName = "multiragapi.snaic.net",
    [string]$AlertEmail = "aicentre@singaporetech.edu.sg",
    [string]$NamePrefix = "backend-rag",
    [string[]]$CheckerRegions = @("ap-southeast-1", "ap-southeast-2", "ap-northeast-1"),
    [int]$RequestIntervalSeconds = 30,
    [int]$FailureThreshold = 3,
    [int]$AlarmEvaluationMinutes = 2,
    # SNS topic the alarms publish to. Empty: the email topic itself (AWS's
    # standard alarm email). scripts/setup_alert_formatter.py prints the topic
    # whose Lambda sends short, plain-language emails instead.
    [string]$AlarmActionTopicName = ""
)

# Creates (or reuses) external uptime monitoring that emails $AlertEmail when the
# API deteriorates and again when it recovers:
#
#   Route 53 health check  GET /health/models  -> unhealthy on 503 (a model is failing) or no answer
#   Route 53 health check  GET /health         -> unhealthy unless the body reports "status":"ok"
#   CloudWatch alarm per health check          -> SNS topic -> email
#
# Route 53 health check metrics only exist in us-east-1, so the alarms and the SNS
# topic are created there regardless of where the service runs.
# Safe to re-run: existing health checks, topic, subscription, and alarms are reused/updated.

$ErrorActionPreference = "Stop"
# Make a failing aws CLI call stop the script (PowerShell 7.3+; ignored by Windows PowerShell 5.1).
$PSNativeCommandUseErrorActionPreference = $true
$AlarmRegion = "us-east-1"

function Write-JsonTempFile {
    param([object]$Value)

    # Passing JSON through file:// avoids Windows argument quoting problems with the aws CLI.
    $path = [System.IO.Path]::GetTempFileName()
    $Value | ConvertTo-Json -Depth 10 -Compress | Set-Content -LiteralPath $path -Encoding ascii
    return $path
}

function Ensure-HealthCheck {
    param(
        [string]$Name,
        [string]$ResourcePath,
        [string]$SearchString
    )

    $existing = aws route53 list-health-checks --output json | ConvertFrom-Json
    $match = $existing.HealthChecks | Where-Object {
        $_.HealthCheckConfig.FullyQualifiedDomainName -eq $DomainName -and
        $_.HealthCheckConfig.ResourcePath -eq $ResourcePath
    } | Select-Object -First 1

    if ($match) {
        Write-Host "Reusing Route 53 health check $($match.Id) for https://$DomainName$ResourcePath"
        return $match.Id
    }

    $config = [ordered]@{
        Type                     = $(if ($SearchString) { "HTTPS_STR_MATCH" } else { "HTTPS" })
        FullyQualifiedDomainName = $DomainName
        Port                     = 443
        ResourcePath             = $ResourcePath
        RequestInterval          = $RequestIntervalSeconds
        FailureThreshold         = $FailureThreshold
        EnableSNI                = $true
        Regions                  = $CheckerRegions
    }
    if ($SearchString) {
        $config.SearchString = $SearchString
    }

    $configPath = Write-JsonTempFile -Value $config
    try {
        $created = aws route53 create-health-check `
            --caller-reference "$Name-$([guid]::NewGuid().ToString('N'))" `
            --health-check-config "file://$configPath" `
            --output json | ConvertFrom-Json
    }
    finally {
        Remove-Item -LiteralPath $configPath -Force
    }

    $healthCheckId = $created.HealthCheck.Id
    aws route53 change-tags-for-resource `
        --resource-type healthcheck `
        --resource-id $healthCheckId `
        --add-tags "Key=Name,Value=$Name" | Out-Null

    Write-Host "Created Route 53 health check $healthCheckId for https://$DomainName$ResourcePath"
    return $healthCheckId
}

function Ensure-EmailSubscription {
    param([string]$TopicArn)

    $subscriptions = aws sns list-subscriptions-by-topic `
        --region $AlarmRegion `
        --topic-arn $TopicArn `
        --output json | ConvertFrom-Json
    $existing = $subscriptions.Subscriptions | Where-Object {
        $_.Protocol -eq "email" -and $_.Endpoint -eq $AlertEmail
    } | Select-Object -First 1

    if ($existing) {
        if ($existing.SubscriptionArn -eq "PendingConfirmation") {
            Write-Host "Email subscription for $AlertEmail is still pending: open the AWS confirmation email and click 'Confirm subscription'."
        }
        else {
            Write-Host "Email subscription for $AlertEmail is confirmed."
        }
        return
    }

    aws sns subscribe `
        --region $AlarmRegion `
        --topic-arn $TopicArn `
        --protocol email `
        --notification-endpoint $AlertEmail | Out-Null
    Write-Host "Subscribed $AlertEmail. AWS sent a confirmation email; alerts start only after someone clicks 'Confirm subscription'."
}

function Set-HealthAlarm {
    param(
        [string]$AlarmName,
        [string]$HealthCheckId,
        [string]$TopicArn,
        [string]$Description
    )

    aws cloudwatch put-metric-alarm `
        --region $AlarmRegion `
        --alarm-name $AlarmName `
        --alarm-description $Description `
        --namespace "AWS/Route53" `
        --metric-name HealthCheckStatus `
        --dimensions "Name=HealthCheckId,Value=$HealthCheckId" `
        --statistic Minimum `
        --period 60 `
        --evaluation-periods $AlarmEvaluationMinutes `
        --threshold 1 `
        --comparison-operator LessThanThreshold `
        --treat-missing-data breaching `
        --alarm-actions $TopicArn `
        --ok-actions $TopicArn | Out-Null

    Write-Host "Alarm '$AlarmName' emails on failure and on recovery."
}

$topicArn = aws sns create-topic `
    --region $AlarmRegion `
    --name "$NamePrefix-health-alerts" `
    --query TopicArn `
    --output text
Write-Host "Using SNS topic $topicArn"
Ensure-EmailSubscription -TopicArn $topicArn

$alarmTopicArn = $topicArn
if ($AlarmActionTopicName) {
    $alarmTopicArn = aws sns create-topic `
        --region $AlarmRegion `
        --name $AlarmActionTopicName `
        --query TopicArn `
        --output text
    Write-Host "Alarms publish to $alarmTopicArn (formatted emails)"
}

$modelsCheckId = Ensure-HealthCheck -Name "$NamePrefix-models" -ResourcePath "/health/models" -SearchString ""
$coreCheckId = Ensure-HealthCheck -Name "$NamePrefix-core" -ResourcePath "/health" -SearchString '"status":"ok"'

Set-HealthAlarm `
    -AlarmName "$NamePrefix-models-unhealthy" `
    -HealthCheckId $modelsCheckId `
    -TopicArn $alarmTopicArn `
    -Description "https://$DomainName/health/models is failing: the API is down or an AI model (generation, embedding, or rerank) is not responding. Open the URL for details; check the 'app' container logs for model_health_probe_failed."

Set-HealthAlarm `
    -AlarmName "$NamePrefix-core-unhealthy" `
    -HealthCheckId $coreCheckId `
    -TopicArn $alarmTopicArn `
    -Description "https://$DomainName/health is not reporting status ok: the API is down or PostgreSQL, Redis, Qdrant, or an active provider's configuration is unhealthy. Open the URL for details."

Write-Host ""
Write-Host "Health alerting is set up for https://$DomainName -> $AlertEmail"

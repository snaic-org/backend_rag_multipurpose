param(
    [string]$Region = "ap-southeast-1",
    [string]$AccountId = "961341555117",
    [string]$RepositoryPrefix = "snaic_website",
    [string]$Cluster = "snaic_website_cluster",
    [string]$Service = "backend-rag-multipurpose",
    [int]$DesiredCount = 1,
    [int]$TimeoutMinutes = 20,
    [int]$PollSeconds = 15,
    [string]$TaskDefinitionPath = "deploy/ecs/task-definition.json",
    [bool]$EnableHttpsAlb = $true,
    [string]$DomainName = "multiragapi.snaic.net",
    [string]$CertificateArn = "",
    [string]$AlbName = "backend-rag-alb",
    [string]$TargetGroupName = "backend-rag-tg",
    [string[]]$AlbSubnetIds = @(),
    [string[]]$AlbSecurityGroupIds = @(),
    [string]$HostedZoneId = "",
    [bool]$UpdateRoute53 = $true,
    [bool]$DisableTaskPublicIp = $false,
    [string[]]$EcsSubnetIds = @(),
    [string[]]$EcsSecurityGroupIds = @(),
    [switch]$SkipBuild,
    [switch]$SkipPush,
    [switch]$SkipRegister,
    [switch]$SkipUpdate
)

$ErrorActionPreference = "Stop"

$ecrBase = "$AccountId.dkr.ecr.$Region.amazonaws.com/$RepositoryPrefix"
$backendImage = "$ecrBase/rag-backend:latest"
$nginxImage = "$ecrBase/rag-nginx:latest"
$postgresImage = "$ecrBase/rag-postgres:latest"
$taskDefinitionArn = $null
$resolvedTaskDefinitionPath = $null
$targetGroupArn = $null
$loadBalancerArn = $null
$loadBalancerDnsName = $null
$loadBalancerCanonicalHostedZoneId = $null

function Get-EcsServiceState {
    param(
        [string]$RegionValue,
        [string]$ClusterValue,
        [string]$ServiceValue
    )

    $raw = aws ecs describe-services --region $RegionValue --cluster $ClusterValue --services $ServiceValue --output json
    if (-not $raw) {
        throw "Failed to describe ECS service."
    }

    $parsed = $raw | ConvertFrom-Json
    if (-not $parsed.services -or $parsed.services.Count -eq 0) {
        throw "ECS service '$ServiceValue' was not found in cluster '$ClusterValue'."
    }

    return $parsed.services[0]
}

function Write-EcsServiceSummary {
    param(
        $ServiceState
    )

    $primaryDeployment = $ServiceState.deployments | Where-Object { $_.status -eq "PRIMARY" } | Select-Object -First 1
    $activeDeploymentCount = @($ServiceState.deployments | Where-Object { $_.status -ne "INACTIVE" }).Count
    $rolloutState = if ($primaryDeployment) { $primaryDeployment.rolloutState } else { "unknown" }
    $rolloutReason = if ($primaryDeployment -and $primaryDeployment.rolloutStateReason) { $primaryDeployment.rolloutStateReason } else { "" }

    Write-Host ("ECS state: running={0} desired={1} pending={2} activeDeployments={3} rollout={4}" -f `
        $ServiceState.runningCount,
        $ServiceState.desiredCount,
        $ServiceState.pendingCount,
        $activeDeploymentCount,
        $rolloutState)

    if ($rolloutReason) {
        Write-Host ("Primary rollout reason: {0}" -f $rolloutReason)
    }
}

function Write-EcsServiceEvents {
    param(
        $ServiceState,
        [int]$MaxEvents = 5
    )

    Write-Host "Recent ECS service events:"
    $ServiceState.events | Select-Object -First $MaxEvents | ForEach-Object {
        Write-Host ("- {0}" -f $_.message)
    }
}

function Set-ContainerEnvironmentValue {
    param(
        $Container,
        [string]$Name,
        [string]$Value
    )

    $entry = $Container.environment | Where-Object { $_.name -eq $Name } | Select-Object -First 1
    if (-not $entry) {
        throw "Container '$($Container.name)' is missing environment variable '$Name' in the task definition."
    }

    $entry.value = $Value
}

function ConvertTo-AwsCsv {
    param([string[]]$Values)
    return ($Values | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() }) -join ","
}

function Get-SubnetVpcId {
    param(
        [string]$RegionValue,
        [string]$SubnetId
    )

    $vpcId = aws ec2 describe-subnets `
        --region $RegionValue `
        --subnet-ids $SubnetId `
        --query "Subnets[0].VpcId" `
        --output text

    if (-not $vpcId -or $vpcId -eq "None") {
        throw "Could not resolve VPC for subnet '$SubnetId'."
    }

    return $vpcId
}

function Get-HostedZoneIdForDomain {
    param(
        [string]$DomainNameValue,
        [string]$ProvidedHostedZoneId
    )

    if ($ProvidedHostedZoneId) {
        return $ProvidedHostedZoneId
    }

    $labels = $DomainNameValue.TrimEnd(".").Split(".")
    for ($index = 1; $index -lt $labels.Count; $index++) {
        $zoneName = (($labels[$index..($labels.Count - 1)]) -join ".")
        $zoneDnsName = "$zoneName."
        $zoneId = aws route53 list-hosted-zones-by-name `
            --dns-name $zoneDnsName `
            --query "HostedZones[?Name=='$zoneDnsName'] | [0].Id" `
            --output text

        if ($zoneId -and $zoneId -ne "None") {
            return $zoneId.Replace("/hostedzone/", "")
        }
    }

    throw "Could not find a Route 53 hosted zone for '$DomainNameValue'. Pass -HostedZoneId explicitly."
}

function Get-PublicSubnetIdsForVpc {
    param(
        [string]$RegionValue,
        [string]$VpcId
    )

    $raw = aws ec2 describe-subnets `
        --region $RegionValue `
        --filters "Name=vpc-id,Values=$VpcId" "Name=state,Values=available" `
        --output json

    $subnets = @((($raw | ConvertFrom-Json).Subnets) | Where-Object { $_.MapPublicIpOnLaunch -eq $true })
    $selected = @()
    foreach ($subnet in ($subnets | Sort-Object AvailabilityZone)) {
        if (($selected | Where-Object { $_.AvailabilityZone -eq $subnet.AvailabilityZone }).Count -eq 0) {
            $selected += $subnet
        }
    }

    $ids = @($selected | Select-Object -First 2 | ForEach-Object { $_.SubnetId })
    if ($ids.Count -lt 2) {
        throw "Could not auto-discover at least two public subnets in VPC '$VpcId'. Pass -AlbSubnetIds explicitly."
    }

    return $ids
}

function Ensure-SecurityGroup {
    param(
        [string]$RegionValue,
        [string]$VpcId,
        [string]$GroupName,
        [string]$Description
    )

    $existing = aws ec2 describe-security-groups `
        --region $RegionValue `
        --filters "Name=vpc-id,Values=$VpcId" "Name=group-name,Values=$GroupName" `
        --query "SecurityGroups[0].GroupId" `
        --output text

    if ($existing -and $existing -ne "None") {
        return $existing
    }

    return aws ec2 create-security-group `
        --region $RegionValue `
        --vpc-id $VpcId `
        --group-name $GroupName `
        --description $Description `
        --query "GroupId" `
        --output text
}

function Add-SecurityGroupIngressIfMissing {
    param(
        [string]$RegionValue,
        [string]$GroupId,
        [string]$Protocol,
        [int]$Port,
        [string]$CidrIp = "",
        [string]$SourceSecurityGroupId = ""
    )

    $args = @(
        "ec2", "authorize-security-group-ingress",
        "--region", $RegionValue,
        "--group-id", $GroupId,
        "--protocol", $Protocol,
        "--port", "$Port"
    )

    if ($CidrIp) {
        $args += @("--cidr", $CidrIp)
    }
    elseif ($SourceSecurityGroupId) {
        $args += @("--source-group", $SourceSecurityGroupId)
    }
    else {
        throw "Ingress rule must specify either -CidrIp or -SourceSecurityGroupId."
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & aws @args 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    $outputText = $output -join "`n"
    if ($exitCode -ne 0 -and $outputText -notmatch "InvalidPermission\.Duplicate") {
        throw $outputText
    }
}

function Resolve-CertificateArn {
    param(
        [string]$RegionValue,
        [string]$DomainNameValue,
        [string]$ProvidedCertificateArn
    )

    if ($ProvidedCertificateArn) {
        return $ProvidedCertificateArn
    }

    Write-Host "Looking up issued ACM certificate for $DomainNameValue..."
    $certArn = aws acm list-certificates `
        --region $RegionValue `
        --certificate-statuses ISSUED `
        --query "CertificateSummaryList[?DomainName=='$DomainNameValue'].CertificateArn | [0]" `
        --output text

    if (-not $certArn -or $certArn -eq "None") {
        throw "No issued ACM certificate found for '$DomainNameValue' in region '$RegionValue'. Pass -CertificateArn if the certificate uses a wildcard or another primary name."
    }

    return $certArn
}

function Ensure-TargetGroup {
    param(
        [string]$RegionValue,
        [string]$Name,
        [string]$VpcId
    )

    Write-Host "Ensuring target group: $Name"
    $existingArn = aws elbv2 describe-target-groups `
        --region $RegionValue `
        --query "TargetGroups[?TargetGroupName=='$Name'].TargetGroupArn | [0]" `
        --output text

    if ($existingArn -and $existingArn -ne "None") {
        aws elbv2 modify-target-group `
            --region $RegionValue `
            --target-group-arn $existingArn `
            --health-check-protocol HTTP `
            --health-check-path /nginx-health `
            --matcher HttpCode=200 | Out-Null
        return $existingArn
    }

    return aws elbv2 create-target-group `
        --region $RegionValue `
        --name $Name `
        --protocol HTTP `
        --port 80 `
        --target-type ip `
        --vpc-id $VpcId `
        --health-check-protocol HTTP `
        --health-check-path /nginx-health `
        --matcher HttpCode=200 `
        --query "TargetGroups[0].TargetGroupArn" `
        --output text
}

function Ensure-LoadBalancer {
    param(
        [string]$RegionValue,
        [string]$Name,
        [string[]]$SubnetIds,
        [string[]]$SecurityGroupIds
    )

    Write-Host "Ensuring ALB: $Name"
    $resolvedSubnets = @($SubnetIds | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() })
    $resolvedSecurityGroups = @($SecurityGroupIds | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() })
    if ($resolvedSubnets.Count -lt 2) {
        throw "Pass at least two public subnet IDs with -AlbSubnetIds when using HTTPS ALB deployment."
    }
    if ($resolvedSecurityGroups.Count -lt 1) {
        throw "Pass an ALB security group ID with -AlbSecurityGroupIds when using HTTPS ALB deployment."
    }

    $existing = aws elbv2 describe-load-balancers `
        --region $RegionValue `
        --query "LoadBalancers[?LoadBalancerName=='$Name'] | [0]" `
        --output json

    if ($existing -and $existing -ne "null") {
        $parsed = $existing | ConvertFrom-Json
        $setSubnetArgs = @(
            "elbv2", "set-subnets",
            "--region", $RegionValue,
            "--load-balancer-arn", $parsed.LoadBalancerArn,
            "--subnets"
        ) + $resolvedSubnets
        & aws @setSubnetArgs | Out-Null

        $setSecurityGroupArgs = @(
            "elbv2", "set-security-groups",
            "--region", $RegionValue,
            "--load-balancer-arn", $parsed.LoadBalancerArn,
            "--security-groups"
        ) + $resolvedSecurityGroups
        & aws @setSecurityGroupArgs | Out-Null

        return $parsed
    }

    $createArgs = @(
        "elbv2", "create-load-balancer",
        "--region", $RegionValue,
        "--name", $Name,
        "--type", "application",
        "--scheme", "internet-facing",
        "--ip-address-type", "ipv4",
        "--subnets"
    ) + $resolvedSubnets + @(
        "--security-groups"
    ) + $resolvedSecurityGroups + @(
        "--output", "json"
    )
    $created = & aws @createArgs

    return ($created | ConvertFrom-Json).LoadBalancers[0]
}

function Ensure-HttpsListeners {
    param(
        [string]$RegionValue,
        [string]$LoadBalancerArnValue,
        [string]$TargetGroupArnValue,
        [string]$CertificateArnValue
    )

    Write-Host "Ensuring ALB listeners..."
    $listeners = aws elbv2 describe-listeners `
        --region $RegionValue `
        --load-balancer-arn $LoadBalancerArnValue `
        --output json | ConvertFrom-Json

    $httpsListener = @($listeners.Listeners | Where-Object { $_.Port -eq 443 }) | Select-Object -First 1
    if (-not $httpsListener) {
        aws elbv2 create-listener `
            --region $RegionValue `
            --load-balancer-arn $LoadBalancerArnValue `
            --protocol HTTPS `
            --port 443 `
            --certificates CertificateArn=$CertificateArnValue `
            --default-actions Type=forward,TargetGroupArn=$TargetGroupArnValue | Out-Null
    }

    $httpListener = @($listeners.Listeners | Where-Object { $_.Port -eq 80 }) | Select-Object -First 1
    if (-not $httpListener) {
        aws elbv2 create-listener `
            --region $RegionValue `
            --load-balancer-arn $LoadBalancerArnValue `
            --protocol HTTP `
            --port 80 `
            --default-actions "Type=redirect,RedirectConfig={Protocol=HTTPS,Port=443,StatusCode=HTTP_301}" | Out-Null
    }
}

function Update-Route53Alias {
    param(
        [string]$HostedZoneIdValue,
        [string]$DomainNameValue,
        [string]$AlbDnsName,
        [string]$AlbHostedZoneId
    )

    if (-not $HostedZoneIdValue) {
        throw "Pass -HostedZoneId when using -UpdateRoute53."
    }

    $changeBatch = @{
        Comment = "Point $DomainNameValue to ECS ALB"
        Changes = @(
            @{
                Action = "UPSERT"
                ResourceRecordSet = @{
                    Name = $DomainNameValue
                    Type = "A"
                    AliasTarget = @{
                        HostedZoneId = $AlbHostedZoneId
                        DNSName = $AlbDnsName
                        EvaluateTargetHealth = $true
                    }
                }
            }
        )
    }

    $tempPath = Join-Path ([System.IO.Path]::GetTempPath()) ("route53-change-{0}.json" -f ([System.Guid]::NewGuid().ToString("N")))
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tempPath, ($changeBatch | ConvertTo-Json -Depth 20), $utf8NoBom)
    try {
        aws route53 change-resource-record-sets `
            --hosted-zone-id $HostedZoneIdValue `
            --change-batch "file://$tempPath" | Out-Null
    }
    finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force
        }
    }
}

function New-ResolvedTaskDefinition {
    param(
        [string]$TemplatePath,
        [string]$BackendImageValue,
        [string]$NginxImageValue,
        [string]$PostgresImageValue
    )

    if (-not (Test-Path -LiteralPath $TemplatePath)) {
        throw "Task definition template '$TemplatePath' was not found."
    }

    $taskDefinition = Get-Content -LiteralPath $TemplatePath -Raw | ConvertFrom-Json
    $containers = @($taskDefinition.containerDefinitions)

    $appContainer = $containers | Where-Object { $_.name -eq "app" } | Select-Object -First 1
    $nginxContainer = $containers | Where-Object { $_.name -eq "nginx" } | Select-Object -First 1
    $postgresContainer = $containers | Where-Object { $_.name -eq "postgres" } | Select-Object -First 1

    if (-not $appContainer -or -not $nginxContainer -or -not $postgresContainer) {
        throw "Task definition must include 'app', 'nginx', and 'postgres' containers."
    }

    $appContainer.image = $BackendImageValue
    $nginxContainer.image = $NginxImageValue
    $postgresContainer.image = $PostgresImageValue

    $tempPath = Join-Path ([System.IO.Path]::GetTempPath()) ("ecs-task-definition-{0}.json" -f ([System.Guid]::NewGuid().ToString("N")))
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tempPath, ($taskDefinition | ConvertTo-Json -Depth 100), $utf8NoBom)
    return $tempPath
}

Write-Host "Using region: $Region"
Write-Host "Using ECS cluster: $Cluster"
Write-Host "Using ECS service: $Service"

if (-not $SkipBuild) {
    Write-Host "Building Docker images..."
    docker build -f backend/Dockerfile -t rag-backend:latest backend
    docker build -f backend/nginx/Dockerfile -t rag-nginx:latest backend/nginx
    docker build -f backend/postgres/Dockerfile -t rag-postgres:latest backend
}

if (-not $SkipPush) {
    Write-Host "Logging Docker into ECR..."
    aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin "$AccountId.dkr.ecr.$Region.amazonaws.com"

    Write-Host "Tagging images for ECR..."
    docker tag rag-backend:latest $backendImage
    docker tag rag-nginx:latest $nginxImage
    docker tag rag-postgres:latest $postgresImage

    Write-Host "Pushing images to ECR..."
    docker push $backendImage
    docker push $nginxImage
    docker push $postgresImage
}

if (-not $SkipRegister) {
    $resolvedTaskDefinitionPath = New-ResolvedTaskDefinition `
        -TemplatePath $TaskDefinitionPath `
        -BackendImageValue $backendImage `
        -NginxImageValue $nginxImage `
        -PostgresImageValue $postgresImage

    Write-Host "Registering new ECS task definition revision..."
    $taskDefinitionArn = aws ecs register-task-definition --region $Region --cli-input-json "file://$resolvedTaskDefinitionPath" --query "taskDefinition.taskDefinitionArn" --output text
    if (-not $taskDefinitionArn) {
        throw "Failed to register task definition."
    }
    Write-Host "Registered task definition: $taskDefinitionArn"
}

if ($EnableHttpsAlb) {
    $serviceStateForAlb = Get-EcsServiceState -RegionValue $Region -ClusterValue $Cluster -ServiceValue $Service
    $serviceSubnets = @($serviceStateForAlb.networkConfiguration.awsvpcConfiguration.subnets)
    if ($serviceSubnets.Count -lt 1) {
        throw "Could not resolve subnets from ECS service '$Service'. Pass -AlbSubnetIds explicitly."
    }

    $vpcId = Get-SubnetVpcId -RegionValue $Region -SubnetId $serviceSubnets[0]
    if ($AlbSubnetIds.Count -lt 2) {
        Write-Host "Auto-discovering public ALB subnets in VPC $vpcId..."
        $AlbSubnetIds = @(Get-PublicSubnetIdsForVpc -RegionValue $Region -VpcId $vpcId)
    }
    $AlbSubnetIds = @($serviceSubnets + $AlbSubnetIds | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique)
    if ($AlbSubnetIds.Count -lt 2) {
        throw "HTTPS ALB deployment needs at least two subnets. Add another public subnet in the ECS VPC."
    }

    if ($AlbSecurityGroupIds.Count -lt 1) {
        Write-Host "Creating or reusing ALB security group..."
        $albSecurityGroupId = Ensure-SecurityGroup `
            -RegionValue $Region `
            -VpcId $vpcId `
            -GroupName "backend-rag-alb-sg" `
            -Description "HTTPS ALB access for backend-rag-multipurpose"
        Add-SecurityGroupIngressIfMissing -RegionValue $Region -GroupId $albSecurityGroupId -Protocol tcp -Port 443 -CidrIp "0.0.0.0/0"
        Add-SecurityGroupIngressIfMissing -RegionValue $Region -GroupId $albSecurityGroupId -Protocol tcp -Port 80 -CidrIp "0.0.0.0/0"
        $AlbSecurityGroupIds = @($albSecurityGroupId)
    }

    if ($EcsSecurityGroupIds.Count -lt 1) {
        Write-Host "Creating or reusing ECS task security group for ALB traffic..."
        $ecsSecurityGroupId = Ensure-SecurityGroup `
            -RegionValue $Region `
            -VpcId $vpcId `
            -GroupName "backend-rag-ecs-task-sg" `
            -Description "ECS task access from backend-rag ALB"
        Add-SecurityGroupIngressIfMissing `
            -RegionValue $Region `
            -GroupId $ecsSecurityGroupId `
            -Protocol tcp `
            -Port 80 `
            -SourceSecurityGroupId $AlbSecurityGroupIds[0]
        $EcsSecurityGroupIds = @($ecsSecurityGroupId)
    }

    $CertificateArn = Resolve-CertificateArn `
        -RegionValue $Region `
        -DomainNameValue $DomainName `
        -ProvidedCertificateArn $CertificateArn

    $targetGroupArn = Ensure-TargetGroup `
        -RegionValue $Region `
        -Name $TargetGroupName `
        -VpcId $vpcId

    $loadBalancer = Ensure-LoadBalancer `
        -RegionValue $Region `
        -Name $AlbName `
        -SubnetIds $AlbSubnetIds `
        -SecurityGroupIds $AlbSecurityGroupIds

    $loadBalancerArn = $loadBalancer.LoadBalancerArn
    $loadBalancerDnsName = $loadBalancer.DNSName
    $loadBalancerCanonicalHostedZoneId = $loadBalancer.CanonicalHostedZoneId

    Ensure-HttpsListeners `
        -RegionValue $Region `
        -LoadBalancerArnValue $loadBalancerArn `
        -TargetGroupArnValue $targetGroupArn `
        -CertificateArnValue $CertificateArn

    if ($UpdateRoute53) {
        Write-Host "Updating Route 53 alias for $DomainName..."
        $HostedZoneId = Get-HostedZoneIdForDomain `
            -DomainNameValue $DomainName `
            -ProvidedHostedZoneId $HostedZoneId
        Update-Route53Alias `
            -HostedZoneIdValue $HostedZoneId `
            -DomainNameValue $DomainName `
            -AlbDnsName $loadBalancerDnsName `
            -AlbHostedZoneId $loadBalancerCanonicalHostedZoneId
    }

    Write-Host "HTTPS ALB ready: https://$DomainName -> $loadBalancerDnsName"
}

if (-not $SkipUpdate) {
    if (-not $taskDefinitionArn) {
        if ($SkipRegister) {
            throw "Task definition ARN is unavailable because registration was skipped. Remove -SkipRegister or provide a task definition ARN manually."
        }
    }

    Write-Host "Updating ECS service..."
    $updateArgs = @(
        "ecs", "update-service",
        "--region", $Region,
        "--cluster", $Cluster,
        "--service", $Service,
        "--task-definition", $taskDefinitionArn,
        "--desired-count", "$DesiredCount",
        "--force-new-deployment"
    )

    if ($EnableHttpsAlb) {
        $updateArgs += @(
            "--load-balancers",
            "targetGroupArn=$targetGroupArn,containerName=nginx,containerPort=80"
        )
    }

    if ($EnableHttpsAlb) {
        $serviceStateForNetwork = Get-EcsServiceState -RegionValue $Region -ClusterValue $Cluster -ServiceValue $Service
        $awsvpcConfig = $serviceStateForNetwork.networkConfiguration.awsvpcConfiguration
        $resolvedEcsSubnetIds = if ($EcsSubnetIds.Count -gt 0) { $EcsSubnetIds } else { @($awsvpcConfig.subnets) }
        $resolvedEcsSecurityGroupIds = if ($EcsSecurityGroupIds.Count -gt 0) { $EcsSecurityGroupIds } else { @($awsvpcConfig.securityGroups) }
        $ecsSubnetCsv = ConvertTo-AwsCsv -Values $resolvedEcsSubnetIds
        $ecsSecurityGroupCsv = ConvertTo-AwsCsv -Values $resolvedEcsSecurityGroupIds
        $assignPublicIpValue = if ($DisableTaskPublicIp) { "DISABLED" } else { "ENABLED" }

        if (-not $ecsSubnetCsv -or -not $ecsSecurityGroupCsv) {
            throw "Could not resolve ECS service subnets/security groups. Pass -EcsSubnetIds and -EcsSecurityGroupIds explicitly."
        }

        $updateArgs += @(
            "--network-configuration",
            "awsvpcConfiguration={subnets=[$ecsSubnetCsv],securityGroups=[$ecsSecurityGroupCsv],assignPublicIp=$assignPublicIpValue}"
        )
    }

    & aws @updateArgs | Out-Null

    Write-Host "Waiting for service stability..."
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ($true) {
        $serviceState = Get-EcsServiceState -RegionValue $Region -ClusterValue $Cluster -ServiceValue $Service
        Write-EcsServiceSummary -ServiceState $serviceState

        $primaryDeployment = $serviceState.deployments | Where-Object { $_.status -eq "PRIMARY" } | Select-Object -First 1
        $activeDeployments = @($serviceState.deployments | Where-Object { $_.status -ne "INACTIVE" })
        $isStable = (
            $serviceState.runningCount -eq $DesiredCount -and
            $serviceState.pendingCount -eq 0 -and
            $activeDeployments.Count -eq 1 -and
            $primaryDeployment -and
            $primaryDeployment.rolloutState -eq "COMPLETED"
        )

        if ($isStable) {
            break
        }

        if ((Get-Date) -ge $deadline) {
            Write-Host "Timed out waiting for ECS service stability."
            Write-EcsServiceEvents -ServiceState $serviceState
            throw "ECS service did not become stable within $TimeoutMinutes minutes."
        }

        Start-Sleep -Seconds $PollSeconds
    }

    Write-Host "ECS service updated successfully."
}

if ($resolvedTaskDefinitionPath -and (Test-Path -LiteralPath $resolvedTaskDefinitionPath)) {
    Remove-Item -LiteralPath $resolvedTaskDefinitionPath -Force
}


<#
.SYNOPSIS
    Let staff computers on the local network reach Reel's dashboard (T-1008, D-060).

.DESCRIPTION
    allow   adds an inbound Windows Firewall rule for Reel's port (TCP), only for private
            networks (home or work), never for public ones (cafés, airports).
    remove  deletes that rule.
    status  shows the rule, this computer's network profile, and firewall rules that
            block Python (made when someone pressed "Cancel" in the Windows Firewall prompt).

    "allow" and "remove" need administrator rights: start PowerShell with
    "Run as administrator". The dashboard must also use HTTPS: see docs\setup\network.md.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\network.ps1 allow
    powershell -ExecutionPolicy Bypass -File scripts\network.ps1 status
    powershell -ExecutionPolicy Bypass -File scripts\network.ps1 remove
#>
param(
    [ValidateSet("allow", "remove", "status")]
    [string]$Action = "status",
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [string]$RuleName = "Reel dashboard"
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal $identity
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-PythonBlocks {
    Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True -ErrorAction SilentlyContinue |
        Where-Object { ($_ | Get-NetFirewallApplicationFilter).Program -like "*\python*.exe" }
}

switch ($Action) {
    "allow" {
        if (-not (Test-Administrator)) {
            throw "Start PowerShell with 'Run as administrator' to change the firewall."
        }
        Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
        Write-Output "Allowed: TCP port $Port from private networks (rule '$RuleName')."
        $blocks = @(Get-PythonBlocks)
        if ($blocks.Count -gt 0) {
            Write-Output "WARNING: these rules block Python and win over the rule above:"
            $blocks | ForEach-Object { Write-Output "  $($_.DisplayName) [$($_.Profile)]" }
            Write-Output "Remove them in 'Windows Defender Firewall with Advanced Security' > Inbound Rules."
        }
    }
    "remove" {
        if (-not (Test-Administrator)) {
            throw "Start PowerShell with 'Run as administrator' to change the firewall."
        }
        $rules = @(Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)
        $rules | Remove-NetFirewallRule
        Write-Output "Removed $($rules.Count) rule(s) named '$RuleName'."
    }
    "status" {
        $rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
        if ($rule) {
            $ports = ($rule | Get-NetFirewallPortFilter).LocalPort -join ", "
            Write-Output "Rule '$RuleName': enabled=$($rule.Enabled), port(s) $ports, profile $($rule.Profile)"
        } else {
            Write-Output "Rule '$RuleName': none (run 'allow' as administrator)."
        }
        foreach ($network in Get-NetConnectionProfile) {
            Write-Output "Network '$($network.Name)' on $($network.InterfaceAlias): $($network.NetworkCategory)"
            if ($network.NetworkCategory -eq "Public") {
                Write-Output "  This network is Public, so the rule does not apply. If it is the school's or your own network, set it to Private: Settings > Network & internet > (the network) > Network profile type."
            }
        }
        $blocks = @(Get-PythonBlocks)
        foreach ($block in $blocks) {
            Write-Output "Blocks Python: $($block.DisplayName) [$($block.Profile)]"
        }
    }
}

<#
.SYNOPSIS
    Let staff computers on the local network reach Reel's dashboard (T-1008, D-060).

.DESCRIPTION
    allow   adds an inbound Windows Firewall rule for Reel's port (TCP). By default it applies
            only on private networks (home or work), and only to computers of the same
            subnet. With -Subnet (for example 192.168.32.0/22) it also applies on networks
            that Windows marks as public, such as a school's shared Wi-Fi, but only to
            computers of that address range. The network itself stays public.
    remove  deletes that rule.
    status  shows the rule, this computer's network profile, and firewall rules that
            block Python (made when someone pressed "Cancel" in the Windows Firewall prompt).

    "allow" and "remove" need administrator rights: start PowerShell with
    "Run as administrator". The dashboard must also use HTTPS: see docs\setup\network.md.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\network.ps1 allow
    powershell -ExecutionPolicy Bypass -File scripts\network.ps1 allow -Subnet 192.168.32.0/22
    powershell -ExecutionPolicy Bypass -File scripts\network.ps1 status
    powershell -ExecutionPolicy Bypass -File scripts\network.ps1 remove
#>
param(
    [ValidateSet("allow", "remove", "status")]
    [string]$Action = "status",
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [string]$RuleName = "Reel dashboard",
    [ValidatePattern('^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$|^$')]
    [string]$Subnet = ""
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
        if ($Subnet) {
            New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
                -Protocol TCP -LocalPort $Port -Profile Private, Public -RemoteAddress $Subnet |
                Out-Null
            Write-Output "Allowed: TCP port $Port from $Subnet only, on private and public networks (rule '$RuleName')."
        } else {
            New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
                -Protocol TCP -LocalPort $Port -Profile Private -RemoteAddress LocalSubnet |
                Out-Null
            Write-Output "Allowed: TCP port $Port from the local subnet of private networks (rule '$RuleName')."
        }
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
            $from = ($rule | Get-NetFirewallAddressFilter).RemoteAddress -join ", "
            Write-Output "Rule '$RuleName': enabled=$($rule.Enabled), port(s) $ports, from $from, profile $($rule.Profile)"
        } else {
            Write-Output "Rule '$RuleName': none (run 'allow' as administrator)."
        }
        foreach ($network in Get-NetConnectionProfile) {
            Write-Output "Network '$($network.Name)' on $($network.InterfaceAlias): $($network.NetworkCategory)"
            if ($network.NetworkCategory -eq "Public" -and $rule -and $rule.Profile -notmatch "Public|Any") {
                Write-Output "  This network is Public, so the rule does not apply here. Either run 'allow -Subnet <this network's range>' as administrator, or, on your own network, set it to Private: Settings > Network & internet > (the network) > Network profile type."
            }
        }
        $blocks = @(Get-PythonBlocks)
        foreach ($block in $blocks) {
            Write-Output "Blocks Python: $($block.DisplayName) [$($block.Profile)]"
        }
    }
}

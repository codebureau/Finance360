<#
.SYNOPSIS
  Pull the Finance360 Backups from OneDrive into a local desktop restic repo (ADR-0011).

.DESCRIPTION
  Run by Windows Task Scheduler outside the app: daily, and at logon to catch up. It does
  `restic copy --from-repo <remote>` into a local repo, so the desktop holds a full, independent,
  verifiable copy. It needs restic and rclone on PATH, an rclone remote of the same name as the
  host's, and the Backup key in a file only you can read. Nothing here is logged except
  progress; the key is passed to restic as a file path, never as text.

  Setup: docs/backup-setup.md, section "The desktop copy".

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup-pull.ps1 `
    -KeyFile C:\Users\me\finance360-backup-key.txt -Remote rclone:onedrive:Finance360 `
    -Repo D:\Finance360-backups
#>
param(
  [Parameter(Mandatory)] [string] $KeyFile,
  [Parameter(Mandatory)] [string] $Remote,
  [Parameter(Mandatory)] [string] $Repo,
  [string] $LogFile = (Join-Path (Split-Path -Parent $Repo) 'backup-pull.log')
)

$ErrorActionPreference = 'Stop'

function Log([string] $message) {
  $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $message
  Add-Content -Path $LogFile -Value $line
  Write-Output $line
}

function Restic {
  # Never echo arguments or environment: only the outcome is logged.
  & restic @args
  if ($LASTEXITCODE -ne 0) { throw "restic $($args[0]) failed (exit $LASTEXITCODE)" }
}

try {
  if (-not (Test-Path -LiteralPath $KeyFile)) { throw "Key file not found: $KeyFile" }
  # The key goes to restic as a file path in the process environment, for both repos.
  $env:RESTIC_PASSWORD_FILE = $KeyFile
  $env:RESTIC_FROM_PASSWORD_FILE = $KeyFile

  & restic -r $Repo cat config *> $null
  if ($LASTEXITCODE -ne 0) {
    Log "Creating the desktop repo at $Repo"
    Restic -r $Repo init --from-repo $Remote --copy-chunker-params
  }
  Log "Copying new snapshots from $Remote"
  Restic -r $Repo copy --from-repo $Remote
  Restic -r $Repo snapshots --latest 1 --compact
  Log 'Done.'
}
catch {
  Log "FAILED: $($_.Exception.Message)"
  exit 1
}
finally {
  Remove-Item Env:RESTIC_PASSWORD_FILE, Env:RESTIC_FROM_PASSWORD_FILE -ErrorAction SilentlyContinue
}

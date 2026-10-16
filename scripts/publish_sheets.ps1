# Requires Windows PowerShell 5.1+ or PowerShell 7. No profile changes required.
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$InputPath,
    [string]$RunId,
    [string]$ConfigPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Preserve nonzero native exit codes even when the caller enables this PS7 option.
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = Split-Path -Parent $PSScriptRoot
$exitCode = 1
$problem = 'Launcher failed; no automatic retry. Check configuration and Python installation.'
$savedEnvironment = @{}
$environmentNames = @('GOOGLE_SPREADSHEET_ID', 'GOOGLE_ALLOWED_SPREADSHEET_ID', 'GOOGLE_CREDENTIALS_FILE')

function Get-ExternalFile([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) { throw 'Absolute path required.' }
    if ([IO.Path]::DirectorySeparatorChar -eq '\' -and
        $Path -notmatch '\A(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\)') {
        throw 'Fully qualified Windows path required.'
    }
    $file = Get-Item -LiteralPath $Path -Force
    if ($file -isnot [IO.FileInfo]) { throw 'Regular file required.' }
    $prefix = $repoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($file.FullName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'File must be outside repository.'
    }
    # Reject links/junctions in every component to prevent an inside-repo alias.
    $component = $file
    while ($null -ne $component) {
        if ($component.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Linked paths are not supported.'
        }
        if ($component -is [IO.FileInfo]) {
            $component = $component.Directory
        } else {
            $component = $component.Parent
        }
    }
    return [IO.Path]::GetFullPath($Path)
}

try {
    if ([string]::IsNullOrWhiteSpace($InputPath)) {
        $InputPath = Join-Path $repoRoot 'src/taostats_pipeline/fixtures'
    } elseif (-not [IO.Path]::IsPathRooted($InputPath)) {
        $InputPath = Join-Path $repoRoot $InputPath
    }
    $problem = 'InputPath must name an existing local input directory.'
    $inputDirectory = Get-Item -LiteralPath $InputPath
    if (-not $inputDirectory.PSIsContainer) { throw 'Invalid input.' }
    $pythonArguments = @('-m', 'taostats_pipeline', 'publish-sheets', '--input', $inputDirectory.FullName)

    $problem = 'RunId must contain 1-128 ASCII letters, digits, underscores or hyphens; required for Apply.'
    if (($Apply -or $RunId) -and $RunId -cnotmatch '\A[A-Za-z0-9_-]{1,128}\z') {
        throw 'Invalid RunId.'
    }
    if ($RunId) { $pythonArguments += @('--run-id', $RunId) }

    if ($Apply) {
        $problem = 'Apply requires ConfigPath: an absolute regular JSON file outside the repository, without links.'
        if ([string]::IsNullOrWhiteSpace($ConfigPath)) { throw 'Missing configuration.' }
        $configFile = Get-ExternalFile $ConfigPath
        $problem = 'Invalid configuration: expected only nonempty string fields spreadsheet_id and credentials_file.'
        $json = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8
        # Validate the deliberately flat, string-only schema BEFORE ConvertFrom-Json:
        # PS 5.1 and 7 can silently collapse duplicate properties differently.
        $jsonString = '"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
        $property = '(?<name>' + $jsonString + ')\s*:\s*' + $jsonString
        $shape = '\A\s*\{\s*' + $property + '\s*,\s*' + $property + '\s*\}\s*\z'
        $match = [regex]::Match($json, $shape)
        if (-not $match.Success) { throw 'Expected exactly two string properties.' }
        $rawNames = @($match.Groups['name'].Captures | ForEach-Object {
            (ConvertFrom-Json ('{"name":' + $_.Value + '}')).name
        })
        if ($rawNames[0] -ceq $rawNames[1] -or
            'spreadsheet_id' -cnotin $rawNames -or 'credentials_file' -cnotin $rawNames) {
            throw 'Duplicate or unexpected property.'
        }
        $config = ConvertFrom-Json $json
        $names = @($config.PSObject.Properties.Name)
        if ($names.Count -ne 2 -or 'spreadsheet_id' -notin $names -or 'credentials_file' -notin $names) {
            throw 'Invalid fields.'
        }
        if ($config.spreadsheet_id -isnot [string] -or
            $config.spreadsheet_id -cnotmatch '\A[A-Za-z0-9_-]{20,}\z' -or
            $config.credentials_file -isnot [string] -or
            [string]::IsNullOrWhiteSpace($config.credentials_file)) { throw 'Invalid values.' }

        $problem = 'Spreadsheet allowlist mismatch: independently set GOOGLE_ALLOWED_SPREADSHEET_ID before Apply.'
        $allowedId = [Environment]::GetEnvironmentVariable('GOOGLE_ALLOWED_SPREADSHEET_ID', 'Process')
        if ([string]::IsNullOrWhiteSpace($allowedId) -or $config.spreadsheet_id -cne $allowedId) {
            throw 'Not allowed.'
        }
        $problem = 'Credentials file must exist at an absolute path outside the repository, without links.'
        $credentialsFile = Get-ExternalFile $config.credentials_file
        # Only file metadata is inspected. Key contents are left to the Python client.
    }

    $problem = 'Python launcher py is unavailable. Install Python with the Windows launcher, then install this package.'
    $python = Get-Command py -CommandType Application -ErrorAction Stop
    & $python.Source --version *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Python unavailable.' }

    if ($Apply) {
        $phrase = 'PUBLISH ' + $RunId
        Write-Host 'Apply will replace data in the independently allowlisted personal spreadsheet.'
        $problem = 'Publication cancelled: exact confirmation phrase was not entered. Python publish was not started.'
        $answer = Read-Host ('Type exactly: ' + $phrase)
        if ($answer -cne $phrase) { throw 'Confirmation refused.' }
        foreach ($name in $environmentNames) {
            $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        }
        [Environment]::SetEnvironmentVariable('GOOGLE_SPREADSHEET_ID', $config.spreadsheet_id, 'Process')
        [Environment]::SetEnvironmentVariable('GOOGLE_ALLOWED_SPREADSHEET_ID', $allowedId, 'Process')
        [Environment]::SetEnvironmentVariable('GOOGLE_CREDENTIALS_FILE', $credentialsFile, 'Process')
        $pythonArguments += '--apply'
    } else {
        $pythonArguments += '--dry-run'
    }
    $problem = 'Python invocation failed; outcome may be unresolved. Inspect run_status before any manual retry.'
    # Argument array: no command-string evaluation. Exactly one publication invocation.
    & $python.Source @pythonArguments
    $exitCode = $LASTEXITCODE
} catch {
    # Never print raw exceptions: they may contain config values or local paths.
    [Console]::Error.WriteLine($problem)
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
}
exit $exitCode

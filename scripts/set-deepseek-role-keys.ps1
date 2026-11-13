# Enter replacement keys locally. Never pass keys as command-line arguments.
$ErrorActionPreference = 'Stop'
$roles = @('SCANNER', 'REVIEWER', 'BUILDER')
$pending = @{}
$original = @{}
$written = @()
try {
    Write-Host 'Use NEW replacement keys. Input is hidden; do not paste keys into chat.'
    Write-Host 'Values will be saved as Windows USER environment variables (not encrypted storage).'
    foreach ($role in $roles) {
        $secure = Read-Host "$role replacement API key" -AsSecureString
        $ptr = [IntPtr]::Zero
        try {
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
            if ($value -notmatch '^sk-[A-Za-z0-9_-]{16,}$') { throw 'Invalid key format; no new keys saved.' }
            $name = "DEEPSEEK_${role}_API_KEY"
            $pending[$name] = $value
            $original[$name] = [Environment]::GetEnvironmentVariable($name, 'User')
            $value = $null
        } finally {
            if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
            $secure.Dispose()
        }
    }
    foreach ($name in $pending.Keys) {
        [Environment]::SetEnvironmentVariable($name, $pending[$name], 'User')
        $written += $name
    }
    Write-Host 'Three role keys saved locally. Restart Codex and terminal before testing.'
} catch {
    $rollbackFailed = $false
    foreach ($name in $written) {
        try { [Environment]::SetEnvironmentVariable($name, $original[$name], 'User') }
        catch { $rollbackFailed = $true }
    }
    if ($rollbackFailed) { Write-Error 'Save failed; rollback incomplete. Check USER environment variables locally.' }
    else { Write-Error 'Input or save failed. Previous USER values retained/restored. No key values printed.' }
    exit 1
} finally {
    $pending.Clear()
    $original.Clear()
    $value = $null
}

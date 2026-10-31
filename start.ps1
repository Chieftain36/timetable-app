$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Install and start Docker Desktop, then run this script again.'
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and wait until its engine is ready.' }
if (-not (Test-Path -LiteralPath '.env')) {
    $dbSecret = [guid]::NewGuid().ToString('N')
    $jwtSecret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $adminSecret = [guid]::NewGuid().ToString('N')
    @(
        "POSTGRES_PASSWORD=$dbSecret"
        "JWT_SECRET_KEY=$jwtSecret"
        'BOOTSTRAP_ADMIN_EMAIL=admin@example.com'
        "BOOTSTRAP_ADMIN_PASSWORD=$adminSecret"
    ) | Set-Content -LiteralPath '.env' -Encoding ASCII
}
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Run: docker compose logs' }
Write-Host 'Open http://localhost:8000 after the application has started.'
Write-Host 'Administrator credentials (also saved in .env):'
Get-Content -LiteralPath '.env' | Where-Object { $_ -match '^BOOTSTRAP_ADMIN_(EMAIL|PASSWORD)=' }
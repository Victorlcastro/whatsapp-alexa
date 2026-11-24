# Baixa a WuzAPI (github.com/asternic/wuzapi) e compila bin\wuzapi.exe para Windows.
# Usa o Go instalado; se não houver Go, usa Docker só para compilar.
$raiz = $PSScriptRoot
$src = Join-Path $raiz 'wuzapi'
$bin = Join-Path $raiz 'bin'

if (-not (Test-Path $src)) {
    git clone --depth 1 https://github.com/asternic/wuzapi.git $src
}
New-Item -ItemType Directory -Force $bin | Out-Null

if (Get-Command go -ErrorAction SilentlyContinue) {
    Push-Location $src
    $env:CGO_ENABLED = '0'; $env:GOOS = 'windows'; $env:GOARCH = 'amd64'
    go build -o (Join-Path $bin 'wuzapi.exe') .
    Pop-Location
} elseif (Get-Command docker -ErrorAction SilentlyContinue) {
    docker run --rm -v "${src}:/src" -w /src -e GOOS=windows -e GOARCH=amd64 -e CGO_ENABLED=0 `
        golang:1.26-bookworm go build -o /src/wuzapi.exe .
    Move-Item -Force (Join-Path $src 'wuzapi.exe') (Join-Path $bin 'wuzapi.exe')
} else {
    throw 'Instale o Go (winget install GoLang.Go) ou o Docker para compilar.'
}

Copy-Item -Recurse -Force (Join-Path $src 'static') $bin
Write-Host "Compilado: $(Join-Path $bin 'wuzapi.exe')"

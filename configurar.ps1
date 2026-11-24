# Primeira configuração: cria o .env com tokens aleatórios e instala as dependências Python.
$raiz = $PSScriptRoot
$env_ = Join-Path $raiz '.env'

function Novo-Token { -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) }) }

if (Test-Path $env_) {
    Write-Host '.env já existe, mantendo.'
} else {
    @(
        "WUZAPI_ADMIN_TOKEN=$(Novo-Token)"
        "WUZAPI_GLOBAL_ENCRYPTION_KEY=$(Novo-Token)"
        "WUZAPI_USER_TOKEN=$(Novo-Token)"
        '# Opcionais:'
        '# PONTE_TTS_PYTHON=C:\caminho\para\python.exe'
        '# PONTE_FALANDO_LOCK=C:\caminho\para\falando.lock'
        '# PONTE_VOZ=pt-BR-AntonioNeural'
    ) | Set-Content -Encoding utf8 $env_
    Write-Host '.env criado com tokens novos.'
}

python -m pip install -r (Join-Path $raiz 'requirements.txt')
Write-Host 'Pronto. Próximo passo: .\compilar-wuzapi.ps1'

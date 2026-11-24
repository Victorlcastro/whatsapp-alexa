# Cria um atalho na pasta Inicializar para a ponte abrir com o Windows (sem janela).
# A ponte, por sua vez, inicia o bin\wuzapi.exe.
$raiz = $PSScriptRoot
$pyw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Ponte WhatsApp Alexa.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = '"' + (Join-Path $raiz 'ponte\server.py') + '"'
$s.WorkingDirectory = Join-Path $raiz 'ponte'
$s.Save()
Write-Host "Atalho criado: $lnk"

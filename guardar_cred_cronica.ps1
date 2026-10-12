# Guarda usuario y clave de cronicadigital.org cifrados con DPAPI (solo este usuario en este PC).
# Queda fuera del repo, en %LOCALAPPDATA%\lecturas\cronica_cred.xml. Lo lee cronica_epub.py.
$dir = Join-Path $env:LOCALAPPDATA "lecturas"
New-Item -ItemType Directory -Force $dir | Out-Null
$cred = Get-Credential -Message "Usuario y clave de cronicadigital.org"
if ($null -eq $cred) { Write-Host "Cancelado, no se guardo nada."; exit 1 }
$cred | Export-Clixml (Join-Path $dir "cronica_cred.xml")
Write-Host "Listo: credencial guardada en $dir\cronica_cred.xml"

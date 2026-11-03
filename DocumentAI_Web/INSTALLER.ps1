param([string]$Projet = 'C:\Users\User\document-ai')
$ErrorActionPreference = 'Stop'
$cible = Join-Path $Projet 'DocumentAI_Web'
$fichiers = @('moteur\corroboration_ocr.py', 'moteur\extraction_vision.py', 'static\app.js', 'rapport_pdf.py')
foreach ($f in $fichiers) {
    if (!(Test-Path (Join-Path $cible $f))) { throw "Fichier existant introuvable : $f. Verifiez le chemin du projet." }
    if (!(Test-Path (Join-Path $PSScriptRoot $f))) { throw "Correctif incomplet : $f" }
}
$sauvegarde = Join-Path $Projet ('sauvegarde_correctif_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
foreach ($f in $fichiers) {
    $backup = Join-Path $sauvegarde $f
    New-Item -ItemType Directory -Force -Path (Split-Path $backup) | Out-Null
    Copy-Item -LiteralPath (Join-Path $cible $f) -Destination $backup
}
foreach ($f in $fichiers) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $f) -Destination (Join-Path $cible $f) -Force
}
Write-Host "Correctif installe. Sauvegarde : $sauvegarde"
Write-Host 'Relancez le serveur, rechargez avec Ctrl+F5 puis importez a nouveau la facture.'

$doc = (Get-ChildItem -Path $PSScriptRoot\.. -Filter '*.doc' | Select-Object -First 1).FullName
$out = Join-Path $PSScriptRoot '..\docs\contract_extract.txt'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$d = $word.Documents.Open($doc)
$d.SaveAs($out, 2)
$d.Close()
$word.Quit()
Write-Output "Saved: $out"

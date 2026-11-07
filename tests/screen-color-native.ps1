$ErrorActionPreference = 'Stop'
# Compile the real Windows worker, without launching a picker or reading pixels.
$source = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\src\screen-color.ps1') -Raw -Encoding UTF8
if ($source.Contains('CopyFromScreen') -or $source.Contains('BackgroundImage')) { throw 'Full-screen snapshot must not return' }
& ([scriptblock]::Create($source.Replace('[MusicScreenColor]::RunPicker()', '')))
$sample = [Func[uint32]]{ [uint32]0x563412 }
$unreadable = [Func[uint32]]{ throw 'Cancel must not sample the desktop' }
$selection = New-Object MusicColorSelection
if ($selection.Handle(0x200, $unreadable)) { throw 'Mouse movement must pass through' }
if (!$selection.Handle(0x201, $sample)) { throw 'Left click was not consumed' }
if ($selection.Color -ne '#123456' -or $selection.Complete) { throw 'Incorrect pixel or premature completion' }
if (!$selection.Handle(0x202, $unreadable) -or !$selection.Complete) { throw 'Matching release was not consumed' }
$cancel = New-Object MusicColorSelection
if (!$cancel.Handle(0x204, $unreadable)) { throw 'Right click was not consumed' }
if (!$cancel.Handle(0x205, $unreadable) -or !$cancel.Complete -or $cancel.Color) { throw 'Right click did not cancel' }
$form = New-Object MusicScreenColor
try {
    if ($form.Width -gt 400 -or $form.Height -gt 120) { throw 'Picker must be a small tool window' }
    if ($form.ShowInTaskbar -or $form.BackgroundImage) { throw 'Unexpected taskbar entry or screen image' }
} finally { $form.Dispose() }
Write-Output 'PASS: native worker compiles; pixel, click/release, cancel, small window; no capture or hooks installed'

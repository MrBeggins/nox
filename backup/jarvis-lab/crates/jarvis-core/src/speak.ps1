$ErrorActionPreference = 'Stop'
# Reads text from $env:JARVIS_TTS_FILE and speaks it via the modern Windows (WinRT)
# speech engine, which exposes voices like "Microsoft Pavel" (ru-RU male).
$txt = [System.IO.File]::ReadAllText($env:JARVIS_TTS_FILE, [System.Text.Encoding]::UTF8)
if ([string]::IsNullOrWhiteSpace($txt)) { exit 0 }

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
[void][Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]

# Helper to await WinRT IAsyncOperation<T> in Windows PowerShell 5.1
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($op, $t) {
    $m = $asTask.MakeGenericMethod($t)
    $task = $m.Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}

$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$vname = $env:JARVIS_TTS_VOICE
if ([string]::IsNullOrWhiteSpace($vname)) { $vname = 'Pavel' }
$all = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
$v = $all | Where-Object { $_.DisplayName -like "*$vname*" } | Select-Object -First 1
if (-not $v) { $v = $all | Where-Object { $_.Language -like 'ru*' } | Select-Object -First 1 }
if ($v) { $synth.Voice = $v }

$rate = 0.0
$ci = [System.Globalization.CultureInfo]::InvariantCulture
$ns = [System.Globalization.NumberStyles]::Float
[double]::TryParse($env:JARVIS_TTS_RATE, $ns, $ci, [ref]$rate) | Out-Null
if ($rate -ge 0.5 -and $rate -le 6.0) { $synth.Options.SpeakingRate = $rate }

$stream = Await ($synth.SynthesizeTextToStreamAsync($txt)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
$size = [uint32]$stream.Size
$reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
Await ($reader.LoadAsync($size)) ([uint32]) | Out-Null
$bytes = New-Object byte[] $size
$reader.ReadBytes($bytes)

$wav = [System.IO.Path]::ChangeExtension($env:JARVIS_TTS_FILE, '.wav')
[System.IO.File]::WriteAllBytes($wav, $bytes)

$player = New-Object System.Media.SoundPlayer $wav
$player.PlaySync()
Remove-Item $wav -ErrorAction SilentlyContinue

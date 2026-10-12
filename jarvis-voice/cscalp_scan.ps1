# Скан CScalp через UI Automation (только чтение): все стаканы — тикер, окно, прямоугольники.
# Один межпроцессный вызов на окно (CacheRequest c поддеревом), вывод — JSON в stdout.
param([string]$Out = '')
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$p = Get-Process | Where-Object { $_.ProcessName -like 'CryptoScalp*' } | Select-Object -First 1
if (-not $p) { '{"ok":false,"error":"CScalp не запущен"}'; exit }

$cr = New-Object System.Windows.Automation.CacheRequest
$cr.TreeScope = [System.Windows.Automation.TreeScope]::Subtree
$cr.TreeFilter = [System.Windows.Automation.Automation]::ControlViewCondition
$cr.AutomationElementMode = [System.Windows.Automation.AutomationElementMode]::None
foreach ($prop in @($AE::NameProperty, $AE::ClassNameProperty, $AE::BoundingRectangleProperty, $AE::AutomationIdProperty, $AE::NativeWindowHandleProperty)) { $cr.Add($prop) }

$doms = New-Object System.Collections.Generic.List[object]
$hidden = New-Object System.Collections.Generic.List[object]
function Rect4($e) { $r = $e.Cached.BoundingRectangle; if ($r.IsEmpty) { return $null }; return @([int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height) }
function HeaderText($tabItem) {
    foreach ($c in $tabItem.CachedChildren) {
        foreach ($t in $c.CachedChildren) { if ($t.Cached.ClassName -eq 'TextBlock' -and $t.Cached.Name) { return @($t.Cached.Name, (Rect4 $c)) } }
    }
    return @($null, $null)
}
function Walk($e, $winTitle, $hwnd) {
    foreach ($c in $e.CachedChildren) {
        if ($c.Cached.ClassName -eq 'TabItem' -and $c.Cached.Name -like '*LayoutDocument*') {
            $h = HeaderText $c
            $main = $null
            foreach ($x in $c.CachedChildren) { if ($x.Cached.ClassName -eq 'MainControl') { $main = $x } }
            if ($main) {
                $dom = $null
                foreach ($x in $main.CachedChildren) { if ($x.Cached.ClassName -eq 'DOMControl') { $dom = Rect4 $x } }
                $doms.Add([ordered]@{ ticker = $h[0]; window = $winTitle; hwnd = $hwnd; header = $h[1]; main = (Rect4 $main); dom = $dom })
            } elseif ($h[0]) {
                $hidden.Add([ordered]@{ ticker = $h[0]; window = $winTitle; hwnd = $hwnd; header = $h[1] })
            }
            continue
        }
        Walk $c $winTitle $hwnd
    }
}
$cond = New-Object System.Windows.Automation.PropertyCondition($AE::ProcessIdProperty, $p.Id)
$root = $AE::RootElement
$act = $cr.Activate()
try {
    $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
    foreach ($w in $wins) { Walk $w $w.Cached.Name ([int64]$w.Cached.NativeWindowHandle) }
} finally { $act.Dispose() }
$json = [ordered]@{ ok = $true; pid = $p.Id; doms = $doms; hidden = $hidden } | ConvertTo-Json -Depth 5 -Compress
if ($Out) { [IO.File]::WriteAllText($Out, $json, (New-Object Text.UTF8Encoding $false)) } else { $json }

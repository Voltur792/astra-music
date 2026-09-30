param(
    [switch]$Apply,
    [switch]$OnlyVkLibrary,
    [switch]$AllowRunning,
    [string]$ConfigPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $env:APPDATA 'astra\astra\config\commands.json'
}
$ConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Файл команд Astra не найден: $ConfigPath"
}

$commandJson = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
    $commands = @(ConvertFrom-Json -InputObject $commandJson -DateKind String)
} else {
    $commands = @(ConvertFrom-Json -InputObject $commandJson)
}
$now = [DateTime]::UtcNow.ToString('o')
$definitions = [System.Collections.Generic.List[object]]::new()
$definitions.Add([pscustomobject]@{ Name = 'Музыка — мои треки ВК'; Id = ''; Action = 'vk_my_music'; Level = 0; Phrases = @('включи мою музыку в вк', 'включи мою музыку в vk', 'включи мои треки в вк', 'включи мои треки вк', 'включи мою музыку вк', 'включи мою музыку в контакте', 'включи мою музыку вконтакте', 'астра включи мою музыку в вк', 'астра астра включи мою музыку в вк') })

$definitions.Add([pscustomobject]@{ Name = 'Пауза'; Id = '89355751-e9af-4ffa-a781-f6edc332d5df'; Action = 'pause'; Level = 0; Phrases = @('поставь паузу', 'пауза', 'астра пауза', 'астра, пауза', 'останови воспроизведение', 'pause') })
$definitions.Add([pscustomobject]@{ Name = 'Плей'; Id = '22c47d00-b012-42e7-bf92-32effc89ecc4'; Action = 'play'; Level = 0; Phrases = @('воспроизведи', 'сними паузу', 'плей', 'продолжи воспроизведение', 'продолжи музыку', 'play') })
$definitions.Add([pscustomobject]@{ Name = 'Следующий трек'; Id = 'cadbeb0e-edf1-49b4-9705-639fb3b5bcab'; Action = 'next'; Level = 0; Phrases = @('следующий трек', 'следующая песня', 'давай дальше', 'переключи трек') })
$definitions.Add([pscustomobject]@{ Name = 'Музыка — предыдущий трек'; Id = ''; Action = 'previous'; Level = 0; Phrases = @('предыдущий трек', 'прошлый трек', 'предыдущая песня', 'верни прошлый трек') })
$definitions.Add([pscustomobject]@{ Name = 'Музыка — стоп'; Id = ''; Action = 'stop'; Level = 0; Phrases = @('останови музыку', 'стоп музыка', 'выключи музыку') })
$definitions.Add([pscustomobject]@{ Name = 'Увеличить громкость'; Id = 'dffe74bd-a831-4edb-b61e-b2aeaf7d2d75'; Action = 'volume_up'; Level = 0; Phrases = @('увеличить громкость', 'увеличь громкость', 'сделай музыку громче', 'громче музыку') })
$definitions.Add([pscustomobject]@{ Name = 'Уменьшение громкости'; Id = 'c4b03b45-dd9e-495d-8c31-8d5545dbf26e'; Action = 'volume_down'; Level = 0; Phrases = @('уменьшить громкость', 'уменьши громкость', 'сделай музыку тише', 'тише музыку') })
$definitions.Add([pscustomobject]@{ Name = 'Максимальная громкость'; Id = 'a8f693af-be79-4366-86dd-272aae38d491'; Action = 'set_level'; Level = 10; Phrases = @('установить громкость на максимум', 'установи громкость на максимум', 'громкость на максимум') })
$definitions.Add([pscustomobject]@{ Name = 'Громкость минимум'; Id = 'c5f86181-caf6-478f-823d-624fd1c991d1'; Action = 'set_level'; Level = 1; Phrases = @('громкость минимум', 'громкость на минимум', 'минимальная громкость') })

$numberWords = @('один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять', 'десять')
for ($level = 1; $level -le 10; $level++) {
    $word = $numberWords[$level - 1]
    $definitions.Add([pscustomobject]@{
        Name = "Музыка — громкость $level"
        Id = ''
        Action = 'set_level'
        Level = $level
        Phrases = @("громкость $level", "громкость на $level", "поставь громкость $level", "установи громкость $level", "громкость $word", "громкость на $word")
    })
}

function New-MusicWorkflow {
    param([object]$Command, [object]$Action)

    $compactId = ([string]$Command.id).Replace('-', '')
    $triggerId = "node_music_${compactId}_trigger"
    $actionId = "node_music_${compactId}_action"
    $edgeId = "xy-edge__$triggerId-$actionId"
    $triggerConfig = [ordered]@{ exact_match = $true; phrases = @($Command.triggers[0].phrases) }
    $nodes = [ordered]@{}
    $nodes[$triggerId] = [ordered]@{
        data = [ordered]@{ config = $triggerConfig; trigger_type = 'text' }
        id = $triggerId; label = ''; node_type = 'trigger'; position_x = 355.0; position_y = 134.0
    }
    # Astra stores plugin actions differently in the graph and in the flat
    # actions array: the graph node uses the handler ID as action_type and
    # keeps only field values in config.
    $nodes[$actionId] = [ordered]@{
        data = [ordered]@{
            action = [ordered]@{ type = $Action.handler_id }
            action_type = $Action.handler_id
            config = $Action.params
        }
        id = $actionId; label = ''; node_type = 'action'; position_x = 355.0; position_y = 224.0
    }
    $edges = [ordered]@{}
    $edges[$edgeId] = [ordered]@{
        id = $edgeId; source_node = $triggerId; source_port = 'default'; target_node = $actionId; target_port = 'default'
    }
    return [ordered]@{ edges = $edges; nodes = $nodes }
}

$created = 0
$updated = 0
if ($OnlyVkLibrary) {
    $definitions = @($definitions | Where-Object { $_.Action -eq 'vk_my_music' })
}
foreach ($definition in $definitions) {
    $command = $null
    if ($definition.Id) {
        $command = $commands | Where-Object { $_.id -eq $definition.Id } | Select-Object -First 1
    }
    if (-not $command) {
        $command = $commands | Where-Object { $_.name -eq $definition.Name } | Select-Object -First 1
    }

    if (-not $command) {
        $command = [pscustomobject]@{
            actions = @(); created_at = $now; description = ''; editor_mode = 'graph'
            enabled = $true; execution = [pscustomobject]@{}; id = [Guid]::NewGuid().ToString()
            name = $definition.Name; slash_description = ''; slash_enabled = $false
            tags = @('astra-music-local'); triggers = @(); updated_at = $now
            workflow = [pscustomobject]@{ edges = [pscustomobject]@{}; nodes = [pscustomobject]@{} }
        }
        $commands += $command
        $created++
    } else {
        $updated++
    }

    $phrases = [System.Collections.Generic.List[string]]::new()
    foreach ($phrase in $definition.Phrases) {
        if (-not $phrases.Contains([string]$phrase)) { $phrases.Add([string]$phrase) }
    }
    foreach ($trigger in @($command.triggers)) {
        if ($trigger.type -ne 'text') { continue }
        foreach ($phrase in @($trigger.phrases)) {
            if (-not $phrases.Contains([string]$phrase)) { $phrases.Add([string]$phrase) }
        }
    }
    $command.triggers = @([pscustomobject]@{
        case_sensitive = $false; exact_match = $true; phrases = @($phrases); type = 'text'
    })

    # Astra drops saved field values from some command-graph plugin actions.
    # Each target action therefore encodes the operation in its handler ID.
    $method = if ($definition.Action -eq 'set_level') {
        "music_volume_$($definition.Level)"
    } else {
        "music_$($definition.Action)"
    }
    $handlerId = "plugin__music_controller__$method"
    $action = [ordered]@{ handler_id = $handlerId; params = [ordered]@{}; type = 'dynamic' }
    $command.actions = @($action)
    $command.workflow = New-MusicWorkflow -Command $command -Action $action
    $command.description = 'Управляет только плеером Astra Music напрямую, без запроса к ИИ.'
    $command.editor_mode = 'graph'
    $command.enabled = $true
    $command.updated_at = $now
}

if (-not $Apply) {
    Write-Output "Подготовлено: $updated существующих и $created новых команд. Для записи запустите с -Apply при закрытой Astra."
    return
}
if (-not $AllowRunning -and (Get-Process -Name Astra -ErrorAction SilentlyContinue)) {
    throw 'Закройте Astra перед записью commands.json: запущенное приложение может перезаписать изменения.'
}

$backup = "$ConfigPath.bak.music-controller.$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))"
Copy-Item -LiteralPath $ConfigPath -Destination $backup -ErrorAction Stop
$json = ConvertTo-Json -InputObject @($commands) -Depth 30
$tempPath = "$ConfigPath.music-controller.$([Guid]::NewGuid().ToString('N')).tmp"
[System.IO.File]::WriteAllText($tempPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::Replace($tempPath, $ConfigPath, $backup)
Write-Output "Записано: $updated существующих и $created новых команд Astra Music. Резервная копия: $backup"

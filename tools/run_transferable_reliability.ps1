param(
    [ValidateSet('all', 'train', 'validate')]
    [string]$Stage = 'all',
    [string]$Python = '',
    [string]$ValidationOut = 'results/runs/transferable_reliability_validation'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($Python) {
    $signalPython = $Python
} else {
    $pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCommand) {
        $signalPython = $pythonCommand.Source
    } else {
        $signalPython = 'C:\Users\James\AppData\Local\Programs\Python\Python311\python.exe'
        if (-not (Test-Path -LiteralPath $signalPython)) {
            throw 'Python was not found. Pass -Python with the installed Python executable path.'
        }
    }
}

function Invoke-SignalPython {
    param([string[]]$SignalArgs)
    & $signalPython -B -m transferable_signal @SignalArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit $LASTEXITCODE). Outputs and checkpoints are preserved."
    }
}

function Get-SignalFileHash {
    param([string]$Path)
    $stream = [System.IO.File]::OpenRead((Resolve-Path -LiteralPath $Path).Path)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-TrainingProtocol {
    param($Record, $Reference)
    foreach ($field in @('version', 'suite_sha256', 'batch_size', 'reward', 'episodes', 'completed_episodes', 'python', 'sumo')) {
        if ($null -eq $Record.$field -or $Record.$field -ne $Reference.$field) {
            throw "Training protocol differs from seed42: $field"
        }
    }
    if ($null -ne $Record.exploration_episodes -and $Record.exploration_episodes -ne 150) {
        throw 'Training exploration schedule differs from the original 150-episode plan.'
    }
    foreach ($field in @('scenarios', 'training_network_sha256')) {
        if (($Record.$field | ConvertTo-Json -Compress) -ne ($Reference.$field | ConvertTo-Json -Compress)) {
            throw "Training protocol differs from seed42: $field"
        }
    }
    $referenceDemand = @($Reference.frozen_demand.PSObject.Properties)
    if (@($Record.frozen_demand.PSObject.Properties).Count -ne $referenceDemand.Count) {
        throw 'Training frozen demand differs from seed42.'
    }
    foreach ($entry in $referenceDemand) {
        if ($Record.frozen_demand.($entry.Name) -ne $entry.Value) {
            throw "Training demand hash differs: $($entry.Name)"
        }
    }
    foreach ($file in @('__init__.py', 'control.py', 'environment.py', 'model.py', 'spec.py', 'suite.py', 'evaluation_support.py')) {
        if ($Record.code_sha256.$file -ne $Reference.code_sha256.$file) {
            throw "Training input/control code differs from seed42: $file"
        }
    }
}

Push-Location -LiteralPath $projectRoot
try {
    $sourceSuite = 'network/transferable_v1/suite.json'
    $validationSuite = 'network/transferable_reliability/suite.json'
    $referenceModel = 'results/runs/transferable_train_150/model.json'
    if (-not (Test-Path -LiteralPath $referenceModel)) {
        throw "Reference seed42 model is missing: $referenceModel"
    }
    $referenceTraining = (Get-Content -LiteralPath $referenceModel -Raw | ConvertFrom-Json).training
    $sourceHash = Get-SignalFileHash -Path $sourceSuite
    if ($referenceTraining.seed -ne 42 -or $referenceTraining.status -ne 'completed' -or $referenceTraining.completed_episodes -ne 150 -or $referenceTraining.episodes -ne 150 -or $referenceTraining.batch_size -ne 32 -or $referenceTraining.suite_sha256 -ne $sourceHash) {
        throw 'Seed42 reference must be the completed 150-episode model trained on the current source suite.'
    }
    foreach ($file in @('__init__.py', 'control.py', 'environment.py', 'model.py', 'spec.py', 'suite.py', 'evaluation_support.py')) {
        $codePath = if ($file -eq 'evaluation_support.py') { $file } else { Join-Path 'transferable_signal' $file }
        if ((Get-SignalFileHash -Path $codePath) -ne $referenceTraining.code_sha256.$file) {
            throw "Source input/control code differs from seed42: $file"
        }
    }
    if ($Stage -ne 'train') {
        if (Test-Path -LiteralPath $ValidationOut) {
            throw 'Validation output already exists. Pass -ValidationOut with a new output folder.'
        }
        Invoke-SignalPython -SignalArgs @('check', '--suite', $validationSuite)
    }

    $modelPaths = @($referenceModel)
    foreach ($trainingSeed in @(43, 44)) {
        $trainingOut = "results/runs/transferable_train_150_seed$trainingSeed"
        $modelPath = Join-Path $trainingOut 'model.json'
        $checkpointPath = Join-Path $trainingOut 'checkpoint.pt'
        $manifestPath = Join-Path $trainingOut 'manifest.json'
        $finished = $false
        if (Test-Path -LiteralPath $manifestPath) {
            $trainingRecord = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
            if ($trainingRecord.status -eq 'completed' -and $trainingRecord.completed_episodes -eq 150 -and $trainingRecord.seed -eq $trainingSeed) {
                Assert-TrainingProtocol -Record $trainingRecord -Reference $referenceTraining
                if (-not (Test-Path -LiteralPath $modelPath)) { throw "Completed model is missing: $modelPath" }
                $modelRecord = Get-Content -LiteralPath $modelPath -Raw | ConvertFrom-Json
                Assert-TrainingProtocol -Record $modelRecord.training -Reference $referenceTraining
                if ($modelRecord.training.seed -ne $trainingSeed -or $modelRecord.training.completed_episodes -ne 150 -or $modelRecord.training.updates -ne $trainingRecord.updates) {
                    throw "Model and completed training manifest differ: $trainingOut"
                }
                $finished = $true
            }
        }
        if ($Stage -ne 'validate' -and -not $finished) {
            $trainingArgs = @('train', '--suite', $sourceSuite, '--episodes', '150', '--seed', "$trainingSeed", '--out', $trainingOut)
            if (Test-Path -LiteralPath $checkpointPath) {
                Write-Output "Resuming seed $trainingSeed from the last completed episode."
                $trainingArgs += @('--resume', $checkpointPath)
            } elseif (Test-Path -LiteralPath $trainingOut) {
                throw "Training folder exists without a resumable checkpoint: $trainingOut"
            } else {
                Write-Output "Starting independent 150-episode training: seed $trainingSeed."
            }
            Invoke-SignalPython -SignalArgs $trainingArgs
            Assert-TrainingProtocol -Record (Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json) -Reference $referenceTraining
            Assert-TrainingProtocol -Record (Get-Content -LiteralPath $modelPath -Raw | ConvertFrom-Json).training -Reference $referenceTraining
            $finished = $true
        } elseif ($finished) {
            Write-Output "Reusing completed training: seed $trainingSeed."
        }
        if (-not $finished) {
            throw "Train seed $trainingSeed first, or use -Stage all."
        }
        $modelPaths += $modelPath
    }

    if ($Stage -ne 'train') {
        Write-Output 'Comparing 3 independent AI models and 3 baselines on 30 frozen demand scenarios (180 runs).'
        Invoke-SignalPython -SignalArgs (@('compare-models', '--suite', $validationSuite, '--models') + $modelPaths + @('--out', $ValidationOut))
        $comparisonRows = Import-Csv -LiteralPath (Join-Path $ValidationOut 'comparison.csv')
        $comparisonRows | Group-Object model_label | ForEach-Object {
            [pscustomobject]@{
                Model = $_.Name
                MeanQueue = [math]::Round(($_.Group | Measure-Object mean_queue_vehicles -Average).Average, 3)
                Arrived = ($_.Group | Measure-Object arrived_vehicles -Sum).Sum
                Incomplete = ($_.Group | Measure-Object departed_not_arrived_vehicles -Sum).Sum
                Teleports = ($_.Group | Measure-Object teleport_events -Sum).Sum
                Collisions = ($_.Group | Measure-Object collision_vehicle_events -Sum).Sum
            }
        } | Format-Table -AutoSize
        Write-Output "Review comparison.csv and per-run logs in $ValidationOut."
    }
} finally {
    Pop-Location
}

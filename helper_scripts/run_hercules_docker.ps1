<#
.SYNOPSIS
    Pull the testzeus/hercules Docker image, ensure .env and agents_llm_config.json
    exist, optionally launch Chrome or Firefox in remote debugging mode (based on
    user selection), and run Hercules in Docker.

    Usage example:
    .\run_hercules_docker.ps1 -BrowserChoice Chrome
    .\run_hercules_docker.ps1 -BrowserChoice Firefox
    .\run_hercules_docker.ps1 -BrowserChoice None
#>

Param(
    [ValidateSet("Chrome","Firefox","None")]
    [string]$BrowserChoice = "None"
)

###############################################################################
# 1) Pull the Hercules Docker image
###############################################################################
Write-Host "Pulling the latest testzeus/hercules Docker image..."
docker pull testzeus/hercules:latest

###############################################################################
# 2) Locate Chrome/Firefox typical paths on Windows
###############################################################################
$chromePathList = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)
$firefoxPathList = @(
    "C:\Program Files\Mozilla Firefox\firefox.exe",
    "C:\Program Files (x86)\Mozilla Firefox\firefox.exe"
)

$chromeFound = $null
$firefoxFound = $null

foreach ($path in $chromePathList) {
    if (Test-Path $path) {
        $chromeFound = $path
        break
    }
}

foreach ($path in $firefoxPathList) {
    if (Test-Path $path) {
        $firefoxFound = $path
        break
    }
}

###############################################################################
# 3) Ensure .env and agents_llm_config.json exist (create or download if missing)
###############################################################################
if (-not (Test-Path ".env")) {
    Write-Host ".env not found."
    if (Test-Path "env-example") {
        Write-Host "Copying env-example to .env..."
        Copy-Item "env-example" ".env"
    }
    else {
        Write-Host "Attempting to download env-example from GitHub..."
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/main/env-example" -OutFile "env-example" -ErrorAction SilentlyContinue

        if (Test-Path "env-example") {
            Write-Host "Copying env-example to .env..."
            Copy-Item "env-example" ".env"
        }
        else {
            Write-Host "Creating a minimal .env with default CDP_ENDPOINT_URL..."
            "CDP_ENDPOINT_URL=http://host.docker.internal:9222" | Out-File ".env"
        }
    }
    Write-Host "Please review/edit the newly created .env file for environment variables (API keys, etc.)."
}

if (-not (Test-Path "agents_llm_config.json")) {
    Write-Host "agents_llm_config.json not found."
    if (Test-Path "agents_llm_config-example.json") {
        Write-Host "Copying agents_llm_config-example.json to agents_llm_config.json..."
        Copy-Item "agents_llm_config-example.json" "agents_llm_config.json"
    }
    else {
        Write-Host "Attempting to download agents_llm_config-example.json from GitHub..."
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/main/agents_llm_config-example.json" -OutFile "agents_llm_config-example.json" -ErrorAction SilentlyContinue

        if (Test-Path "agents_llm_config-example.json") {
            Write-Host "Copying agents_llm_config-example.json to agents_llm_config.json..."
            Copy-Item "agents_llm_config-example.json" "agents_llm_config.json"
        }
        else {
            Write-Host "Creating a minimal agents_llm_config.json with placeholders."
            @'
{
  "llm_providers": [
    {
      "provider_name": "OpenAI",
      "api_key": "YOUR_API_KEY",
      "model": "gpt-3.5-turbo"
    }
  ]
}
'@ | Out-File "agents_llm_config.json"
        }
    }
    Write-Host "Please review/edit the newly created agents_llm_config.json file to set your LLM provider API keys."
}

###############################################################################
# 4) Create or prepare the opt directory
###############################################################################
Write-Host "Ensuring 'opt' directory structure exists..."
New-Item -ItemType Directory -Path ".\opt\input" -Force | Out-Null
New-Item -ItemType Directory -Path ".\opt\output" -Force | Out-Null
New-Item -ItemType Directory -Path ".\opt\test_data" -Force | Out-Null

###############################################################################
# 5) (Optional) Launch the chosen browser in CDP mode with extra configurations
###############################################################################
Write-Host "Browser choice: $BrowserChoice"
if ($BrowserChoice -ne "None") {
    # Chrome arguments
    $chromeArgs = @(
        "--remote-debugging-port=9222",
        "--user-data-dir=$($env:TEMP)\chrome-test-profile",
        "--disable-session-crashed-bubble",
        "--disable-notifications",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-background-timer-throttling",
        "--disable-popup-blocking",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-window-activation",
        "--disable-focus-on-load",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-position=0,0",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process"
    )

    # Prepare a Firefox profile
    $firefoxProfileDir = Join-Path $env:TEMP "firefox-test-profile"
    if (-not (Test-Path $firefoxProfileDir)) {
        New-Item -ItemType Directory -Path $firefoxProfileDir | Out-Null
    }
    @'
user_pref("xpinstall.signatures.required", false);
user_pref("extensions.autoDisableScopes", 0);
user_pref("extensions.enabledScopes", 15);
user_pref("extensions.installDistroAddons", false);
user_pref("extensions.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage", "about:blank");
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("extensions.webextensions.userScripts.enabled", true);
'@ | Out-File (Join-Path $firefoxProfileDir "user.js")

    if ($BrowserChoice -eq "Chrome") {
        if ($chromeFound) {
            Write-Host "Launching Google Chrome at: $chromeFound"
            Start-Process $chromeFound -ArgumentList $chromeArgs -WindowStyle Minimized
            Start-Sleep -Seconds 5
        }
        else {
            Write-Host "Chrome was requested but not found in common paths. Skipping."
        }
    }
    elseif ($BrowserChoice -eq "Firefox") {
        if ($firefoxFound) {
            Write-Host "Launching Firefox at: $firefoxFound"
            Start-Process $firefoxFound -ArgumentList @(
                "--remote-debugging-port=9222",
                "--profile", $firefoxProfileDir
            ) -WindowStyle Minimized
            Start-Sleep -Seconds 5
        }
        else {
            Write-Host "Firefox was requested but not found in common paths. Skipping."
        }
    }
}
else {
    Write-Host "Browser launch skipped."
}

###############################################################################
# 6) Ensure CDP_ENDPOINT_URL is set to localhost:9222 in the .env (optional)
###############################################################################
$fileContent = Get-Content ".env"
if ($fileContent -match "^CDP_ENDPOINT_URL=") {
    $newContent = $fileContent -replace "^CDP_ENDPOINT_URL=.*","CDP_ENDPOINT_URL=http://host.docker.internal:9222"
    $newContent | Out-File ".env"
} else {
    Add-Content ".env" "`r`nCDP_ENDPOINT_URL=http://host.docker.internal:9222"
}

###############################################################################
# 7) Run Hercules in Docker with .env, agents_llm_config.json, and opt mounted
###############################################################################
Write-Host "Running testzeus/hercules:latest Docker container..."
docker run --env-file=.env `
  -v "${PWD}\agents_llm_config.json:/testzeus-hercules/agents_llm_config.json" `
  -v "${PWD}\opt:/testzeus-hercules/opt" `
  --rm -it testzeus/hercules:latest

Write-Host "Hercules run completed."

# (Optional) Cleanup: If you launched the browser, you could kill it:
# Get-Process chrome, firefox -ErrorAction SilentlyContinue | Stop-Process
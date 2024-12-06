# Ensure the script runs with administrator privileges
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "This script must be run as Administrator!" -ForegroundColor Red
    Exit 1
}

# Step 0: Temporarily bypass execution policy for this session
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

# Step 1: Install Python 3.11 from Microsoft Store
Write-Host "Installing Python 3.11 from Microsoft Store..." -ForegroundColor Green
Start-Process -NoNewWindow -Wait "ms-windows-store://pdp/?productid=9NRWMJP3717K"

# Step 2: Verify Python Installation
Write-Host "Verifying Python installation..." -ForegroundColor Green
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python is not installed or not in PATH. Please restart and try again after installation." -ForegroundColor Red
    Exit 1
}

# Step 3: Upgrade pip
Write-Host "Upgrading pip..." -ForegroundColor Green
Start-Process -NoNewWindow -Wait -FilePath "python" -ArgumentList "-m", "pip", "install", "--upgrade", "pip"

# Step 4: Install Playwright and Other Dependencies
Write-Host "Installing Playwright and other Python dependencies..." -ForegroundColor Green
Start-Process -NoNewWindow -Wait -FilePath "python" -ArgumentList "-m", "pip", "install", "--upgrade", "testzeus-hercules"
Start-Process -NoNewWindow -Wait -FilePath "python" -ArgumentList "-m", "pip", "install", "playwright"

# Step 5: Install Playwright Dependencies
Write-Host "Installing Playwright dependencies..." -ForegroundColor Green
try {
    python -m playwright install --with-deps
    Write-Host "Playwright installed successfully."
} catch {
    Write-Host "Failed to install Playwright dependencies. Ensure Playwright is installed correctly." -ForegroundColor Red
}

# Step 6: Install FFmpeg
Write-Host "Downloading and installing FFmpeg..." -ForegroundColor Green
try {
    $ffmpegZipURL = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
    $ffmpegZipPath = "$env:TEMP\ffmpeg.zip"
    $ffmpegExtractPath = "C:\ffmpeg"
    Invoke-WebRequest -Uri $ffmpegZipURL -OutFile $ffmpegZipPath
    Expand-Archive -Path $ffmpegZipPath -DestinationPath $ffmpegExtractPath -Force
    $ffmpegBinPath = "$ffmpegExtractPath\bin"
    [Environment]::SetEnvironmentVariable("Path", $([Environment]::GetEnvironmentVariable("Path", "User") + ";$ffmpegBinPath"), "User")
    Write-Host "FFmpeg installed and added to PATH successfully."
} catch {
    Write-Host "Failed to download or install FFmpeg." -ForegroundColor Red
}

# Step 7: Notify user to restart the terminal
Write-Host "Setup complete! Please restart your terminal to apply PATH changes." -ForegroundColor Yellow

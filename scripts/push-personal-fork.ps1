# Push main to https://github.com/Hariprathap1804/nas_academy_lms
# Requires a GitHub PAT for Hariprathap1804 (repo scope): https://github.com/settings/tokens
# Usage: .\scripts\push-personal-fork.ps1 -Token ghp_your_pat_here

param(
    [Parameter(Mandatory = $true)]
    [string] $Token
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
git push "https://${Token}@github.com/Hariprathap1804/nas_academy_lms.git" main:main

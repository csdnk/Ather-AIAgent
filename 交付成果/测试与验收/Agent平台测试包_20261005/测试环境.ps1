param([ValidateSet('Start','Stop','Status','Seed','Accounts')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
& 'C:/Users/27921/.codex/worktrees/agent-platform/aether/AgentJYS-main/scripts/platform/manage_identity_lab.ps1' `
    -Action $Action `
    -Config 'E:/projects/codex/.agent-work/aether/workspace-support/agent-platform-implementation-20261005/platform-lab/private.json' `
    -Python 'E:/projects/codex/.agent-work/aether/workspace-support/tv-174013/Scripts/python.exe' `
    -Docker 'C:/Users/27921/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'

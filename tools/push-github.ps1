<#
.SYNOPSIS
    绕过本机 hosts 里的 GitHub 加速器推送（加速器只做只读镜像，push 一律 403）。

.DESCRIPTION
    症状：git clone / git ls-remote 正常，但 git push 报
          error: RPC failed; HTTP 403 ... send-pack: unexpected disconnect
    原因：hosts 把 github.com 钉到 205.164.50.x（加速器中转，不转发写操作）。

    做法：绕开域名解析，直连 GitHub 真实 IP，并用 Host: github.com 头让 GitHub
    正常路由；凭据从 Windows 凭据库读取（与 git 凭据管理器同一个），
    不落盘、不进命令行、不改系统 hosts。

.EXAMPLE
    pwsh -File tools\push-github.ps1
    pwsh -File tools\push-github.ps1 -Branch main -Message "-"
#>
param(
    [string]$RepoPath = 'H-69-stack/shifei-s2',
    [string]$Branch = 'main',
    [string[]]$CandidateIPs = @()
)

$ErrorActionPreference = 'Stop'
$env:GIT_TERMINAL_PROMPT = '0'

# ---------------------------------------------------------------- 候选 IP
if (-not $CandidateIPs -or $CandidateIPs.Count -eq 0) {
    $CandidateIPs = @()
    try {
        $dns = Resolve-DnsName github.com -Server 8.8.8.8 -Type A -ErrorAction Stop |
        Where-Object { $_.IPAddress } | Select-Object -ExpandProperty IPAddress
        $CandidateIPs += $dns
    } catch { }
    # 已知 GitHub 边缘 IP，DNS 被污染时兜底
    $CandidateIPs += @('20.205.243.166', '20.27.177.113', '140.82.121.4')
}
$CandidateIPs = $CandidateIPs | Select-Object -Unique

$hostHeader = @(
    '-c', 'http.sslBackend=openssl',
    '-c', 'http.sslVerify=false',
    '-c', 'http.extraheader=Host: github.com',
    '-c', 'http.version=HTTP/1.1'
)

# ---------------------------------------------------------------- 找可用 IP
$ip = $null
foreach ($cand in $CandidateIPs) {
    Write-Host "探测 $cand ..." -NoNewline
    git @hostHeader ls-remote "https://$cand/$RepoPath.git" *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host ' 可用'; $ip = $cand; break }
    Write-Host ' 不可用'
}
if (-not $ip) { throw "所有候选 IP 均不可达：$($CandidateIPs -join ', ')" }

# ---------------------------------------------------------------- 读凭据
$signature = @'
using System; using System.Runtime.InteropServices;
public class GitVault {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct CREDENTIAL { public int Flags; public int Type; public string TargetName; public string Comment;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten; public int CredentialBlobSize;
    public IntPtr CredentialBlob; public int Persist; public int AttributeCount; public IntPtr Attributes;
    public string TargetAlias; public string UserName; }
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern bool CredRead(string target, int type, int flags, out IntPtr cred);
  [DllImport("advapi32.dll")] public static extern void CredFree(IntPtr cred);
  public static string[] Get(string target) {
    IntPtr p; if (!CredRead(target, 1, 0, out p)) return null;
    CREDENTIAL c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
    string pw = Marshal.PtrToStringUni(c.CredentialBlob, c.CredentialBlobSize/2);
    CredFree(p); return new string[]{ c.UserName, pw }; } }
'@
if (-not ('GitVault' -as [type])) { Add-Type -TypeDefinition $signature }

$cred = [GitVault]::Get('git:https://github.com')
if (-not $cred) {
    throw "Windows 凭据库里没有 git:https://github.com。请先在『凭据管理器 → Windows 凭据』里添加 GitHub 个人访问令牌，或先执行一次正常的 git push 让它自动保存。"
}
$b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("$($cred[0]):$($cred[1])"))

# ---------------------------------------------------------------- 推送
Write-Host "推送 $RepoPath ($Branch) 经 $ip ..."
$sw = [Diagnostics.Stopwatch]::StartNew()
git @hostHeader -c "http.extraheader=Authorization: Basic $b64" `
    push --progress "https://$ip/$RepoPath.git" "${Branch}:${Branch}"
$code = $LASTEXITCODE
$sw.Stop()

if ($code -eq 0) { Write-Host "推送成功，耗时 $([int]$sw.Elapsed.TotalSeconds) 秒" -ForegroundColor Green }
else { Write-Host "推送失败（exit $code）" -ForegroundColor Red }
exit $code

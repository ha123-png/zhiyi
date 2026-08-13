$url = "http://127.0.0.1:5180"
$deadline = (Get-Date).AddSeconds(30)

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
        if ($response.StatusCode -eq 200) {
            Start-Process $url
            exit 0
        }
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show(
    "页面没有在 30 秒内启动。请查看“文档数据管道”窗口中的错误提示。",
    "文档数据管道"
) | Out-Null
exit 1

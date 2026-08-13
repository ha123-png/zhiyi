# Security Policy

## Supported version

当前仅维护最新的 Beta Release。

## Reporting a vulnerability

请不要通过公开 Issue 披露漏洞、API Key、真实业务文件或可利用细节。请使用 GitHub 仓库的 **Security → Report a vulnerability** 私密报告功能；若该功能暂不可用，请只提交不含利用细节的 Issue，请求维护者建立私密沟通渠道。

报告请包含受影响版本、复现条件、潜在影响和建议修复方向。不要上传第三方或客户数据。

## Security boundary

知意当前定位为本机单用户应用，不提供公网服务、多租户隔离或企业身份认证。API 只应监听回环地址，所有 AI 结果在关键业务使用前必须人工核验。

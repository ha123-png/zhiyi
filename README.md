# 知意

知意是一款本地优先的智能文档结构化工具。它把图片、PDF、Word、Excel 等文件交给用户选择的多模态模型，按字段模板提取数据，并通过人工确认、规则校验和修订记录，把结果整理成可追溯、可导出的数据表。

> 当前版本：`v0.1.0 Beta`。适合体验、学习和受控场景试用。所有 AI 提取结果在用于财务、库存或其他重要业务前都必须人工核验。

<p align="center">
  <img src="apps/web/public/logo.png" alt="知意 Logo" width="128" />
</p>

## 主要能力

- 本地优先：服务默认只监听本机，可连接 LM Studio、Ollama 或 OpenAI-compatible 云端接口。
- 模板化提取：内置发票、送货单模板，也可创建自定义字段模板。
- 人工审核：提取结果可以修改、确认、忽略或重新指定目标表。
- 数据仓库：支持编辑、修订历史、追加列、分 Sheet、导入、合并和 Excel 导出。
- 可追溯任务：记录原文件、模型名称、处理状态、耗时、问题和完成时间。
- 自动化接口：提供本地 API 与 MCP；MCP 权限默认关闭，可按任务、结果、数据、控制、写入和文件能力分别授权。
- 桌面体验：Windows 独立窗口、单实例运行、桌面快捷方式、备份恢复与可选彻底卸载。

## Windows 安装

前往 [Releases](https://github.com/ha123-png/zhiyi/releases) 下载最新的 `Zhiyi-*-win-x64-setup.exe`。

当前安装包尚未进行商业代码签名，Windows SmartScreen 可能提示来源未知。请只从本仓库 Release 下载，并核对 Release 中公布的 SHA-256。

知意不捆绑模型。首次使用前需要选择一种模型方案：

1. **LM Studio**：安装并加载支持图像输入的模型，推荐上下文至少 `8192`。
2. **Ollama**：使用支持视觉输入的模型，并在知意中填写对应模型名。
3. **云端兼容接口**：填写服务商提供的 Base URL、模型名和 API Key，并确认该服务支持多模态输入。

使用云端模型时，待处理文件会发送给你配置的服务商；知意不会代替服务商提供隐私承诺。敏感材料建议使用本地模型。

## 典型流程

```text
上传文件 → 选择/智能匹配模板 → 多模态模型提取 → 规则校验
→ 必要时人工修正 → 确认进入数据表 → Excel / API / MCP
```

## 本地开发

需要 Windows、Node.js、Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```powershell
git clone https://github.com/ha123-png/zhiyi.git
cd zhiyi
npm.cmd ci
uv sync --project apps/api --dev --python 3.12
npm.cmd run dev:safe
```

开发入口：

- Web：`http://127.0.0.1:5180`
- API：`http://127.0.0.1:8010`
- Worker：单进程、模型任务并发为 1

## 测试

```powershell
npm.cmd run test:web
npm.cmd run build:web
uv --cache-dir .uv-cache run --project apps/api pytest apps/api/tests
uv --cache-dir .uv-cache run --project apps/api ruff check apps/api/src apps/api/tests
```

Windows 安装包构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-release.ps1 -BuildInstaller
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/smoke-release-lifecycle.ps1
```

## 数据与隐私

- 默认数据目录位于当前用户的 `%LOCALAPPDATA%\Zhiyi`。
- 普通卸载默认保留数据；卸载时可选择同时删除全部本地数据。
- API 默认仅监听本机；不要在未增加正式身份认证的情况下将端口暴露到局域网或公网。
- API Key 使用 Windows Credential Manager 保存，不应写入仓库或日志。
- MCP 能力默认全部关闭，授权变化在新的 MCP 连接中生效。

详细说明见 [隐私与数据边界](docs/PRIVACY.md) 和 [安全政策](SECURITY.md)。

## 已知限制

- 当前只提供经过验证的 Windows x64 安装包；macOS/Linux 尚无桌面发行版。
- 项目尚未经过大规模并发、长期压力或企业生产环境认证。
- 提取质量取决于文件清晰度、模板、模型能力、上下文长度与硬件。
- 当前不是 ERP 自动入账系统，不应无人值守地执行不可逆业务操作。
- 安装包尚未进行 Authenticode 代码签名。

## 项目背景

本项目由产品构想、架构取舍、真实流程验收和 AI 编程代理协作完成。AI 参与了大量实现，项目维护者负责需求设计、风险边界、测试、代码验收和发布决策。我们欢迎对架构、质量、安全和交互提出具体 Issue。

## 参与贡献

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。提交安全问题时不要创建公开 Issue，请按 [SECURITY.md](SECURITY.md) 操作。

## 许可证

知意源代码采用 [Apache License 2.0](LICENSE) 发布。第三方依赖仍分别遵循其自身许可证；发行构建会生成 SBOM 与第三方许可证清单。

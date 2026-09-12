# 知意

**把文件里的信息，整理成用得上的数据。**

知意是一款 Windows 本地智能数据工作台。你定义需要的字段、含义与规则，知意调用你选择的模型理解资料，整理出表格或卡片，并保留核对、原件访问和导出入口。

文件的版式可以不同，你关注的信息可以保持一致。活动物资清单、会议行动项、资料摘录等工作，都可以从一套可复用的模板开始。模板是一份给模型的具体要求；它能约束输出，但不能保证模型理解永远正确。

> 当前源码：0.3.0 Beta。安装包是否已公开提供，以 [Releases](https://github.com/ha123-png/zhiyi/releases) 为准。重要结果请对照原件核验。

<p align="center"><img src="apps/web/public/logo.png" alt="知意" width="112" /></p>

![活动物资清单的真实仓库界面，使用虚构演示资料](docs/assets/interface-preview.png)

## 从一份文件开始

1. **连接模型。** 使用已安装的 LM Studio、Ollama，或兼容的云端接口。知意不捆绑模型服务和模型文件。
2. **保存模板。** 自己定义字段，或让 AI 根据需求和样例起草，再检查字段类型、含义与建议规则。
3. **上传并整理。** 选择模板，也可使用智能匹配。拿不准时由你决定，不强行选择。
4. **检查结果。** 提取结果会先进入仓库；没有待处理事项时自动完成，有规则或名称提示时保留待核对状态。入表不等于已经人工检查。
5. **继续使用。** 表格管理、卡片阅读、搜索、分 Sheet、导出，下一批资料继续使用模板。

完整起步说明见 [快速开始](docs/QUICK_START.md)。

## 主要能力

- **模板积累：** 字段、用途、提示、展示与规则共同版本化。可查看差异并把历史版本设为当前，不复制成冗余新版本。旧任务保留当时引用。
- **结果可核对：** 必填、范围、枚举、整段文字格式、计算与汇总规则；正则支持试填。用户决定修正或明确忽略提示。
- **来源与范围：** 原文件独立保留。较大文件可能只向模型提供部分内容，界面与导出记录处理范围；局部读取不改写原件。
- **仓库与导出：** 数据编辑、修订记录、卡片、分 Sheet、导入与合并。待核对来源在 Excel、CSV、JSON 中保留标记；独立副本不会被来源任务后续确认自动改写。
- **模型方案：** 本地或云端方案、任务配置快照、连接检查、启动与加载提示、可读诊断。文字连接成功不等于视觉能力合格。
- **API / MCP：** 权限范围内的任务查询、限定目录导入、数据读取、带版本检查的修改与导出。默认关闭，按需授权。
- **本地备份与桌面运行：** 完整业务备份恢复、单实例管理、安装升级、关闭后台进程。普通卸载保留用户数据。

## 格式与兼容边界

支持 PDF、DOCX、XLSX、常见图片、TXT/Markdown 等输入。复杂文件中的表格结构、公式、版式与图文关系并非都能完整提取；图片和扫描件需要视觉模型。CSV/JSON/Excel 数据导入可直接用于仓库，不必绕模型。

本轮使用虚构材料实测：LM Studio 下的 Qwen 3.5 4B 与云端 Qwen 3.6 Flash 文本链路通过；云端视觉样例通过，本地 4B 视觉样例把“未填写”保留为文字而非预期空值，记为质量差异。Ollama 协议和失败路径有自动测试，本轮没有真实 Ollama 推理证据。

有界压力覆盖 80 页 PDF、36,006 行三工作表 XLSX、20,002 段 DOCX、约 3.9 MB 长文本、20 帧 TIFF 及 40 个排队任务。原件摘要、局部范围、数量与重复确认检查通过；这些是有限样例，不是任意大小、无限时长或统一准确率保证。

## Windows 安装与升级

安装包以 `Zhiyi-*-win-x64-setup.exe` 命名。发布后从本仓库 [Releases](https://github.com/ha123-png/zhiyi/releases) 获取，核对随包 SHA-256。当前未进行 Authenticode 签名，不关闭系统安全防护来运行未知来源的副本。

升级前建议使用应用内完整备份。普通卸载只移除程序；明确选择删除数据才会清除本地资料。安装器只关闭目标安装目录的知意，不按进程名关闭其他安装。

## 开发与检查

使用 Node.js 24、Python 3.12 和 uv；本轮锁定环境见依赖文件。

```powershell
git clone https://github.com/ha123-png/zhiyi.git
cd zhiyi
npm ci
uv sync --project apps/api --dev --locked
npm run dev:safe
```

Web 为 `http://127.0.0.1:5180`，API 为 `http://127.0.0.1:8010`。模型任务默认串行处理。

```powershell
npm run test:web
npm run build:web
uv run --project apps/api ruff check apps/api/src apps/api/tests
uv run --project apps/api pytest apps/api/tests --basetemp=.local/pytest-fresh
npx playwright install --only-shell chromium
npm run test:browser
uv run --project apps/api python scripts/verify-release-pressure.py
```

浏览器和压力检查使用隔离合成资料，不调用付费模型。pytest 临时目录应使用新的目录。在干净检出、已安装 NSIS 的 Windows 环境构建：

```powershell
./scripts/build-release.ps1 -BuildInstaller
./scripts/smoke-release-lifecycle.ps1
```

构建生成版本、源码摘要、迁移版本、SBOM 和依赖许可记录。测试不能承诺以后每次升级都无缺陷，新增业务改动仍需相关验收。

## 隐私与项目边界

默认数据目录为 `%LOCALAPPDATA%\Zhiyi`。调用本地地址时相关模型请求在该本地服务处理；选择云端地址时相关文档内容、字段和提示发送给该服务商。API Key 使用 Windows Credential Manager；不把密钥、业务原件或完整备份提交到仓库。

API 默认仅监听本机。MCP 权限默认关闭，导出同时需要文件访问和数据读取，新授权在新连接生效。当前没有组织账户或公网身份认证，不能直接作为公网服务部署。

目前专注 Windows，不提供手机 App，也不提供企业级稳定性承诺。详见 [隐私与数据边界](docs/PRIVACY.md)、[安全政策](SECURITY.md) 和 [更新记录](CHANGELOG.md)。

欢迎提交具体问题与使用反馈。贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。源代码采用 [Apache License 2.0](LICENSE)，第三方依赖遵循各自许可。

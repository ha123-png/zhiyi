# 开发与构建脚本

- `npm.cmd run dev:safe` 启动本地开发服务。工作区运行目录在 `.local/`。
- `npm.cmd run test:browser` 使用隔离数据运行浏览器集成测试。
- [build-release.ps1](build-release.ps1) 生成 Windows 安装候选及源码、载荷和依赖摘要。
- [smoke-release-lifecycle.ps1](smoke-release-lifecycle.ps1) 使用隔离数据验证安装、覆盖、恢复及卸载。
- [verify-offline-journey.py](verify-offline-journey.py) 与 [verify-release-pressure.py](verify-release-pressure.py) 运行无模型调用的工作流与完整性测试。
- [verify-release-model.py](verify-release-model.py) 用于显式配置模型服务后的合成材料测试。常规 CI 不发出这些模型请求。
- [check-doc-governance.py](check-doc-governance.py) 核对公开产品文档目录及本地链接。

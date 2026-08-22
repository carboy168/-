# 工程规范智能体

## 正式源码与构建

仓库根目录是唯一正式源码根目录。桌面 UI、安装器、配置、提示词、测试与规范数据均直接位于本仓库目录树中，Windows 安装包和源码 ZIP 必须从同一个 Git commit 的根目录构建。

`EngineeringNormAgent_V1.0_source.zip` 仅作为 V1.0 历史归档保留，不参与构建，也不会进入新生成的源码 ZIP。

正式 Windows 构建入口只有 `.github/workflows/build-windows-installer.yml`。它支持 `main` 分支自动构建和 GitHub Actions 手动触发。工作流只上传 Actions artifact，不自动创建、覆盖或删除 GitHub Release/tag；发布仍需单独人工确认。

本地构建入口为 `一键生成Windows安装包.bat`，同样直接使用仓库根目录源码。

## 数据库升级

运行 `python migrations.py` 可执行统一、幂等迁移。`schema_migrations` 记录已完成版本，当前最新 schema version 为 7。旧 `migrate_phase2.py` 至 `migrate_phase5.py` 保留为兼容入口。

迁移只创建缺失表、索引与幂等种子记录，不删除、重置或覆盖规范库、项目库、审查记录及用户数据。桌面版升级时仍会在迁移前备份用户数据库。

## Provider

应用层统一通过 `provider.AIProvider` 调用模型。当前支持 OpenAI、DeepSeek、通义千问（Qwen）和智谱 GLM；RAG、审查引擎和桌面设置页不直接导入 OpenAI SDK。非敏感配置保存在数据库中，API Key 由环境变量或 Windows Credential Manager 提供，不写入 SQLite/JSON。

## 本地测试

```powershell
python -m unittest discover -s tests -v
python desktop_tools/preflight.py
python desktop_tools/backend_smoke_test.py
```

回归测试使用 Provider Mock，不调用真实 API。

项目数据库、规范全文和项目资料默认保存在 `%LOCALAPPDATA%\EngineeringNormAgent`，软件升级不会覆盖这些用户数据。

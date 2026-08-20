# 工程规范智能体 V1.0 Desktop Windows版

本仓库用于自动构建 Windows 直接安装版。

上传/提交到 `main` 后，GitHub Actions 会自动执行：

预检 → 后台测试 → PyInstaller → Inno Setup → 生成 Setup.exe → 发布 Release。

## 最终安装文件

构建成功后打开仓库右侧 **Releases**，进入：

**工程规范智能体 V1.0 Desktop Windows版**

下载：

`工程规范智能体_V1.0_Setup.exe`

双击安装即可。

项目数据库、规范全文和项目资料默认保存在：

`%LOCALAPPDATA%\EngineeringNormAgent`

软件升级不会覆盖这些用户数据。

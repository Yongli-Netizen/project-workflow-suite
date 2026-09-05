# Project Workflow Suite

面向 Codex 的项目交付插件，包含持久化多 Agent 编排、跨会话接力、验收证据管理，以及亚马逊软件需求澄清能力。

## 包含内容

- `project-workflow`：任务依赖、文件占用、检查、独立审查、验收和 checkpoint。
- `amazon-requirements-discovery`：把模糊的亚马逊运营软件需求整理成可批准、可验收的项目基线。
- `codebase-memory-mcp` 集成说明：以本机独立安装的代码图谱服务进行结构发现和影响分析。

## 环境要求

- Codex Desktop 或提供 `codex` CLI 的 Codex 环境。
- Python 3.12 或更高版本。
- 独立安装并可从 `PATH` 调用的 `codebase-memory-mcp`。

本插件的安装器不会下载或执行第三方脚本。请先按照 [codebase-memory-mcp 项目说明](https://github.com/DeusData/codebase-memory-mcp)完成依赖安装。

## 安装

从 GitHub Releases 下载源码包并解压，或克隆仓库，然后在插件根目录执行：

```powershell
.\scripts\install.ps1
```

macOS 或 Linux：

```sh
sh ./scripts/install.sh
```

安装器会把插件复制到用户目录下的独立 marketplace，不会修改已有的 `personal` marketplace。安装完成后重启 Codex，并新建任务加载插件。

## 使用

在 Codex 对话中调用：

```text
$project-workflow
$amazon-requirements-discovery
```

插件不会自动获得发布、安装依赖或业务高风险操作权限。实际权限仍由 Codex 和用户授权控制。

## 更新

下载新版本并重新运行对应平台的安装脚本。安装器会覆盖同名插件文件并重新注册该插件的独立 marketplace。

## 发布者检查

在 Windows 上生成可上传到 GitHub Releases 的 ZIP 和 SHA256 文件：

```powershell
.\scripts\package.ps1
```

产物写入 `release/`。打包脚本会排除 Git 元数据、测试缓存、已有发布包和本地工作流账本。

发布前至少执行：

```powershell
python .\skills\project-workflow\scripts\validate_skill.py
python -m unittest discover -s .\skills\project-workflow\tests -v
python -m unittest discover -s .\skills\amazon-requirements-discovery\tests -v
```

测试结果只证明本地功能检查通过，不代表真实业务环境或外部服务已验收。

## 许可证

[MIT](LICENSE)

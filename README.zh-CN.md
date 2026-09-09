# worktree-env (`wte`)

[English](README.md)

为每个 Git Worktree 自动准备独立、可运行的本地开发环境。轻量，极简，No magic。

## 痛点描述

Git Worktree 被广泛用于并行开发和任务隔离。但一个新 Worktree 通常只有代码，并不是一个可以立即运行的开发环境：

- 前端、后端、数据库和调试器仍使用相同的固定端口，多个 Worktree 无法同时启动。
- `.env`、私钥和其他本机 Secrets 需要重复复制，且容易误提交。
- 项目内各服务之间的 URL 和端口配置需要手动保持一致。

常见方案通常需要在项目代码中加入额外脚本、修改启动方式，或者修改 `AGENTS.md`、`CLAUDE.md`、创建 skill 等。
这些做法均具有一定的侵入性，要么在团队内统一推行，要么影响其他团队成员开发。

`wte` 基于 Git `post-checkout` Hook，为项目的 worktrees 分配稳定且不冲突的端口、链接环境变量并生成本地配置。
Hook 不随仓库提交，本地全局可用，不污染任何代码。

## 对比现有产品

- [Portless](https://github.com/vercel-labs/portless)：需要通过 `portless` 启动应用，引入反向代理、本地 CA 和后台服务。
- [devports](https://github.com/bendechrai/devports)：使用 `devports` 命令包装 worktree 创建和删除，Agent 或 IDE 直接创建的 worktree 不会自动处理。
- [Worktrunk](https://worktrunk.dev/)：用 `wt` 代替原生 Git 工作流，不参与环境准备，Agent 或 IDE 直接创建的 worktree 不会自动处理。
- [workz](https://github.com/rohansx/workz)：使用 .workz.toml、workz sync/start 或针对 Cursor、Claude Code、Worktrunk 分别配置 Hook。
- [Hyve](https://github.com/eladkishon/hyve)：采用 `hyve create/run` 工作流，依赖 Docker、数据库容器和服务编排。

`wte` 不接管 Worktree 的创建方式和项目启动流程，而是在 Worktree 创建后自动投影完整的本地开发环境。无需修改项目代码、启动命令或 Agent 提示词，无需向项目添加脚本，也无需流量代理和常驻进程；无论 Worktree 由 Git、IDE 还是 Coding Agent 创建，都可以被自动处理。

所有规则均通过仓库外的声明式 Profile 显式配置。`wte` 会为每个 Worktree 分配稳定端口、挂载 Secrets、生成本地配置，并可在后台初始化依赖，使 Worktree 创建既就绪。

## 功能概述

- 从全机共享端口池分配连续端口块，并在 Worktree 路径存活期间保持稳定。
- 将仓库外的 Secrets 以软链接形式挂载进 Worktree，作为唯一事实来源，避免复制粘贴。
- 以仓库为维度组织，而非以服务为维度组织。支持 monorepo，支持多端口申请。
- 可选的宿主机目录监控，自动发现新加入的 linked worktree，可解决 coding agent 沙箱权限问题。
- 不影响项目本身的 Git Hooks，在 `wte` 的 `post-checkout` Hook 执行完后，将继续调用项目自身的可执行 Git Hook。
- 日常同步由原生 Git Hooks 完成，无需 wrapper 或 skill 参与，无须修改 coding agent 提示词或启动命令。

## 环境要求

- macOS 或 Linux
- Python 3.9+
- Git
- Bash

## 安装

```bash
uv tool install worktree-env
```

## 升级

```bash
uv tool upgrade worktree-env
```

## 可选的配置 Skill

[wte-setup](skills/wte-setup/SKILL.md) 帮助 Agent 分析项目、生成 Profile 草案、将本机配置迁移到仓库外并验收。它会区分一次性迁移与长期 Worktree 投影，并在执行计划中的变更前征求批准。Skill 仅用于辅助配置；日常同步由 Git Hooks 和可选的 Monitor 完成。

先获取本仓库（已有克隆可直接使用）：

```bash
git clone --depth 1 https://github.com/archcst/worktree-env.git
cd worktree-env
```

阅读 `skills/wte-setup/SKILL.md` 及其 `references/` 内容后，在仓库根目录执行以下命令，将完整 Skill 安装到用户级目录：

```bash
(
  set -eu
  dest="$HOME/.agents/skills/wte-setup"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    printf 'Already installed: %s; back it up and move it aside before installing.\n' "$dest" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$dest")"
  cp -R skills/wte-setup "$dest"
)
```

Pi 会自动发现 `~/.agents/skills/`。在需要配置的项目中开启新会话，调用：

```text
/skill:wte-setup 帮我为当前项目配置 worktree-env
```

其他 Agent 工具请使用其规定的用户级 Skill 目录和调用方式，复制时保留完整的 `wte-setup/` 目录及参考文件。安装 Skill 只复制指令，不会运行 `wte init` 或修改项目配置；实际使用时，Agent 可以引导安装 `worktree-env`。

更新时，先拉取仓库最新变更并阅读 Skill，将已安装目录移到**所有 Skill 发现目录之外**备份，再执行上面的安装命令；个人修改也保留在备份中。卸载时只移除已安装的 Skill 目录，不影响 wte 的 Profiles、Hooks 或 Monitor。

## 开始使用

```bash
wte init
```

该命令会：

- 在 `~/.config/wte/` 中初始化个人配置目录。
- 执行 `git config --global core.hooksPath ~/.config/wte/hooks` 安装全局 Git Hook 分发器。

> 如果全局 `core.hooksPath` 已指向其他位置，`wte` 会显示当前值并拒绝修改，确认其用途并妥善迁移或移除后重试 `wte init`。

## `~/.config/wte/`

个人 Profile、Hooks 和运行状态统一存放在：

```text
~/.config/wte/
├── config.yaml                       # 全机端口池
├── project_a.yaml                    # 项目 A Profile
├── project_b.yaml                    # 项目 B Profile
├── hooks/                            # 全局 Git Hook 分发器
└── state/                            # 该目录被 wte 管理，不应手动编辑。
    ├── ports.json                    # 端口注册表
    ├── ports.lock                    # 并发锁
    ├── hooks-state.json              # Hook 安装状态
    └── reconciler.log                # Monitor 运行日志（仅启动可选的 Monitor 时存在，详见 Monitor 章节）
```

根目录中除 `config.yaml` 外的 `*.yaml` 文件均会被作为项目 Profile 加载。

可以通过 `WTE_CONFIG_HOME` 修改目录；未设置时遵循 `XDG_CONFIG_HOME`。

## 配置文件示例

### 端口段配置

全机端口范围位于 `~/.config/wte/config.yaml`：

```yaml
port-range:
  start: 20000
  end: 29999
```

默认范围为 `20000-29999`，可手动修改，修改后新 Worktree 将从新的端口段中分配，已分配的端口不变。

### 项目 Profile

复制配置模板：

```bash
cp ~/.config/wte/project.example.yaml.template \
   ~/.config/wte/example-project.yaml
```

以下为一个前后端分离项目的 Profile 示例：

```yaml
name: example-project

match:
  # 指向该项目的 main worktree 目录。
  main-worktree: $HOME/code/example-app

port-claims:
  # 需要申请的端口的名称，项目需要多少就申请多少，id 在同一个 profile 中应保持唯一
  - id: frontend_port
  - id: backend_port

link-files:
  # 软链接共享的环境变量
  # source 指向原始环境变量文件，target 是目标位置，路径是基于 worktree 根目录的相对路径。
  - source: $HOME/path/to/your/frontend.env
    target: frontend-dir/.env
  - source: $HOME/path/to/your/backend.env
    target: backend-dir/.env

write-files:
  # 前端配置：
  - target: frontend-dir/.env.development
    body: |
      VITE_PORT=${frontend_port}
      SERVER_URL=http://127.0.0.1:${backend_port}

  # 后端配置：
  - target: backend-dir/.env.development
    body: |
      PORT=${backend_port}
```

> 该示例适用于前后端均可加载 `.env.{env name}` 的情况，请根据具体项目环境变量加载方式自行修改。
>
> Worktree 目录删除后，其对应的端口会在下一次触发 `wte` 时被回收。

## Monitor 说明

部分 coding agent 创建 Worktree 时使用沙箱环境，因此 `wte` 的 Hook 可能无法被触发。
需要支持这类工具时，可启用 Monitor：

```bash
wte monitor enable
```

它会监控 Profile 配置目录，以及每个已配置 main worktree 对应的 `.git/worktrees/` 元数据目录：

- macOS 使用带 `WatchPaths` 的 LaunchAgents。
- Linux 使用 systemd user path units。

受监控目录变化时，操作系统启动一次短生命周期的 Reconciler。_它不是常驻 Daemon，也不进行定时轮询_，资源消耗极低。

Reconciler 会：

1. 执行 `git worktree list --porcelain` 获取真实 Worktree 列表。
2. 与 `ports.json` 对比，为尚未注册的 Worktree 分配端口、挂载 Secrets、生成文件并启动配置的初始化命令。

新增、删除 Profile 或修改 `main-worktree` 后，Monitor 会自动刷新仓库监听路径。现有安装升级到该版本后，需要执行一次 `wte monitor enable` 以启用 Profile 自动监控；此后的 Profile 变更无需再手动刷新 Monitor。

关闭 Monitor 可使用：

```bash
wte monitor disable
```

执行后将不再监控文件系统的变化。

## 异步初始化

`wte` 可在 Worktree 创建后自动运行命令，可用于环境初始化：

```yaml
setup-commands:
  - command: [npm]
    args: [install]
    cwd: frontend-dir # 若在 Worktree 根目录执行，则填写“.”
    skip-if: node_modules # 若该文件或目录存在，则跳过该命令

  - command: [uv]
    args: [sync]
    cwd: backend-dir # 若在 Worktree 根目录执行，则填写“.”
    skip-if: .venv # 若该文件或目录存在，则跳过该命令
```

典型时间线如下：

```text
创建 Worktree
  → wte 完成环境投影并在后台启动 npm install
  → 用户描述任务，AI 阅读代码、分析和修改
  → 用户或 AI 启动项目时，依赖通常已经准备完成
```

异步初始化可由正常的 `post-checkout` Hook、发现未注册 Worktree 的 Monitor Reconciler 或 `wte sync` 启动。建议为 `setup-commands` 中的命令配置合适的 `skip-if`，避免再次同步时重复执行已经完成的初始化。

## `wte` 支持的命令

```text
wte init              创建个人配置和模板，并安装核心 Git Hooks
wte sync              同步当前 Worktree 环境并启动配置的初始化命令
wte list              查看仍存活的 Worktree 端口分配
wte doctor            诊断配置、Profiles、注册表、Secrets、Hooks 和 Monitor
wte monitor enable    安装或刷新可选的宿主机监控
wte monitor disable   只移除宿主机监控，保留 Git Hooks
wte uninstall         移除 Hooks 和 Monitor，但保留配置和运行状态
```

`wte` 通常会自动完成同步，无需手动运行 `wte sync`。
仅在修改 Profile 后，如需将最新配置重新投影到某个 Worktree，可进入该 Worktree 根目录执行 `wte sync` 触发同步。

## 许可证

[MIT](LICENSE)

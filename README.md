# worktree-env (`wte`)

[中文文档](README.zh-CN.md)

Automatically prepare an isolated, runnable local development environment for every
Git worktree. Lightweight, minimal, no magic.

## The problem

Git worktrees are widely used for parallel development and task isolation. However,
a new worktree usually contains only code, not a development environment that is
ready to run:

- The frontend, backend, database, and debugger still use the same fixed ports,
  preventing multiple worktrees from running at the same time.
- `.env` files, private keys, and other local secrets must be copied repeatedly and
  can easily be committed by mistake.
- URLs and port settings shared by services within a project must be kept in sync
  manually.

Common solutions often require adding extra scripts to the project, changing how it
is started, modifying `AGENTS.md` or `CLAUDE.md`, or creating skills.
These approaches are intrusive to some degree: they must either be adopted across
the team or affect how other team members work.

`wte` uses a Git `post-checkout` hook to assign stable, conflict-free ports to a
project's worktrees, link environment variables, and generate local configuration.
The hook is not committed to the repository. It is available globally on the local
machine and does not modify any project code.

## Comparison with existing tools

- [Portless](https://github.com/vercel-labs/portless): Requires applications to be
  started through `portless`, introducing a reverse proxy, a local CA, and a
  background service.
- [devports](https://github.com/bendechrai/devports): Wraps worktree creation and
  removal in `devports` commands; worktrees created directly by an agent or IDE are
  not handled automatically.
- [Worktrunk](https://worktrunk.dev/): Replaces the native Git workflow with `wt`,
  does not participate in environment setup, and does not automatically handle
  worktrees created directly by an agent or IDE.
- [workz](https://github.com/rohansx/workz): Uses `.workz.toml`,
  `workz sync`/`workz start`, or separately configured hooks for Cursor, Claude Code,
  and Worktrunk.
- [Hyve](https://github.com/eladkishon/hyve): Adopts a
  `hyve create`/`hyve run` workflow and depends on Docker, database containers, and
  service orchestration.

`wte` does not take over how worktrees are created or how a project is started.
Instead, it automatically projects a complete local development environment after a
worktree is created. It requires no changes to project code, start commands, or agent
prompts; no scripts need to be added to the project, and no traffic proxy or resident
process is required. Worktrees created by Git, an IDE, or a coding agent can all be
handled automatically.

All rules are declared explicitly in profiles stored outside the repository. For each
worktree, `wte` assigns stable ports, mounts secrets, generates local configuration,
and can initialize dependencies in the background, making the worktree ready
immediately after creation.

## Features

- Allocates contiguous port blocks from a machine-wide shared pool and keeps them
  stable for the lifetime of the worktree path.
- Mounts secrets stored outside the repository into the worktree as symlinks,
  preserving a single source of truth and avoiding copy and paste.
- Organizes configuration by repository rather than by service. Supports monorepos
  and multiple port requests.
- Optionally monitors host directories to discover newly added linked worktrees,
  including those created within coding agent sandboxes.
- Preserves the project's own Git hooks. After the `post-checkout` hook for `wte`
  finishes, it invokes the project's own executable Git hook.
- Native Git hooks handle day-to-day synchronization without a wrapper or an
  agent skill, and without changing coding agent prompts or project start commands.

## Requirements

- macOS or Linux
- Python 3.9+
- Git
- Bash

## Installation

```bash
uv tool install worktree-env
```

## Upgrading

```bash
uv tool upgrade worktree-env
```

## Optional setup skill

The [wte-setup skill](skills/wte-setup/SKILL.md) helps an agent inspect your project,
draft a profile, migrate local configuration outside the repository, and verify the
result. It distinguishes one-time migration from ongoing worktree projection and
requests approval for planned changes. The skill is optional; normal synchronization
runs through Git hooks and the optional Monitor.

Get the skill from this repository (use your existing checkout if you have one):

```bash
git clone --depth 1 https://github.com/archcst/worktree-env.git
cd worktree-env
```

Review `skills/wte-setup/SKILL.md` and its `references/` directory, then run this
from the repository root to install the complete skill at user scope:

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

Pi discovers `~/.agents/skills/` automatically. Open a new agent session in the
project you want to configure and invoke:

```text
/skill:wte-setup Help me configure worktree-env for this project.
```

For other agent tools, use their documented user-level skill directory and invocation
method. Copy the entire `wte-setup/` directory, including its references. Installing
the skill only copies instructions; it does not run `wte init` or change project
configuration. During use, the agent can guide you through installing `worktree-env`.

To update, pull the latest repository changes, review the skill, and move the existing
installed directory to a backup **outside all skill discovery directories** before
rerunning the install block. Keep any personal edits in that backup. To remove the
skill, remove only its installed directory; wte profiles, hooks, and Monitor remain
unchanged.

## Getting started

```bash
wte init
```

This command:

- Initializes the personal configuration directory at `~/.config/wte/`.
- Runs `git config --global core.hooksPath ~/.config/wte/hooks` to install the global
  Git hook dispatcher.

> If the global `core.hooksPath` already points somewhere else, `wte` displays the
> current value and refuses to change it. Confirm its purpose and migrate or remove
> it as appropriate before retrying `wte init`.

## `~/.config/wte/`

Personal profiles, hooks, and runtime state are stored together in:

```text
~/.config/wte/
├── config.yaml                       # Machine-wide port pool
├── project_a.yaml                    # Project A profile
├── project_b.yaml                    # Project B profile
├── hooks/                            # Global Git hook dispatcher
└── state/                            # Managed by wte; do not edit manually.
    ├── ports.json                    # Port registry
    ├── ports.lock                    # Concurrency lock
    ├── hooks-state.json              # Hook installation state
    └── reconciler.log                # Monitor log (present only when the optional Monitor is enabled; see the Monitor section)
```

Every root-level `*.yaml` file except `config.yaml` is loaded as a project profile.

Set `WTE_CONFIG_HOME` to change this directory. When it is not set,
`XDG_CONFIG_HOME` is respected.

## Configuration examples

### Port range

The machine-wide port range is configured in `~/.config/wte/config.yaml`:

```yaml
port-range:
  start: 20000
  end: 29999
```

The default range is `20000-29999`. You can change it manually; new worktrees will
be allocated from the new range, while existing allocations remain unchanged.

### Project profile

Copy the configuration template:

```bash
cp ~/.config/wte/project.example.yaml.template \
   ~/.config/wte/example-project.yaml
```

The following profile describes a project with separate frontend and backend services:

```yaml
name: example-project

match:
  # Points to the project's main worktree directory.
  main-worktree: $HOME/code/example-app

port-claims:
  # Names of the ports to request. Add as many as the project needs;
  # each id must be unique within the profile.
  - id: frontend_port
  - id: backend_port

link-files:
  # Files or directories shared through symlinks.
  # source points to an existing file or directory, while target is a path
  # relative to the worktree root.
  - source: $HOME/path/to/your/frontend.env
    target: frontend-dir/.env
  - source: $HOME/path/to/your/backend.env
    target: backend-dir/.env
  - source: $HOME/path/to/your/certs
    target: backend-dir/certs

write-files:
  # Frontend configuration:
  - target: frontend-dir/.env.development
    body: |
      VITE_PORT=${frontend_port}
      SERVER_URL=http://127.0.0.1:${backend_port}

  # Backend configuration:
  - target: backend-dir/.env.development
    body: |
      PORT=${backend_port}
```

`link-files` shares the source directly: edits through any worktree affect all
worktrees using it. Existing real directories at a target are rejected, not
removed or merged; existing files and symlinks may be replaced. Link and write
targets must not be equal or nested inside one another. Sources must exist as
files or directories; missing or unsupported sources are skipped with a warning.

> This example applies when both the frontend and backend can load `.env.{env name}`
> files. Adjust it to match how your project loads environment variables.
>
> After a worktree directory is deleted, its ports are reclaimed the next time `wte`
> is triggered.

## Monitor

Some coding agents create worktrees inside a sandbox, which can prevent the `wte` hook from running.
To support these tools, enable the Monitor:

```bash
wte monitor enable
```

It watches the profile directory and the `.git/worktrees/` metadata directory
associated with each configured main worktree:

- macOS uses LaunchAgents with `WatchPaths`.
- Linux uses systemd user path units.

When a watched directory changes, the operating system starts a short-lived Reconciler.
_It is not a resident daemon and does not poll on a timer_, so its resource usage is
minimal.

The Reconciler:

1. Runs `git worktree list --porcelain` to retrieve the actual list of worktrees.
2. Compares it with `ports.json`, then assigns ports, mounts secrets, generates
   files, and starts configured initializers for unregistered worktrees.

When profiles are added, removed, or pointed to a different `main-worktree`, the
Monitor automatically refreshes its repository watch paths. After upgrading an
existing installation to this version, run `wte monitor enable` once to activate
automatic profile monitoring; subsequent profile changes require no manual refresh.

To disable the Monitor, run:

```bash
wte monitor disable
```

Afterward, filesystem changes will no longer be monitored.

## Asynchronous initialization

`wte` can automatically run commands after a worktree is created, allowing the
environment to be initialized in the background:

```yaml
setup-commands:
  - command: [npm]
    args: [install]
    cwd: frontend-dir  # Use "." to run from the worktree root.
    skip-if: node_modules  # Skip this command if the file or directory exists.

  - command: [uv]
    args: [sync]
    cwd: backend-dir  # Use "." to run from the worktree root.
    skip-if: .venv  # Skip this command if the file or directory exists.
```

A typical timeline looks like this:

```text
Create a worktree
  → wte projects the environment and starts npm install in the background
  → The user describes the task; the AI reads, analyzes, and modifies the code
  → By the time the user or AI starts the project, dependencies are usually ready
```

Asynchronous initialization is started by the normal `post-checkout` hook, by the
Monitor Reconciler when it discovers an unregistered worktree, or by `wte sync`.
Configure an appropriate `skip-if` for each command in `setup-commands` to avoid
rerunning completed initialization during a later sync.

## Commands supported by `wte`

```text
wte init              Create personal configuration and templates, and install core Git hooks
wte sync              Synchronize the environment and start configured setup commands
wte list              List port allocations for worktrees that still exist
wte doctor            Diagnose configuration, profiles, registry, secrets, hooks, and Monitor
wte monitor enable    Install or refresh optional host monitoring
wte monitor disable   Remove host monitoring only, preserving Git hooks
wte uninstall         Remove hooks and the Monitor, preserving configuration and runtime state
```

`wte` normally keeps worktrees synchronized automatically, so manual syncs are not
necessary. Only after updating a profile, if the latest configuration needs to be
projected again, run `wte sync` from the root of the target worktree.

## License

[MIT](LICENSE)

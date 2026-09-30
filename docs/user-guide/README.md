# User Guide: `git2svn` CLI

`git2svn` is a lightweight, zero-dependency Python utility designed to synchronize changes from a local Git repository into a local Subversion (SVN) working copy.

---

## 1. Global Options & Environment

All options can be specified either before or after subcommands:

```bash
git2svn [-g/--git-dir <path>] [-s/--svn-dir <path>] [-n/--dry-run] [-v/--verbose] <command>
```

| Option | Flag | Environment Variable | Git Config Key (`.git/config`) | Default | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `--version` | `-V` | — | — | — | Show program's version number and exit. |
| `--svn-dir` | `-s` | `SVN_DIR` | `git2svn.svnDir` | None (required) | Path to the local Subversion working copy (`.svn` root). |
| `--git-dir` | `-g` | — | — | Current Git root | Path to the local Git repository root. |
| `--dry-run` | `-n` | — | `git2svn.dryRun` | `False` | Print actions (file copies, patches, SVN commands) without modifying disk. |
| `--verbose` | `-v` | — | — | `False` | Print detailed debug logs and execution traces. |
| `--copy` | — | — | `git2svn.copy` | `False` | Extract exact binary snapshots directly from Git object DB. |
| `--no-update` | `-u / --update` | — | `git2svn.autoUpdate` | `True` | Run `svn update` after `replay` to bump local base revision to `HEAD` (enabled by default). |
| `<ref1> [ref2]` | — | — | `git2svn.defaultRange` | None | Default revision or range (e.g. `svn-mirror/trunk..trunk`) when omitted from CLI. |

### 1.1 Persisting Settings via `git config`

To avoid typing `--svn-dir` or specifying commit ranges on every run, configure options directly in your repository's `.git/config`:

```bash
# Configure SVN working copy path for this repository:
git config git2svn.svnDir "C:/Projects/my-svn-checkout"

# Configure default integration range:
git config git2svn.defaultRange "svn-mirror/trunk..trunk"

# Optional: Disable automatic 'svn update' if desired (defaults to true):
git config git2svn.autoUpdate false
```

#### Precedence Hierarchy:
1. Explicit CLI arguments (`--svn-dir`, `--dry-run`, `--copy`, `--no-update` / `-u`)
2. Environment variables (`SVN_DIR`)
3. Repository or global Git configuration (`git config git2svn.*`)
4. System defaults

---

## 2. Core Actions

`git2svn` centers around distinct actions with strict commit boundaries:

- **[`setup`](commands.md#4-command-setup):** **Repository bootstrap.** Automates initial repository configuration, branch tracking detection, and productivity aliases (`git svn-push`, `git svn-pull`).
- **[`stage`](commands.md#1-command-stage):** **Never commits.** Prepares changes in the SVN workspace (`svn add`, `svn rm`), leaving the working copy dirty for visual review in TortoiseSVN or manual commit.
- **[`replay`](commands.md#2-command-replay):** **Always commits.** Sequentially ports Git commits into SVN history, preserving author commit messages and providing stateful conflict recovery.

---

## 3. Guide Contents

- **[Command Reference](commands.md):** Detailed guide for `setup`, `stage` (single, range, `--copy`, `--snapshot`), `replay` (`--continue`, `--abort`, `--skip`, `--update`), and structural staging mechanics.
- **[Integration Branch Workflow](integration-workflow.md):** Recommended 3-branch model (`svn-mirror/trunk` + `trunk` + topic branches) and productivity aliases for seamless bidirectional SVN-to-Git synchronization.
- **[Troubleshooting & FAQ](troubleshooting.md):** Solutions for patch rejections (`.rej`), mixed revisions, merge commit restrictions, and Windows `svn.exe` path discovery.


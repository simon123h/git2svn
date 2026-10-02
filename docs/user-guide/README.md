# User Guide: `git2svn` CLI

`git2svn` is a lightweight, zero-dependency Python utility designed to synchronize changes from a local Git repository into a local Subversion (SVN) working copy.

---

## 1. Global Options & Environment

The recommended workflow is to run `git2svn setup <url-or-path>` once per repository. After setup, operational commands (`stage`, `replay`, `diff`, `status`) run directly without arguments:

```bash
git2svn [-g/--git-dir <path>] [-s/--svn-dir <path>] [--svn-url <url>] [-n/--dry-run] [-v/--verbose] <command>
```

| Option | Flag | Environment Variable | Git Config Key (`.git/config`) | Default | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `--version` | `-V` | — | — | — | Show program's version number and exit. |
| `--svn-dir` | `-s` | `SVN_DIR` | `git2svn.svnDir` | Managed `.git/git2svn/svn_wc` | Path to the local Subversion working copy (`.svn` root). Optional if configured or URL provided. |
| `--svn-url` | — | `SVN_URL` | `git2svn.svnUrl` | None | SVN repository URL (managed checkout in `.git/git2svn/svn_wc/`). |
| `--git-dir` | `-g` | — | — | Current Git root | Path to the local Git repository root. |
| `--dry-run` | `-n` | — | `git2svn.dryRun` | `False` | Print actions (file copies, patches, SVN commands) without modifying disk. |
| `--verbose` | `-v` | — | — | `False` | Print detailed debug logs and execution traces. |
| `--copy` | — | — | `git2svn.copy` | `False` | Extract exact binary snapshots directly from Git object DB. |
| — | — | — | `git2svn.mirrorRemote` | Auto (`svn-mirror` / `origin`) | Upstream Git remote mirroring Subversion for dynamic range resolution (`<mirror>/<svn-branch>..HEAD`). |

### 1.1 Recommended Onboarding: `git2svn setup`

Rather than configuring settings manually, run `git2svn setup` with your SVN repository URL or local working copy:

```bash
# Option A: Automatic managed working copy in .git/git2svn/svn_wc:
git2svn setup https://svn.example.com/repo/trunk

# Option B: Existing local SVN working copy:
git2svn setup /path/to/svn
```

This automatically detects tracking branches, configures `git2svn.svnDir`, `git2svn.svnUrl`, `git2svn.mirrorRemote`, `pull.ff only`, and installs Git aliases (`git svn-push`, `git svn-pull`, `git svn-status`).

#### Precedence Hierarchy:
1. Explicit CLI arguments (`--svn-dir`, `--svn-url`, `--dry-run`, `--copy`, `--no-update` / `-u`)
2. Environment variables (`SVN_DIR`, `SVN_URL`)
3. Repository or global Git configuration (`git config git2svn.*`)
4. System defaults (managed checkout at `.git/git2svn/svn_wc`)

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


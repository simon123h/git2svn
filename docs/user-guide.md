# User Guide: `git2svn` CLI Commands & Workflows

This document provides a comprehensive command-line reference and user guide for `git2svn`.

---

## 1. Global Options & Environment

All options can be specified either before or after subcommands:

```bash
git2svn [-g/--git-dir <path>] [-s/--svn-dir <path>] [-n/--dry-run] [-v/--verbose] <command>
```

| Option | Flag | Environment Variable | Git Config Key (`.git/config`) | Default | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `--svn-dir` | `-s` | `SVN_DIR` | `git2svn.svnDir` | None (required) | Path to the local Subversion working copy (`.svn` root). |
| `--git-dir` | `-g` | — | — | Current Git root | Path to the local Git repository root. |
| `--dry-run` | `-n` | — | `git2svn.dryRun` | `False` | Print actions (file copies, patches, SVN commands) without modifying disk. |
| `--verbose` | `-v` | — | — | `False` | Print detailed debug logs and execution traces. |
| `--copy` | — | — | `git2svn.copy` | `False` | Extract exact binary snapshots directly from Git object DB. |
| `--update` | `-u` | — | `git2svn.autoUpdate` | `False` | Run `svn update` after `replay` to bump local base revision to `HEAD`. |
| `<ref1> [ref2]` | — | — | `git2svn.defaultRange` | None | Default revision or range (e.g. `svn-mirror/trunk..trunk`) when omitted from CLI. |

### 1.1 Persisting Settings via `git config`

To avoid typing `--svn-dir` or specifying commit ranges on every run, configure options directly in your repository's `.git/config`:

```bash
# Configure SVN working copy path for this repository:
git config git2svn.svnDir "C:/Projects/my-svn-checkout"

# Configure default integration range:
git config git2svn.defaultRange "svn-mirror/trunk..trunk"

# Automatically update SVN working copy after replay:
git config git2svn.autoUpdate true
```

#### Precedence Hierarchy:
1. Explicit CLI arguments (`--svn-dir`, `--dry-run`, `--copy`)
2. Environment variables (`SVN_DIR`)
3. Repository or global Git configuration (`git config git2svn.*`)
4. System defaults

---

## 2. Command: `stage`

Prepares changes in the SVN workspace **without committing**. This leaves the working copy dirty, ready for code review in TortoiseSVN, `svn diff`, or standard manual commit workflows.

### 2.1 Single Commit
Port changes from a single Git revision:
```bash
git2svn stage a1b2c3d4 -s /path/to/svn
```

### 2.2 Range (Squash)
Squash an entire branch or commit series into a single uncommitted SVN changeset. Both dot-notation and two positional arguments are supported:

```bash
# Using dot notation:
git2svn stage main..feature/login -s /path/to/svn

# Using two arguments:
git2svn stage main feature/login -s /path/to/svn
```

### 2.3 Direct Object Extraction (`--copy`)
Bypasses diff generation and `git apply`, instead extracting exact file bytes directly from Git's object database (`git show <ref>:<path>`). Recommended for binary assets, images, or large refactors:

```bash
git2svn stage main..feature/assets --copy -s /path/to/svn
```

### 2.4 Full Tree Alignment (`--snapshot`)
Compares the full tree of a target Git branch or commit against the SVN workspace without needing to know where it branched off from in Git history:
* Identifies and stages deletions (`svn rm`) for files missing in Git.
* Identifies and stages additions (`svn add --parents`) for files new in Git.
* Updates modified files to match Git content, preserving SVN line-ending conventions.

```bash
git2svn stage feature/login --snapshot -s /path/to/svn
```

---

## 3. Command: `replay`

Sequentially ports Git commits onto the SVN workspace, creating an **atomic `svn commit` for each** with its original Git commit message, author body, and timestamps.

> [!IMPORTANT]
> `replay` requires a linear Git history (fast-forward only). If a range contains merge commits, rebase your Git branch first (`git rebase main`).

### 3.1 Single Commit (Cherry-pick)
Replay a single Git commit and immediately commit it to SVN:
```bash
git2svn replay a1b2c3d4 -s /path/to/svn
```

### 3.2 Range Replay
Replay each commit in the range sequentially into SVN history:
```bash
git2svn replay main..feature/login -s /path/to/svn
```

### 3.3 Updating Working Copy Base Revision (`--update` / `-u`)
Subversion working copies operate with **mixed revisions**: when `git2svn replay` commits revisions to the SVN repository, only touched files are updated locally while the working copy base revision remains pegged at the previous revision (meaning `svn log` won't show new commits without `svn update`).

Use `-u` or `--update` to automatically run `svn update` upon successful replay completion:
```bash
git2svn replay -u main..feature/login -s /path/to/svn
```

You can also enable this permanently for your repository in `.git/config`:
```bash
git config git2svn.autoUpdate true
```

---

## 4. Conflict Resolution Lifecycle

When a patch conflict occurs during a multi-commit `replay`:

```mermaid
flowchart TD
    Start["git2svn replay ref1..ref2"] --> Apply["Apply next commit"]
    Apply --> Clean{"Patch clean?"}
    Clean -->|Yes| Commit["svn commit -F msg"]
    Commit --> More{"More commits?"}
    More -->|Yes| Apply
    More -->|No| Done(["Replay Complete"])

    Clean -->|No| Pause["[PAUSED] State saved to .svn/git2svn-replay.json"]
    Pause --> UserChoice{"User Action"}

    UserChoice -->|Fix & Stage| Continue["git2svn replay --continue"]
    Continue --> Resume["Commit resolved & resume queue"]
    Resume --> More

    UserChoice -->|Discard Remaining| Abort["git2svn replay --abort"]
    Abort --> Reverted(["Reverted to last clean commit"])

    UserChoice -->|Skip this commit| Skip["git2svn replay --skip"]
    Skip --> SkipResume["Discard commit & resume queue"]
    SkipResume --> More
```

### Resolution Steps:
1. **Execution Pauses:** All previous commits remain committed. Replay state is persisted in `<svn_dir>/.svn/git2svn-replay.json`.
2. **Resolve Conflicts:**
   - Inspect `.rej` files in the SVN workspace.
   - Edit files to resolve conflicts.
   - Run `svn add` / `svn rm` if files were added or removed.
   - Delete leftover `*.rej` and `*.orig` files.
3. **Resume Replay:**
   ```bash
   git2svn replay --continue
   ```
   *This automatically commits the resolved changeset using the original Git commit message and proceeds with the rest of the queue.*

### Alternative Actions:
* **Abort:** Discards uncommitted changes and resets the SVN working copy to the last clean revision:
  ```bash
  git2svn replay --abort
  ```
* **Skip:** Discards the failed commit entirely and continues with the next commit in the queue:
  ```bash
  git2svn replay --skip
  ```

---

## 5. Structural Staging Mechanics

During patch application or file copying, `git2svn` maps Git status codes (`git diff --name-status`) to the corresponding Subversion commands:

| Git Status | Description | SVN Action Executed |
| :--- | :--- | :--- |
| **`A`** | Added file | Creates parent directories, runs `svn add <filepath> --parents` |
| **`D`** | Deleted file | Runs `svn rm <filepath>` |
| **`M`** | Modified file | Contents updated via `patch` or direct copy (no SVN structural command needed) |
| **`R`** | Renamed file (`R100 old new`) | Runs `svn rm <old>` followed by `svn add <new> --parents` |
| **`C`** | Copied file (`C100 src dst`) | Runs `svn add <new> --parents` |

---

## 6. Troubleshooting & FAQ

### Q: Why does `replay` fail with "SVN workspace has uncommitted changes"?
`replay` requires a clean SVN workspace so every Git commit is ported as an isolated, atomic Subversion revision. Before running `replay`, review and commit or revert uncommitted changes in your SVN working copy:
```bash
svn status
svn revert -R .
```

### Q: Why does `replay` reject my branch with a "merge commit" error?
`replay` ports changes commit-by-commit to maintain a clean linear SVN history. If your Git branch has merge commits, rebase it first against your target branch:
```bash
git checkout feature/login
git rebase main
```
Alternatively, if you want to squash the entire branch into a single uncommitted SVN changeset without rebasing, use `stage`:
```bash
git2svn stage main..feature/login -s /path/to/svn
```

### Q: How do I handle patch rejections (`.rej` files)?
If `git apply` encounters conflicting context lines, it writes `.rej` files into the SVN working copy and pauses.
1. Open the `.rej` file to see the rejected hunk.
2. Manually make the necessary edits in the target file.
3. Delete the `.rej` (and any `.orig`) file.
4. Run `git2svn replay --continue`.

### Q: Subversion error on Windows: `svn: command not found`
If using TortoiseSVN, ensure the **"command line client tools"** feature was selected during installation. If not, re-run the TortoiseSVN installer, choose **Modify**, and enable the command-line tools component. `git2svn` will also automatically discover `svn.exe` in `C:\Program Files\TortoiseSVN\bin\svn.exe` or `C:\Program Files\SlikSvn\bin\svn.exe`.

---

## 7. Recommended Workflow: Git Integration Branch Model

When using `git2svn` alongside an incremental SVN-to-Git mirror (such as `all-fast-export` or `svn2git`), the **Integration Branch Model** provides full Git freedom during development while keeping interactions with SVN completely deterministic, linear, and clean.

```mermaid
flowchart TD
    Feature["feature/* (working branches)"] -->|1. git rebase & merge --ff-only| Trunk["local 'trunk' (staging branch)"]
    Trunk -->|2. git2svn replay -u| SVN["Local SVN Workspace"]
    SVN -->|"3. svn commit (automatic in replay)"| Upstream["Remote SVN Repository"]
    Upstream -->|"4. incremental mirror (svn2git)"| Mirror["svn-mirror/trunk (tracking branch)"]
    Mirror -->|5. git checkout trunk && git reset --hard| Trunk
```

### Branch Roles
1. **`svn-mirror/trunk` (Remote-tracking branch):** Read-only mirror updated incrementally by your SVN-to-Git synchronization tool. Represents official SVN truth.
2. **`trunk` (Local staging / gateway branch):** Local branch tracking your mirror where commits are queued up and verified before shipping to SVN.
3. **`feature/*` (Topic branches):** Where you write code, create intermediate commits, and test.

### Step-by-Step Daily Workflow

#### 1. Start a Feature from Latest SVN
```bash
# Update local trunk to latest mirrored SVN revision
git checkout trunk
git reset --hard svn-mirror/trunk

# Create a topic branch
git checkout -b feature/login-page
```

#### 2. Develop & Commit Freely in Git
Make as many intermediate commits, amends, or rebases as needed:
```bash
git commit -m "feat: add login form"
git commit -m "test: add unit tests for login"
```

#### 3. Integrate into Local `trunk` (Preserving Linearity)
Because `git2svn replay` enforces a strictly linear history, **never create merge commits on `trunk`**. Choose either fast-forward or squash:

* **Option A: Fast-Forward Merge (Preserves atomic commits):**
  ```bash
  git checkout feature/login-page
  git rebase trunk
  git checkout trunk
  git merge --ff-only feature/login-page
  ```

* **Option B: Squash Merge (1 feature = 1 SVN revision):**
  ```bash
  git checkout trunk
  git merge --squash feature/login-page
  git commit -m "feat: complete login page implementation"
  ```

#### 4. Replay Commits to SVN
Replay the uncommitted range from the mirror base to `trunk`:
```bash
git2svn replay -u svn-mirror/trunk..trunk
```
*(The `-u` flag automatically runs `svn update` upon completion, ensuring your SVN working copy base revision is bumped to `HEAD`.)*

> [!TIP]
> If you configure `git config git2svn.defaultRange "svn-mirror/trunk..trunk"`, you can simply run:
> ```bash
> git2svn replay
> ```
> regardless of what feature branch you have checked out, safely and automatically shipping only the commits on `trunk`!

#### 5. Sync Mirror & Clean Up
Trigger your inbound SVN mirror tool and fast-forward your local `trunk` to match the official upstream history:
```bash
# Run incremental mirror import (e.g. svn2git / all-fast-export)
run-svn2git-sync

# Fetch and reset local trunk to authoritative mirror state
git fetch svn-mirror
git checkout trunk
git reset --hard svn-mirror/trunk

# Safely delete the merged feature branch
git branch -d feature/login-page
```


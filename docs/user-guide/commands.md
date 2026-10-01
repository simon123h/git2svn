# Command Reference: `setup`, `stage` & `replay`

This document details the usage, flags, and mechanics for `git2svn setup`, `git2svn stage`, and `git2svn replay`.


---

## 1. Command: `stage`

Prepares changes in the SVN workspace **without committing**. This leaves the working copy dirty, ready for code review in TortoiseSVN, `svn diff`, or standard manual commit workflows.

### 1.1 Single Commit
Port changes from a single Git revision:
```bash
git2svn stage a1b2c3d4 -s /path/to/svn
```

### 1.2 Range (Squash)
Squash an entire branch or commit series into a single uncommitted SVN changeset. Both dot-notation and two positional arguments are supported:

```bash
# Using dot notation:
git2svn stage main..feature/login -s /path/to/svn

# Using two arguments:
git2svn stage main feature/login -s /path/to/svn
```

### 1.3 Direct Object Extraction (`--copy`)
Bypasses diff generation and `git apply`, instead extracting exact file bytes directly from Git's object database (`git show <ref>:<path>`). Recommended for binary assets, images, or large refactors:

```bash
git2svn stage main..feature/assets --copy -s /path/to/svn
```

### 1.4 Full Tree Alignment (`--snapshot`)
Compares the full tree of a target Git branch or commit against the SVN workspace without needing to know where it branched off from in Git history:
* Identifies and stages deletions (`svn rm`) for files missing in Git.
* Identifies and stages additions (`svn add --parents`) for files new in Git.
* Updates modified files to match Git content, preserving SVN line-ending conventions.

```bash
git2svn stage feature/login --snapshot -s /path/to/svn
```

---

## 2. Command: `replay`

Sequentially ports Git commits onto the SVN workspace, creating an **atomic `svn commit` for each** with its original Git commit message, author body, and timestamps.

> [!IMPORTANT]
> `replay` requires a linear Git history (fast-forward only). If a range contains merge commits, rebase your Git branch first (`git rebase main`).

### 2.1 Single Commit (Cherry-pick)
Replay a single Git commit and immediately commit it to SVN:
```bash
git2svn replay a1b2c3d4 -s /path/to/svn
```

### 2.2 Range Replay
Replay each commit in the range sequentially into SVN history:
```bash
git2svn replay main..feature/login -s /path/to/svn
```

### 2.3 Automatic Working Copy Base Revision Update (`--no-update`)
Subversion working copies operate with **mixed revisions**: when `git2svn replay` commits revisions to the SVN repository, only touched files are updated locally while the working copy base revision remains pegged at the previous revision.

To keep your working copy aligned at `HEAD` and ready for subsequent mirror synchronizations, **`git2svn replay` automatically runs `svn update` upon completion by default**.

If you need to skip the automatic update (e.g. over high-latency connections), pass `--no-update`:
```bash
git2svn replay --no-update main..feature/login -s /path/to/svn
```

You can also disable automatic updates persistently for a repository via `.git/config`:
```bash
git config git2svn.autoUpdate false
```

---

## 3. Conflict Resolution Lifecycle

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

## 4. Command: `setup`

Automates initial repository configuration for a fresh clone or existing repository.

```bash
git2svn setup /path/to/svn
```

### What `setup` does:
1. **Validates SVN Working Copy:** Verifies that `/path/to/svn` contains a `.svn` directory.
2. **Auto-Detects Local Trunk Branch:** Inspects local branches with preference for `trunk`, falling back to `main`, `master`, or the current branch.
3. **Auto-Detects Remote Mirror Tracking Branch:** Scans remote branches for `svn-mirror/trunk`, `origin/trunk`, or branches matching the detected trunk name.
4. **Writes Git Configuration:**
   - `git config git2svn.svnDir <path/to/svn>`
   - `git config git2svn.defaultRange "<remote_branch>..<trunk_branch>"`
   - `git config pull.ff only` (prevents accidental merge commits when pulling)
5. **Configures Safe Productivity Aliases:**
   - `git config alias.svn-push`: Replays trunk to SVN, fetches SVN mirror, and safely resets trunk *only* if `trunk` matches `svn-mirror/trunk` (guarded by `git diff --quiet`).
   - `git config alias.svn-pull`: Fetches SVN mirror, fast-forwards trunk if clean, and automatically rebases local commits if unpushed work exists on trunk.
   - `git config alias.svn-status`: Inspects synchronization health, pending commits, and workspace state via `git2svn status`.

---

## 5. Command: `status`

Inspects synchronization health, pending commits in `git2svn.defaultRange`, and working tree states across both Git and Subversion.

```bash
git2svn status
```

### What `status` reports:
- **Git Workspace:** Active branch, latest commit hash & subject, and working tree cleanliness.
- **SVN Working Copy:** Path, target SVN URL, base revision, cleanliness, and lock status.
- **Synchronization Queue:** Configured `git2svn.defaultRange`, pending commit count & titles, and linear history validation (warns if merge commits exist).
- **In-Progress Replay:** State of paused replays (if a conflict occurred), remaining commits, active `.rej` conflict files, and actionable recovery commands (`--continue`, `--abort`, `--skip`).

### Exit Codes:
- `0`: Workspace is clean, unlocked, and synchronization queue is valid.
- `1`: SVN working copy is locked/dirty, replay is paused due to conflict, or merge commits violate linear history.

---

## 6. Structural Staging Mechanics

During patch application or file copying, `git2svn` maps Git status codes (`git diff --name-status`) to the corresponding Subversion commands:

| Git Status | Description | SVN Action Executed |
| :--- | :--- | :--- |
| **`A`** | Added file | Creates parent directories, runs `svn add <filepath> --parents` |
| **`D`** | Deleted file | Runs `svn rm <filepath>` |
| **`M`** | Modified file | Contents updated via `patch` or direct copy (no SVN structural command needed) |
| **`R`** | Renamed file (`R100 old new`) | Runs `svn rm <old>` followed by `svn add <new> --parents` |
| **`C`** | Copied file (`C100 src dst`) | Runs `svn add <new> --parents` |

# Command Reference: `stage`, `diff`, `replay`, `switch`, `setup`, `status`, `clean`, `doctor` & `completion`

This document details the usage, flags, and mechanics for all `git2svn` commands.


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

### 1.5 Immediate Diff Preview (`--diff` / `-p`)
Immediately preview the staged diff upon completion without needing to run a separate command:

```bash
git2svn stage main..feature/login --diff
```

---

## 2. Command: `diff`

Inspect uncommitted changes in the SVN workspace staged by `git2svn stage`.

```bash
# View full unified diff of staged changes:
git2svn diff

# View compact diffstat summary:
git2svn diff --stat
```

---

## 3. Command: `replay`

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

### 2.3 Automatic Working Copy Base Alignment
Subversion working copies operate with **mixed revisions**: when `git2svn replay` commits revisions to the SVN repository, only touched files are updated locally while the working copy base revision remains pegged at the previous revision. Furthermore, staging or replaying against a stale base revision causes `svn: E155015: Item is out of date` conflicts.

To guarantee atomic correctness:
- **`git2svn` automatically updates the working copy before staging or replaying**, ensuring your patches apply against the latest remote `HEAD`.
- **`git2svn replay` automatically updates the working copy upon completion**, ensuring the working copy base revision is cleanly bumped to `HEAD` for immediate subsequent operations or mirror pulls.

### 2.4 Auto-Skip Already Replayed Commits (`--force`)
When working iteratively on a feature branch (e.g. replaying `main..feature`, adding another commit to `feature`, and running `main..feature` again), earlier commits are already present in Subversion. Attempting to re-patch them would trigger patch failures or duplicate commit noise.

`git2svn replay` automatically inspects recent SVN commit logs:
- Any commit whose exact message already exists in the recent SVN log is **automatically skipped** with an informative `[SKIP]` notice:
  ```text
  [SKIP] [1/3] a1b2c3d4 'feat: initial feature structure' already committed to SVN. Skipping.
  [2/3] Applying c5d6e7f8: feat: add secondary calculation module... OK (0.15s)
  ```
- To bypass this automatic deduplication check and force replay of all commits, pass `--force`:
  ```bash
  git2svn replay main..feature --force
  ```

### 2.5 Branch Mismatch Detection Guard (`-y` / `--yes`)
To protect developers from accidentally committing Git feature branch work onto Subversion `trunk` (or vice-versa), `git2svn replay` checks whether the active Git branch aligns with the active SVN working copy branch before executing any commits.
- Standard root names (`trunk`, `main`, and `master`) are treated as equivalent.
- If your Git branch is `feature/user-auth` while the SVN working copy points to `trunk`, `git2svn` prints a prominent warning and asks for interactive confirmation:
  ```text
  [WARN]   Branch mismatch detected:
    Git branch : feature/user-auth
    SVN target : trunk

  You are about to replay commits from Git 'feature/user-auth' into SVN 'trunk'.
  Do you want to proceed? [y/N]: 
  ```
- If denied (`n` or `Enter`), replay immediately aborts without touching Subversion.
- To bypass this prompt in automated environments or CI/CD pipelines, pass `-y` or `--yes`:
  ```bash
  git2svn replay -y
  ```

### 2.6 Dynamic Default Range Resolution
When `git2svn replay` or `git2svn stage` is invoked without explicit commit references:
1. `git2svn` detects the active SVN working copy branch name (`<svn-branch>`).
2. It locates the configured mirror remote (`svn-mirror` or `origin`).
3. If `<mirror-remote>/<svn-branch>` exists in Git, `git2svn` dynamically calculates the range as:
   ```text
   <mirror-remote>/<svn-branch>..HEAD
   ```
This allows running `git2svn replay` seamlessly on feature branches without needing manual range arguments.

### 2.7 Interactive Step-by-Step Replay (`-i` / `--interactive`)
When replaying a multi-commit branch or sensitive commits, pass `-i` or `--interactive` to step through each revision before committing to Subversion:

```bash
git2svn replay origin/trunk..HEAD -i
```

Before each commit, `git2svn` displays the commit hash, commit message subject, and an interactive confirmation prompt:
```text
Apply commit 4c89ab12 (2/5): "feat(auth): add OAuth2 refresh token handling"?
[y]es / [s]kip / [a]ll / [d]iff / [q]uit / [?] [y]: 
```

#### Available Interactive Actions:
| Key | Command | Description |
|---|---|---|
| `y`, `yes`, `Enter` | **Apply** | Apply patch, stage changes in SVN, and commit with original message |
| `s`, `skip`, `n` | **Skip** | Skip this commit without touching SVN, advancing to the next commit |
| `a`, `all` | **Apply All** | Apply this and all remaining commits without prompting (turns off interactivity) |
| `d`, `diff` | **Show Diff** | Display colorized diffstat and patch diff (`git show --stat -p`), then re-prompt |
| `q`, `quit` | **Quit & Pause** | Pause replay cleanly and preserve state in `.svn/git2svn-replay.json` (`state="PAUSED"`) |
| `?`, `help` | **Help** | Display the interactive command menu |

#### Pausing & Resuming:
If you quit (`q` or `Ctrl+C`) during an interactive replay session:
- The session is saved cleanly in `PAUSED` state without generating conflict artifacts.
- To resume replay from the paused commit:
  ```bash
  git2svn replay --continue
  # Or resume with interactive prompting retained:
  git2svn replay --continue -i
  ```
- To skip the paused commit and resume the rest of the queue:
  ```bash
  git2svn replay --skip
  # Or interactively:
  git2svn replay --skip -i
  ```
- To abort and revert any pending changes:
  ```bash
  git2svn replay --abort
  ```

---

## 3. Command: `switch`

Switches the active Subversion working copy to another branch (e.g. `^/trunk` or `^/branches/<name>`).

```bash
# Switch to SVN trunk (^/trunk):
git2svn switch trunk

# Switch to a feature or maintenance branch (^/branches/release-2.0):
git2svn switch release-2.0

# Switch using explicit SVN branch or tag paths:
git2svn switch branches/team-feature
git2svn switch tags/v1.0.0
```

### Features:
- **Clean Workspace Verification:** Ensures the SVN working copy has no uncommitted changes before switching (run `git2svn clean` or `git2svn status` first).
- **Shorthand Normalization:**
  - `trunk`, `main`, and `master` automatically resolve to `^/trunk`.
  - Feature names like `auth-fix` resolve to `^/branches/auth-fix`.
  - Full URLs (`https://`, `svn://`, `^/...`) are preserved verbatim.
- **Dry-run Support:** Test switch targets with `git2svn switch <name> -n`.

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
   # Or with interactive confirmation enabled:
   git2svn replay --continue -i
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
  # Or with interactive confirmation enabled:
  git2svn replay --skip -i
  ```

---

## 5. Command: `setup`

Automates initial repository configuration for a fresh clone or existing repository. Accepts either a local working copy path or an SVN repository URL:

```bash
# Option A: With an SVN repository URL (managed working copy in .git/git2svn/svn_wc/):
git2svn setup https://svn.example.com/repo/trunk

# Option B: With an existing local SVN working copy:
git2svn setup /path/to/svn
```

### What `setup` does:
1. **Dynamic Target Detection:**
   - If an **SVN URL** (`https://`, `svn://`, `file://`, etc.) or repository store is given:
     - Sets up a managed internal working copy in `.git/git2svn/svn_wc/`.
     - Automatically runs `svn checkout <url> .git/git2svn/svn_wc/`.
     - Sets `git config git2svn.svnUrl <url>` and `git config git2svn.svnDir <managed_dir>`.
     - Future commands (`stage`, `replay`, `diff`, `status`) work seamlessly without needing `--svn-dir`!
   - If a **local working copy path** is given:
     - Validates that the path contains a `.svn` directory.
     - Sets `git config git2svn.svnDir <path/to/svn>`.
2. **Auto-Detects Local Trunk Branch:** Inspects local branches with preference for `trunk`, falling back to `main`, `master`, or the current branch.
3. **Auto-Detects Remote Mirror Tracking Branch:** Scans remote branches for `svn-mirror/trunk`, `origin/trunk`, or branches matching the detected trunk name.
4. **Writes Git Configuration:**
   - `git config git2svn.svnDir <path/to/svn>`
   - `git config git2svn.svnUrl <url>` (if URL provided)
   - `git config git2svn.mirrorRemote <mirror_remote>` (e.g. `svn-mirror` or `origin`)
   - `git config pull.ff only` (prevents accidental merge commits when pulling)
5. **Configures Safe Productivity Aliases:**
   - `git config alias.svn-push`: Replays trunk to SVN, fetches SVN mirror, and safely resets trunk *only* if `trunk` matches `svn-mirror/trunk` (guarded by `git diff --quiet`).
   - `git config alias.svn-pull`: Fetches SVN mirror, fast-forwards trunk if clean, and automatically rebases local commits if unpushed work exists on trunk.
   - `git config alias.svn-status`: Inspects synchronization health, pending commits, and workspace state via `git2svn status`.
6. **Installs Pre-Push Hook Guard:** Automatically installs `.git/hooks/pre-push` to block all direct `git push` commands targeting the SVN mirror remote (`origin` or `svn-mirror`), preventing history divergence with `svn2git` while allowing pushes to other collaboration remotes (forks/PRs).

---

## 6. Command: `status`

Inspects synchronization health, pending commits in the active synchronization range (`<mirrorRemote>/<svn-branch>..HEAD`), and working tree states across both Git and Subversion.

```bash
git2svn status
```

### What `status` reports:
- **Git Workspace:** Active branch, latest commit hash & subject, and working tree cleanliness.
- **SVN Working Copy:** Path, target SVN URL, base revision, cleanliness, and lock status.
- **Synchronization Queue:** Resolved sync range (showing whether dynamic or explicit override), pending commit count & titles, and linear history validation (warns if merge commits exist).
- **In-Progress Replay:** State of paused replays (if a conflict occurred), remaining commits, active `.rej` conflict files, and actionable recovery commands (`--continue`, `--abort`, `--skip`).

### Exit Codes:
- `0`: Workspace is clean, unlocked, and synchronization queue is valid.
- `1`: SVN working copy is locked/dirty, replay is paused due to conflict, or merge commits violate linear history.

---

## 7. Command: `clean`

Resets the SVN workspace to a clean, unlocked state by reverting uncommitted changes, removing untracked conflict artifacts (`.rej` / `.orig`), deleting unversioned files/directories, and releasing SVN locks.

```bash
# Clean staged uncommitted changes, conflict files, and release locks:
git2svn clean

# Completely delete the local managed working copy (fresh re-checkout on next run):
git2svn clean --purge
```

### What `clean` does:
1. **Releases Locks:** Invokes `svn cleanup` to clear stale `.svn` working copy locks.
2. **Reverts Changes:** Runs `svn revert -R .` to discard any staged or dirty modifications.
3. **Removes Conflict Artifacts:** Scans and removes any leftover `.rej` or `.orig` patch files.
4. **Removes Unversioned Files:** Identifies unversioned items (`?` in `svn status`) and deletes them.
5. **Clears Replay State:** Deletes `.git2svn-replay.json` if a paused replay was abandoned.
6. **Purge Mode (`--purge`):** Completely deletes the workspace directory (ideal for wiping a managed workspace in `.git/git2svn/svn_wc/`).

---

## 8. Command: `doctor`

Runs pre-flight diagnostics to inspect your environment, verify tool prerequisites (Git and Subversion CLI binaries), check repository and mirror tracking configurations, inspect pre-push hooks, and validate SVN working copy health.

```bash
# Run diagnostics on the current repository and workspace:
git2svn doctor

# Automatically repair fixable issues (aliases, pull.ff, hooks, locks, checkout):
git2svn doctor --fix

# Check a specific Git repository or SVN working copy:
git2svn doctor --git-dir /path/to/repo --svn-dir /path/to/svn
```

### Automated Remediation (`--fix`):
Passing `--fix` attempts safe, automated repair of common workspace and configuration problems:
- **Missing or broken pre-push hook guard:** Installs `.git/hooks/pre-push` and marks it executable (`chmod +x`).
- **`pull.ff` policy unset:** Configures `git config pull.ff only` to prevent accidental merge commits.
- **Missing aliases:** Automatically creates `alias.svn-push`, `alias.svn-pull`, and `alias.svn-status`.
- **Missing mirror remote:** Auto-detects and sets `git2svn.mirrorRemote` from detected remotes (`origin` or `svn-mirror`).
- **Managed working copy missing:** Automatically checks out the working copy if `git2svn.svnUrl` is configured.
- **Stale working copy locks:** Runs `svn cleanup` to release locks.

### What `doctor` diagnoses:
- **Git Environment:**
  - `git` CLI executable available in PATH and version.
  - Current directory validity as a Git repository, active branch, and latest commit.
  - Git working tree cleanliness (detects uncommitted modifications or untracked files).
- **Subversion Environment:**
  - `svn` CLI executable detected (supporting standard Unix paths, TortoiseSVN, and SlikSVN) and version.
- **SVN Working Copy:**
  - Working copy resolution (CLI argument, `$SVN_DIR`, `git2svn.svnDir`, or managed workspace in `.git/git2svn/svn_wc`).
  - Working copy validity (`.svn` presence and metadata parsing).
  - Remote repository accessibility, target URL, and base revision.
  - Working copy cleanliness and lock status (detects locks requiring `svn cleanup`).
  - Branch alignment (verifies that the checked out SVN branch matches the active Git branch or trunk).
- **Repository Configuration & Safety:**
  - `git2svn.mirrorRemote` configuration and presence in `git remote`.
  - Remote mirror tracking branch existence (e.g. `origin/trunk` or `<mirrorRemote>/<branch>`).
  - Fast-forward pull policy (`pull.ff = only`).
  - Productivity aliases (`git svn-push`, `git svn-pull`, `git svn-status`).
  - Pre-push hook guard installation and executable permissions (`.git/hooks/pre-push`).
- **Replay State:**
  - Scans for paused replay sessions blocked by patch conflicts.

### Diagnostic Badges & Exit Codes:
- `[OK]   `: The check passed completely.
- `[FIXED]`: An issue was automatically remediated by `--fix`.
- `[WARN] `: Non-critical recommendation or missing optional feature (hints provided on how to resolve).
- `[FAIL] `: Critical failure blocking synchronization (actionable remediation provided).

**Exit Codes:**
- `0`: All critical checks passed (workspace is functional).
- `1`: One or more critical checks failed.

---

## 9. Command: `init-mirror`

Bootstraps a local Subversion mirror Git repository using the official `git-svn` tool and automatically configures `git2svn` in a single command.

This is especially valuable when your organization does not run a server-side `svn2git` mirror daemon (like SubGit or a DevOps cron job):

```bash
# Basic setup with standard Subversion repository layout (trunk, branches, tags):
git2svn init-mirror https://svn.example.com/repo/myproject my-project --stdlayout

# Or with custom layout and fetching only recent revisions:
git2svn init-mirror https://svn.example.com/repo/myproject my-project \
    -T trunk -b branches -t tags \
    --revision 5000:HEAD

# Configure without fetching immediately:
git2svn init-mirror https://svn.example.com/repo/myproject my-project --stdlayout --no-fetch
```

### What `init-mirror` does automatically:
1. **Prerequisite Check:** Verifies `git-svn` is installed on your workstation, displaying package manager installation instructions if absent.
2. **Git Repository Initialization:** Initializes an empty Git repository in the target directory (defaulting to the current working directory).
3. **`git svn init`:** Configures Subversion remote tracking with standard or custom paths and remote ref prefix (`svn-mirror/`).
4. **`git svn fetch`:** Downloads revisions from Subversion into `refs/remotes/svn-mirror/trunk`.
5. **Branch Checkout:** Sets up the local `trunk` tracking branch.
6. **Automatic `git2svn setup`:** Sets up the managed local SVN working copy (`.git/git2svn/svn_wc`), configures fast-forward pull policy, and sets up productivity aliases adapted for `git-svn`:
   - `git svn-pull`: Invokes `git svn fetch` and merges/rebases the tracking branch.
   - `git svn-push`: Invokes `git2svn replay`, updates the mirror via `git svn fetch`, and verifies alignment.

### Options:
- `--stdlayout`: Use standard Subversion layout (`trunk`, `branches`, `tags`).
- `-T`, `--trunk <path>`: Subdirectory path for trunk (default: `trunk`).
- `-b`, `--branches <path>`: Subdirectory path for branches (default: `branches`).
- `-t`, `--tags <path>`: Subdirectory path for tags (default: `tags`).
- `--prefix <prefix>`: Prefix for remote tracking refs (default: `svn-mirror/`).
- `-r`, `--revision <rev>`: Initial SVN revision or range to fetch (e.g. `1000:HEAD`).
- `--no-fetch`: Initialize configuration without immediately pulling revisions.
- `-n`, `--dry-run`: Preview planned actions and commands without executing them.

---

## 10. Command: `clone`

Clones an SVN-to-Git mirror repository using `--origin svn-mirror` (or a custom name), automatically runs `git2svn setup <svn-url>` inside the clone to provision the managed SVN working copy, and optionally configures a secondary team Git remote (e.g. GitLab/GitHub) as `origin`:

```bash
# Clone mirror, configure managed SVN workspace, and set secondary team Git remote:
git2svn clone git@gitlab.com:mirrors/my-project.git my-project \
    --svn-url https://svn.example.com/repo/my-project/trunk \
    --origin-url git@github.com:my-org/my-project.git

# Clone mirror with default target directory derived from URL:
git2svn clone https://gitlab.com/mirrors/my-project.git \
    --svn-url https://svn.example.com/repo/my-project/trunk
```

### What `clone` does automatically:
1. **Directory Pre-check:** Verifies the destination path does not already exist with conflicting contents.
2. **Git Mirror Clone:** Executes `git clone <mirror-url> <target-dir> --origin svn-mirror` (or `--mirror-remote <name>`).
3. **Automated Setup:** Automatically executes `git2svn setup <svn-url>` in the cloned repository:
   - Configures `git2svn.svnUrl`, `git2svn.svnDir` (`.git/git2svn/svn_wc`), and `git2svn.mirrorRemote`.
   - Checks out the managed SVN working copy.
   - Installs pre-push protection hook (`.git/hooks/pre-push`).
   - Configures productivity aliases (`git svn-pull`, `git svn-push`, `git svn-status`).
4. **Secondary Remote Setup:** If `--origin-url` is specified, registers the secondary team repository as `origin`.

### Options:
- `mirror_url`: URL of the SVN-to-Git mirror repository to clone from.
- `directory`: Optional destination folder (default: derived from repository URL).
- `--svn-url <url>`: Authoritative Subversion repository URL to configure with `git2svn setup`.
- `--origin-url <url>`: Optional secondary developer Git remote URL to configure as `origin`.
- `--mirror-remote <name>`: Remote name for the mirror repository (default: `svn-mirror`).
- `-n`, `--dry-run`: Preview planned actions without cloning or executing commands.

---

## 11. Command: `completion`

Generates standalone shell tab-completion scripts for `bash`, `zsh`, or `fish`. Autocompletes subcommands, options, and dynamically suggests Git branches, tags, and SVN branch names.

```bash
# 1. Activate immediately in your current Bash session:
eval "$(git2svn completion bash)"

# 2. Or automatically install to standard user completion directory:
git2svn completion --install

# 3. Or generate script for custom shell configuration:
git2svn completion zsh > ~/.zsh/completion/_git2svn
git2svn completion fish > ~/.config/fish/completions/git2svn.fish
```

### Installation Targets (`--install`):
- **Bash:** `~/.local/share/bash-completion/completions/git2svn`
- **Zsh:** `~/.zsh/completion/_git2svn`
- **Fish:** `~/.config/fish/completions/git2svn.fish`

---

## 11. Structural Staging Mechanics

During patch application or file copying, `git2svn` maps Git status codes (`git diff --name-status`) to the corresponding Subversion commands:

| Git Status | Description | SVN Action Executed |
| :--- | :--- | :--- |
| **`A`** | Added file | Creates parent directories, runs `svn add <filepath> --parents` |
| **`D`** | Deleted file | Runs `svn rm <filepath>` |
| **`M`** | Modified file | Contents updated via `patch` or direct copy (no SVN structural command needed) |
| **`R`** | Renamed file (`R100 old new`) | Runs `svn rm <old>` followed by `svn add <new> --parents` |
| **`C`** | Copied file (`C100 src dst`) | Runs `svn add <new> --parents` |


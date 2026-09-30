# Recommended Workflow: Git Integration Branch Model

When using `git2svn` alongside an incremental SVN-to-Git mirror (such as `all-fast-export` or `svn2git`), the **Integration Branch Model** provides full Git freedom during development while keeping interactions with SVN completely deterministic, linear, and clean.

```mermaid
flowchart TD
    Feature["feature/* (working branches)"] -->|1. git rebase & merge --ff-only| Trunk["local 'trunk' (staging branch)"]
    Trunk -->|2. git2svn replay -u| SVN["Local SVN Workspace"]
    SVN -->|"3. svn commit (automatic in replay)"| Upstream["Remote SVN Repository"]
    Upstream -->|"4. incremental mirror (svn2git)"| Mirror["svn-mirror/trunk (tracking branch)"]
    Mirror -->|5. git checkout trunk && git reset --hard| Trunk
```

---

## 1. Branch Roles

1. **`svn-mirror/trunk` (Remote-tracking branch):** Read-only mirror updated incrementally by your SVN-to-Git synchronization tool. Represents official SVN truth.
2. **`trunk` (Local staging / gateway branch):** Local branch tracking your mirror where commits are queued up and verified before shipping to SVN.
3. **`feature/*` (Topic branches):** Where you write code, create intermediate commits, and test.

---

## 2. Step-by-Step Daily Workflow

### 2.1 Start a Feature from Latest SVN
```bash
# Update local trunk to latest mirrored SVN revision
git checkout trunk
git reset --hard svn-mirror/trunk

# Create a topic branch
git checkout -b feature/login-page
```

### 2.2 Develop & Commit Freely in Git
Make as many intermediate commits, amends, or rebases as needed:
```bash
git commit -m "feat: add login form"
git commit -m "test: add unit tests for login"
```

### 2.3 Integrate into Local `trunk` (Preserving Linearity)
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

### 2.4 Replay Commits to SVN
Replay the uncommitted range from the mirror base to `trunk`:
```bash
git2svn replay svn-mirror/trunk..trunk
```
*(By default, `replay` automatically executes `svn update` upon completion, ensuring your SVN working copy base revision is bumped to `HEAD`.)*

> [!TIP]
> If you configure `git config git2svn.defaultRange "svn-mirror/trunk..trunk"`, you can simply run:
> ```bash
> git2svn replay
> ```
> regardless of what feature branch you currently have checked out, safely and automatically shipping only the commits on `trunk`!

### 2.5 Sync Mirror & Clean Up
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

---

## 3. Productivity Tip: Git Aliases (e.g. `git svn-push`)

To make daily operation feel identical to a standard Git workflow, configure custom Git aliases in your repository's `.git/config` (or globally in `~/.gitconfig`):

```bash
# 1. git svn-push: Replays trunk to SVN, triggers mirror sync, and fast-forwards trunk
git config alias.svn-push "!f() { \
    git2svn replay && \
    run-svn2git-sync && \
    git fetch svn-mirror && \
    git checkout trunk && \
    git reset --hard svn-mirror/trunk; \
}; f"

# 2. git svn-pull: Updates SVN mirror, fetches into Git, and fast-forwards trunk (assuming there is a `run-svn2git-sync` command)
git config alias.svn-pull "!f() { \
    run-svn2git-sync && \
    git fetch svn-mirror && \
    git checkout trunk && \
    git reset --hard svn-mirror/trunk; \
}; f"
```

*(Note: Replace `run-svn2git-sync` with your actual mirror synchronization command or script).*

### Daily Developer Experience:
With these aliases configured alongside `git config git2svn.defaultRange "svn-mirror/trunk..trunk"`:

```bash
# Pull latest SVN state into local Git trunk:
git svn-pull

# Code on a feature branch, rebase, and fast-forward into trunk:
git checkout trunk
git merge --ff-only feature/login

# Push all trunk commits to SVN and update mirror in one step:
git svn-push
```

---

## 4. Linearity Guard (Preventing Accidental Merge Commits)

`git2svn replay` strictly enforces linear histories because Subversion cannot represent multi-parent Git merge graphs.

### Best Practice Configuration:
To prevent Git from ever creating accidental merge commits when pulling:
```bash
# Enforce fast-forward only when pulling
git config pull.ff only
```

### Pre-Commit Guard:
The repository's [`.githooks/pre-commit`](../../.githooks/pre-commit) hook automatically checks whether a commit being created on `trunk` is a merge commit (detecting `.git/MERGE_HEAD`), aborting the commit immediately:
```text
[git2svn pre-commit hook] ERROR: Merge commits are not permitted on 'trunk'.
[git2svn pre-commit hook] git2svn replay requires a strictly linear history.
[git2svn pre-commit hook] Please rebase your feature branch and use fast-forward: 'git merge --ff-only <branch>'
```



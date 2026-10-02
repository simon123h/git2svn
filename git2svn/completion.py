from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def detect_shell() -> str:
    """Detect current shell from $SHELL environment variable, defaulting to 'bash'."""
    raw_shell = os.environ.get("SHELL", "").strip()
    if raw_shell:
        shell_name = Path(raw_shell).name.lower()
        if shell_name in ("bash", "zsh", "fish"):
            return shell_name
    return "bash"


def generate_bash_completion() -> str:
    """Generate standalone Bash tab-completion script for git2svn."""
    return """# Bash tab-completion for git2svn
# Generated automatically by `git2svn completion bash`

_git2svn_completions() {
    local cur prev words cword
    _init_completion -n = 2>/dev/null || {
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    }

    local commands="stage diff replay switch setup status clean doctor completion"
    local common_opts="--git-dir -g --svn-dir -s --svn-url --dry-run -n --verbose -v --color -V --version -h --help"

    # Find the active subcommand (if any)
    local cmd=""
    local i=1
    while [ $i -lt $cword ]; do
        case "${words[i]}" in
            stage|diff|replay|switch|setup|status|clean|doctor|completion)
                cmd="${words[i]}"
                break
                ;;
            --git-dir|-g|--svn-dir|-s|--svn-url|--color)
                i=$((i + 1))  # skip option value
                ;;
        esac
        i=$((i + 1))
    done

    # If completing option value
    case "$prev" in
        --git-dir|-g|--svn-dir|-s)
            COMPREPLY=( $(compgen -d -- "$cur") )
            return 0
            ;;
        --color)
            COMPREPLY=( $(compgen -W "auto always never" -- "$cur") )
            return 0
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "$cur") )
            return 0
            ;;
    esac

    # If no subcommand is set yet
    if [ -z "$cmd" ]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=( $(compgen -W "$common_opts" -- "$cur") )
        else
            COMPREPLY=( $(compgen -W "$commands" -- "$cur") )
        fi
        return 0
    fi

    # Completing flags within a subcommand
    if [[ "$cur" == -* ]]; then
        case "$cmd" in
            stage)
                COMPREPLY=( $(compgen -W "$common_opts --copy --snapshot --diff -p" -- "$cur") )
                ;;
            diff)
                COMPREPLY=( $(compgen -W "$common_opts --stat" -- "$cur") )
                ;;
            replay)
                COMPREPLY=( $(compgen -W "$common_opts --copy --continue --abort --skip --force -y --yes -i --interactive" -- "$cur") )
                ;;
            clean)
                COMPREPLY=( $(compgen -W "$common_opts --purge" -- "$cur") )
                ;;
            completion)
                COMPREPLY=( $(compgen -W "--install -h --help" -- "$cur") )
                ;;
            *)
                COMPREPLY=( $(compgen -W "$common_opts" -- "$cur") )
                ;;
        esac
        return 0
    fi

    # Positional argument completion based on subcommand
    case "$cmd" in
        stage|replay)
            local refs=""
            if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
                refs=$(git for-each-ref --format='%(refname:short)' refs/heads/ refs/tags/ refs/remotes/ 2>/dev/null)
                refs="$refs HEAD"
            fi
            COMPREPLY=( $(compgen -W "$refs" -- "$cur") )
            ;;
        switch)
            local branches="trunk"
            if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
                branches="$branches $(git for-each-ref --format='%(refname:short)' refs/heads/ 2>/dev/null)"
            fi
            COMPREPLY=( $(compgen -W "$branches" -- "$cur") )
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "$cur") )
            ;;
        setup)
            COMPREPLY=( $(compgen -d -- "$cur") )
            ;;
    esac
}

complete -F _git2svn_completions git2svn
"""


def generate_zsh_completion() -> str:
    """Generate standalone Zsh tab-completion script for git2svn."""
    return """#compdef git2svn
# Zsh tab-completion for git2svn
# Generated automatically by `git2svn completion zsh`

_git2svn() {
    local curcontext="$curcontext" state line
    typeset -A opt_args

    local -a common_opts
    common_opts=(
        '(-g --git-dir)'{-g,--git-dir}'[Path to Git repository]:git repository:_files -/'
        '(-s --svn-dir)'{-s,--svn-dir}'[Path to SVN working copy]:svn working copy:_files -/'
        '--svn-url[SVN repository URL]:svn url:'
        '(-n --dry-run)'{-n,--dry-run}'[Print actions without executing]'
        '(-v --verbose)'{-v,--verbose}'[Enable verbose output]'
        '--color[Control colored output]:color mode:(auto always never)'
        '(-V --version)'{-V,--version}'[Show version number and exit]'
        '(-h --help)'{-h,--help}'[Show help message and exit]'
    )

    local -a subcommands
    subcommands=(
        'stage:Stage changes in SVN workspace without committing'
        'diff:Inspect uncommitted changes in the SVN workspace'
        'replay:Sequentially port Git commits into SVN history'
        'switch:Switch SVN working copy to a different branch'
        'setup:Automate initial repository configuration and aliases'
        'status:Inspect synchronization health and pending commits'
        'clean:Revert uncommitted changes and clear SVN locks'
        'doctor:Run pre-flight diagnostics on Git, SVN, hooks, and configuration'
        'completion:Generate shell tab-completion scripts'
    )

    _arguments -C \
        $common_opts \
        '1: :->command' \
        '*: :->args' && return 0

    case $state in
        command)
            _describe -t commands 'git2svn command' subcommands
            ;;
        args)
            case $words[1] in
                stage)
                    _arguments \
                        $common_opts \
                        '--copy[Extract files directly from Git object database]' \
                        '--snapshot[Align entire working copy to match target ref]' \
                        '(-p --diff)'{-p,--diff}'[Preview staged changes via svn diff]' \
                        '1:start ref:__git2svn_git_refs' \
                        '2:end ref:__git2svn_git_refs'
                    ;;
                diff)
                    _arguments \
                        $common_opts \
                        '--stat[Display diffstat summary of changed files]'
                    ;;
                replay)
                    _arguments \
                        $common_opts \
                        '--copy[Extract files directly from Git object database]' \
                        '--continue[Resume replay after resolving conflicts]' \
                        '--abort[Abort paused replay session and revert changes]' \
                        '--skip[Skip failed commit and continue with next]' \
                        '--force[Bypass duplicate commit check]' \
                        '(-y --yes)'{-y,--yes}'[Automatically confirm branch mismatch prompt]' \
                        '(-i --interactive)'{-i,--interactive}'[Interactive step-by-step confirmation for each commit]' \
                        '1:start ref:__git2svn_git_refs' \
                        '2:end ref:__git2svn_git_refs'
                    ;;
                switch)
                    _arguments \
                        $common_opts \
                        '1:branch:__git2svn_branches'
                    ;;
                setup)
                    _arguments \
                        $common_opts \
                        '1:SVN target:_files'
                    ;;
                clean)
                    _arguments \
                        $common_opts \
                        '--purge[Completely delete local managed SVN working copy]'
                    ;;
                doctor)
                    _arguments \
                        $common_opts
                    ;;
                completion)
                    _arguments \
                        '--install[Automatically install completion script for detected shell]' \
                        '1:shell:(bash zsh fish)'
                    ;;
            esac
            ;;
    esac
}

__git2svn_git_refs() {
    local -a refs
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        refs=(${(f)"$(git for-each-ref --format='%(refname:short)' refs/heads/ refs/tags/ refs/remotes/ 2>/dev/null)"})
        refs+=(HEAD)
        _describe -t refs 'git ref' refs
    fi
}

__git2svn_branches() {
    local -a branches
    branches=(trunk)
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        branches+=(${(f)"$(git for-each-ref --format='%(refname:short)' refs/heads/ 2>/dev/null)"})
    fi
    _describe -t branches 'branch' branches
}

_git2svn "$@"
"""


def generate_fish_completion() -> str:
    """Generate standalone Fish tab-completion script for git2svn."""
    return """# Fish tab-completion for git2svn
# Generated automatically by `git2svn completion fish`

function __fish_git2svn_needs_command
    set -l cmd (commandline -opc)
    if test (count $cmd) -eq 1
        return 0
    end
    return 1
end

function __fish_git2svn_using_command
    set -l cmd (commandline -opc)
    if test (count $cmd) -gt 1
        if test $argv[1] = $cmd[2]
            return 0
        end
    end
    return 1
end

function __fish_git2svn_git_refs
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1
        git for-each-ref --format='%(refname:short)' refs/heads/ refs/tags/ refs/remotes/ 2>/dev/null
        echo HEAD
    end
end

function __fish_git2svn_branches
    echo trunk
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1
        git for-each-ref --format='%(refname:short)' refs/heads/ 2>/dev/null
    end
end

# Common options
complete -c git2svn -s g -l git-dir -r -d "Path to Git repository"
complete -c git2svn -s s -l svn-dir -r -d "Path to SVN working copy"
complete -c git2svn -l svn-url -r -d "SVN repository URL"
complete -c git2svn -s n -l dry-run -d "Print actions without executing"
complete -c git2svn -s v -l verbose -d "Enable verbose output"
complete -c git2svn -l color -x -a "auto always never" -d "Control colored output"
complete -c git2svn -s V -l version -d "Show version number"
complete -c git2svn -s h -l help -d "Show help"

# Subcommands
complete -c git2svn -n __fish_git2svn_needs_command -a stage -d "Stage changes in SVN workspace without committing"
complete -c git2svn -n __fish_git2svn_needs_command -a diff -d "Inspect uncommitted changes in the SVN workspace"
complete -c git2svn -n __fish_git2svn_needs_command -a replay -d "Sequentially port Git commits into SVN history"
complete -c git2svn -n __fish_git2svn_needs_command -a switch -d "Switch SVN working copy to a different branch"
complete -c git2svn -n __fish_git2svn_needs_command -a setup -d "Automate initial repository configuration and aliases"
complete -c git2svn -n __fish_git2svn_needs_command -a status -d "Inspect synchronization health and pending commits"
complete -c git2svn -n __fish_git2svn_needs_command -a clean -d "Revert uncommitted changes and clear SVN locks"
complete -c git2svn -n __fish_git2svn_needs_command -a doctor -d "Run pre-flight diagnostics on Git, SVN, hooks, and configuration"
complete -c git2svn -n __fish_git2svn_needs_command -a completion -d "Generate shell tab-completion scripts"

# stage
complete -c git2svn -n "__fish_git2svn_using_command stage" -l copy -d "Extract files directly from Git object database"
complete -c git2svn -n "__fish_git2svn_using_command stage" -l snapshot -d "Align entire working copy to match target ref"
complete -c git2svn -n "__fish_git2svn_using_command stage" -s p -l diff -d "Preview staged changes via svn diff"
complete -c git2svn -n "__fish_git2svn_using_command stage" -a "(__fish_git2svn_git_refs)" -d "Git reference"

# diff
complete -c git2svn -n "__fish_git2svn_using_command diff" -l stat -d "Display diffstat summary of changed files"

# replay
complete -c git2svn -n "__fish_git2svn_using_command replay" -l copy -d "Extract files directly from Git object database"
complete -c git2svn -n "__fish_git2svn_using_command replay" -l continue -d "Resume replay after resolving conflicts"
complete -c git2svn -n "__fish_git2svn_using_command replay" -l abort -d "Abort paused replay session and revert changes"
complete -c git2svn -n "__fish_git2svn_using_command replay" -l skip -d "Skip failed commit and continue with next"
complete -c git2svn -n "__fish_git2svn_using_command replay" -l force -d "Bypass duplicate commit check"
complete -c git2svn -n "__fish_git2svn_using_command replay" -s y -l yes -d "Automatically confirm branch mismatch prompt"
complete -c git2svn -n "__fish_git2svn_using_command replay" -s i -l interactive -d "Interactive step-by-step confirmation for each commit"
complete -c git2svn -n "__fish_git2svn_using_command replay" -a "(__fish_git2svn_git_refs)" -d "Git reference"

# switch
complete -c git2svn -n "__fish_git2svn_using_command switch" -a "(__fish_git2svn_branches)" -d "Branch name"

# setup
complete -c git2svn -n "__fish_git2svn_using_command setup" -r -d "SVN target"

# clean
complete -c git2svn -n "__fish_git2svn_using_command clean" -l purge -d "Completely delete local managed SVN working copy"

# completion
complete -c git2svn -n "__fish_git2svn_using_command completion" -l install -d "Automatically install completion script for detected shell"
complete -c git2svn -n "__fish_git2svn_using_command completion" -a "bash zsh fish" -d "Target shell"
"""


def get_completion_script(shell: str) -> str:
    """Return completion script for target shell ('bash', 'zsh', or 'fish')."""
    s = shell.lower().strip()
    if s == "bash":
        return generate_bash_completion()
    if s == "zsh":
        return generate_zsh_completion()
    if s == "fish":
        return generate_fish_completion()
    raise ValueError(f"Unsupported shell: '{shell}'. Supported shells: bash, zsh, fish.")


def get_default_install_path(shell: str) -> Path:
    """Return default user completion directory file path for target shell."""
    home = Path.home()
    s = shell.lower().strip()
    if s == "bash":
        return home / ".local" / "share" / "bash-completion" / "completions" / "git2svn"
    if s == "zsh":
        return home / ".zsh" / "completion" / "_git2svn"
    if s == "fish":
        return home / ".config" / "fish" / "completions" / "git2svn.fish"
    raise ValueError(f"Unsupported shell: '{shell}'")


def install_completion(shell: str) -> Path:
    """Install completion script to default user path and return the destination Path."""
    script = get_completion_script(shell)
    target_path = get_default_install_path(shell)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(script, encoding="utf-8")
    return target_path


def run_completion(shell: Optional[str] = None, install: bool = False) -> int:
    """
    Handle the `git2svn completion` CLI command.
    If install=True, installs script to standard user completion path.
    Otherwise, prints the completion script to stdout.
    """
    target_shell = (shell or detect_shell()).lower().strip()
    if target_shell not in ("bash", "zsh", "fish"):
        print(f"Error: Unsupported shell '{target_shell}'. Choose from: bash, zsh, fish.", file=sys.stderr)
        return 1

    if install:
        dest = install_completion(target_shell)
        print(f"Successfully installed {target_shell} completion to: {dest}")
        if target_shell == "bash":
            print("To activate now, run:\n  source ~/.local/share/bash-completion/completions/git2svn")
        elif target_shell == "zsh":
            print(
                "Ensure ~/.zsh/completion is in your $fpath in ~/.zshrc:\n"
                "  fpath=(~/.zsh/completion $fpath)\n"
                "  autoload -Uz compinit && compinit"
            )
        elif target_shell == "fish":
            print("Fish will load the completion automatically in newly opened shells.")
        return 0

    script = get_completion_script(target_shell)
    print(script, end="")
    return 0

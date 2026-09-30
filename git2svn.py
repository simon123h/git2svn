#!/usr/bin/env python3
"""
git2svn entrypoint script.
Delegates to the modularized git2svn package.
"""

from __future__ import annotations

import sys

from git2svn.cli import main

if __name__ == "__main__":
    sys.exit(main())

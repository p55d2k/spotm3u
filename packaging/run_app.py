"""PyInstaller entry point for the packaged spotm3u application.

Importing the launcher by its full package path keeps the package's relative
imports valid inside the frozen bundle (a bare ``launcher.py`` script would
have no parent package).
"""

from spotm3u.launcher import main

if __name__ == "__main__":
    main()

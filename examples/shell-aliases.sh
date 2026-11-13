# Source this from ~/.bashrc or ~/.zshrc to route installs through slopwatch.
# `command pip ...` still bypasses it when you really need to.

pip()  { if [ "$1" = "install" ]; then slopwatch run pip "$@"; else command pip "$@"; fi; }
pip3() { if [ "$1" = "install" ]; then slopwatch run pip3 "$@"; else command pip3 "$@"; fi; }
npm()  { case "$1" in install|i|add) slopwatch run npm "$@" ;; *) command npm "$@" ;; esac; }

#!/bin/sh
# Set up unprivileged access and a desktop entry.
#
# Nothing here is required to *use* m693ctl - running it as root works - but
# without the udev rule every invocation needs sudo, which is a poor way to
# live with a mouse.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
prefix=${PREFIX:-$HOME/.local}

echo "Installing the udev rule (needs root)…"
sudo install -m 0644 "$here/99-redragon-m693.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules

# The rule TAGs the node with uaccess, which covers a logged-in seat.  The
# input group is the fallback for the cases uaccess does not reach - a remote
# session, or a system whose logind is not managing the seat.
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    echo "Adding $USER to the input group…"
    sudo usermod -aG input "$USER"
    echo "  (log out and back in, or use: sg input -c '…', for this to apply)"
fi

echo "Installing the desktop entry under $prefix…"
install -d "$prefix/share/applications" "$prefix/share/icons/hicolor/scalable/apps"
install -m 0644 "$here/packaging/m693-gui.desktop" "$prefix/share/applications/"
install -m 0644 "$here/m693gui/assets/m693.svg" \
    "$prefix/share/icons/hicolor/scalable/apps/m693-gui.svg"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$prefix/share/applications" || true
fi

echo
echo "Done. Re-plug the receiver, then check with:  $here/m693ctl list"
echo "If m693-gui is not on PATH yet, install the package with:"
echo "    pip install --user '$here'[gui]"

# Maintainer: Raj singh chauhan <rishabhjain27082004@gmail.com>
pkgname=m693ctl
pkgver=1.2.2
pkgrel=1
pkgdesc="Driver, CLI and GUI for the Redragon M693-RGB gaming mouse"
arch=('any')
url="https://github.com/rajchauhan28/m693-linux"
license=('MIT')
# The driver and CLI need nothing but python; Qt is only for the GUI, so it is
# an optional dependency rather than a hard one - m693ctl stays usable on a
# headless box.
depends=('python' 'hicolor-icon-theme')
optdepends=('pyside6: the graphical configuration tool (m693-gui)')
makedepends=('git' 'python-build' 'python-installer' 'python-wheel'
             'python-setuptools')
# Built from the working tree this PKGBUILD sits in.  The checkout is not
# named after the package: makepkg would put its bare clone next to the
# PKGBUILD, where an "m693ctl" directory would collide with the launcher
# script of the same name.
_src=m693-src
source=("${_src}::git+file://${startdir}")
sha256sums=('SKIP')
install="${pkgname}.install"

pkgver() {
    cd "$srcdir/${_src}"
    printf '%s' "$(python -c 'import m693; print(m693.__version__)')"
}

build() {
    cd "$srcdir/${_src}"
    python -m build --wheel --no-isolation
}

check() {
    cd "$srcdir/${_src}"
    # The GUI tests need a Qt platform plugin; skip them when pyside6 is not
    # installed rather than failing a build that does not depend on it.
    python tests/test_unknowns.py
    python tests/test_retry.py
    python tests/test_battery.py
    if python -c 'import PySide6' 2>/dev/null; then
        export QT_QPA_PLATFORM=offscreen
        for t in tests/test_*.py; do python "$t" || return 1; done
    fi
}

package() {
    cd "$srcdir/${_src}"
    python -m installer --destdir="$pkgdir" dist/*.whl

    # Unprivileged access to the vendor HID interface.
    install -Dm644 99-redragon-m693.rules \
        "$pkgdir/usr/lib/udev/rules.d/99-redragon-m693.rules"

    install -Dm644 packaging/m693-gui.desktop \
        "$pkgdir/usr/share/applications/m693-gui.desktop"
    install -Dm644 m693gui/assets/m693.svg \
        "$pkgdir/usr/share/icons/hicolor/scalable/apps/m693-gui.svg"

    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
    install -Dm644 PROTOCOL.md "$pkgdir/usr/share/doc/$pkgname/PROTOCOL.md"
}

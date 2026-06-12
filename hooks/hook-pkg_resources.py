from PyInstaller.utils.hooks import collect_all

# pkg_resources vendored packages — collected in full so the
# VendorImporter shim can resolve them inside the frozen app.
# Module-level variables datas/binaries/hiddenimports are
# required by the PyInstaller hook API; they must be initialised
# at module scope before being accumulated.
datas, binaries, hiddenimports = [], [], []

_VENDOR_PKGS = [
    'pkg_resources',
    'pkg_resources._vendor',
    'pkg_resources._vendor.jaraco',
    'pkg_resources._vendor.jaraco.text',
    'pkg_resources._vendor.more_itertools',
    'pkg_resources._vendor.platformdirs',
    'pkg_resources._vendor.packaging',
    'pkg_resources._vendor.importlib_resources',
    'platformdirs',
    'jaraco',
    'jaraco.text',
    'more_itertools',
]

for _pkg in _VENDOR_PKGS:
    try:
        _d, _b, _h = collect_all(_pkg)
        datas          += _d
        binaries        += _b
        hiddenimports   += _h
    except Exception:
        pass  # package not installed; skip silently

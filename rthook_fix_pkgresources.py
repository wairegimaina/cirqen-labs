# rthook_fix_pkgresources.py
# Patches pkg_resources to skip dist-info with invalid version strings.
def _patch_pkg_resources():
    try:
        import pkg_resources
        from pkg_resources.extern.packaging.version import InvalidVersion
        _orig_add = pkg_resources.WorkingSet.add_entry
        def _safe_add_entry(self, entry):
            try:
                _orig_add(self, entry)
            except InvalidVersion:
                pass
        pkg_resources.WorkingSet.add_entry = _safe_add_entry
        _orig_find = pkg_resources.find_on_path
        def _safe_find_on_path(importer, path_item, only=False):
            try:
                yield from _orig_find(importer, path_item, only=only)
            except InvalidVersion:
                return
        pkg_resources.find_on_path = _safe_find_on_path
    except Exception:
        pass
_patch_pkg_resources()

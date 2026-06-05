"""Scaffold a new deployment: drop a starter config.yaml + rubric.md (the bundled
examples) into a target directory so a freshly pip-installed user can get going
without cloning the repo."""
import os
import shutil

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# (bundled example file, name written into the target dir)
_FILES = [
    ("config.example.yaml", "config.yaml"),
    ("rubric.example.md", "rubric.md"),
]


def init_project(dest=".", force=False):
    """Copy the example config + rubric into `dest`. Existing files are skipped
    unless `force`. Returns (written, skipped) lists of paths."""
    os.makedirs(dest, exist_ok=True)
    written, skipped = [], []
    for src_name, out_name in _FILES:
        out = os.path.join(dest, out_name)
        if os.path.exists(out) and not force:
            skipped.append(out)
            continue
        shutil.copyfile(os.path.join(_DATA, src_name), out)
        written.append(out)
    return written, skipped

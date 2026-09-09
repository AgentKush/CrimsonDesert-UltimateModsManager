"""Directory picker that works under Wine/Proton.

GitHub #386 (NonDScript, Bazzite): Wine's native folder dialog hides
dot-folders and cannot be told to show them, so a Linux user browsing
for the game under ``Z:/home/<user>/.local/share/Steam`` literally
cannot enter ``.local``. When running under Wine we swap to Qt's own
(non-native) dialog with hidden entries visible; on real Windows the
native dialog is kept — it's the one users expect.
"""
from __future__ import annotations

from PySide6.QtCore import QDir
from PySide6.QtWidgets import QFileDialog, QWidget

from cdumm.platform import is_wine


def pick_directory(parent: QWidget | None, title: str,
                   start_dir: str = "") -> str:
    """Folder picker; returns the chosen path or "" on cancel."""
    if not is_wine():
        return QFileDialog.getExistingDirectory(parent, title, start_dir)

    dlg = QFileDialog(parent, title, start_dir)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dlg.setFilter(dlg.filter() | QDir.Filter.Hidden)
    if dlg.exec():
        files = dlg.selectedFiles()
        if files:
            return files[0]
    return ""

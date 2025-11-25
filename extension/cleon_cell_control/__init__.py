"""Cleon Cell Control - JupyterLab extension for cell manipulation from kernel."""

from ._version import __version__
from .api import CellController, insert_and_run, insert_cell, replace_cell, execute_cell

__all__ = [
    "__version__",
    "CellController",
    "insert_and_run",
    "insert_cell",
    "replace_cell",
    "execute_cell"
]


def _jupyter_labextension_paths():
    return [{
        "src": "labextension",
        "dest": "cleon-cell-control"
    }]

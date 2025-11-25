"""Test that import cleon automatically registers all magics including gemini."""

import sys
import types


class MockIPython:
    def __init__(self):
        self.magics_manager = type("obj", (object,), {"magics": {"cell": {}}})()
        self.registered = []
        self.user_ns = {"In": [], "Out": {}}

    def register_magic_function(self, func, magic_kind, magic_name):
        self.registered.append(magic_name)
        if magic_kind == "cell":
            self.magics_manager.magics["cell"][magic_name] = func


def test_gemini_magic_registered_on_import():
    """Test that %%gemini magic is automatically registered when importing cleon."""
    # Create mock instance
    mock_ip = MockIPython()

    # Create a mock IPython module
    mock_ipython = types.ModuleType("IPython")
    mock_ipython.get_ipython = lambda: mock_ip  # type: ignore
    mock_ipython.display = types.ModuleType("IPython.display")
    mock_ipython.display.display = lambda *args, **kwargs: None
    mock_ipython.display.update_display = lambda *args, **kwargs: None
    mock_ipython.display.HTML = lambda data: None
    mock_ipython.display.Markdown = lambda data: None

    # Save original modules
    orig_ipython = sys.modules.get("IPython")
    orig_display = sys.modules.get("IPython.display")

    try:
        # Monkey patch IPython BEFORE importing cleon
        sys.modules["IPython"] = mock_ipython
        sys.modules["IPython.display"] = mock_ipython.display

        # Remove cleon from cache to force fresh import
        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        # Reset auto-init flag if it exists
        import cleon

        cleon._AUTO_INITIALIZED = False

        # Re-trigger auto registration
        cleon._auto_register_magic()

        # Check if gemini is registered
        assert "gemini" in mock_ip.magics_manager.magics["cell"], (
            f"%%gemini magic not registered. Registered: {mock_ip.registered}"
        )

    finally:
        # Restore original modules
        if orig_ipython is not None:
            sys.modules["IPython"] = orig_ipython
        elif "IPython" in sys.modules:
            del sys.modules["IPython"]

        if orig_display is not None:
            sys.modules["IPython.display"] = orig_display
        elif "IPython.display" in sys.modules:
            del sys.modules["IPython.display"]

"""Test script to verify gemini magic registration."""


class MockIPython:
    def __init__(self):
        self.magics_manager = type("obj", (object,), {"magics": {"cell": {}}})()
        self.registered = []

    def register_magic_function(self, func, magic_kind, magic_name):
        self.registered.append(magic_name)
        if magic_kind == "cell":
            self.magics_manager.magics["cell"][magic_name] = func


def test_refresh_auto_route_registers_gemini():
    """Test that refresh_auto_route registers the gemini magic."""
    import cleon.magic

    # Create mock IPython instance
    mock_ip = MockIPython()

    # Save original
    original_get_ipython = cleon.magic.get_ipython

    try:
        # Monkey patch get_ipython
        cleon.magic.get_ipython = lambda: mock_ip

        # Test refresh_auto_route
        cleon.magic.refresh_auto_route(ipython=mock_ip)

        # Verify gemini is registered
        assert "gemini" in mock_ip.magics_manager.magics["cell"], (
            f"%%gemini magic not found. Registered: {mock_ip.registered}"
        )

    finally:
        # Restore
        cleon.magic.get_ipython = original_get_ipython

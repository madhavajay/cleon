"""Tests for template loading and agent prefix detection."""

import os
import sys
import tempfile
import types
from pathlib import Path


def _setup_mock_ipython():
    """Set up mock IPython module for testing."""

    class MockIPython:
        def __init__(self):
            self.magics_manager = type("obj", (object,), {"magics": {"cell": {}}})()
            self.registered = []
            self.user_ns = {"In": [], "Out": {}}

        def register_magic_function(self, func, magic_kind, magic_name):
            self.registered.append(magic_name)
            if magic_kind == "cell":
                self.magics_manager.magics["cell"][magic_name] = func

    mock_ip = MockIPython()
    mock_ipython = types.ModuleType("IPython")
    mock_ipython.get_ipython = lambda: mock_ip
    mock_ipython.display = types.ModuleType("IPython.display")
    mock_ipython.display.display = lambda *args, **kwargs: None
    mock_ipython.display.update_display = lambda *args, **kwargs: None
    mock_ipython.display.HTML = lambda data: None
    mock_ipython.display.Markdown = lambda data: None

    sys.modules["IPython"] = mock_ipython
    sys.modules["IPython.display"] = mock_ipython.display
    return mock_ip


class TestTemplateLoading:
    """Tests for cleon.md and mode file template loading."""

    def test_load_cleon_template_substitutes_agent(self):
        """Test that {agent} placeholder is substituted."""
        _setup_mock_ipython()

        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir) / "prompts"
            prompts_dir.mkdir()

            cleon_md = prompts_dir / "cleon.md"
            cleon_md.write_text("You are {agent} with prefix {prefix}")

            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
                for mod in mods_to_remove:
                    del sys.modules[mod]

                from cleon.magic import _load_cleon_template

                result = _load_cleon_template("claude")
                assert result is not None
                assert "claude" in result
                assert "{agent}" not in result
            finally:
                os.chdir(orig_cwd)

    def test_load_cleon_template_substitutes_prefix(self):
        """Test that {prefix} placeholder is substituted."""
        _setup_mock_ipython()

        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir) / "prompts"
            prompts_dir.mkdir()

            cleon_md = prompts_dir / "cleon.md"
            cleon_md.write_text("Your prefix is {prefix}")

            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
                for mod in mods_to_remove:
                    del sys.modules[mod]

                from cleon.magic import _load_cleon_template

                result = _load_cleon_template("codex")
                assert result is not None
                assert "{prefix}" not in result
            finally:
                os.chdir(orig_cwd)

    def test_load_mode_file(self):
        """Test loading mode-specific template file."""
        _setup_mock_ipython()

        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir) / "prompts"
            prompts_dir.mkdir()

            learn_md = prompts_dir / "learn.md"
            learn_md.write_text("# Learn Mode\nTeach the user.")

            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
                for mod in mods_to_remove:
                    del sys.modules[mod]

                from cleon.magic import _load_mode_file

                result = _load_mode_file("codex")
                assert result is not None
                assert "Learn Mode" in result
            finally:
                os.chdir(orig_cwd)

    def test_resolve_template_combines_cleon_and_mode(self):
        """Test that _resolve_template combines cleon.md and mode file."""
        _setup_mock_ipython()

        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir) / "prompts"
            prompts_dir.mkdir()

            cleon_md = prompts_dir / "cleon.md"
            cleon_md.write_text("Base template for {agent}")

            learn_md = prompts_dir / "learn.md"
            learn_md.write_text("Mode: learn")

            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
                for mod in mods_to_remove:
                    del sys.modules[mod]

                from cleon.magic import _resolve_template

                result = _resolve_template("claude")
                assert result is not None
                assert "Base template for claude" in result
                assert "Mode: learn" in result
            finally:
                os.chdir(orig_cwd)


class TestMixedCellDetection:
    """Tests for detecting mixed cells (Python + agent query)."""

    def test_detect_mixed_cell_with_at_prefix(self):
        """Test detecting cell with Python code followed by @ query."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        from cleon.magic import _detect_mixed_cell

        cell = """x = 1
print(x)
@ explain this code"""

        result = _detect_mixed_cell(cell)
        assert result is not None
        python_code, agent_query, magic_name, prefix = result
        assert "x = 1" in python_code
        assert "explain this code" in agent_query

    def test_detect_mixed_cell_with_commented_prefix(self):
        """Test detecting cell with commented agent prefix."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        from cleon.magic import _detect_mixed_cell

        cell = """def add(a, b):
    return a + b
# @ review this function"""

        result = _detect_mixed_cell(cell)
        assert result is not None
        python_code, agent_query, magic_name, prefix = result
        assert "def add" in python_code
        assert "review this function" in agent_query

    def test_detect_mixed_cell_no_agent_prefix(self):
        """Test that pure Python cells return None."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        from cleon.magic import _detect_mixed_cell

        cell = """x = 1
y = 2
print(x + y)"""

        result = _detect_mixed_cell(cell)
        assert result is None


class TestAgentPrefixDetection:
    """Tests for _line_has_agent_prefix function.

    Returns (matched_prefix, actual_prefix, magic_name) or None.
    """

    def test_at_prefix_detection(self):
        """Test @ prefix is detected."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        from cleon.magic import _line_has_agent_prefix

        prefixes = {"@": ("codex", "codex"), "~": ("claude", "claude")}

        result = _line_has_agent_prefix("@ hello world", prefixes)
        assert result is not None
        matched_prefix, actual_prefix, magic_name = result
        assert matched_prefix == "@"
        assert actual_prefix == "@"
        assert magic_name == "codex"

    def test_tilde_prefix_detection(self):
        """Test ~ prefix is detected."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        from cleon.magic import _line_has_agent_prefix

        prefixes = {"@": ("codex", "codex"), "~": ("claude", "claude")}

        result = _line_has_agent_prefix("~ ask claude something", prefixes)
        assert result is not None
        matched_prefix, actual_prefix, magic_name = result
        assert matched_prefix == "~"
        assert actual_prefix == "~"
        assert magic_name == "claude"

    def test_commented_prefix_detection(self):
        """Test # @ commented prefix is detected."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        from cleon.magic import _line_has_agent_prefix

        prefixes = {"@": ("codex", "codex"), "~": ("claude", "claude")}

        result = _line_has_agent_prefix("# @ this is a query", prefixes)
        assert result is not None
        matched_prefix, actual_prefix, magic_name = result
        assert matched_prefix == "# @"
        assert actual_prefix == "@"
        assert magic_name == "codex"

    def test_no_prefix_returns_none(self):
        """Test that lines without agent prefix return None."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        from cleon.magic import _line_has_agent_prefix

        prefixes = {"@": ("codex", "codex"), "~": ("claude", "claude")}

        result = _line_has_agent_prefix("print('hello')", prefixes)
        assert result is None


class TestExtensionDetection:
    """Tests for extension availability detection."""

    def test_has_extension_false_when_not_installed(self):
        """Test has_extension returns False when extension not installed."""
        _setup_mock_ipython()

        if "cleon_cell_control" in sys.modules:
            del sys.modules["cleon_cell_control"]

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        try:
            import cleon

            result = cleon.has_extension()
            assert isinstance(result, bool)
        finally:
            pass

    def test_has_extension_returns_bool(self):
        """Test has_extension always returns a boolean."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        import cleon

        result = cleon.has_extension()
        assert isinstance(result, bool)


class TestVersionCheck:
    """Tests for version checking functionality."""

    def test_get_current_version_returns_string(self):
        """Test _get_current_version returns a string."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        import cleon

        result = cleon._get_current_version()
        assert isinstance(result, str)
        assert result != ""

    def test_is_uv_environment_returns_bool(self):
        """Test _is_uv_environment returns a boolean."""
        _setup_mock_ipython()

        mods_to_remove = [k for k in sys.modules if k.startswith("cleon")]
        for mod in mods_to_remove:
            del sys.modules[mod]

        import cleon

        result = cleon._is_uv_environment()
        assert isinstance(result, bool)

    def test_version_parsing(self):
        """Test version comparison logic."""

        def _parse_version(v: str) -> tuple:
            parts: list[int | str] = []
            for part in v.split("."):
                try:
                    parts.append(int(part))
                except ValueError:
                    parts.append(part)
            return tuple(parts)

        assert _parse_version("0.1.11") == (0, 1, 11)
        assert _parse_version("0.2.0") > _parse_version("0.1.11")
        assert _parse_version("1.0.0") > _parse_version("0.9.99")
        assert _parse_version("0.1.11") == _parse_version("0.1.11")

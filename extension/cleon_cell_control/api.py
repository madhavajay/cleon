"""Python API for controlling Jupyter cells from the kernel."""

from __future__ import annotations

import time
from typing import Literal, Callable

_controller: CellController | None = None


class CellController:
    """Controller for manipulating Jupyter notebook cells via comm."""

    COMM_TARGET = "cleon_cell_control"

    def __init__(self):
        self._comm = None
        self._last_response = None
        self._response_received = False

    def _ensure_comm(self):
        """Ensure comm channel is open."""
        if self._comm is not None:
            return

        try:
            from ipykernel.comm import Comm
        except ImportError:
            raise RuntimeError("ipykernel not available - are you in a Jupyter kernel?")

        self._comm = Comm(target_name=self.COMM_TARGET)
        self._comm.on_msg(self._handle_response)

    def _handle_response(self, msg):
        """Handle response from frontend."""
        self._last_response = msg.get("content", {}).get("data", {})
        self._response_received = True

    def _send_and_wait(self, data: dict, timeout: float = 2.0) -> dict:
        """Send command and wait for response."""
        self._ensure_comm()
        self._response_received = False
        self._last_response = None

        self._comm.send(data)

        start = time.time()
        while not self._response_received and (time.time() - start) < timeout:
            time.sleep(0.05)

        if not self._response_received:
            return {"status": "timeout", "message": "No response from frontend"}

        return self._last_response or {"status": "error", "message": "Empty response"}

    def insert_below(
        self,
        code: str = "",
        cell_type: Literal["code", "markdown"] = "code"
    ) -> dict:
        """Insert a new cell below the current cell."""
        return self._send_and_wait({
            "action": "insert_below",
            "code": code,
            "cell_type": cell_type
        })

    def insert_above(
        self,
        code: str = "",
        cell_type: Literal["code", "markdown"] = "code"
    ) -> dict:
        """Insert a new cell above the current cell."""
        return self._send_and_wait({
            "action": "insert_above",
            "code": code,
            "cell_type": cell_type
        })

    def replace(self, code: str) -> dict:
        """Replace the current cell's content."""
        return self._send_and_wait({
            "action": "replace",
            "code": code
        })

    def execute(self) -> dict:
        """Execute the current cell."""
        return self._send_and_wait({"action": "execute"})

    def insert_and_run(
        self,
        code: str,
        cell_type: Literal["code", "markdown"] = "code"
    ) -> dict:
        """Insert a new cell below and immediately execute it."""
        return self._send_and_wait({
            "action": "insert_and_run",
            "code": code,
            "cell_type": cell_type
        })


def _get_controller() -> CellController:
    """Get or create the global cell controller."""
    global _controller
    if _controller is None:
        _controller = CellController()
    return _controller


def insert_cell(
    code: str = "",
    position: Literal["above", "below"] = "below",
    cell_type: Literal["code", "markdown"] = "code"
) -> dict:
    """Insert a new cell.

    Args:
        code: The code/content to put in the cell
        position: "above" or "below" the current cell
        cell_type: "code" or "markdown"

    Returns:
        Response dict with status
    """
    ctrl = _get_controller()
    if position == "above":
        return ctrl.insert_above(code, cell_type)
    return ctrl.insert_below(code, cell_type)


def replace_cell(code: str) -> dict:
    """Replace the current cell's content.

    Args:
        code: The new content for the cell

    Returns:
        Response dict with status
    """
    return _get_controller().replace(code)


def execute_cell() -> dict:
    """Execute the current cell.

    Returns:
        Response dict with status
    """
    return _get_controller().execute()


def insert_and_run(
    code: str,
    cell_type: Literal["code", "markdown"] = "code"
) -> dict:
    """Insert a new cell below and immediately execute it.

    This is the main function for the "clipboard to cell" feature.

    Args:
        code: The code to insert and run
        cell_type: "code" or "markdown"

    Returns:
        Response dict with status

    Example:
        >>> from cleon_cell_control import insert_and_run
        >>> insert_and_run("print('Hello from new cell!')")
    """
    return _get_controller().insert_and_run(code, cell_type)


def create_insert_button(code: str, label: str = "📥 Insert & Run") -> None:
    """Create an HTML button that inserts and runs code when clicked.

    This creates a button in the cell output that, when clicked,
    will insert a new cell below and execute the provided code.

    Args:
        code: The code to insert and run when button is clicked
        label: Button label text

    Example:
        >>> from cleon_cell_control import create_insert_button
        >>> create_insert_button("import pandas as pd\\ndf = pd.DataFrame()")
    """
    from IPython.display import display, HTML
    import html
    import uuid

    btn_id = f"cleon_insert_{uuid.uuid4().hex[:8]}"
    escaped_code = html.escape(code).replace("\\", "\\\\").replace("`", "\\`")

    html_content = f'''
    <button id="{btn_id}" style="
        padding: 6px 12px;
        background: #4a9eff;
        color: white;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-size: 14px;
    " onmouseover="this.style.background='#3a8eef'"
      onmouseout="this.style.background='#4a9eff'">
        {label}
    </button>
    <script>
    (function() {{
        var btn = document.getElementById("{btn_id}");
        btn.onclick = function() {{
            var code = `{escaped_code}`;
            var kernel = Jupyter.notebook ?
                Jupyter.notebook.kernel :
                (IPython.notebook ? IPython.notebook.kernel : null);

            if (kernel) {{
                kernel.execute(
                    "from cleon_cell_control import insert_and_run; insert_and_run(" + JSON.stringify(code) + ")"
                );
                btn.textContent = "✓ Inserted";
                btn.style.background = "#28a745";
                setTimeout(function() {{
                    btn.textContent = "{label}";
                    btn.style.background = "#4a9eff";
                }}, 1500);
            }} else {{
                alert("No kernel available");
            }}
        }};
    }})();
    </script>
    '''
    display(HTML(html_content))

from typing import Any, Optional, Tuple

def auth(provider: Optional[str] = ...) -> None: ...
def run(
    prompt: str,
    json_events: Optional[bool] = ...,
    json_result: Optional[bool] = ...,
) -> Tuple[Any, Any]: ...

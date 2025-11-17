#![allow(clippy::useless_conversion)]
#![allow(unsafe_op_in_unsafe_fn)]

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyModule;
use serde::Serialize;
use serde_json::Value as JsonValue;
use std::env;
use std::process::{Command, Stdio};

fn codex_binary() -> String {
    env::var("CLEON_BIN").unwrap_or_else(|_| "codex".to_string())
}

fn run_command(args: &[&str]) -> PyResult<std::process::Output> {
    let mut cmd = Command::new(codex_binary());
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

    cmd.output()
        .map_err(|e| PyRuntimeError::new_err(format!("failed to execute codex: {e}")))
        .and_then(|output| {
            if output.status.success() {
                Ok(output)
            } else {
                Err(PyRuntimeError::new_err(format!(
                    "codex exited with {status}",
                    status = output.status
                )))
            }
        })
}

fn json_to_py(py: Python<'_>, value: &impl Serialize) -> PyResult<PyObject> {
    let json_mod = PyModule::import_bound(py, "json")?;
    let loads = json_mod.getattr("loads")?;
    let serialized =
        serde_json::to_string(value).map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
    let obj = loads.call1((serialized,))?;
    Ok(obj.into_py(py))
}

fn parse_cli_output(stdout: &str, capture_events: bool) -> PyResult<(JsonValue, Vec<JsonValue>)> {
    let mut events = Vec::new();
    let mut final_result: Option<JsonValue> = None;

    for line in stdout.lines() {
        let parsed: JsonValue = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if capture_events {
            events.push(parsed.clone());
        }
        if parsed
            .get("type")
            .and_then(|t| t.as_str())
            .is_some_and(|t| t == "turn.result")
            && let Some(result) = parsed.get("result")
        {
            final_result = Some(result.clone());
        }
    }

    match final_result {
        Some(result) => Ok((result, events)),
        None => Err(PyRuntimeError::new_err(
            "codex output missing turn.result payload",
        )),
    }
}

#[pyfunction(signature = (provider=None))]
fn auth(provider: Option<&str>) -> PyResult<()> {
    if env::var("CLEON_SKIP_AUTH").as_deref() == Ok("1") {
        return Ok(());
    }
    let requested = provider.unwrap_or("codex");
    if requested.eq_ignore_ascii_case("codex") {
        run_command(&["login"]).map(|_| ())
    } else {
        Err(PyValueError::new_err(
            "Only the 'codex' provider is currently supported.",
        ))
    }
}

#[pyfunction(signature = (prompt, json_events=None, json_result=None))]
#[allow(unsafe_op_in_unsafe_fn)]
fn run(
    py: Python<'_>,
    prompt: &str,
    json_events: Option<bool>,
    json_result: Option<bool>,
) -> PyResult<(PyObject, PyObject)> {
    if let Ok(fake) = env::var("CLEON_FAKE_RESULT") {
        let parsed: JsonValue = serde_json::from_str(&fake)
            .map_err(|e| PyRuntimeError::new_err(format!("invalid CLEON_FAKE_RESULT: {e}")))?;
        let result_py = json_to_py(py, &parsed)?;
        let events_py = py.None();
        return Ok((result_py, events_py));
    }

    let mut args = Vec::new();
    if json_events.unwrap_or(true) {
        args.push("--json-events");
    }
    if json_result.unwrap_or(true) {
        args.push("--json-result");
    }
    args.push(prompt);

    let output = run_command(&args)?;
    let stdout = String::from_utf8(output.stdout)
        .map_err(|e| PyRuntimeError::new_err(format!("invalid UTF-8 output: {e}")))?;

    let capture_events = json_events.unwrap_or(true);
    let (result, events) = parse_cli_output(&stdout, capture_events)?;

    let result_py = json_to_py(py, &result)?;
    let events_py = if capture_events {
        json_to_py(py, &events)?
    } else {
        py.None()
    };

    Ok((result_py, events_py))
}

#[pymodule]
fn _cleon(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(auth, m)?)?;
    m.add_function(wrap_pyfunction!(run, m)?)?;
    Ok(())
}

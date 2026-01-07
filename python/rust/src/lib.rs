//! Python bindings for cleon using PyO3.
//!
//! This module provides Python wrappers for the core pi-mono-rust types:
//! - PyAuthStorage: Auth credential storage
//! - PyAgentSession: Agent session management
//! - Event streaming via callbacks

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use pi::agent::{
    AgentEvent, AgentMessage, AgentStateOverride, ApprovalFn, ApprovalRequest, ApprovalResponse,
    LlmContext, Model as AgentModel,
};
use pi::api::google_gemini_cli::{stream_google_gemini_cli, GeminiCliCallOptions, GeminiCliTool};
use pi::api::openai_codex::{stream_openai_codex_responses, CodexStreamOptions, CodexTool};
use pi::api::{
    build_anthropic_messages, openai_context_to_input_items, stream_anthropic,
    stream_openai_responses, AnthropicCallOptions, AnthropicTool, OpenAICallOptions, OpenAITool,
};
use pi::coding_agent::{
    AgentSession, AgentSessionConfig, AgentSessionEvent, AuthCredential, AuthStorage,
    Model as RegistryModel, ModelRegistry, SettingsManager,
};
use pi::core::messages::{AssistantMessage, ContentBlock, UserContent};

use std::cell::RefCell;
use std::path::PathBuf;
use std::rc::Rc;

/// The default system prompt prefix required for OAuth tokens.
const DEFAULT_OAUTH_SYSTEM_PROMPT: &str = "You are Claude Code, an AI assistant made by Anthropic.";

/// Python wrapper for AuthStorage
///
/// Note: This class is not thread-safe (unsendable) because the underlying
/// AuthStorage uses non-Send types. It must be used from a single thread.
#[pyclass(unsendable, name = "AuthStorage")]
pub struct PyAuthStorage {
    inner: RefCell<AuthStorage>,
}

#[pymethods]
impl PyAuthStorage {
    #[new]
    fn new(path: &str) -> Self {
        Self {
            inner: RefCell::new(AuthStorage::new(PathBuf::from(path))),
        }
    }

    /// Get credential for a provider
    fn get(&self, provider: &str) -> Option<PyObject> {
        Python::with_gil(|py| {
            self.inner
                .borrow()
                .get(provider)
                .map(|cred| credential_to_py(py, cred))
        })
    }

    /// Set credential for a provider
    fn set(&self, provider: &str, credential: &Bound<'_, PyDict>) -> PyResult<()> {
        let cred = py_to_credential(credential)?;
        self.inner.borrow_mut().set(provider, cred);
        Ok(())
    }

    /// Remove credential for a provider
    fn remove(&self, provider: &str) {
        self.inner.borrow_mut().remove(provider);
    }

    /// Check if provider has auth
    fn has_auth(&self, provider: &str) -> bool {
        self.inner.borrow().has_auth(provider)
    }

    /// Get API key for a provider
    fn get_api_key(&self, provider: &str) -> Option<String> {
        self.inner.borrow().get_api_key(provider)
    }

    /// List all providers with stored credentials
    fn list(&self) -> Vec<String> {
        self.inner.borrow().list()
    }

    /// Reload credentials from file
    fn reload(&self) {
        self.inner.borrow_mut().reload();
    }

    /// Get the auth file path
    fn path(&self) -> String {
        self.inner.borrow().path().to_string_lossy().to_string()
    }
}

fn credential_to_py(py: Python<'_>, cred: &AuthCredential) -> PyObject {
    let dict = PyDict::new(py);
    match cred {
        AuthCredential::ApiKey { key } => {
            dict.set_item("type", "api_key").unwrap();
            dict.set_item("key", key).unwrap();
        }
        AuthCredential::OAuth {
            access,
            refresh,
            expires,
            enterprise_url,
            project_id,
            email,
            account_id,
        } => {
            dict.set_item("type", "oauth").unwrap();
            dict.set_item("access", access).unwrap();
            if let Some(r) = refresh {
                dict.set_item("refresh", r).unwrap();
            }
            if let Some(e) = expires {
                dict.set_item("expires", e).unwrap();
            }
            if let Some(u) = enterprise_url {
                dict.set_item("enterprise_url", u).unwrap();
            }
            if let Some(p) = project_id {
                dict.set_item("project_id", p).unwrap();
            }
            if let Some(em) = email {
                dict.set_item("email", em).unwrap();
            }
            if let Some(a) = account_id {
                dict.set_item("account_id", a).unwrap();
            }
        }
    }
    dict.into()
}

fn py_to_credential(dict: &Bound<'_, PyDict>) -> PyResult<AuthCredential> {
    let cred_type: String = dict
        .get_item("type")?
        .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("Missing 'type' field"))?
        .extract()?;

    match cred_type.as_str() {
        "api_key" => {
            let key: String = dict
                .get_item("key")?
                .ok_or_else(|| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>("Missing 'key' field")
                })?
                .extract()?;
            Ok(AuthCredential::ApiKey { key })
        }
        "oauth" => {
            let access: String = dict
                .get_item("access")?
                .ok_or_else(|| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>("Missing 'access' field")
                })?
                .extract()?;
            let refresh: Option<String> = dict.get_item("refresh")?.and_then(|v| v.extract().ok());
            let expires: Option<i64> = dict.get_item("expires")?.and_then(|v| v.extract().ok());
            let enterprise_url: Option<String> = dict
                .get_item("enterprise_url")?
                .and_then(|v| v.extract().ok());
            let project_id: Option<String> =
                dict.get_item("project_id")?.and_then(|v| v.extract().ok());
            let email: Option<String> = dict.get_item("email")?.and_then(|v| v.extract().ok());
            let account_id: Option<String> =
                dict.get_item("account_id")?.and_then(|v| v.extract().ok());
            Ok(AuthCredential::OAuth {
                access,
                refresh,
                expires,
                enterprise_url,
                project_id,
                email,
                account_id,
            })
        }
        _ => Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Unknown credential type: {}",
            cred_type
        ))),
    }
}

/// Python wrapper for AgentSession
///
/// Note: This class is not thread-safe (unsendable) because the underlying
/// AgentSession uses Rc/RefCell types. It must be used from a single thread.
#[pyclass(unsendable, name = "AgentSession")]
pub struct PyAgentSession {
    // AgentSession uses Rc/RefCell internally for listeners, so we need RefCell here
    inner: RefCell<Option<AgentSession>>,
    // Store Python callbacks for event subscription
    callbacks: Rc<RefCell<Vec<PyObject>>>,
    // Store approval callback (Python function that takes dict and returns string)
    approval_callback: Rc<RefCell<Option<PyObject>>>,
}

#[pymethods]
impl PyAgentSession {
    #[new]
    #[pyo3(signature = (cwd, agent_dir=None, provider=None, model=None))]
    fn new(
        cwd: &str,
        agent_dir: Option<&str>,
        provider: Option<&str>,
        model: Option<&str>,
    ) -> PyResult<Self> {
        // Create components for AgentSessionConfig
        let agent_dir_str = agent_dir.unwrap_or("").to_string();
        let settings_manager = SettingsManager::create(cwd, &agent_dir_str);

        // Create auth storage and model registry
        let auth_path = if agent_dir_str.is_empty() {
            pi::config::get_agent_dir().join("auth.json")
        } else {
            PathBuf::from(&agent_dir_str).join("auth.json")
        };
        let auth_storage = AuthStorage::new(auth_path.clone());
        let model_registry = ModelRegistry::new(auth_storage, None::<PathBuf>);

        // Determine provider and model
        let provider_str = provider
            .map(String::from)
            .or_else(|| settings_manager.get_default_provider())
            .unwrap_or_else(|| "anthropic".to_string());
        let model_id = model
            .map(String::from)
            .or_else(|| settings_manager.get_default_model());

        // Find the model in registry
        let registry_model = if let Some(model_id) = &model_id {
            model_registry.find(&provider_str, model_id)
        } else {
            // Get first available model for provider
            model_registry
                .get_available()
                .into_iter()
                .find(|m| m.provider == provider_str)
        };

        let registry_model = registry_model.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "No model found for provider '{}'",
                provider_str
            ))
        })?;

        // Get API key for this model
        let api_key = model_registry.get_api_key(&registry_model).ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "No API key found for provider '{}'. Run authentication first.",
                provider_str
            ))
        })?;
        let use_oauth = model_registry.is_using_oauth(&registry_model);

        // Build the streaming function based on the API type
        let stream_fn = build_py_stream_fn(registry_model.clone(), api_key.clone(), use_oauth)?;

        // Create the agent model
        let agent_model = AgentModel {
            id: registry_model.id.clone(),
            name: registry_model.name.clone(),
            api: registry_model.api.clone(),
            provider: registry_model.provider.clone(),
        };

        // Create Agent with initial state override and streaming function
        let initial_state = AgentStateOverride {
            model: Some(agent_model),
            ..Default::default()
        };

        let agent = pi::agent::Agent::new(pi::agent::AgentOptions {
            initial_state: Some(initial_state),
            stream_fn: Some(stream_fn),
            ..Default::default()
        });

        // Create session manager
        let session_manager =
            pi::core::session_manager::SessionManager::create(PathBuf::from(cwd));

        // Re-create model_registry for AgentSessionConfig (we moved the previous one)
        let model_registry = ModelRegistry::new(AuthStorage::new(auth_path), None::<PathBuf>);

        let config = AgentSessionConfig {
            agent,
            session_manager,
            settings_manager,
            model_registry,
        };

        let callbacks: Rc<RefCell<Vec<PyObject>>> = Rc::new(RefCell::new(Vec::new()));

        let session = AgentSession::new(config);

        // Set up event forwarding to Python callbacks
        // Note: The unsubscribe closure is intentionally dropped since we manage
        // callbacks through the PyAgentSession wrapper
        let callbacks_ref = callbacks.clone();
        let _unsubscribe = session.subscribe(move |event| {
            Python::with_gil(|py| {
                let event_dict = session_event_to_py(py, event);
                for callback in callbacks_ref.borrow().iter() {
                    if let Err(e) = callback.call1(py, (event_dict.clone_ref(py),)) {
                        eprintln!("Error calling Python event callback: {}", e);
                    }
                }
            });
        });

        Ok(Self {
            inner: RefCell::new(Some(session)),
            callbacks,
            approval_callback: Rc::new(RefCell::new(None)),
        })
    }

    /// Set the approval callback.
    ///
    /// The callback takes a dict with:
    ///   - tool_call_id: str
    ///   - tool_name: str
    ///   - args: str (JSON)
    ///   - command: str (optional, for bash tools)
    ///   - cwd: str (optional)
    ///   - reason: str (optional)
    ///
    /// And returns one of: "approve", "approve_session", "deny", "abort"
    #[pyo3(signature = (callback=None))]
    fn set_approval_callback(&self, callback: Option<PyObject>) -> PyResult<()> {
        // Store the callback by moving it
        let has_callback = callback.is_some();
        *self.approval_callback.borrow_mut() = callback;

        if !has_callback {
            // Clear the approval callback on the agent
            let mut inner = self.inner.borrow_mut();
            if let Some(session) = inner.as_mut() {
                session.agent.set_on_approval(None);
            }
            return Ok(());
        }

        // Create a Rust closure that calls the Python callback
        let py_callback = self.approval_callback.clone();
        let approval_fn: Box<ApprovalFn> = Box::new(
            move |request: &ApprovalRequest| -> ApprovalResponse {
                let callback_ref = py_callback.borrow();
                let Some(ref callback) = *callback_ref else {
                    return ApprovalResponse::Approve;
                };

                Python::with_gil(|py| {
                    // Create a dict with the request data
                    let dict = PyDict::new(py);
                    dict.set_item("tool_call_id", &request.tool_call_id)
                        .unwrap();
                    dict.set_item("tool_name", &request.tool_name).unwrap();
                    dict.set_item("args", request.args.to_string()).unwrap();
                    if let Some(ref command) = request.command {
                        dict.set_item("command", command).unwrap();
                    }
                    if let Some(ref cwd) = request.cwd {
                        dict.set_item("cwd", cwd).unwrap();
                    }
                    if let Some(ref reason) = request.reason {
                        dict.set_item("reason", reason).unwrap();
                    }

                    // Call the Python callback
                    let result = callback.call1(py, (dict,));
                    match result {
                        Ok(response) => {
                            if let Ok(s) = response.extract::<String>(py) {
                                match s.as_str() {
                                    "approve" => ApprovalResponse::Approve,
                                    "approve_session" => ApprovalResponse::ApproveSession,
                                    "deny" => ApprovalResponse::Deny,
                                    "abort" => ApprovalResponse::Abort,
                                    _ => {
                                        eprintln!(
                                            "Unknown approval response '{}', defaulting to approve",
                                            s
                                        );
                                        ApprovalResponse::Approve
                                    }
                                }
                            } else {
                                eprintln!("Approval callback did not return a string, defaulting to approve");
                                ApprovalResponse::Approve
                            }
                        }
                        Err(e) => {
                            eprintln!("Error calling approval callback: {}", e);
                            ApprovalResponse::Approve
                        }
                    }
                })
            },
        );

        // Set the callback on the agent
        let mut inner = self.inner.borrow_mut();
        let session = inner.as_mut().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Session has been disposed")
        })?;
        session
            .agent
            .set_on_approval(Some(Rc::new(RefCell::new(approval_fn))));

        Ok(())
    }

    /// Send a prompt to the agent
    fn prompt(&self, text: &str) -> PyResult<()> {
        let mut inner = self.inner.borrow_mut();
        let session = inner.as_mut().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Session has been disposed")
        })?;

        session.prompt(text).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Prompt failed: {}", e))
        })
    }

    /// Subscribe to session events
    fn subscribe(&self, callback: PyObject) -> PyResult<usize> {
        let mut callbacks = self.callbacks.borrow_mut();
        let id = callbacks.len();
        callbacks.push(callback);
        Ok(id)
    }

    /// Unsubscribe from session events
    fn unsubscribe(&self, id: usize) -> PyResult<()> {
        let mut callbacks = self.callbacks.borrow_mut();
        if id < callbacks.len() {
            callbacks.remove(id);
        }
        Ok(())
    }

    /// Check if the agent is currently streaming
    fn is_streaming(&self) -> bool {
        self.inner
            .borrow()
            .as_ref()
            .map(|s| s.is_streaming())
            .unwrap_or(false)
    }

    /// Abort the current operation
    fn abort(&self) {
        if let Some(session) = self.inner.borrow().as_ref() {
            session.abort();
        }
    }

    /// Start a new session
    fn new_session(&self) {
        if let Some(session) = self.inner.borrow_mut().as_mut() {
            session.new_session();
        }
    }

    /// Switch to a different session by path
    fn switch_session(&self, path: &str) -> PyResult<bool> {
        let mut inner = self.inner.borrow_mut();
        let session = inner.as_mut().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Session has been disposed")
        })?;

        session.switch_session(PathBuf::from(path)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Switch session failed: {}",
                e
            ))
        })
    }

    /// Get the current session ID
    fn session_id(&self) -> Option<String> {
        self.inner.borrow().as_ref().map(|s| s.session_id())
    }

    /// Get the current session file path
    fn session_file(&self) -> Option<String> {
        self.inner
            .borrow()
            .as_ref()
            .and_then(|s| s.session_file())
            .map(|p| p.to_string_lossy().to_string())
    }

    /// Get the last assistant text response
    fn get_last_assistant_text(&self) -> Option<String> {
        self.inner
            .borrow()
            .as_ref()
            .and_then(|s| s.get_last_assistant_text())
    }

    /// Get session stats
    fn get_session_stats(&self, py: Python<'_>) -> PyResult<PyObject> {
        let inner = self.inner.borrow();
        let session = inner.as_ref().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Session has been disposed")
        })?;

        let stats = session.get_session_stats();
        let dict = PyDict::new(py);
        dict.set_item(
            "session_file",
            stats.session_file.map(|p| p.to_string_lossy().to_string()),
        )?;
        dict.set_item("session_id", stats.session_id)?;
        dict.set_item("user_messages", stats.user_messages)?;
        dict.set_item("assistant_messages", stats.assistant_messages)?;
        dict.set_item("tool_calls", stats.tool_calls)?;
        dict.set_item("tool_results", stats.tool_results)?;
        dict.set_item("total_messages", stats.total_messages)?;
        dict.set_item("cost", stats.cost)?;

        let tokens = PyDict::new(py);
        tokens.set_item("input", stats.tokens.input)?;
        tokens.set_item("output", stats.tokens.output)?;
        tokens.set_item("cache_read", stats.tokens.cache_read)?;
        tokens.set_item("cache_write", stats.tokens.cache_write)?;
        tokens.set_item("total", stats.tokens.total)?;
        dict.set_item("tokens", tokens)?;

        Ok(dict.into())
    }

    /// Dispose the session
    fn dispose(&self) {
        if let Some(mut session) = self.inner.borrow_mut().take() {
            session.dispose();
        }
    }
}

fn session_event_to_py(py: Python<'_>, event: &AgentSessionEvent) -> PyObject {
    let dict = PyDict::new(py);

    match event {
        AgentSessionEvent::Agent(agent_event) => {
            dict.set_item("type", "agent").unwrap();
            dict.set_item("event", agent_event_to_py(py, agent_event))
                .unwrap();
        }
        AgentSessionEvent::AutoCompactionStart { reason } => {
            dict.set_item("type", "auto_compaction_start").unwrap();
            dict.set_item("reason", reason).unwrap();
        }
        AgentSessionEvent::AutoCompactionEnd { aborted } => {
            dict.set_item("type", "auto_compaction_end").unwrap();
            dict.set_item("aborted", *aborted).unwrap();
        }
    }

    dict.into()
}

fn agent_event_to_py(py: Python<'_>, event: &AgentEvent) -> PyObject {
    let dict = PyDict::new(py);
    dict.set_item("kind", event.kind()).unwrap();

    match event {
        AgentEvent::AgentStart => {}
        AgentEvent::AgentEnd { messages } => {
            let msgs: Vec<PyObject> = messages.iter().map(|m| message_to_py(py, m)).collect();
            dict.set_item("messages", PyList::new(py, msgs).unwrap())
                .unwrap();
        }
        AgentEvent::TurnStart => {}
        AgentEvent::TurnEnd {
            message,
            tool_results,
        } => {
            dict.set_item("message", message_to_py(py, message))
                .unwrap();
            let results: Vec<PyObject> = tool_results
                .iter()
                .map(|r| tool_result_to_py(py, r))
                .collect();
            dict.set_item("tool_results", PyList::new(py, results).unwrap())
                .unwrap();
        }
        AgentEvent::MessageStart { message }
        | AgentEvent::MessageUpdate { message }
        | AgentEvent::MessageEnd { message } => {
            dict.set_item("message", message_to_py(py, message))
                .unwrap();
        }
        AgentEvent::ToolExecutionStart {
            tool_call_id,
            tool_name,
            args,
        } => {
            dict.set_item("tool_call_id", tool_call_id).unwrap();
            dict.set_item("tool_name", tool_name).unwrap();
            dict.set_item("args", args.to_string()).unwrap();
        }
        AgentEvent::ToolExecutionUpdate {
            tool_call_id,
            tool_name,
            args,
            partial_result,
        } => {
            dict.set_item("tool_call_id", tool_call_id).unwrap();
            dict.set_item("tool_name", tool_name).unwrap();
            dict.set_item("args", args.to_string()).unwrap();
            dict.set_item(
                "partial_result",
                tool_result_inner_to_py(py, partial_result),
            )
            .unwrap();
        }
        AgentEvent::ToolExecutionEnd {
            tool_call_id,
            tool_name,
            result,
            is_error,
        } => {
            dict.set_item("tool_call_id", tool_call_id).unwrap();
            dict.set_item("tool_name", tool_name).unwrap();
            dict.set_item("result", tool_result_inner_to_py(py, result))
                .unwrap();
            dict.set_item("is_error", *is_error).unwrap();
        }
        AgentEvent::ApprovalRequest(request) => {
            dict.set_item("tool_call_id", &request.tool_call_id)
                .unwrap();
            dict.set_item("tool_name", &request.tool_name).unwrap();
            dict.set_item("args", request.args.to_string()).unwrap();
            if let Some(command) = &request.command {
                dict.set_item("command", command).unwrap();
            }
            if let Some(cwd) = &request.cwd {
                dict.set_item("cwd", cwd).unwrap();
            }
            if let Some(reason) = &request.reason {
                dict.set_item("reason", reason).unwrap();
            }
        }
    }

    dict.into()
}

fn message_to_py(py: Python<'_>, message: &AgentMessage) -> PyObject {
    let dict = PyDict::new(py);
    dict.set_item("role", message.role()).unwrap();

    match message {
        AgentMessage::User(user) => {
            dict.set_item("content", user_content_to_py(py, &user.content))
                .unwrap();
            dict.set_item("timestamp", user.timestamp).unwrap();
        }
        AgentMessage::Assistant(assistant) => {
            let content: Vec<PyObject> = assistant
                .content
                .iter()
                .map(|b| content_block_to_py(py, b))
                .collect();
            dict.set_item("content", PyList::new(py, content).unwrap())
                .unwrap();
            dict.set_item("stop_reason", &assistant.stop_reason)
                .unwrap();
            dict.set_item("timestamp", assistant.timestamp).unwrap();
        }
        AgentMessage::ToolResult(result) => {
            dict.set_item("tool_call_id", &result.tool_call_id).unwrap();
            dict.set_item("tool_name", &result.tool_name).unwrap();
            let content: Vec<PyObject> = result
                .content
                .iter()
                .map(|b| content_block_to_py(py, b))
                .collect();
            dict.set_item("content", PyList::new(py, content).unwrap())
                .unwrap();
            dict.set_item("is_error", result.is_error).unwrap();
            dict.set_item("timestamp", result.timestamp).unwrap();
        }
        AgentMessage::Custom(custom) => {
            dict.set_item("custom_role", &custom.role).unwrap();
            dict.set_item("text", &custom.text).unwrap();
            dict.set_item("timestamp", custom.timestamp).unwrap();
        }
    }

    dict.into()
}

fn user_content_to_py(py: Python<'_>, content: &UserContent) -> PyObject {
    match content {
        UserContent::Text(text) => text.clone().into_pyobject(py).unwrap().into(),
        UserContent::Blocks(blocks) => {
            let py_blocks: Vec<PyObject> =
                blocks.iter().map(|b| content_block_to_py(py, b)).collect();
            PyList::new(py, py_blocks).unwrap().into()
        }
    }
}

fn content_block_to_py(py: Python<'_>, block: &ContentBlock) -> PyObject {
    let dict = PyDict::new(py);

    match block {
        ContentBlock::Text { text, .. } => {
            dict.set_item("type", "text").unwrap();
            dict.set_item("text", text).unwrap();
        }
        ContentBlock::Thinking { thinking, .. } => {
            dict.set_item("type", "thinking").unwrap();
            dict.set_item("text", thinking).unwrap();
        }
        ContentBlock::ToolCall {
            id,
            name,
            arguments,
            ..
        } => {
            dict.set_item("type", "tool_call").unwrap();
            dict.set_item("id", id).unwrap();
            dict.set_item("name", name).unwrap();
            dict.set_item("arguments", arguments.to_string()).unwrap();
        }
        ContentBlock::Image { mime_type, data } => {
            dict.set_item("type", "image").unwrap();
            dict.set_item("mime_type", mime_type).unwrap();
            dict.set_item("data", data).unwrap();
        }
    }

    dict.into()
}

fn tool_result_to_py(py: Python<'_>, result: &pi::core::messages::ToolResultMessage) -> PyObject {
    let dict = PyDict::new(py);
    dict.set_item("tool_call_id", &result.tool_call_id).unwrap();
    dict.set_item("tool_name", &result.tool_name).unwrap();
    let content: Vec<PyObject> = result
        .content
        .iter()
        .map(|b| content_block_to_py(py, b))
        .collect();
    dict.set_item("content", PyList::new(py, content).unwrap())
        .unwrap();
    dict.set_item("is_error", result.is_error).unwrap();
    dict.set_item("timestamp", result.timestamp).unwrap();
    dict.into()
}

fn tool_result_inner_to_py(py: Python<'_>, result: &pi::agent::AgentToolResult) -> PyObject {
    let dict = PyDict::new(py);
    let content: Vec<PyObject> = result
        .content
        .iter()
        .map(|b| content_block_to_py(py, b))
        .collect();
    dict.set_item("content", PyList::new(py, content).unwrap())
        .unwrap();
    dict.set_item("details", result.details.to_string())
        .unwrap();
    dict.into()
}

// ============================================================================
// OAuth helper functions
// ============================================================================

/// Get the Anthropic OAuth authorization URL.
/// Returns a tuple of (auth_url, verifier).
/// The verifier must be stored and passed to anthropic_exchange_code.
#[pyfunction]
fn anthropic_get_auth_url() -> (String, String) {
    pi::coding_agent::anthropic_get_auth_url()
}

/// Exchange an authorization code for Anthropic OAuth credentials.
/// Requires the code from the callback and the verifier from anthropic_get_auth_url.
#[pyfunction]
fn anthropic_exchange_code(code: &str, verifier: &str) -> PyResult<PyObject> {
    let creds = pi::coding_agent::anthropic_exchange_code(code, verifier).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Failed to exchange code: {}", e))
    })?;

    Python::with_gil(|py| {
        let dict = PyDict::new(py);
        dict.set_item("access", &creds.access)?;
        dict.set_item("refresh", &creds.refresh)?;
        dict.set_item("expires", creds.expires)?;
        if let Some(url) = &creds.enterprise_url {
            dict.set_item("enterprise_url", url)?;
        }
        if let Some(pid) = &creds.project_id {
            dict.set_item("project_id", pid)?;
        }
        if let Some(aid) = &creds.account_id {
            dict.set_item("account_id", aid)?;
        }
        Ok(dict.into())
    })
}

/// Refresh an Anthropic OAuth token.
#[pyfunction]
fn anthropic_refresh_token(refresh_token: &str) -> PyResult<PyObject> {
    let creds = pi::coding_agent::anthropic_refresh_token(refresh_token).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Failed to refresh token: {}", e))
    })?;

    Python::with_gil(|py| {
        let dict = PyDict::new(py);
        dict.set_item("access", &creds.access)?;
        dict.set_item("refresh", &creds.refresh)?;
        dict.set_item("expires", creds.expires)?;
        Ok(dict.into())
    })
}

/// Get the OpenAI Codex OAuth authorization URL.
/// Returns a tuple of (auth_url, verifier, state).
/// The verifier and state must be stored and passed to openai_codex_exchange_code.
#[pyfunction]
fn openai_codex_get_auth_url() -> (String, String, String) {
    pi::coding_agent::openai_codex_get_auth_url()
}

/// Exchange an authorization code for OpenAI Codex OAuth credentials.
/// Requires the code from the callback and the verifier from openai_codex_get_auth_url.
#[pyfunction]
fn openai_codex_exchange_code(code: &str, verifier: &str) -> PyResult<PyObject> {
    let creds = pi::coding_agent::openai_codex_exchange_code(code, verifier).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Failed to exchange code: {}", e))
    })?;

    Python::with_gil(|py| {
        let dict = PyDict::new(py);
        dict.set_item("access", &creds.access)?;
        dict.set_item("refresh", &creds.refresh)?;
        dict.set_item("expires", creds.expires)?;
        Ok(dict.into())
    })
}

/// Refresh an OpenAI Codex OAuth token.
#[pyfunction]
fn openai_codex_refresh_token(refresh_token: &str) -> PyResult<PyObject> {
    let creds = pi::coding_agent::openai_codex_refresh_token(refresh_token).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Failed to refresh token: {}", e))
    })?;

    Python::with_gil(|py| {
        let dict = PyDict::new(py);
        dict.set_item("access", &creds.access)?;
        dict.set_item("refresh", &creds.refresh)?;
        dict.set_item("expires", creds.expires)?;
        Ok(dict.into())
    })
}

/// Get the default agent directory path
#[pyfunction]
fn get_agent_dir() -> String {
    pi::config::get_agent_dir().to_string_lossy().to_string()
}

// ============================================================================
// API streaming function builders
// ============================================================================

type AgentStreamFn =
    Box<dyn FnMut(&AgentModel, &LlmContext, &mut pi::agent::StreamEvents) -> AssistantMessage>;

/// Build a streaming function based on the model's API type.
fn build_py_stream_fn(
    model: RegistryModel,
    api_key: String,
    use_oauth: bool,
) -> PyResult<AgentStreamFn> {
    match model.api.as_str() {
        "anthropic-messages" => Ok(build_anthropic_stream_fn(model, api_key, use_oauth)),
        "openai-responses" => Ok(build_openai_stream_fn(model, api_key)),
        "openai-codex-responses" => Ok(build_codex_stream_fn(model, api_key)),
        "google-gemini-cli" => {
            // Gemini uses resolve_google_gemini_cli_credentials which expects JSON with token/projectId
            // If api_key is the placeholder from has_auth(), pass None to use fallback resolution
            let api_key_opt = if api_key == "<gemini-cli>" {
                None
            } else {
                Some(api_key.as_str())
            };
            let (access_token, project_id) =
                pi::cli::auth::resolve_google_gemini_cli_credentials(api_key_opt).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                        "Failed to resolve Gemini credentials: {}",
                        e
                    ))
                })?;
            Ok(build_gemini_stream_fn(model, access_token, project_id))
        }
        api => Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Unsupported API type: '{}'. Supported: anthropic-messages, openai-responses, openai-codex-responses, google-gemini-cli",
            api
        ))),
    }
}

fn build_anthropic_stream_fn(
    model: RegistryModel,
    api_key: String,
    use_oauth: bool,
) -> AgentStreamFn {
    Box::new(move |_agent_model, context, events| {
        // OAuth tokens require the Claude Code identification in the system prompt
        let system_with_oauth_prefix = if use_oauth {
            if context.system_prompt.trim().is_empty() {
                DEFAULT_OAUTH_SYSTEM_PROMPT.to_string()
            } else {
                format!(
                    "{}\n\n{}",
                    DEFAULT_OAUTH_SYSTEM_PROMPT, context.system_prompt
                )
            }
        } else {
            context.system_prompt.clone()
        };
        let system = if system_with_oauth_prefix.trim().is_empty() {
            None
        } else {
            Some(system_with_oauth_prefix.as_str())
        };

        let messages = build_anthropic_messages(context);

        // No tools in basic chat mode for now
        let tool_specs: Vec<AnthropicTool> = vec![];

        let response = stream_anthropic(
            &model,
            messages,
            AnthropicCallOptions {
                model: &model.id,
                api_key: &api_key,
                use_oauth,
                tools: &tool_specs,
                base_url: if model.base_url.is_empty() {
                    "https://api.anthropic.com/v1"
                } else {
                    model.base_url.as_str()
                },
                extra_headers: model.headers.as_ref(),
                system,
            },
            events,
        );

        match response {
            Ok(response) => response,
            Err(err) => assistant_error_message(&model, &err),
        }
    })
}

fn build_openai_stream_fn(model: RegistryModel, api_key: String) -> AgentStreamFn {
    Box::new(move |_agent_model, context, events| {
        let input = openai_context_to_input_items(&model, context);

        // No tools in basic chat mode for now
        let tool_specs: Vec<OpenAITool> = vec![];

        let response = stream_openai_responses(
            &model,
            input,
            OpenAICallOptions {
                model: &model.id,
                api_key: &api_key,
                tools: &tool_specs,
                base_url: if model.base_url.is_empty() {
                    "https://api.openai.com/v1"
                } else {
                    model.base_url.as_str()
                },
                extra_headers: model.headers.as_ref(),
            },
            events,
        );

        match response {
            Ok(response) => response,
            Err(err) => assistant_error_message(&model, &err),
        }
    })
}

fn build_codex_stream_fn(model: RegistryModel, api_key: String) -> AgentStreamFn {
    Box::new(move |_agent_model, context, events| {
        // No tools in basic chat mode for now
        let tool_specs: Vec<CodexTool> = vec![];

        let response = stream_openai_codex_responses(
            &model,
            context,
            &api_key,
            &tool_specs,
            CodexStreamOptions {
                codex_mode: Some(true),
                extra_headers: model.headers.clone(),
                ..Default::default()
            },
            events,
        );

        match response {
            Ok(response) => response,
            Err(err) => assistant_error_message(&model, &err),
        }
    })
}

fn build_gemini_stream_fn(
    model: RegistryModel,
    access_token: String,
    project_id: String,
) -> AgentStreamFn {
    Box::new(move |_agent_model, context, events| {
        // No tools in basic chat mode for now
        let tool_specs: Vec<GeminiCliTool> = vec![];

        let system = if context.system_prompt.trim().is_empty() {
            None
        } else {
            Some(context.system_prompt.as_str())
        };

        let response = stream_google_gemini_cli(
            &model,
            context,
            GeminiCliCallOptions {
                model: &model.id,
                access_token: &access_token,
                project_id: &project_id,
                tools: &tool_specs,
                base_url: if model.base_url.is_empty() {
                    ""
                } else {
                    model.base_url.as_str()
                },
                system,
                thinking_enabled: model.reasoning,
            },
            events,
        );

        match response {
            Ok(response) => response,
            Err(err) => assistant_error_message(&model, &err),
        }
    })
}

fn assistant_error_message(model: &RegistryModel, error: &str) -> AssistantMessage {
    use pi::core::messages::{Cost, Usage};
    use std::time::{SystemTime, UNIX_EPOCH};

    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0);

    AssistantMessage {
        content: vec![ContentBlock::Text {
            text: String::new(),
            text_signature: None,
        }],
        api: model.api.clone(),
        provider: model.provider.clone(),
        model: model.id.clone(),
        usage: Usage {
            input: 0,
            output: 0,
            cache_read: 0,
            cache_write: 0,
            total_tokens: Some(0),
            cost: Some(Cost {
                input: 0.0,
                output: 0.0,
                cache_read: 0.0,
                cache_write: 0.0,
                total: 0.0,
            }),
        },
        stop_reason: "error".to_string(),
        error_message: Some(error.to_string()),
        timestamp,
    }
}

/// Python module definition
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyAuthStorage>()?;
    m.add_class::<PyAgentSession>()?;
    m.add_function(wrap_pyfunction!(anthropic_get_auth_url, m)?)?;
    m.add_function(wrap_pyfunction!(anthropic_exchange_code, m)?)?;
    m.add_function(wrap_pyfunction!(anthropic_refresh_token, m)?)?;
    m.add_function(wrap_pyfunction!(openai_codex_get_auth_url, m)?)?;
    m.add_function(wrap_pyfunction!(openai_codex_exchange_code, m)?)?;
    m.add_function(wrap_pyfunction!(openai_codex_refresh_token, m)?)?;
    m.add_function(wrap_pyfunction!(get_agent_dir, m)?)?;
    Ok(())
}

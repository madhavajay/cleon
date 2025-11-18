use std::io::{self, IsTerminal, Read, Write};
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Context, Result, bail};
use clap::{Args, Parser, Subcommand};
use codex_app_server_protocol::AuthMode;
use codex_core::auth::{self, enforce_login_restrictions, login_with_api_key, logout};
use codex_core::config::{Config, ConfigOverrides};
use codex_core::default_client::{self, SetOriginatorError};
use codex_core::find_conversation_path_by_id_str;
use codex_core::protocol::{
    AskForApproval, Event, EventMsg, Op, ReviewDecision, SandboxPolicy, SessionSource,
};
use codex_core::{AuthManager, CodexAuth, ConversationManager, NewConversation};
use codex_exec::event_processor_with_jsonl_output::EventProcessorWithJsonOutput;
use codex_exec::exec_events::{ThreadEvent, ThreadItemDetails, Usage};
use codex_login::{ServerOptions, run_device_code_login, run_login_server};
use codex_protocol::config_types::{
    ForcedLoginMethod, ReasoningEffort as ReasoningEffortConfig, ReasoningSummary,
};
use codex_protocol::user_input::UserInput;
use serde::Serialize;
use serde_json::json;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::signal;
use tokio::sync::mpsc::{UnboundedReceiver, unbounded_channel};

#[derive(Debug, Parser)]
#[command(
    author,
    version,
    about = "Codex session runner with JSON-friendly output"
)]
struct Cli {
    /// Optional prompt text. Use "-" to force reading stdin.
    #[arg(value_name = "PROMPT")]
    prompt: Option<String>,

    /// Resume a previous session by its session UUID.
    #[arg(long = "resume", value_name = "SESSION_ID")]
    resume: Option<String>,

    /// Run only a single prompt non-interactively.
    #[arg(long = "non-interactive", default_value_t = false)]
    non_interactive: bool,

    /// Emit every Codex event as JSON (one line per event) as it arrives.
    #[arg(long = "json-events", default_value_t = false)]
    json_events: bool,

    /// After each turn, print the aggregated summary as JSON instead of plain text.
    #[arg(long = "json-result", default_value_t = false)]
    json_result: bool,

    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Start the Codex login flow (browser/device/api-key).
    Login(LoginArgs),
    /// Remove stored authentication credentials.
    Logout,
    /// Show stored authentication status.
    Status,
}

#[derive(Debug, Args)]
struct LoginArgs {
    /// Read the API key from stdin (e.g. `printenv OPENAI_API_KEY | codex_mod login --with-api-key`)
    #[arg(long = "with-api-key", default_value_t = false)]
    with_api_key: bool,

    /// Provide the API key directly.
    #[arg(long = "api-key", value_name = "KEY")]
    api_key: Option<String>,

    /// Use the OAuth device code flow instead of starting a local server.
    #[arg(long = "device-code", default_value_t = false)]
    device_code: bool,
}

#[tokio::main]
async fn main() {
    if let Err(err) = run().await {
        eprintln!("error: {err:?}");
        std::process::exit(1);
    }
}

async fn run() -> Result<()> {
    match default_client::set_default_originator("codex_mod".to_string()) {
        Err(err) if !matches!(err, SetOriginatorError::AlreadyInitialized) => {
            eprintln!("warning: failed to set custom originator: {err:?}");
        }
        _ => {}
    }

    let Cli {
        prompt,
        resume,
        non_interactive,
        json_events,
        json_result,
        command,
    } = Cli::parse();

    match command {
        Some(Command::Login(args)) => handle_login(args).await,
        Some(Command::Logout) => handle_logout().await,
        Some(Command::Status) => handle_status().await,
        None => run_session(prompt, resume, !non_interactive, json_events, json_result).await,
    }
}

async fn run_session(
    prompt: Option<String>,
    resume_session: Option<String>,
    interactive: bool,
    json_events: bool,
    json_result: bool,
) -> Result<()> {
    let mut session = FullCodexSession::new(resume_session).await?;

    if interactive {
        run_interactive(&mut session, prompt, json_events, json_result).await?;
    } else {
        let prompt = read_prompt(prompt)?;
        let result = session.send_turn(prompt, json_events).await?;
        output_turn_result(&result, json_result)?;
    }

    session.shutdown().await?;
    Ok(())
}

async fn run_interactive(
    session: &mut FullCodexSession,
    initial_prompt: Option<String>,
    json_events: bool,
    json_result: bool,
) -> Result<()> {
    if let Some(prompt) = initial_prompt {
        let result = session.send_turn(prompt, json_events).await?;
        output_turn_result(&result, json_result)?;
    }

    let stdin = io::stdin();
    loop {
        eprint!("codex> ");
        io::stderr().flush().ok();
        let mut line = String::new();
        if stdin.read_line(&mut line)? == 0 {
            break;
        }
        let trimmed = line.trim();
        if trimmed == "__CLEON_STOP__" {
            break;
        }
        if trimmed.is_empty() {
            continue;
        }
        let result = session.send_turn(trimmed.to_string(), json_events).await?;
        output_turn_result(&result, json_result)?;
    }
    Ok(())
}

async fn handle_login(args: LoginArgs) -> Result<()> {
    if args.device_code && (args.with_api_key || args.api_key.is_some()) {
        bail!("--device-code cannot be combined with API key options");
    }

    let config = load_config().await?;

    match config.forced_login_method {
        Some(ForcedLoginMethod::Api)
            if args.device_code || (!args.with_api_key && args.api_key.is_none()) =>
        {
            bail!("This workspace requires API key login.")
        }
        Some(ForcedLoginMethod::Chatgpt) if args.with_api_key || args.api_key.is_some() => {
            bail!("This workspace requires ChatGPT login.")
        }
        _ => {}
    }

    if args.with_api_key || args.api_key.is_some() {
        let api_key = if let Some(key) = args.api_key {
            key
        } else {
            read_api_key_from_stdin()?
        };
        login_with_api_key(
            &config.codex_home,
            api_key.trim(),
            config.cli_auth_credentials_store_mode,
        )
        .context("failed to store API key credentials")?;
        println!("Successfully stored API key credentials.");
        return Ok(());
    }

    if args.device_code {
        let opts = ServerOptions::new(
            config.codex_home.clone(),
            auth::CLIENT_ID.to_string(),
            config.forced_chatgpt_workspace_id.clone(),
            config.cli_auth_credentials_store_mode,
        );
        run_device_code_login(opts)
            .await
            .context("device code login failed")?;
        println!("Device code login completed.");
        return Ok(());
    }

    let server = run_login_server(ServerOptions::new(
        config.codex_home.clone(),
        auth::CLIENT_ID.to_string(),
        config.forced_chatgpt_workspace_id.clone(),
        config.cli_auth_credentials_store_mode,
    ))
    .context("failed to start local login server")?;

    println!(
        "Opened login in your browser. If it did not open automatically, visit:\n  {}\n",
        server.auth_url
    );
    server
        .block_until_done()
        .await
        .context("login server exited early")?;
    println!("Browser login completed.");
    Ok(())
}

async fn handle_logout() -> Result<()> {
    let config = load_config().await?;
    match logout(&config.codex_home, config.cli_auth_credentials_store_mode) {
        Ok(true) => {
            println!("Removed stored credentials.");
            Ok(())
        }
        Ok(false) => {
            println!("No stored credentials were found.");
            Ok(())
        }
        Err(err) => Err(err).context("failed to remove stored credentials"),
    }
}

async fn handle_status() -> Result<()> {
    let config = load_config().await?;
    match CodexAuth::from_auth_storage(&config.codex_home, config.cli_auth_credentials_store_mode)
        .context("failed to load auth state")?
    {
        Some(auth) => match auth.mode {
            AuthMode::ApiKey => {
                let key = auth.get_token().await.context("failed to load API key")?;
                println!("Logged in with API key ({}...)", safe_key_preview(&key));
                Ok(())
            }
            AuthMode::ChatGPT => {
                println!("Logged in with ChatGPT session.");
                Ok(())
            }
        },
        None => {
            println!("Not logged in.");
            Ok(())
        }
    }
}

fn output_turn_result(result: &TurnResult, _json_result: bool) -> Result<()> {
    let envelope = serde_json::json!({
        "type": "turn.result",
        "result": result,
    });

    println!("{}", envelope);

    Ok(())
}

struct FullCodexSession {
    conversation: Arc<codex_core::CodexConversation>,
    event_rx: UnboundedReceiver<Event>,
    event_processor: EventProcessorWithJsonOutput,
    bootstrap_events: Vec<ThreadEvent>,
    session_id: Option<String>,
    rollout_path: Option<String>,
    default_cwd: PathBuf,
    default_approval: AskForApproval,
    default_sandbox_policy: SandboxPolicy,
    default_model: String,
    default_effort: Option<ReasoningEffortConfig>,
    default_summary: ReasoningSummary,
}

impl FullCodexSession {
    async fn new(resume_session: Option<String>) -> Result<Self> {
        let config = Arc::new(load_config().await?);

        enforce_login_restrictions(&config)
            .await
            .context("login restrictions check failed")?;

        let auth_manager = AuthManager::shared(
            config.codex_home.clone(),
            true,
            config.cli_auth_credentials_store_mode,
        );

        let conversation_manager =
            ConversationManager::new(auth_manager.clone(), SessionSource::Cli);
        let NewConversation {
            conversation_id: _,
            conversation,
            session_configured,
        } = if let Some(resume) = resume_session {
            let path = find_conversation_path_by_id_str(&config.codex_home, &resume)
                .await
                .context("failed to search for session to resume")?;
            let Some(rollout_path) = path else {
                bail!("No saved session found with ID {resume}");
            };
            conversation_manager
                .resume_conversation_from_rollout(
                    (*config).clone(),
                    rollout_path,
                    auth_manager.clone(),
                )
                .await?
        } else {
            conversation_manager
                .new_conversation((*config).clone())
                .await?
        };

        let (tx, rx) = unbounded_channel::<Event>();
        let event_conversation = conversation.clone();
        tokio::spawn(async move {
            loop {
                match event_conversation.next_event().await {
                    Ok(event) => {
                        if tx.send(event).is_err() {
                            break;
                        }
                    }
                    Err(err) => {
                        eprintln!("event stream closed: {err}");
                        break;
                    }
                }
            }
        });

        let mut event_processor = EventProcessorWithJsonOutput::new(None);
        let bootstrap_event = Event {
            id: String::new(),
            msg: EventMsg::SessionConfigured(session_configured.clone()),
        };
        let bootstrap_events = event_processor.collect_thread_events(&bootstrap_event);
        let mut session_id = None;
        let mut rollout_path = None;
        if let EventMsg::SessionConfigured(cfg) = &bootstrap_event.msg {
            session_id = Some(cfg.session_id.to_string());
            rollout_path = Some(cfg.rollout_path.display().to_string());
        }

        Ok(Self {
            conversation,
            event_rx: rx,
            event_processor,
            bootstrap_events,
            session_id,
            rollout_path,
            default_cwd: config.cwd.clone(),
            default_approval: config.approval_policy,
            default_sandbox_policy: config.sandbox_policy.clone(),
            default_model: config.model.clone(),
            default_effort: config.model_reasoning_effort,
            default_summary: config.model_reasoning_summary,
        })
    }

    async fn send_turn(&mut self, user_text: String, emit_json_events: bool) -> Result<TurnResult> {
        let items = vec![UserInput::Text { text: user_text }];

        self.conversation
            .submit(Op::UserTurn {
                items,
                cwd: self.default_cwd.clone(),
                approval_policy: self.default_approval,
                sandbox_policy: self.default_sandbox_policy.clone(),
                model: self.default_model.clone(),
                effort: self.default_effort,
                summary: self.default_summary,
                final_output_json_schema: None,
            })
            .await?;

        self.collect_turn_events(emit_json_events).await
    }

    async fn collect_turn_events(&mut self, emit_json_events: bool) -> Result<TurnResult> {
        let mut result = TurnResult::default();
        let mut approvals: VecDeque<(String, EventMsg)> = VecDeque::new();
        let mut stdin_lines = BufReader::new(tokio::io::stdin()).lines();

        if !self.bootstrap_events.is_empty() {
            if emit_json_events {
                for event in &self.bootstrap_events {
                    println!("{}", serde_json::to_string(event)?);
                }
            }
            result.append_events(std::mem::take(&mut self.bootstrap_events));
        }

        loop {
            tokio::select! {
                _ = signal::ctrl_c() => {
                    let _ = self.conversation.submit(Op::Interrupt).await;
                    result.errors.push("Interrupted by user".to_string());
                    result.completed = true;
                    break;
                }
                maybe_event = self.event_rx.recv() => {
                    let Some(event) = maybe_event else {
                        break;
                    };
                    match &event.msg {
                EventMsg::SessionConfigured(cfg) => {
                    self.session_id = Some(cfg.session_id.to_string());
                    self.rollout_path = Some(cfg.rollout_path.display().to_string());
                }
                EventMsg::ExecApprovalRequest(req) => {
                    approvals.push_back((event.id.clone(), EventMsg::ExecApprovalRequest(req.clone())));
                    println!("{}", serde_json::to_string(&serde_json::json!({
                        "type": "approval.request",
                        "id": event.id,
                        "kind": "exec",
                        "command": req.command,
                        "cwd": req.cwd,
                        "reason": req.reason,
                        "risk": req.risk,
                    }))?);
                    eprintln!(
                        "APPROVAL REQUEST {}: command={:?} cwd={} reason={:?} risk={:?}",
                        event.id, req.command, req.cwd.display(), req.reason, req.risk.as_ref().map(|r| r.risk_level.as_str())
                    );
                    eprintln!("Respond with: approve | approve_session | deny | abort");
                }
                EventMsg::ApplyPatchApprovalRequest(req) => {
                    approvals.push_back((event.id.clone(), EventMsg::ApplyPatchApprovalRequest(req.clone())));
                    println!("{}", serde_json::to_string(&serde_json::json!({
                        "type": "approval.request",
                        "id": event.id,
                        "kind": "patch",
                        "reason": req.reason,
                        "grant_root": req.grant_root,
                        "files": req.changes.keys().collect::<Vec<_>>(),
                    }))?);
                    eprintln!(
                        "PATCH APPROVAL {}: files={} reason={:?} grant_root={:?}",
                        event.id, req.changes.len(), req.reason, req.grant_root
                    );
                    eprintln!("Respond with: approve | approve_session | deny | abort");
                        }
                        _ => {}
                    }
                    let thread_events = self.event_processor.collect_thread_events(&event);
                    if emit_json_events {
                        for ev in &thread_events {
                            println!("{}", serde_json::to_string(ev)?);
                        }
                    }
                    result.append_events(thread_events);
                    if result.turn_complete() {
                        break;
                    }
                }
                line = stdin_lines.next_line(), if !approvals.is_empty() => {
                    let Some(line) = line? else { continue };
                    if let Some((id, pending)) = approvals.pop_front() {
                        match parse_decision(&line) {
                            Some(decision) => {
                                match pending {
                                    EventMsg::ExecApprovalRequest(_) => {
                                        self.conversation.submit(Op::ExecApproval { id, decision }).await?;
                                    }
                                    EventMsg::ApplyPatchApprovalRequest(_) => {
                                        self.conversation.submit(Op::PatchApproval { id, decision }).await?;
                                    }
                                    _ => {}
                                }
                            }
                            None => {
                                eprintln!("invalid approval response, expected one of: approve, approve_session, deny, abort");
                                approvals.push_front((id, pending));
                            }
                        }
                    }
                }
            }
        }

        Ok(result)
    }

    async fn shutdown(&self) -> Result<()> {
        let _ = self.conversation.submit(Op::Shutdown).await;
        if let Some(id) = &self.session_id {
            let resume_cmd = format!("cleon --resume {id}");
            let info = json!({
                "type": "session.resume",
                "session_id": id,
                "rollout_path": self.rollout_path,
                "resume_command": resume_cmd,
            });
            println!("{info}");
        }
        Ok(())
    }
}

#[derive(Debug, Default, Serialize)]
pub struct TurnResult {
    events: Vec<ThreadEvent>,
    pub final_message: Option<String>,
    pub reasoning: Vec<String>,
    pub usage: Option<Usage>,
    pub errors: Vec<String>,
    #[serde(skip_serializing)]
    completed: bool,
}

impl TurnResult {
    fn append_events(&mut self, events: Vec<ThreadEvent>) {
        for event in events {
            self.update_from_event(&event);
            self.events.push(event);
        }
    }

    fn update_from_event(&mut self, event: &ThreadEvent) {
        match event {
            ThreadEvent::TurnCompleted(ev) => {
                self.completed = true;
                self.usage = Some(ev.usage.clone());
            }
            ThreadEvent::TurnFailed(ev) => {
                self.completed = true;
                self.errors.push(ev.error.message.clone());
            }
            ThreadEvent::Error(err) => {
                self.errors.push(err.message.clone());
            }
            ThreadEvent::ItemCompleted(item) => self.capture_item(&item.item),
            ThreadEvent::ItemUpdated(item) => self.capture_item(&item.item),
            _ => {}
        }
    }

    fn capture_item(&mut self, item: &codex_exec::exec_events::ThreadItem) {
        match &item.details {
            ThreadItemDetails::AgentMessage(msg) => {
                self.final_message = Some(msg.text.clone());
            }
            ThreadItemDetails::Reasoning(reason) => {
                self.reasoning.push(reason.text.clone());
            }
            _ => {}
        }
    }

    fn turn_complete(&self) -> bool {
        self.completed
    }
}

fn parse_decision(input: &str) -> Option<ReviewDecision> {
    match input.trim().to_lowercase().as_str() {
        "approve" | "y" | "yes" => Some(ReviewDecision::Approved),
        "approve_session" | "session" | "always" => Some(ReviewDecision::ApprovedForSession),
        "deny" | "n" | "no" => Some(ReviewDecision::Denied),
        "abort" | "stop" => Some(ReviewDecision::Abort),
        _ => None,
    }
}

fn read_prompt(prompt: Option<String>) -> Result<String> {
    match prompt {
        Some(p) if p.trim() == "-" => read_prompt_from_stdin(),
        Some(p) => Ok(p),
        None => read_prompt_from_stdin(),
    }
}

fn read_prompt_from_stdin() -> Result<String> {
    let mut buffer = String::new();
    let mut stdin = io::stdin();
    if stdin.is_terminal() {
        bail!("No prompt provided. Pass one as an argument or pipe text into stdin.");
    }
    stdin
        .read_to_string(&mut buffer)
        .context("failed to read stdin")?;
    let trimmed = buffer.trim();
    if trimmed.is_empty() {
        bail!("No prompt provided via stdin.");
    }
    Ok(trimmed.to_string())
}

fn read_api_key_from_stdin() -> Result<String> {
    let mut buffer = String::new();
    let mut stdin = io::stdin();
    if stdin.is_terminal() {
        bail!("--with-api-key expects the key on stdin.");
    }
    stdin
        .read_to_string(&mut buffer)
        .context("failed to read API key from stdin")?;
    let trimmed = buffer.trim();
    if trimmed.is_empty() {
        bail!("No API key provided via stdin.");
    }
    Ok(trimmed.to_string())
}

fn safe_key_preview(key: &str) -> String {
    if key.len() <= 6 {
        return "***".into();
    }
    let prefix = &key[..3];
    let suffix = &key[key.len() - 3..];
    format!("{prefix}***{suffix}")
}

async fn load_config() -> Result<Config> {
    let overrides = ConfigOverrides::default();
    Config::load_with_cli_overrides(Vec::new(), overrides)
        .await
        .context("failed to load Codex config")
}
use std::collections::VecDeque;

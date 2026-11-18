//! Only the trusted local main window may spawn the fixed, bundled runtime.
//! Never accepts executable paths, arbitrary arguments or remote URLs.
use std::sync::Mutex;
use tauri::{AppHandle, Manager, State, WebviewWindow, Window};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

#[derive(Default)]
pub struct RuntimeState {
    pub(crate) child: Mutex<Option<CommandChild>>,
}

fn require_main(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Il comando è riservato alla finestra principale.".into());
    }
    Ok(())
}

pub fn owned_running(state: &RuntimeState) -> Result<bool, String> {
    let guard = state.child.lock().map_err(|_| "Stato del backend non accessibile.".to_string())?;
    Ok(guard.is_some())
}

#[tauri::command]
pub fn start_experimental_runtime(
    window: WebviewWindow,
    app: AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<String, String> {
    require_main(&window)?;
    let mut guard = state.child.lock().map_err(|_| "Stato del backend non accessibile.".to_string())?;
    if guard.is_some() {
        return Err("Backend già avviato da questa applicazione.".into());
    }
    // In diagnostic-only installers (no externalBin), this fails closed:
    // never search for a system python.exe or launch an untrusted executable.
    let binary = app.shell().sidecar("gestionale-runtime")
        .map_err(|_| "Questa build non contiene il sidecar FastAPI.".to_string())?;
    let (mut rx, child) = binary.args(["--serve"]).spawn()
        .map_err(|_| "Non è stato possibile eseguire il sidecar confezionato.".to_string())?;
    *guard = Some(child);
    drop(guard);

    let observer = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            if matches!(event, CommandEvent::Terminated(_)) {
                if let Ok(mut lock) = observer.state::<RuntimeState>().child.lock() {
                    lock.take();
                }
                break;
            }
        }
    });
    Ok("Avvio del backend richiesto. Verifica il servizio prima di aprirlo.".into())
}

#[tauri::command]
pub fn stop_experimental_runtime(
    window: WebviewWindow,
    state: State<'_, RuntimeState>,
) -> Result<String, String> {
    require_main(&window)?;
    let mut guard = state.child.lock().map_err(|_| "Stato del backend non accessibile.".to_string())?;
    let child = guard.take()
        .ok_or_else(|| "Non esiste un sidecar di questa applicazione da arrestare.".to_string())?;
    child.kill().map_err(|_| "Non è stato possibile terminare il backend.".to_string())?;
    Ok("Sidecar arrestato; il database MongoDB esterno non è stato fermato.".into())
}

#[tauri::command]
pub fn open_owned_local_backend(
    window: WebviewWindow,
    app: AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<(), String> {
    require_main(&window)?;
    if !owned_running(&state)? {
        return Err("Avvia prima un sidecar locale gestito da questa applicazione.".into());
    }
    crate::open_local_backend(app)
}

pub fn stop_on_main_close(window: &Window, event: &tauri::WindowEvent) {
    if window.label() == "main" && matches!(event, tauri::WindowEvent::Destroyed) {
        if let Some(state) = window.app_handle().try_state::<RuntimeState>() {
            if let Ok(mut guard) = state.child.lock() {
                if let Some(child) = guard.take() {
                    let _ = child.kill();
                }
            }
        }
    }
}

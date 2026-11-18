//! Non-operational preview. The only target is a fixed loopback service.
//! No process spawning, no MongoDB, no cloud access, no arbitrary URL navigation.
use serde::Serialize;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;
use tauri::Manager;
use tauri::{webview::WebviewWindowBuilder, WebviewUrl};
mod sidecar;

const LOCAL_PORT: u16 = 18085;
const LOCAL_URL: &str = "http://127.0.0.1:18085/";
const APP_NAME: &str = "God Services Gestionale Tabacchi";

#[derive(Serialize)]
struct ProbeResult {
    ready: bool,
    detail: String,
}

fn matches_backend_health(raw: &[u8]) -> bool {
    let text = match std::str::from_utf8(raw) {
        Ok(t) => t,
        Err(_) => return false,
    };
    let (head, body) = match text.split_once("\r\n\r\n") {
        Some(parts) => parts,
        None => return false,
    };
    // Never trust an HTTP redirect or arbitrary process occupying the port.
    if !head.lines().next().is_some_and(|line| line.starts_with("HTTP/1.1 200 ")
        || line.starts_with("HTTP/1.0 200 ")) {
        return false;
    }
    match serde_json::from_str::<serde_json::Value>(body) {
        Ok(doc) => doc.get("app").and_then(|x| x.as_str()) == Some(APP_NAME)
            && doc.get("status").and_then(|x| x.as_str()) == Some("ok")
            && doc.get("database").and_then(|x| x.as_str()) == Some("ok"),
        Err(_) => false,
    }
}

fn check_loopback_service() -> Result<(), String> {
    let address = SocketAddr::from(([127, 0, 0, 1], LOCAL_PORT));
    let mut socket = TcpStream::connect_timeout(&address, Duration::from_millis(800))
        .map_err(|_| "Servizio locale non avviato sulla porta 18085.".to_string())?;
    socket
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| "Impossibile verificare il timeout del servizio.".to_string())?;
    socket
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| "Impossibile verificare la connessione.".to_string())?;
    socket
        .write_all(b"GET /api/ HTTP/1.1\r\nHost: 127.0.0.1:18085\r\nAccept: application/json\r\nConnection: close\r\n\r\n")
        .map_err(|_| "Il controllo del backend non è riuscito.".to_string())?;
    let mut response = Vec::new();
    socket
        .take(8193)
        .read_to_end(&mut response)
        .map_err(|_| "Il backend non ha completato il controllo.".to_string())?;
    if response.len() > 8192 || !matches_backend_health(&response) {
        return Err("Il processo sulla porta 18085 non è un backend locale verificato.".to_string());
    }
    Ok(())
}

#[tauri::command]
fn probe_local_backend() -> ProbeResult {
    match check_loopback_service() {
        Ok(()) => ProbeResult {
            ready: true,
            detail: "Backend locale riconosciuto. Questa verifica non certifica l'identità del processo.".into(),
        },
        Err(detail) => ProbeResult { ready: false, detail },
    }
}

#[tauri::command]
fn open_local_backend(app: tauri::AppHandle) -> Result<(), String> {
    open_local_backend_inner(app)
}

pub(crate) fn open_local_backend_inner(app: tauri::AppHandle) -> Result<(), String> {
    // Repeat immediately before opening. Does not prevent a local port-swap
    // race: do not use this preview on a shared/untrusted OS account.
    check_loopback_service()?;
    if let Some(window) = app.get_webview_window("gestionale") {
        return window.set_focus().map_err(|_| "Impossibile attivare la finestra.".into());
    }
    let url = LOCAL_URL
        .parse()
        .map_err(|_| "URL locale non valido.".to_string())?;
    WebviewWindowBuilder::new(&app, "gestionale", WebviewUrl::External(url))
        .title("Gestionale Tabacchi — laboratorio locale")
        .inner_size(1280.0, 820.0)
        .min_inner_size(850.0, 600.0)
        .build()
        .map_err(|_| "Impossibile aprire la finestra del gestionale.".to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::matches_backend_health;
    #[test]
    fn accepts_only_expected_backend_signature() {
        let ok = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"app\":\"God Services Gestionale Tabacchi\",\"status\":\"ok\",\"database\":\"ok\"}";
        assert!(matches_backend_health(ok));
        let forged = b"HTTP/1.1 200 OK\r\n\r\n{\"app\":\"another\",\"status\":\"ok\",\"database\":\"ok\"}";
        assert!(!matches_backend_health(forged));
        assert!(!matches_backend_health(b"HTTP/1.1 302 Found\r\nLocation: https://example.com\r\n\r\n"));
        assert!(!matches_backend_health(b"HTTP/1.1 200 OK\r\n\r\nnot-json"));
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(sidecar::RuntimeState::default())
        .on_window_event(sidecar::stop_on_main_close)
        .invoke_handler(tauri::generate_handler![
            probe_local_backend,
            open_local_backend,
            sidecar::start_experimental_runtime,
            sidecar::stop_experimental_runtime,
            sidecar::open_owned_local_backend
        ])
        .run(tauri::generate_context!())
        .expect("failed to start isolated desktop preview");
}

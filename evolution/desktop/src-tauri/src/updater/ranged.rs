use std::time::Duration;

use base64::{engine::general_purpose::STANDARD, Engine};
use futures_util::{future::try_join_all, StreamExt};
use minisign_verify::{PublicKey, Signature};
use reqwest::{header, Client, StatusCode, Url};
#[cfg(test)]
mod tests;

pub(super) fn release_url_matches(url: &Url, version: &str) -> bool {
    ["windows-setup.exe", "windows.msi"].iter().any(|suffix| {
        url.as_str() == format!("https://siyen.site/releases/siyen-evolution-{version}-{suffix}")
    })
}

/// A server without strong validators or byte ranges uses the standard updater.
pub(super) async fn fetch(url: &Url, progress: impl Fn(usize, u64) + Sync) -> Option<Vec<u8>> {
    let client = Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(8))
        .read_timeout(Duration::from_secs(20))
        .build()
        .ok()?;
    let head = client
        .head(url.clone())
        .header(header::ACCEPT_ENCODING, "identity")
        .send()
        .await
        .ok()?;
    if head.status() != StatusCode::OK
        || head.headers().get(header::ACCEPT_RANGES)?.as_bytes() != b"bytes"
    {
        return None;
    }
    let headers = head.headers();
    let size = headers
        .get(header::CONTENT_LENGTH)?
        .to_str()
        .ok()?
        .parse::<u64>()
        .ok()?;
    let etag = headers.get(header::ETAG)?.to_str().ok()?;
    if !(2 * 1024 * 1024..=128 * 1024 * 1024).contains(&size)
        || !(etag.starts_with('"') && etag.ends_with('"'))
    {
        return None;
    }

    let parts = try_join_all((0..4).map(|i| {
        let start = size * i / 4;
        let end = size * (i + 1) / 4 - 1;
        read_part(&client, url, etag, size, start, end, &progress)
    }))
    .await
    .ok()?;
    // try_join_all preserves the requested range order, regardless of delivery order.
    Some(parts.concat())
}

async fn read_part(
    client: &Client,
    url: &Url,
    etag: &str,
    size: u64,
    start: u64,
    end: u64,
    progress: &(impl Fn(usize, u64) + Sync),
) -> Result<Vec<u8>, ()> {
    let response = client
        .get(url.clone())
        .header(header::ACCEPT_ENCODING, "identity")
        .header(header::RANGE, format!("bytes={start}-{end}"))
        .header(header::IF_RANGE, etag)
        .send()
        .await
        .map_err(|_| ())?;
    let headers = response.headers();
    let length = (end - start + 1) as usize;
    if response.status() != StatusCode::PARTIAL_CONTENT
        || headers.get(header::ETAG).and_then(|v| v.to_str().ok()) != Some(etag)
        || headers
            .get(header::CONTENT_RANGE)
            .and_then(|v| v.to_str().ok())
            != Some(&format!("bytes {start}-{end}/{size}"))
        || headers
            .get(header::CONTENT_LENGTH)
            .and_then(|v| v.to_str().ok()?.parse::<usize>().ok())
            != Some(length)
        || headers
            .get(header::CONTENT_ENCODING)
            .is_some_and(|v| v.as_bytes() != b"identity")
    {
        return Err(());
    }
    let mut body = Vec::with_capacity(length);
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| ())?;
        if chunk.len() > length - body.len() {
            return Err(());
        }
        body.extend_from_slice(&chunk);
        progress(chunk.len(), size);
    }
    if body.len() != length {
        return Err(());
    }
    Ok(body)
}

pub(super) fn install_verified(
    bytes: Vec<u8>,
    key: &str,
    signature: &str,
    install: impl FnOnce(Vec<u8>) -> Result<(), String>,
) -> Result<(), String> {
    // Same minisign verifier and legacy-signature policy as tauri-plugin-updater.
    fn decode(text: &str) -> Result<String, String> {
        String::from_utf8(STANDARD.decode(text).map_err(|_| "更新签名编码错误")?)
            .map_err(|_| "更新签名文本无效".into())
    }
    let key = PublicKey::decode(&decode(key)?).map_err(|_| "更新公钥无效")?;
    let signature = Signature::decode(&decode(signature)?).map_err(|_| "更新签名无效")?;
    key.verify(&bytes, &signature, true)
        .map_err(|_| "安装包签名验证失败")?;
    install(bytes)
}

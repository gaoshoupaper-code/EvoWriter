use super::*;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio::sync::Barrier;

async fn serve(bytes: Arc<Vec<u8>>) -> String {
    let socket = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
    let origin = format!("http://{}", socket.local_addr().unwrap());
    let ready = Arc::new(Barrier::new(4));
    tokio::spawn(async move {
        while let Ok((connection, _)) = socket.accept().await {
            let bytes = bytes.clone();
            let ready = ready.clone();
            tokio::spawn(async move {
                let mut io = BufReader::new(connection);
                let mut request = String::new();
                io.read_line(&mut request).await.unwrap();
                let mut headers = String::new();
                loop {
                    let mut line = String::new();
                    if io.read_line(&mut line).await.unwrap() == 0 || line == "\r\n" {
                        break;
                    }
                    headers.push_str(&line);
                }
                let size = bytes.len();
                let stream = io.get_mut();
                if request.starts_with("HEAD ") {
                    let reply = format!("HTTP/1.1 200 OK\r\nConnection: close\r\nAccept-Ranges: bytes\r\nETag: \"v1\"\r\nContent-Length: {size}\r\n\r\n");
                    stream.write_all(reply.as_bytes()).await.unwrap();
                    return;
                }
                let headers = headers.to_ascii_lowercase();
                assert!(headers.contains("if-range: \"v1\""));
                let (a, b) = headers
                    .lines()
                    .find_map(|line| line.strip_prefix("range: bytes="))
                    .unwrap()
                    .split_once('-')
                    .unwrap();
                let start: usize = a.parse().unwrap();
                let end: usize = b.parse().unwrap();
                let status = if request.contains("/ignored ") {
                    "200 OK"
                } else {
                    "206 Partial Content"
                };
                let reported_start = if request.contains("/wrong-range ") {
                    start + 1
                } else {
                    start
                };
                let etag = if request.contains("/changed ") {
                    "v2"
                } else {
                    "v1"
                };
                let length = end - start + 1;
                let reply = format!("HTTP/1.1 {status}\r\nConnection: close\r\nETag: \"{etag}\"\r\nContent-Range: bytes {reported_start}-{end}/{size}\r\nContent-Length: {length}\r\n\r\n");
                if request.contains("/complete ") {
                    ready.wait().await;
                }
                if stream.write_all(reply.as_bytes()).await.is_ok() {
                    let stop = if request.contains("/truncated ") {
                        end
                    } else {
                        end + 1
                    };
                    let _ = stream.write_all(&bytes[start..stop]).await;
                }
            });
        }
    });
    origin
}

#[tokio::test]
async fn reassembles_four_concurrent_ranges_and_rejects_unusable_responses() {
    let bytes: Arc<Vec<u8>> = Arc::new((0..2_097_155).map(|n| (n % 251) as u8).collect());
    let origin = serve(bytes.clone()).await;
    let received = AtomicU64::new(0);
    let url = Url::parse(&format!("{origin}/complete")).unwrap();
    let result = tokio::time::timeout(
        Duration::from_secs(8),
        fetch(&url, |n, total| {
            assert_eq!(total, bytes.len() as u64);
            received.fetch_add(n as u64, Ordering::Relaxed);
        }),
    )
    .await
    .expect("four requests must be in flight together");
    assert_eq!(result.unwrap(), *bytes);
    assert_eq!(received.load(Ordering::Relaxed), bytes.len() as u64);
    for path in ["ignored", "wrong-range", "changed", "truncated"] {
        let url = Url::parse(&format!("{origin}/{path}")).unwrap();
        assert!(
            fetch(&url, |_, _| {}).await.is_none(),
            "{path} must use the standard updater"
        );
    }
}

#[test]
fn matches_only_expected_https_release_and_version() {
    let good = "https://siyen.site/releases/siyen-evolution-0.2.94-windows-setup.exe";
    assert!(release_url_matches(&Url::parse(good).unwrap(), "0.2.94"));
    assert!(!release_url_matches(&Url::parse(good).unwrap(), "0.2.95"));
    for url in [
        good.replace("https", "http"),
        good.replace("siyen.site", "elsewhere.invalid"),
        good.to_owned() + "?other",
    ] {
        assert!(!release_url_matches(&Url::parse(&url).unwrap(), "0.2.94"));
    }
}

#[test]
fn a_modified_installer_never_reaches_install() {
    // Public minisign-verify test vector. The original message is the four bytes "test".
    let key = STANDARD.encode("untrusted comment: minisign public key E7620F1842B4E81F\nRWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3");
    let sig = STANDARD.encode("untrusted comment: signature from minisign secret key\nRUQf6LRCGA9i559r3g7V1qNyJDApGip8MfqcadIgT9CuhV3EMhHoN1mGTkUidF/z7SrlQgXdy8ofjb7bNJJylDOocrCo8KLzZwo=\ntrusted comment: timestamp:1556193335\tfile:test\ny/rUw2y8/hOUYjZU71eHp/Wo1KZ40fGy2VJEDl34XMJM+TX48Ss/17u3IvIfbVR1FkZZSNCisQbuQY+bHwhEBg==");
    let mut installed = false;
    install_verified(b"test".to_vec(), &key, &sig, |b| {
        assert_eq!(b, b"test");
        installed = true;
        Ok(())
    })
    .unwrap();
    assert!(installed);
    assert!(install_verified(b"Test".to_vec(), &key, &sig, |_| panic!(
        "modified binary installed"
    ))
    .is_err());
    assert!(
        install_verified(b"test".to_vec(), &key, "invalid", |_| panic!(
            "invalid signature installed"
        ))
        .is_err()
    );
}

#[tokio::test]
#[ignore = "downloads the currently published Windows installer for release verification"]
async fn verify_published_installer_over_four_connections() {
    let feed: serde_json::Value = reqwest::get("https://siyen.site/releases/latest-evo.json")
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    let version = feed["version"].as_str().unwrap();
    let platform = &feed["platforms"]["windows-x86_64"];
    let url = Url::parse(platform["url"].as_str().unwrap()).unwrap();
    assert!(release_url_matches(&url, version));
    let config: serde_json::Value =
        serde_json::from_str(include_str!("../../../tauri.conf.json")).unwrap();
    let key = config["plugins"]["updater"]["pubkey"].as_str().unwrap();
    let started = std::time::Instant::now();
    let package = fetch(&url, |_, _| {})
        .await
        .expect("the production origin must support verified ranges");
    let size = package.len();
    install_verified(
        package,
        key,
        platform["signature"].as_str().unwrap(),
        |_| Ok(()),
    )
    .unwrap();
    println!(
        "verified {version}: {size} bytes in {:.2}s",
        started.elapsed().as_secs_f64()
    );
}

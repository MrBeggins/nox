fn main() {
    // Тестовая сборка ядра (cargo test -p jarvis-core) линкуется с Vosk сама — без этого пути
    // «LNK1181: не удается открыть входной файл libvosk.lib». Обычной сборке app путь даёт jarvis-app/build.rs.
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    let lib_path = std::path::Path::new(&manifest_dir).join("..\\..\\lib\\windows\\amd64");
    println!("cargo:rustc-link-search=native={}", lib_path.display());
}

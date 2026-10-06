fn main() {
    // link to Vosk lib
    // println!("cargo:rustc-link-lib=libvosk.dll");

    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    let lib_path = std::path::Path::new(&manifest_dir)
        .join("..\\..\\lib\\windows\\amd64");

    println!("cargo:rustc-link-search=native={}", lib_path.display());

    // Common Controls v6: без этой зависимости в манифесте winit/tray-icon
    // падают при старте с «TaskDialogIndirect не найдена в comctl32».
    // Встраиваем манифест ПРЯМО в exe (/MANIFEST:EMBED), иначе он остаётся
    // внешним файлом рядом с deps и до финального бинаря не доезжает.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let man = std::path::Path::new(&manifest_dir).join("nox.manifest");
        println!("cargo:rerun-if-changed={}", man.display());
        println!("cargo:rustc-link-arg=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg=/MANIFESTINPUT:{}", man.display());
    }
}

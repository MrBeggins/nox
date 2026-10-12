<script lang="ts">
    // «Мозг» — ОДИН выбор на всё (ответы, роутер задач, наблюдатели, сценарии). Ровно два варианта:
    // облако по API-ключу (APIMIRA и т.п., модель — из каталога) или Ollama на этом ПК (бесплатно).
    // Подписка Claude убрана. Применяется сразу, без перезапуска.
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import InfoDot from "@/components/elements/InfoDot.svelte"
    import { reloadSettings } from "@/stores"

    type Preset = { name: string, url: string, model: string, key?: string, desc?: string }

    const CLOUD_URL_DEFAULT = "https://apimira.com/v1"
    const OLLAMA_DEFAULT = "qwen2.5:3b"

    let backend = "openai"              // "openai" = облако, "ollama" = локально
    let cloudUrl = CLOUD_URL_DEFAULT, cloudModel = "", ollamaModel = OLLAMA_DEFAULT
    let keySaved = "", newKey = ""
    let presets: Preset[] = []
    let cloudCatalog: string[] = [], ollamaList: string[] = []
    let ollamaOk = true, cloudOk = true
    let busy = false, toast = ""
    function flash(m: string) { toast = m; setTimeout(() => (toast = ""), 2600) }
    const rd = async (key: string) => (await invoke<string>("db_read", { key })) || ""
    const wr = (key: string, val: string) => invoke("db_write", { key, val })

    async function load() {
        try {
            const b = (await rd("brain_backend")).toLowerCase()
            const url = await rd("openai_url")
            const model = await rd("openai_model")
            ollamaModel = (await rd("ollama_model")) || OLLAMA_DEFAULT
            keySaved = await rd("api_key__openai")
            const raw = await rd("ai_models")
            presets = raw ? JSON.parse(raw) : []
            if (url.includes("11434")) {
                // старый «локальный» выбор хранился как openai + адрес Ollama -> переносим в новые поля
                backend = "ollama"; ollamaModel = model || ollamaModel
                cloudUrl = CLOUD_URL_DEFAULT; cloudModel = (presets[0] && presets[0].model) || ""
            } else {
                backend = b === "ollama" ? "ollama" : "openai"   // старые claude/auto -> облако
                cloudUrl = url || CLOUD_URL_DEFAULT; cloudModel = model
            }
        } catch (e) { console.error(e) }
        try {
            const r = JSON.parse(await invoke<string>("brain_models"))
            ollamaList = r.ollama || []; cloudCatalog = r.cloud || []
            ollamaOk = !!r.ollama_ok; cloudOk = !!r.cloud_ok
        } catch (e) { ollamaOk = false; cloudOk = false }
    }
    onMount(load)

    $: masked = keySaved ? keySaved.slice(0, 3) + "…" + keySaved.slice(-4) : ""
    $: presetName = (presets.find((p) => p.model === cloudModel) || { name: cloudModel || "модель не выбрана" }).name
    $: nowText = backend === "ollama" ? `Ollama (бесплатно) · ${ollamaModel}` : `Облако по ключу · ${presetName}`
    $: catalogRest = cloudCatalog.filter((m) => !presets.some((p) => p.model === m))

    async function apply(next: string) {
        if (busy) return
        if (next === "openai" && !keySaved && !newKey.trim()) { flash("Вставьте API-ключ облака"); return }
        if (next === "openai" && !cloudModel) { flash("Выберите модель облака"); return }
        busy = true
        try {
            if (newKey.trim()) { await wr("api_key__openai", newKey.trim()); keySaved = newKey.trim(); newKey = "" }
            await wr("openai_url", cloudUrl.trim() || CLOUD_URL_DEFAULT)
            if (cloudModel) await wr("openai_model", cloudModel)
            await wr("ollama_model", ollamaModel || OLLAMA_DEFAULT)
            await wr("brain_backend", next)
            backend = next
            reloadSettings()            // голосовой движок подхватит сразу
            flash("Теперь думает: " + nowText)
        } catch (e) { flash("Не удалось сохранить") }
        busy = false
    }
    // смена модели внутри уже выбранного варианта — применить сразу
    function pickCloud(m: string) { cloudModel = m; if (backend === "openai") apply("openai") }
    function pickOllama(m: string) { ollamaModel = m; if (backend === "ollama") apply("ollama") }
</script>

<div class="page">
    <h2>Мозг</h2>
    <p class="hint">Кто думает за Нокса — один выбор на всё: ответы на вопросы, понимание задач, наблюдатели и сценарии.
        Два варианта: облако по API-ключу или Ollama на этом компьютере. Применяется сразу.</p>
    <div class="now">Сейчас думает: <b>{nowText}</b></div>
    {#if toast}<div class="toast">{toast}</div>{/if}

    <div class="cards">
        <!-- ОБЛАКО -->
        <div class="card" class:sel={backend === "openai"}>
            <button class="head" disabled={busy} on:click={() => apply("openai")}>
                <span class="radio" class:on={backend === "openai"}></span>
                <span class="name">Облако по API-ключу</span>
                <span class="tags"><span class="tag">умнее</span><span class="tag">нужен интернет</span></span>
            </button>
            <div class="desc">Модели через ваш ключ APIMIRA (или другой OpenAI-совместимый сервис). Если облако не ответит —
                Нокс подстрахуется локальной Ollama.</div>

            <label>Модель
                <div class="chips">
                    {#each presets as p}
                        <button class="chip" class:on={cloudModel === p.model} title={p.desc || p.model}
                            on:click={() => pickCloud(p.model)}>{p.name}</button>
                    {/each}
                </div>
                <select value={cloudModel} on:change={(e) => pickCloud(e.currentTarget.value)}>
                    {#if !cloudModel}<option value="">— выберите модель —</option>{/if}
                    {#each presets as p}<option value={p.model}>{p.name} · {p.model}</option>{/each}
                    {#if catalogRest.length}
                        <optgroup label="Весь каталог">
                            {#each catalogRest as m}<option value={m}>{m}</option>{/each}
                        </optgroup>
                    {/if}
                </select>
            </label>
            {#if !cloudOk && keySaved}<div class="warn">Каталог облака не загрузился (нет сети или ключ не подошёл) — доступен быстрый выбор выше.</div>{/if}

            <label>API-ключ<InfoDot text="Ключ облака (для APIMIRA начинается с am-). Хранится только на этом ПК." />
                <input type="password" bind:value={newKey} placeholder={masked ? `сохранён: ${masked} — вставьте новый, чтобы заменить` : "вставьте ключ am-…"} />
            </label>
            <details class="adv">
                <summary>Адрес сервиса · для опытных</summary>
                <input bind:value={cloudUrl} placeholder={CLOUD_URL_DEFAULT} />
            </details>
            {#if backend === "openai" && newKey.trim()}
                <button class="btn" disabled={busy} on:click={() => apply("openai")}>Сохранить ключ</button>
            {/if}
        </div>

        <!-- OLLAMA -->
        <div class="card" class:sel={backend === "ollama"}>
            <button class="head" disabled={busy} on:click={() => apply("ollama")}>
                <span class="radio" class:on={backend === "ollama"}></span>
                <span class="name">Ollama — бесплатно</span>
                <span class="tags"><span class="tag">офлайн</span><span class="tag">бесплатно</span></span>
            </button>
            <div class="desc">Работает на этом компьютере, без интернета и без ключа. Быстрый, но заметно проще облака:
                годится для простых команд и разбора новостей.</div>
            <label>Модель<InfoDot text="Модели, установленные в Ollama. 3b — быстрее, 7b — умнее, но медленнее." />
                <select value={ollamaModel} on:change={(e) => pickOllama(e.currentTarget.value)}>
                    {#if !ollamaList.includes(ollamaModel)}<option value={ollamaModel}>{ollamaModel}</option>{/if}
                    {#each ollamaList as m}<option value={m}>{m}</option>{/each}
                </select>
            </label>
            {#if !ollamaOk}<div class="warn">Ollama не запущена — Нокс поднимет её сам при старте; список моделей появится после этого.</div>{/if}
        </div>
    </div>
</div>

<style>
    .page { padding: 0 0.9rem 2rem; color: #dfeff0; }
    h2 { margin: 0 0 0.2rem; font-size: 1.25rem; color: #eafeff; }
    .hint { color: rgba(223,239,240,0.55); font-size: 0.8rem; margin: 0.15rem 0 0.6rem; }
    .now { font-size: 0.85rem; color: rgba(223,239,240,0.75); margin-bottom: 0.7rem; }
    .now b { color: #2fe6e6; }
    .toast { background: rgba(47,230,230,0.14); border: 1px solid #2fe6e6; color: #eafeff;
        padding: 0.4rem 0.7rem; border-radius: 8px; font-size: 0.8rem; margin-bottom: 0.6rem; }

    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 0.7rem; }
    .card { background: rgba(15,26,30,0.6); border: 1px solid rgba(82,254,254,0.14); border-radius: 12px;
        padding: 0.8rem 0.95rem; display: flex; flex-direction: column; gap: 0.5rem; }
    .card.sel { border-color: #2fe6e6; background: rgba(47,230,230,0.1); box-shadow: 0 0 0 1px #2fe6e6 inset; }
    .head { display: flex; align-items: center; gap: 0.5rem; background: none; border: none; padding: 0;
        color: inherit; cursor: pointer; text-align: left; font: inherit; flex-wrap: wrap; }
    .head:disabled { cursor: default; }
    .radio { width: 15px; height: 15px; border-radius: 50%; border: 2px solid rgba(223,239,240,0.4); flex: none; }
    .radio.on { border-color: #2fe6e6; background: #2fe6e6; box-shadow: 0 0 8px #2fe6e6; }
    .name { font-weight: 700; color: #eafeff; font-size: 0.95rem; }
    .tags { display: flex; gap: 0.3rem; margin-left: auto; }
    .tag { font-size: 0.66rem; color: #94a3b8; border: 1px solid rgba(148,163,184,0.3); border-radius: 999px; padding: 0.05rem 0.45rem; }
    .desc { font-size: 0.76rem; color: rgba(223,239,240,0.62); line-height: 1.4; }
    label { display: block; font-size: 0.78rem; color: rgba(223,239,240,0.75); }
    .chips { display: flex; flex-wrap: wrap; gap: 0.3rem; margin: 0.3rem 0; }
    .chip { font-size: 0.74rem; padding: 0.25rem 0.55rem; border-radius: 999px; cursor: pointer;
        background: rgba(35,50,55,0.7); border: 1px solid rgba(82,254,254,0.2); color: #cdeff0; }
    .chip.on { background: rgba(47,230,230,0.22); border-color: #2fe6e6; color: #eafeff; }
    input, select { width: 100%; margin-top: 0.28rem; padding: 0.45rem 0.55rem; background: rgba(8,16,19,0.85);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 7px; color: #eafeff; font-size: 0.82rem; box-sizing: border-box; }
    input:focus, select:focus { outline: none; border-color: #2fe6e6; }
    .warn { font-size: 0.74rem; color: #fde68a; }
    .adv summary { cursor: pointer; font-size: 0.75rem; color: rgba(223,239,240,0.6); }
    .btn { align-self: flex-start; padding: 0.4rem 0.8rem; background: rgba(47,230,230,0.18); border: 1px solid #2fe6e6;
        border-radius: 8px; color: #eafeff; font-size: 0.8rem; cursor: pointer; }
</style>

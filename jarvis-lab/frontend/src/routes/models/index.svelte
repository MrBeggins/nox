<script lang="ts">
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import HDivider from "@/components/elements/HDivider.svelte"
    import InfoDot from "@/components/elements/InfoDot.svelte"

    // active backend
    let backend = "claude"
    // openai-compatible connection (used by the "openai" backend: ChatGPT or local)
    let url = ""
    let model = ""
    let key = ""

    type Preset = { name: string, url: string, model: string, key?: string }
    let presets: Preset[] = []

    let toast = ""
    function flash(m: string) { toast = m; setTimeout(() => (toast = ""), 1600) }

    async function load() {
        try {
            backend = (await invoke<string>("db_read", { key: "brain_backend" })) || "claude"
            url = await invoke<string>("db_read", { key: "openai_url" })
            model = await invoke<string>("db_read", { key: "openai_model" })
            key = await invoke<string>("db_read", { key: "api_key__openai" })
            const raw = await invoke<string>("db_read", { key: "ai_models" })
            presets = raw ? JSON.parse(raw) : []
        } catch (e) { console.error(e) }
    }
    onMount(load)

    async function setBackend(b: string) {
        backend = b
        await invoke("db_write", { key: "brain_backend", val: b })
        flash("Активный мозг: " + b)
    }

    async function saveConnection() {
        await invoke("db_write", { key: "openai_url", val: url })
        await invoke("db_write", { key: "openai_model", val: model })
        await invoke("db_write", { key: "api_key__openai", val: key })
        flash("Подключение сохранено")
    }

    function usePresetLocal() { url = "http://127.0.0.1:11434/v1"; model = model || "qwen2.5:7b"; key = "" }
    function usePresetChatGPT() { url = "https://api.openai.com/v1"; model = model || "gpt-4o-mini" }

    // custom presets
    let p_name = "", p_url = "", p_model = "", p_key = ""
    async function savePresets() {
        await invoke("db_write", { key: "ai_models", val: JSON.stringify(presets) })
    }
    async function addPreset() {
        if (!p_name.trim() || !p_url.trim()) { flash("Укажите название и URL"); return }
        presets = [...presets, { name: p_name.trim(), url: p_url.trim(), model: p_model.trim(), key: p_key.trim() }]
        p_name = ""; p_url = ""; p_model = ""; p_key = ""
        await savePresets(); flash("Модель добавлена")
    }
    async function removePreset(i: number) {
        presets = presets.filter((_, idx) => idx !== i)
        await savePresets()
    }
    async function applyPreset(p: Preset) {
        url = p.url; model = p.model; key = p.key || ""
        await saveConnection()
        await setBackend("openai")
        flash("Применена модель: " + p.name)
    }
</script>

<div class="page">
    <h2>ИИ-модели</h2>
    <p class="hint">Несколько «мозгов» под разные задачи. Переключай активный или подключи свою модель.</p>
    {#if toast}<div class="toast">{toast}</div>{/if}

    <HDivider />

    <h3>Активный мозг<InfoDot text="Какой мозг отвечает на разговорные вопросы и команды ассистента. Claude — умнее, нужен вход+VPN. OpenAI-совместимая — ChatGPT по ключу ИЛИ локальная Ollama (бесплатно, офлайн). Авто — сначала дешёвая/локальная, при сбое Claude. (Это мозг ассистента; мозг сценариев настраивается отдельно во вкладке «Сценарии».)" /></h3>
    <div class="backends">
        <button class="be" class:sel={backend==='claude'} on:click={() => setBackend('claude')}>
            <div class="be-name">Claude</div>
            <div class="be-sub">Подписка · нужен VPN + вход</div>
        </button>
        <button class="be" class:sel={backend==='openai'} on:click={() => setBackend('openai')}>
            <div class="be-name">OpenAI-совместимая</div>
            <div class="be-sub">ChatGPT (ключ) или локальная (Ollama)</div>
        </button>
        <button class="be" class:sel={backend==='auto'} on:click={() => setBackend('auto')}>
            <div class="be-name">Авто</div>
            <div class="be-sub">Сначала локальная/дешёвая → Claude</div>
        </button>
    </div>

    <HDivider />

    <h3>Подключение OpenAI-совместимой модели</h3>
    <p class="hint">Используется, когда активен мозг «OpenAI-совместимая» или «Авто».</p>
    <div class="presets-quick">
        <button class="btn" on:click={usePresetLocal}>⚡ Локальная (Ollama)</button>
        <button class="btn" on:click={usePresetChatGPT}>☁ ChatGPT</button>
    </div>
    <label>Базовый URL<InfoDot text="Адрес API модели. Локальная Ollama: http://127.0.0.1:11434/v1 (бесплатно, офлайн). ChatGPT: https://api.openai.com/v1 (нужен ключ). Кнопки-пресеты выше подставляют нужный адрес." />
        <input bind:value={url} placeholder="http://127.0.0.1:11434/v1  или  https://api.openai.com/v1" />
    </label>
    <label>Модель<InfoDot text="Имя модели. Для Ollama — напр. qwen2.5:3b (быстрее) или qwen2.5:7b (умнее). Для ChatGPT — напр. gpt-4o-mini. Должна быть скачана/доступна у провайдера." />
        <input bind:value={model} placeholder="qwen2.5:7b  /  gpt-4o-mini" />
    </label>
    <label>API-ключ (для ChatGPT; для локальной — пусто)<InfoDot text="Ключ доступа к облачной модели (ChatGPT: sk-...). Для локальной Ollama ключ не нужен — оставь пусто. Хранится локально." />
        <input type="password" bind:value={key} placeholder="sk-..." />
    </label>
    <button class="btn primary" on:click={saveConnection}>Сохранить подключение</button>

    <HDivider />

    <h3>Мои модели</h3>
    <p class="hint">Сохрани свои конфигурации и переключайся одной кнопкой.</p>

    <div class="list">
        {#each presets as p, i}
            <div class="card">
                <div>
                    <div class="cmd-id">{p.name}</div>
                    <div class="card-desc">{p.url} · {p.model || "—"}{p.key ? " · 🔑" : ""}</div>
                </div>
                <div class="card-actions">
                    <button class="btn small primary" on:click={() => applyPreset(p)}>Применить</button>
                    <button class="btn small danger" on:click={() => removePreset(i)}>🗑</button>
                </div>
            </div>
        {/each}
        {#if presets.length === 0}<p class="hint">Пока нет сохранённых моделей.</p>{/if}
    </div>

    <div class="add-form">
        <div class="add-grid">
            <input bind:value={p_name} placeholder="Название (My GPT)" />
            <input bind:value={p_url} placeholder="Базовый URL" />
            <input bind:value={p_model} placeholder="Модель" />
            <input type="password" bind:value={p_key} placeholder="Ключ (опц.)" />
        </div>
        <button class="btn primary" on:click={addPreset}>+ Добавить модель</button>
    </div>
</div>

<style>
    .page { padding: 0 0.9rem 2rem; color: #dfeff0; }
    h2 { margin: 0 0 0.2rem; font-size: 1.25rem; color: #eafeff; }
    h3 { margin: 0.6rem 0 0.4rem; font-size: 1rem; color: #cdeff0; }
    .hint { color: rgba(223,239,240,0.55); font-size: 0.8rem; margin: 0.15rem 0 0.6rem; }
    .toast { background: rgba(47,230,230,0.14); border: 1px solid #2fe6e6; color: #eafeff;
        padding: 0.4rem 0.7rem; border-radius: 8px; font-size: 0.8rem; margin-bottom: 0.5rem; }

    .backends { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.6rem; }
    .be { text-align: left; background: rgba(15,26,30,0.6); border: 1px solid rgba(82,254,254,0.14);
        border-radius: 10px; padding: 0.7rem 0.85rem; cursor: pointer; color: #dfeff0; transition: all 0.15s ease; }
    .be:hover { background: rgba(82,254,254,0.08); }
    .be.sel { border-color: #2fe6e6; background: rgba(47,230,230,0.14); box-shadow: 0 0 0 1px #2fe6e6 inset; }
    .be-name { font-weight: 600; color: #eafeff; font-size: 0.9rem; }
    .be-sub { font-size: 0.74rem; color: rgba(223,239,240,0.6); margin-top: 0.2rem; }

    .presets-quick { display: flex; gap: 0.5rem; margin-bottom: 0.6rem; flex-wrap: wrap; }
    label { display: block; font-size: 0.78rem; color: rgba(223,239,240,0.75); margin-bottom: 0.6rem; }
    input {
        width: 100%; margin-top: 0.28rem; padding: 0.5rem 0.6rem;
        background: rgba(8,16,19,0.85); border: 1px solid rgba(82,254,254,0.2);
        border-radius: 7px; color: #eafeff; font-size: 0.85rem; box-sizing: border-box;
    }
    input:focus { outline: none; border-color: #2fe6e6; }

    .btn { padding: 0.5rem 0.9rem; background: rgba(35,50,55,0.7);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 8px; color: #cdeff0;
        font-size: 0.82rem; cursor: pointer; transition: all 0.15s ease; }
    .btn:hover { background: rgba(82,254,254,0.12); }
    .btn.primary { background: rgba(47,230,230,0.18); border-color: #2fe6e6; color: #eafeff; }
    .btn.small { padding: 0.35rem 0.6rem; }
    .btn.danger { color: #ffb4b4; border-color: rgba(255,80,80,0.35); }

    .list { display: flex; flex-direction: column; gap: 0.5rem; margin: 0.4rem 0 0.8rem; }
    .card { display: flex; justify-content: space-between; align-items: center; gap: 0.75rem;
        background: rgba(15,26,30,0.6); border: 1px solid rgba(82,254,254,0.12); border-radius: 10px; padding: 0.65rem 0.9rem; }
    .cmd-id { font-weight: 600; color: #eafeff; font-size: 0.9rem; }
    .card-desc { font-size: 0.76rem; color: rgba(223,239,240,0.6); margin-top: 0.2rem; word-break: break-all; }
    .card-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }

    .add-form { background: rgba(15,26,30,0.5); border: 1px dashed rgba(82,254,254,0.2); border-radius: 10px; padding: 0.8rem; }
    .add-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.5rem; margin-bottom: 0.6rem; }
    .add-grid input { margin-top: 0; }
</style>

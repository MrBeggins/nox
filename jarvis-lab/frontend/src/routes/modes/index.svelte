<script lang="ts">
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import HDivider from "@/components/elements/HDivider.svelte"
    import { reloadSettings } from "@/stores"

    function applyLive() { try { reloadSettings() } catch (e) { console.error(e) } }

    type Mode = { id: string, name: string, wake_word: string, brain: string, model: string }
    let modes: Mode[] = []
    let activeId = ""
    let toast = ""
    function flash(m: string) { toast = m; setTimeout(() => (toast = ""), 2000) }

    // редактор
    let editing: Mode | null = null
    let isNew = false
    let f_name = "", f_wake = "джарвис", f_brain = "openai", f_model = "qwen2.5:7b"

    // тумблеры
    type Tgl = { key: string, title: string, desc: string, value: boolean }
    let toggles: Tgl[] = [
        { key: "brain_enabled",  title: "🧠 Мозг", desc: "Разговорные ответы ИИ.", value: true },
        { key: "tts_enabled",    title: "🔊 Голос", desc: "Озвучка ответов.", value: true },
        { key: "vision_enabled", title: "👁 Зрение", desc: "Анализ экрана (тратит токены).", value: false },
    ]
    let loaded = false

    async function load() {
        try {
            const raw = await invoke<string>("db_read", { key: "modes" })
            modes = raw ? JSON.parse(raw) : []
            activeId = await invoke<string>("db_read", { key: "active_mode" })
            if (modes.length === 0) {
                // создать профиль по умолчанию из текущих настроек
                const wake = (await invoke<string>("db_read", { key: "wake_word" })) || "джарвис"
                const brain = (await invoke<string>("db_read", { key: "brain_backend" })) || "openai"
                const model = (await invoke<string>("db_read", { key: "openai_model" })) || "qwen2.5:7b"
                modes = [{ id: "main", name: "Основной", wake_word: wake, brain, model }]
                activeId = "main"
                await persist()
                await invoke("db_write", { key: "active_mode", val: "main" })
            }
            for (const t of toggles) {
                const v = await invoke<string>("db_read", { key: t.key }); t.value = v === "true"
            }
            toggles = toggles
        } catch (e) { console.error(e) }
        loaded = true
    }
    onMount(load)

    async function persist() {
        await invoke("db_write", { key: "modes", val: JSON.stringify(modes) })
    }

    function startNew() {
        isNew = true; editing = null
        f_name = ""; f_wake = "джарвис"; f_brain = "openai"; f_model = "qwen2.5:7b"
    }
    function startEdit(m: Mode) {
        isNew = false; editing = m
        f_name = m.name; f_wake = m.wake_word; f_brain = m.brain; f_model = m.model
    }
    function cancel() { editing = null; isNew = false }

    async function saveMode() {
        if (!f_name.trim()) { flash("Укажите название режима"); return }
        if (isNew) {
            const id = "m" + Date.now()
            modes = [...modes, { id, name: f_name.trim(), wake_word: (f_wake.trim() || "джарвис").toLowerCase(), brain: f_brain, model: f_model.trim() }]
        } else if (editing) {
            editing.name = f_name.trim(); editing.wake_word = (f_wake.trim() || "джарвис").toLowerCase()
            editing.brain = f_brain; editing.model = f_model.trim()
            modes = modes
        }
        await persist()
        // если редактировали активный — применить сразу
        if (editing && editing.id === activeId) await activate(editing)
        cancel(); flash("Режим сохранён")
    }
    async function removeMode(m: Mode) {
        if (modes.length <= 1) { flash("Нужен хотя бы один режим"); return }
        if (!confirm(`Удалить режим «${m.name}»?`)) return
        modes = modes.filter(x => x.id !== m.id)
        await persist()
        if (activeId === m.id) await activate(modes[0])
    }
    async function activate(m: Mode) {
        activeId = m.id
        await invoke("db_write", { key: "active_mode", val: m.id })
        await invoke("db_write", { key: "wake_word", val: m.wake_word })
        await invoke("db_write", { key: "brain_backend", val: m.brain })
        if (m.model) await invoke("db_write", { key: "openai_model", val: m.model })
        applyLive()  // мозг/режим/команды применяются сразу; имя — после перезапуска
        flash(`Активен режим «${m.name}». Мозг и команды — сразу; имя-обращение — после перезапуска ассистента.`)
    }

    async function toggle(t: Tgl) {
        t.value = !t.value; toggles = toggles
        await invoke("db_write", { key: t.key, val: String(t.value) })
        applyLive()
    }
</script>

<div class="page">
    <h2>Режимы</h2>
    <p class="hint">Профили ассистента: у каждого своё имя-обращение, свой мозг и свой набор команд (команды привязываются на вкладке «Команды»).</p>
    {#if toast}<div class="toast">{toast}</div>{/if}

    <div class="actions"><button class="btn primary" on:click={startNew}>+ Новый режим</button></div>

    {#if editing !== null || isNew}
        <div class="editor">
            <h3>{isNew ? "Новый режим" : `Редактирование: ${editing?.name}`}</h3>
            <label>Название режима
                <input bind:value={f_name} placeholder="Трейдинг / Дом / Игры" />
            </label>
            <label>Имя-обращение (wake word)
                <input bind:value={f_wake} placeholder="джарвис / кит / петя" />
            </label>
            <div class="grid2">
                <label>Мозг
                    <select bind:value={f_brain}>
                        <option value="openai">Локальная / OpenAI-совместимая</option>
                        <option value="claude">Claude</option>
                        <option value="auto">Авто</option>
                    </select>
                </label>
                <label>Модель (для локальной/OpenAI)
                    <input bind:value={f_model} placeholder="qwen2.5:7b / gpt-4o-mini" />
                </label>
            </div>
            <div class="actions">
                <button class="btn primary" on:click={saveMode}>Сохранить</button>
                <button class="btn" on:click={cancel}>Отмена</button>
            </div>
        </div>
    {/if}

    <div class="list">
        {#each modes as m}
            <div class="card" class:active={m.id === activeId}>
                <div>
                    <div class="card-title">
                        <span class="nm">{m.name}</span>
                        {#if m.id === activeId}<span class="badge">активен</span>{/if}
                    </div>
                    <div class="sub">🗣 «{m.wake_word}» · мозг: {m.brain}{m.model ? ` (${m.model})` : ""}</div>
                </div>
                <div class="card-actions">
                    {#if m.id !== activeId}<button class="btn small primary" on:click={() => activate(m)}>Включить</button>{/if}
                    <button class="btn small" on:click={() => startEdit(m)}>✎</button>
                    <button class="btn small danger" on:click={() => removeMode(m)}>🗑</button>
                </div>
            </div>
        {/each}
    </div>

    <HDivider />
    <h3>Возможности</h3>
    <div class="list">
        {#each toggles as t}
            <div class="row">
                <div>
                    <div class="card-title"><span class="nm">{t.title}</span></div>
                    <div class="sub">{t.desc}</div>
                </div>
                <button class="switch" class:on={t.value} disabled={!loaded} on:click={() => toggle(t)}>
                    <span class="knob"></span>
                </button>
            </div>
        {/each}
    </div>
</div>

<style>
    .page { padding: 0 0.9rem 2rem; color: #dfeff0; }
    h2 { margin: 0 0 0.2rem; font-size: 1.25rem; color: #eafeff; }
    h3 { margin: 0.6rem 0 0.4rem; font-size: 1rem; color: #cdeff0; }
    .hint { color: rgba(223,239,240,0.55); font-size: 0.8rem; margin: 0.15rem 0 0.6rem; }
    .toast { background: rgba(47,230,230,0.14); border: 1px solid #2fe6e6; color: #eafeff;
        padding: 0.4rem 0.7rem; border-radius: 8px; font-size: 0.8rem; margin-bottom: 0.5rem; }
    .actions { display: flex; gap: 0.5rem; margin: 0.4rem 0; }
    .editor { background: rgba(15,26,30,0.7); border: 1px solid rgba(82,254,254,0.18); border-radius: 12px; padding: 1rem; margin-bottom: 0.8rem; }
    .grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px,1fr)); gap: 0.6rem; }
    label { display: block; font-size: 0.78rem; color: rgba(223,239,240,0.75); margin-bottom: 0.6rem; }
    input, select { width: 100%; margin-top: 0.28rem; padding: 0.5rem 0.6rem; background: rgba(8,16,19,0.85);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 7px; color: #eafeff; font-size: 0.85rem; box-sizing: border-box; }
    input:focus, select:focus { outline: none; border-color: #2fe6e6; }
    .list { display: flex; flex-direction: column; gap: 0.5rem; margin: 0.4rem 0; }
    .card, .row { display: flex; justify-content: space-between; align-items: center; gap: 0.75rem;
        background: rgba(15,26,30,0.6); border: 1px solid rgba(82,254,254,0.12); border-radius: 10px; padding: 0.7rem 0.9rem; }
    .card.active { border-color: #2fe6e6; box-shadow: 0 0 0 1px #2fe6e6 inset; }
    .card-title { display: flex; align-items: center; gap: 0.5rem; }
    .nm { font-weight: 600; color: #eafeff; font-size: 0.92rem; }
    .badge { font-size: 0.64rem; text-transform: uppercase; background: rgba(47,230,230,0.18); color: #52fefe; padding: 0.1rem 0.4rem; border-radius: 5px; }
    .sub { font-size: 0.76rem; color: rgba(223,239,240,0.6); margin-top: 0.2rem; }
    .card-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }
    .btn { padding: 0.5rem 0.9rem; background: rgba(35,50,55,0.7); border: 1px solid rgba(82,254,254,0.2);
        border-radius: 8px; color: #cdeff0; font-size: 0.82rem; cursor: pointer; }
    .btn:hover { background: rgba(82,254,254,0.12); }
    .btn.primary { background: rgba(47,230,230,0.18); border-color: #2fe6e6; color: #eafeff; }
    .btn.small { padding: 0.35rem 0.55rem; }
    .btn.danger { color: #ffb4b4; border-color: rgba(255,80,80,0.35); }
    .switch { position: relative; width: 50px; height: 28px; flex-shrink: 0; background: rgba(255,255,255,0.12);
        border: 1px solid rgba(255,255,255,0.15); border-radius: 999px; cursor: pointer; padding: 0; }
    .switch.on { background: rgba(47,230,230,0.35); border-color: #2fe6e6; }
    .knob { position: absolute; top: 2px; left: 2px; width: 22px; height: 22px; background: #eafeff; border-radius: 50%; transition: transform 0.2s ease; }
    .switch.on .knob { transform: translateX(22px); background: #52fefe; }
</style>

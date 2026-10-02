<script lang="ts">
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import HDivider from "@/components/elements/HDivider.svelte"

    type Cmd = {
        id: string
        type: string
        description?: string
        exe_path?: string
        exe_args?: string[]
        cli_cmd?: string
        cli_args?: string[]
        script?: string
        sandbox?: string
        phrases?: Record<string, string[]>
        sounds?: Record<string, string[]>
        modes?: string[]
    }

    let commands: Cmd[] = []
    let availableModes: { id: string, name: string }[] = []
    let loading = true
    let editing: Cmd | null = null
    let isNew = false
    let error = ""
    let saving = false

    // form fields
    let f_id = ""
    let f_type = "ahk"
    let f_desc = ""
    let f_exe_path = ""
    let f_exe_args = ""
    let f_cli_cmd = ""
    let f_cli_args = ""
    let f_script = ""
    let f_sandbox = "standard"
    let f_ru = ""
    let f_en = ""
    let f_ua = ""
    let f_modes: string[] = []

    async function load() {
        loading = true
        try {
            commands = await invoke<Cmd[]>("get_commands_list")
            const raw = await invoke<string>("db_read", { key: "modes" })
            availableModes = raw ? JSON.parse(raw) : []
        } catch (e) {
            error = String(e)
        }
        loading = false
    }

    function toggleMode(id: string) {
        f_modes = f_modes.includes(id) ? f_modes.filter(m => m !== id) : [...f_modes, id]
    }
    onMount(load)

    function startNew() {
        isNew = true
        editing = null
        f_id = ""; f_type = "ahk"; f_desc = ""
        f_exe_path = ""; f_exe_args = ""; f_cli_cmd = ""; f_cli_args = ""
        f_script = ""; f_sandbox = "standard"
        f_ru = ""; f_en = ""; f_ua = ""
        f_modes = []
        error = ""
    }

    function startEdit(c: Cmd) {
        isNew = false
        editing = c
        f_id = c.id
        f_type = c.type || "ahk"
        f_desc = c.description || ""
        f_exe_path = c.exe_path || ""
        f_exe_args = (c.exe_args || []).join(", ")
        f_cli_cmd = c.cli_cmd || ""
        f_cli_args = (c.cli_args || []).join(", ")
        f_script = c.script || ""
        f_sandbox = c.sandbox || "standard"
        f_ru = (c.phrases?.ru || []).join("\n")
        f_en = (c.phrases?.en || []).join("\n")
        f_ua = (c.phrases?.ua || []).join("\n")
        f_modes = c.modes || []
        error = ""
    }

    function cancel() { editing = null; isNew = false; error = "" }

    function lines(s: string): string[] {
        return s.split("\n").map(x => x.trim()).filter(Boolean)
    }
    function csv(s: string): string[] {
        return s.split(",").map(x => x.trim()).filter(Boolean)
    }

    async function save() {
        error = ""
        if (!f_id.trim()) { error = "Укажите ID команды (латиницей)"; return }
        if (lines(f_ru).length + lines(f_en).length + lines(f_ua).length === 0) {
            error = "Добавьте хотя бы одну фразу"; return
        }
        const phrases: Record<string, string[]> = {}
        if (lines(f_ru).length) phrases.ru = lines(f_ru)
        if (lines(f_en).length) phrases.en = lines(f_en)
        if (lines(f_ua).length) phrases.ua = lines(f_ua)

        const input: Cmd = { id: f_id.trim(), type: f_type, description: f_desc, phrases, modes: f_modes }
        if (f_type === "ahk") { input.exe_path = f_exe_path; input.exe_args = csv(f_exe_args) }
        if (f_type === "cli") { input.cli_cmd = f_cli_cmd; input.cli_args = csv(f_cli_args) }
        if (f_type === "lua") { input.script = f_script; input.sandbox = f_sandbox }

        saving = true
        try {
            await invoke("save_command", { input })
            await load()
            cancel()
        } catch (e) {
            error = String(e)
        }
        saving = false
    }

    async function remove(c: Cmd) {
        if (!confirm(`Удалить команду «${c.id}»?`)) return
        try {
            await invoke("delete_command", { id: c.id })
            await load()
        } catch (e) {
            error = String(e)
        }
    }
</script>

<div class="page">
    <div class="page-head">
        <div>
            <h2>Команды</h2>
            <p class="hint">Голосовые команды Джарвиса. Скажи любую из фраз — выполнится действие.</p>
        </div>
        <button class="btn primary" on:click={startNew}>+ Добавить команду</button>
    </div>

    {#if error}<div class="err">{error}</div>{/if}

    {#if editing !== null || isNew}
        <div class="editor">
            <h3>{isNew ? "Новая команда" : `Редактирование: ${editing?.id}`}</h3>

            <label>ID (латиницей, уникальный)
                <input bind:value={f_id} placeholder="open_notepad" disabled={!isNew} />
            </label>

            <label>Тип действия
                <select bind:value={f_type}>
                    <option value="ahk">Запуск программы/скрипта (.exe / AHK)</option>
                    <option value="cli">Команда в терминале (CLI)</option>
                    <option value="lua">Lua-скрипт (продвинутое)</option>
                </select>
            </label>

            <label>Описание (необязательно)
                <input bind:value={f_desc} placeholder="Открывает блокнот" />
            </label>

            {#if f_type === "ahk"}
                <label>Путь к .exe / скрипту
                    <input bind:value={f_exe_path} placeholder="ahk/Run notepad.exe  или  C:\\Windows\\notepad.exe" />
                </label>
                <label>Аргументы (через запятую)
                    <input bind:value={f_exe_args} placeholder="arg1, arg2" />
                </label>
            {:else if f_type === "cli"}
                <label>Команда
                    <input bind:value={f_cli_cmd} placeholder="cmd" />
                </label>
                <label>Аргументы (через запятую)
                    <input bind:value={f_cli_args} placeholder="/c, echo, hi" />
                </label>
            {:else}
                <label>Lua-скрипт
                    <textarea bind:value={f_script} rows="6" placeholder="-- ваш скрипт"></textarea>
                </label>
                <label>Уровень песочницы
                    <select bind:value={f_sandbox}>
                        <option value="minimal">minimal</option>
                        <option value="standard">standard</option>
                        <option value="full">full</option>
                    </select>
                </label>
            {/if}

            <div class="phrases-grid">
                <label>Фразы 🇷🇺 (по одной в строке)
                    <textarea bind:value={f_ru} rows="4" placeholder="открой блокнот&#10;запусти блокнот"></textarea>
                </label>
                <label>Фразы 🇬🇧
                    <textarea bind:value={f_en} rows="4" placeholder="open notepad"></textarea>
                </label>
                <label>Фразы 🇺🇦
                    <textarea bind:value={f_ua} rows="4" placeholder="відкрий блокнот"></textarea>
                </label>
            </div>

            {#if availableModes.length > 0}
                <div class="modes-box">
                    <div class="modes-label">Режимы (пусто = во всех):</div>
                    <div class="modes-chips">
                        {#each availableModes as m}
                            <button type="button" class="mchip" class:on={f_modes.includes(m.id)} on:click={() => toggleMode(m.id)}>
                                {f_modes.includes(m.id) ? '✓ ' : ''}{m.name}
                            </button>
                        {/each}
                    </div>
                </div>
            {/if}

            <div class="actions">
                <button class="btn primary" on:click={save} disabled={saving}>{saving ? "Сохранение…" : "Сохранить"}</button>
                <button class="btn" on:click={cancel}>Отмена</button>
            </div>
        </div>
    {/if}

    <HDivider />

    {#if loading}
        <p class="hint">Загрузка…</p>
    {:else}
        <p class="hint">Всего команд: {commands.length}</p>
        <div class="list">
            {#each commands as c}
                <div class="card">
                    <div class="card-main">
                        <div class="card-title">
                            <span class="cmd-id">{c.id}</span>
                            <span class="badge">{c.type}</span>
                        </div>
                        {#if c.description}<div class="card-desc">{c.description}</div>{/if}
                        <div class="card-phrases">
                            {#each (c.phrases?.ru || c.phrases?.en || []).slice(0, 4) as p}
                                <span class="chip">{p}</span>
                            {/each}
                            {#if (c.phrases?.ru || c.phrases?.en || []).length > 4}<span class="chip more">…</span>{/if}
                        </div>
                    </div>
                    <div class="card-actions">
                        <button class="btn small" on:click={() => startEdit(c)}>✎</button>
                        <button class="btn small danger" on:click={() => remove(c)}>🗑</button>
                    </div>
                </div>
            {/each}
        </div>
    {/if}
</div>

<style>
    .page { padding: 0 0.9rem 2rem; color: #dfeff0; }
    .page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: 0.5rem; }
    h2 { margin: 0; font-size: 1.25rem; color: #eafeff; }
    h3 { margin: 0 0 0.6rem; color: #cdeff0; font-size: 1rem; }
    .hint { color: rgba(223,239,240,0.55); font-size: 0.8rem; margin: 0.2rem 0 0.6rem; }
    .err { background: rgba(255,80,80,0.12); border: 1px solid rgba(255,80,80,0.4); color: #ffb4b4; padding: 0.5rem 0.75rem; border-radius: 8px; margin-bottom: 0.6rem; font-size: 0.82rem; }

    .editor {
        background: rgba(15, 26, 30, 0.7);
        border: 1px solid rgba(82,254,254,0.18);
        border-radius: 12px; padding: 1rem; margin-bottom: 1rem;
    }
    label { display: block; font-size: 0.78rem; color: rgba(223,239,240,0.75); margin-bottom: 0.7rem; }
    input, select, textarea {
        width: 100%; margin-top: 0.28rem; padding: 0.5rem 0.6rem;
        background: rgba(8,16,19,0.85); border: 1px solid rgba(82,254,254,0.2);
        border-radius: 7px; color: #eafeff; font-size: 0.85rem; font-family: inherit;
        box-sizing: border-box;
    }
    input:focus, select:focus, textarea:focus { outline: none; border-color: #2fe6e6; }
    input:disabled { opacity: 0.6; }
    textarea { resize: vertical; }
    .phrases-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.6rem; }
    .modes-box { margin-bottom: 0.7rem; }
    .modes-label { font-size: 0.78rem; color: rgba(223,239,240,0.75); margin-bottom: 0.35rem; }
    .modes-chips { display: flex; flex-wrap: wrap; gap: 0.35rem; }
    .mchip { padding: 0.3rem 0.6rem; font-size: 0.78rem; background: rgba(35,50,55,0.7);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 999px; color: #cdeff0; cursor: pointer; }
    .mchip.on { background: rgba(47,230,230,0.18); border-color: #2fe6e6; color: #eafeff; }

    .actions { display: flex; gap: 0.5rem; margin-top: 0.4rem; }
    .btn {
        padding: 0.5rem 0.9rem; background: rgba(35,50,55,0.7);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 8px;
        color: #cdeff0; font-size: 0.82rem; cursor: pointer; transition: all 0.15s ease;
    }
    .btn:hover { background: rgba(82,254,254,0.12); }
    .btn.primary { background: rgba(47,230,230,0.18); border-color: #2fe6e6; color: #eafeff; }
    .btn.small { padding: 0.35rem 0.55rem; }
    .btn.danger { color: #ffb4b4; border-color: rgba(255,80,80,0.35); }
    .btn:disabled { opacity: 0.5; cursor: default; }

    .list { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.4rem; }
    .card {
        display: flex; justify-content: space-between; align-items: center; gap: 0.75rem;
        background: rgba(15,26,30,0.6); border: 1px solid rgba(82,254,254,0.12);
        border-radius: 10px; padding: 0.7rem 0.9rem;
    }
    .card-title { display: flex; align-items: center; gap: 0.5rem; }
    .cmd-id { font-weight: 600; color: #eafeff; font-size: 0.9rem; }
    .badge { font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.5px;
        background: rgba(47,230,230,0.15); color: #52fefe; padding: 0.12rem 0.4rem; border-radius: 5px; }
    .card-desc { font-size: 0.78rem; color: rgba(223,239,240,0.6); margin-top: 0.2rem; }
    .card-phrases { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.4rem; }
    .chip { font-size: 0.72rem; background: rgba(255,255,255,0.06); color: rgba(223,239,240,0.8);
        padding: 0.12rem 0.45rem; border-radius: 5px; }
    .chip.more { opacity: 0.6; }
    .card-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }
</style>

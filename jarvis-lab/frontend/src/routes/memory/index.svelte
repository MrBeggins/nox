<script lang="ts">
    // Окно «Память»: правила общения и факты о пользователе — мозг Нокса учитывает их в каждом ответе.
    // Хранилище — C:\jarvis-voice\nox_memory.json (через ридер :8126), голосом: «запомни правило: …», «запомни обо мне: …».
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"

    type Item = { id?: string, text: string, on: boolean, created?: number }
    let rules: Item[] = []
    let facts: Item[] = []
    let prompt = ""
    let newRule = ""
    let newFact = ""
    let busy = false
    let msg = ""
    let err = ""
    let showPrompt = false

    async function load() {
        err = ""
        try {
            const d = JSON.parse(await invoke<string>("memory_get"))
            rules = d.rules || []; facts = d.facts || []; prompt = d.prompt || ""
        } catch (e) { err = "Нокс не запущен — память недоступна" }
    }

    async function save(note = "Сохранено") {
        if (busy) return
        busy = true; msg = ""; err = ""
        try {
            const d = JSON.parse(await invoke<string>("memory_save", { jsonData: JSON.stringify({ rules, facts }) }))
            if (d.ok === false) { err = d.text || "Не сохранилось" }
            else { rules = d.rules || []; facts = d.facts || []; prompt = d.prompt || ""; msg = note }
        } catch (e) { err = typeof e === "string" ? e : "Не сохранилось" }
        busy = false
    }

    function add(kind: "rule" | "fact") {
        const t = (kind === "rule" ? newRule : newFact).trim()
        if (!t) return
        if (kind === "rule") { rules = [...rules, { text: t, on: true }]; newRule = "" }
        else { facts = [...facts, { text: t, on: true }]; newFact = "" }
        save("Добавлено")
    }
    function del(kind: "rule" | "fact", i: number) {
        if (kind === "rule") rules = rules.filter((_, j) => j !== i)
        else facts = facts.filter((_, j) => j !== i)
        save("Удалено")
    }

    onMount(load)
</script>

<div class="mem">
    <h2>Память</h2>
    <p class="nx-desc">
        Правила общения и то, что Нокс знает о вас. Он учитывает это в каждом ответе: в разговоре, в поиске
        в интернете и в объяснении новостей. Голосом: «запомни правило: …», «запомни обо мне: …»,
        «что ты обо мне знаешь», «какие у тебя правила», «забудь, что …».
    </p>
    {#if err}<div class="nx-err">{err}</div>{/if}

    <div class="nx-block">
        <div class="nx-group">Правила общения</div>
        {#each rules as r, i}
            <div class="row" class:off={!r.on}>
                <input type="checkbox" bind:checked={r.on} on:change={() => save(r.on ? "Правило включено" : "Правило выключено")} title="учитывать" />
                <textarea class="nx-in txt" rows="1" bind:value={r.text} on:change={() => save()}></textarea>
                <button class="x" on:click={() => del("rule", i)} title="удалить">✕</button>
            </div>
        {/each}
        <div class="row add">
            <input class="nx-in" placeholder="Новое правило, например: «отвечай с цифрами и уровнями»" bind:value={newRule}
                   on:keydown={(e) => e.key === "Enter" && add("rule")} />
            <button class="nx-btn" on:click={() => add("rule")} disabled={busy || !newRule.trim()}>Добавить</button>
        </div>
    </div>

    <div class="nx-block">
        <div class="nx-group">Обо мне</div>
        {#each facts as f, i}
            <div class="row" class:off={!f.on}>
                <input type="checkbox" bind:checked={f.on} on:change={() => save(f.on ? "Включено" : "Выключено")} title="учитывать" />
                <textarea class="nx-in txt" rows="1" bind:value={f.text} on:change={() => save()}></textarea>
                <button class="x" on:click={() => del("fact", i)} title="удалить">✕</button>
            </div>
        {/each}
        <div class="row add">
            <input class="nx-in" placeholder="Например: «держу шорт по Газпрому до пятницы», «стоп не больше 2% депозита»" bind:value={newFact}
                   on:keydown={(e) => e.key === "Enter" && add("fact")} />
            <button class="nx-btn" on:click={() => add("fact")} disabled={busy || !newFact.trim()}>Добавить</button>
        </div>
    </div>

    <div class="nx-row">
        {#if msg}<span class="nx-ok">{msg}</span>{/if}
        <span class="spacer"></span>
        <button class="nx-clr" on:click={() => (showPrompt = !showPrompt)}>{showPrompt ? "Скрыть" : "Показать"}, что получает мозг</button>
    </div>
    {#if showPrompt}<pre class="prompt">{prompt || "(память пуста)"}</pre>{/if}
    <p class="nx-hint">Память уходит в облачный мозг вместе с вопросом (ключ APIMIRA) или в локальный Ollama — пишите сюда то,
        чем не страшно делиться с выбранным мозгом. Номера счетов и пароли сюда не нужны.</p>
</div>

<style lang="scss">
$nx-accent: #35e0d0; $nx-muted: #8592a6;
.mem { max-width: 760px; margin: 0 auto; padding: 1.2rem 1rem 2rem; }
h2 { color: #fff; font-size: 1.35rem; margin: 0 0 0.3rem; }
.nx-block { margin-bottom: 1.1rem; padding-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.06); }
.nx-group { font-size: 0.7rem; letter-spacing: 1.4px; text-transform: uppercase; color: $nx-accent; font-weight: 700; margin: 0.4rem 0 0.8rem; }
.nx-desc { font-size: 0.8rem; color: rgba(255,255,255,0.55); margin: 0 0 1rem; }
.nx-hint { font-size: 0.72rem; color: rgba(255,255,255,0.38); margin: 0.6rem 0 0; }
.nx-ok { font-size: 0.8rem; color: $nx-accent; }
.nx-err { font-size: 0.78rem; color: #ff6d6d; background: rgba(255,109,109,0.08); border: 1px solid rgba(255,109,109,0.25); border-radius: 8px; padding: 8px 10px; margin-bottom: 1rem; }
.nx-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.nx-in { width: 100%; box-sizing: border-box; background: rgba(16,22,31,0.9); border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; color: #fff; padding: 8px 10px; font-size: 0.82rem; font-family: inherit; }
.nx-in:focus { outline: none; border-color: $nx-accent; }
.nx-btn { padding: 8px 16px; border-radius: 8px; border: 1px solid $nx-accent; background: rgba(53,224,208,0.14); color: $nx-accent; font-weight: 700; font-size: 0.8rem; cursor: pointer; white-space: nowrap; }
.nx-btn:disabled { opacity: 0.5; cursor: default; }
.nx-clr { background: none; border: none; color: $nx-muted; font-size: 0.78rem; cursor: pointer; text-decoration: underline; }
.row { display: flex; gap: 8px; align-items: flex-start; margin-bottom: 6px; }
.row.off .txt { opacity: 0.45; }
.row input[type=checkbox] { margin-top: 10px; accent-color: $nx-accent; }
.txt { resize: vertical; min-height: 36px; line-height: 1.35; }
.x { background: none; border: none; color: $nx-muted; cursor: pointer; font-size: 0.85rem; padding: 8px 4px; }
.x:hover { color: #ff6d6d; }
.add { margin-top: 10px; }
.spacer { flex: 1; }
.prompt { white-space: pre-wrap; font-size: 0.72rem; color: rgba(255,255,255,0.6); background: rgba(16,22,31,0.9);
          border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 10px; margin-top: 8px; }
</style>

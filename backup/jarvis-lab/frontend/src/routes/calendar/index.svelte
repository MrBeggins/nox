<script lang="ts">
    import { onMount, onDestroy } from "svelte"
    import { invoke } from "@tauri-apps/api/core"

    type Item = { id: string; kind: string; text: string; when: string; done: boolean; created: number; source: string }

    let items: Item[] = []
    let loadError = ""

    // форма ручного ввода
    let newKind = "rem"
    let newText = ""
    let newWhen = ""

    const typeLabel: Record<string, string> = { cmd: "КОМАНДА", evt: "СОБЫТИЕ", rem: "НАПОМНИТЬ" }

    async function load() {
        try {
            const raw = await invoke<string>("agenda_list")
            items = JSON.parse(raw || "[]")
            loadError = ""
        } catch (e) {
            loadError = String(e)
        }
    }

    async function add() {
        const text = newText.trim()
        if (!text) return
        try {
            await invoke("agenda_add", { kind: newKind, text, when: newWhen.trim() })
            newText = ""; newWhen = ""
            await load()
        } catch (e) { console.error(e) }
    }

    async function toggle(it: Item) {
        try { await invoke("agenda_set_done", { id: it.id, done: !it.done }); await load() }
        catch (e) { console.error(e) }
    }

    async function remove(it: Item) {
        try { await invoke("agenda_delete", { id: it.id }); await load() }
        catch (e) { console.error(e) }
    }

    async function clearDone() {
        try { await invoke("agenda_clear_done"); await load() }
        catch (e) { console.error(e) }
    }

    function onKey(e: KeyboardEvent) { if (e.key === "Enter") add() }

    // делим на активные/выполненные
    $: active = items.filter(i => !i.done)
    $: done = items.filter(i => i.done)

    let timer: ReturnType<typeof setInterval>
    onMount(() => { load(); timer = setInterval(load, 4000) })
    onDestroy(() => clearInterval(timer))
</script>

<div class="cal">
    <div class="cal-head">
        <h3>Календарь</h3>
        <span class="voicehint"><span class="mic">🎙</span>Скажите «запиши…», «напомни…», «добавь событие…» — Nox запишет сюда</span>
    </div>

    <div class="addbar">
        <div class="seg">
            <button class:sel={newKind==="cmd"} on:click={() => newKind="cmd"}>Команда</button>
            <button class:sel={newKind==="evt"} on:click={() => newKind="evt"}>Событие</button>
            <button class:sel={newKind==="rem"} on:click={() => newKind="rem"}>Напоминание</button>
        </div>
        <input class="txt" placeholder="Что записать…" bind:value={newText} on:keydown={onKey} />
        <input class="when" placeholder="когда (необяз.)" bind:value={newWhen} on:keydown={onKey} />
        <button class="addbtn" on:click={add} disabled={!newText.trim()}>＋</button>
    </div>

    {#if loadError}
        <div class="err">Не удалось прочитать записи: {loadError}</div>
    {/if}

    {#if active.length}
        <div class="daygrp">
            <div class="dayhdr">Активные · {active.length}</div>
            {#each active as it (it.id)}
                <div class="entry">
                    <button class="chk" on:click={() => toggle(it)} title="Отметить выполненным"></button>
                    <span class="type {it.kind}">{typeLabel[it.kind] || "ЗАПИСЬ"}</span>
                    <div class="txt2">
                        <div class="t">{it.text}</div>
                        <div class="s">
                            {#if it.source === "voice"}🎙 голос{:else}✍ вручную{/if}{#if it.when} · {it.when}{/if}
                        </div>
                    </div>
                    <button class="del" on:click={() => remove(it)} title="Удалить">✕</button>
                </div>
            {/each}
        </div>
    {:else}
        <div class="empty">Записей пока нет. Скажите Ноксу «напомни…» или добавьте вручную выше.</div>
    {/if}

    {#if done.length}
        <div class="daygrp">
            <div class="dayhdr">
                Выполнено · {done.length}
                <button class="clr" on:click={clearDone}>очистить</button>
            </div>
            {#each done as it (it.id)}
                <div class="entry done">
                    <button class="chk on" on:click={() => toggle(it)} title="Вернуть в активные">✓</button>
                    <span class="type {it.kind}">{typeLabel[it.kind] || "ЗАПИСЬ"}</span>
                    <div class="txt2"><div class="t">{it.text}</div></div>
                    <button class="del" on:click={() => remove(it)} title="Удалить">✕</button>
                </div>
            {/each}
        </div>
    {/if}
</div>

<style lang="scss">
    $accent: #35e0d0; $amber: #f6b352; $muted: #8592a6; $ink: #e9eef6; $line: #1e2736; $elev: #1b2434;
    .cal { padding: 18px; display: flex; flex-direction: column; gap: 14px; }
    .cal-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    h3 { margin: 0; font-family: "Sora", sans-serif; font-size: 19px; color: $ink; }
    .voicehint { display: inline-flex; align-items: center; gap: 8px; color: $muted; font-size: 12.5px;
        border: 1px dashed #253045; border-radius: 999px; padding: 6px 12px; }
    .voicehint .mic { width: 20px; height: 20px; border-radius: 50%; background: $accent; display: grid; place-items: center; font-size: 11px; }

    .addbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
        background: $elev; border: 1px solid $line; border-radius: 12px; padding: 10px; }
    .seg { display: flex; border: 1px solid #253045; border-radius: 9px; overflow: hidden;
        button { font-size: 12px; padding: 7px 11px; color: $muted; font-weight: 600; cursor: pointer; background: none; border: none; }
        button.sel { background: rgba(53,224,208,.16); color: $accent; } }
    .txt { flex: 1; min-width: 140px; background: #10161f; border: 1px solid #253045; border-radius: 9px;
        color: $ink; padding: 9px 11px; font-size: 13px; }
    .when { width: 130px; background: #10161f; border: 1px solid #253045; border-radius: 9px;
        color: $ink; padding: 9px 11px; font-size: 12px; }
    .txt:focus, .when:focus { outline: none; border-color: $accent; }
    .addbtn { width: 38px; height: 38px; border-radius: 9px; border: 1px solid $accent; background: rgba(53,224,208,.14);
        color: $accent; font-size: 20px; font-weight: 700; cursor: pointer; line-height: 1; }
    .addbtn:disabled { opacity: .4; cursor: default; }

    .err { color: #ff6d6d; font-size: 12.5px; }
    .empty { color: $muted; font-size: 13px; padding: 14px 0; opacity: .85; }

    .daygrp { display: flex; flex-direction: column; gap: 8px; }
    .dayhdr { font-size: 11px; letter-spacing: 1.4px; text-transform: uppercase; color: $muted; font-weight: 700; margin-top: 6px;
        font-family: "JetBrains Mono", monospace; display: flex; align-items: center; gap: 10px; }
    .clr { margin-left: auto; background: none; border: none; color: $muted; font-size: 11px; cursor: pointer; text-transform: none; letter-spacing: 0; text-decoration: underline; }

    .entry { display: grid; grid-template-columns: auto auto 1fr auto; gap: 12px; align-items: center;
        background: $elev; border: 1px solid $line; border-radius: 12px; padding: 11px 13px; }
    .chk { width: 20px; height: 20px; border-radius: 6px; border: 1.6px solid #37465c; background: none; cursor: pointer;
        color: $accent; font-size: 12px; display: grid; place-items: center; }
    .chk.on { border-color: $accent; background: rgba(53,224,208,.16); }
    .type { font-size: 10.5px; font-weight: 700; font-family: "JetBrains Mono", monospace; letter-spacing: .4px; border-radius: 6px; padding: 3px 8px; white-space: nowrap; }
    .type.cmd { background: rgba(246,179,82,.18); color: $amber; }
    .type.evt { background: rgba(53,224,208,.16); color: $accent; }
    .type.rem { background: rgba(133,146,166,.18); color: $muted; }
    .txt2 { min-width: 0; }
    .txt2 .t { font-weight: 600; color: $ink; overflow-wrap: anywhere; }
    .txt2 .s { color: $muted; font-size: 11px; margin-top: 2px; }
    .entry.done .txt2 .t { text-decoration: line-through; color: $muted; }
    .del { background: none; border: none; color: #45526640; cursor: pointer; font-size: 14px; }
    .del:hover { color: #ff6d6d; }
</style>

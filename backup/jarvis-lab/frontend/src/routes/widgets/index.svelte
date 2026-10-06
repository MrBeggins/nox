<script lang="ts">
    import { onMount, onDestroy } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import { goto } from "@roxi/routify"
    import { isJarvisRunning, lastAssistantReply, recentReplies } from "@/stores"

    // каталог виджетов
    const CATALOG: { id: string; title: string; w2?: boolean }[] = [
        { id: "assistant", title: "Ассистент" },
        { id: "news", title: "Лента новостей", w2: true },
        { id: "next", title: "Ближайшее" },
        { id: "watches", title: "Слежки" },
        { id: "voice", title: "Голос" },
        { id: "portfolio", title: "Портфель" },
    ]
    const DEFAULT = ["assistant", "news", "next", "watches", "voice"]

    let enabled: string[] = DEFAULT
    let showPalette = false

    function loadLayout() {
        try {
            const s = localStorage.getItem("nox_widgets")
            if (s) enabled = JSON.parse(s)
        } catch (e) { /* ignore */ }
    }
    function saveLayout() {
        try { localStorage.setItem("nox_widgets", JSON.stringify(enabled)) } catch (e) { /* ignore */ }
    }
    function addWidget(id: string) { if (!enabled.includes(id)) { enabled = [...enabled, id]; saveLayout() }; showPalette = false }
    function removeWidget(id: string) { enabled = enabled.filter(x => x !== id); saveLayout() }

    $: meta = (id: string) => CATALOG.find(c => c.id === id)
    $: available = CATALOG.filter(c => !enabled.includes(c.id))

    // живые данные
    let agenda: any[] = []
    let filter: any = { tickers: [], phrases: [], sources: [] }
    let monOn = false
    let readerOk = true
    let magCount = 0
    let magMon = false
    let voiceSpeed = 0      // 0 = динамическая; иначе фикс. множитель 0.8..1.5

    async function refresh() {
        try { agenda = JSON.parse(await invoke<string>("agenda_list") || "[]") } catch { agenda = [] }
        try {
            const f = JSON.parse(await invoke<string>("reader_filter_get"))
            if (f.filter) filter = f.filter
            const m = JSON.parse(await invoke<string>("reader_mon", { on: -1 }))
            monOn = !!m.on
            readerOk = true
        } catch { readerOk = false }
        try {
            const mf = JSON.parse(await invoke<string>("magellan_filter_get"))
            magCount = mf.filter?.tickers?.length || 0
            magMon = !!mf.mon
        } catch { /* magellan offline */ }
    }

    async function loadVoiceSpeed() {
        try { const s = JSON.parse(await invoke<string>("tts_speed_get")); voiceSpeed = s.value || 0 } catch { /* tts offline */ }
    }
    async function setVoiceSpeed(v: number) {
        voiceSpeed = v
        try { await invoke("tts_speed_set", { v }) } catch (e) { console.error(e) }
    }

    $: nextItem = agenda.find(a => !a.done && (a.kind === "evt" || a.kind === "rem")) || agenda.find(a => !a.done)
    $: newsTopics = (filter.tickers?.length || 0) + (filter.phrases?.length || 0)

    let timer: ReturnType<typeof setInterval>
    onMount(() => { loadLayout(); refresh(); loadVoiceSpeed(); timer = setInterval(refresh, 5000) })
    onDestroy(() => clearInterval(timer))
</script>

<div class="grid">
    {#each enabled as id (id)}
        {#if meta(id)}
            <div class="card" class:w2={meta(id).w2}>
                <div class="ch">{meta(id).title}<button class="rm" on:click={() => removeWidget(id)} title="Убрать">✕</button></div>

                {#if id === "assistant"}
                    <div class="row">
                        <span class="miniorb" class:off={!$isJarvisRunning}></span>
                        <div>
                            <div class="b">{$isJarvisRunning ? "На связи" : "Не запущен"}</div>
                            <div class="mut">{$lastAssistantReply || "ожидание"}</div>
                        </div>
                    </div>

                {:else if id === "news"}
                    {#if $recentReplies.length}
                        <div class="news">
                            {#each $recentReplies.slice(0, 4) as r}
                                <div class="nl"><time>{r.time}</time>{r.text}</div>
                            {/each}
                        </div>
                    {:else}
                        <div class="mut">Пока тихо. {monOn ? "Монитор включён." : "Монитор выключен."}</div>
                    {/if}

                {:else if id === "next"}
                    {#if nextItem}
                        <div class="big">{nextItem.when || "—"}</div>
                        <div class="mut">{nextItem.text}</div>
                    {:else}
                        <div class="mut">Нет ближайших записей</div>
                    {/if}

                {:else if id === "watches"}
                    <div class="wl">
                        <span class="dot" class:on={monOn}></span>
                        Новости: <b>{readerOk ? (monOn ? "читает" : "выкл") : "офлайн"}</b>
                        {#if newsTopics}· {newsTopics} фильтр.{/if}
                    </div>
                    <div class="wl">
                        <span class="dot" class:on={magMon}></span>
                        Магеллан: <b>{magMon ? "следит" : "выкл"}</b> · {magCount} бумаг
                    </div>
                    <button class="gear" on:click={() => $goto('/settings')}>настроить →</button>

                {:else if id === "voice"}
                    <div class="mut">F5 · клон-голос</div>
                    <input class="rng" type="range" min="0.8" max="1.5" step="0.05"
                        value={voiceSpeed || 1.15}
                        style="--p:{Math.round((((voiceSpeed || 1.15) - 0.8) / 0.7) * 100)}%"
                        on:input={(e) => setVoiceSpeed(+e.currentTarget.value)} />
                    <div class="mut">
                        {#if voiceSpeed}скорость {voiceSpeed.toFixed(2)}× <button class="gear" on:click={() => setVoiceSpeed(0)}>сброс (динамич.)</button>
                        {:else}динамическая скорость (потяни, чтобы зафиксировать){/if}
                    </div>

                {:else if id === "portfolio"}
                    <div class="big">—</div>
                    <div class="mut">спросите голосом: «портфель»</div>
                {/if}
            </div>
        {/if}
    {/each}

    {#if available.length}
        <div class="addcard" on:click={() => showPalette = !showPalette} on:keydown role="button" tabindex="0">
            <span class="plus">＋</span>Добавить виджет
        </div>
    {/if}
</div>

{#if showPalette && available.length}
    <div class="palette">
        {#each available as c}
            <button class="pill" on:click={() => addWidget(c.id)}>＋ {c.title}</button>
        {/each}
    </div>
{/if}

<style lang="scss">
    $accent: #35e0d0; $amber: #f6b352; $muted: #8592a6; $ink: #e9eef6; $line: #1e2736; $elev: #1b2434;
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; padding: 16px; }
    .card { background: $elev; border: 1px solid $line; border-radius: 13px; padding: 14px; display: flex; flex-direction: column; gap: 10px; min-height: 132px; color: $ink; }
    .card.w2 { grid-column: span 2; }
    .ch { display: flex; align-items: center; gap: 8px; font-size: 11px; letter-spacing: 1.2px; text-transform: uppercase; color: $muted; font-weight: 700; font-family: "JetBrains Mono", monospace; }
    .ch .rm { margin-left: auto; background: none; border: none; color: #2b3646; cursor: pointer; font-size: 12px; }
    .ch .rm:hover { color: #ff6d6d; }
    .row { display: flex; align-items: center; gap: 12px; }
    .b { font-weight: 700; }
    .big { font-family: "Sora", sans-serif; font-size: 26px; font-weight: 600; }
    .mut { color: $muted; font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
    .news { font-size: 12.5px; line-height: 1.5; display: flex; flex-direction: column; gap: 6px; }
    .nl { display: flex; gap: 8px; align-items: baseline; }
    .nl time { font-family: "JetBrains Mono", monospace; color: $muted; font-size: 11px; flex: none; }
    .miniorb { width: 40px; height: 40px; border-radius: 50%; background: radial-gradient(circle at 35% 32%, rgba(255,255,255,.5), transparent 45%), #{$accent}; box-shadow: 0 0 20px -4px $accent; }
    .miniorb.off { filter: grayscale(1); opacity: .4; box-shadow: none; }
    .wl { display: flex; align-items: center; gap: 8px; font-size: 13px; color: $ink; }
    .wl b { color: $accent; font-weight: 700; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #45526a; flex: none; }
    .dot.on { background: $accent; box-shadow: 0 0 8px -1px $accent; }
    .gear { background: none; border: none; color: $muted; font-size: 11.5px; cursor: pointer; text-decoration: underline; padding: 0; margin-top: auto; align-self: flex-start; }
    .gear:hover { color: $accent; }
    .rng { -webkit-appearance: none; appearance: none; width: 100%; height: 6px; border-radius: 999px;
        background: linear-gradient(to right, #{$accent} 0 var(--p, 50%), #253045 var(--p, 50%) 100%);
        outline: none; margin-top: auto; cursor: pointer; }
    .rng::-webkit-slider-thumb { -webkit-appearance: none; width: 18px; height: 18px; border-radius: 50%; background: $accent; border: 2px solid #fff; cursor: pointer; box-shadow: 0 0 8px -1px $accent; }
    .addcard { border: 1.4px dashed #253045; border-radius: 13px; display: grid; place-items: center; color: $muted; font-weight: 600; min-height: 132px; cursor: pointer; gap: 6px; font-size: 13px; }
    .plus { font-size: 20px; }
    .palette { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 16px 16px; }
    .pill { background: rgba(53,224,208,.14); border: 1px solid $accent; color: $accent; border-radius: 999px; padding: 7px 13px; font-size: 12.5px; font-weight: 600; cursor: pointer; }
    @media (max-width: 860px) { .grid { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 520px) { .grid { grid-template-columns: 1fr; } .card.w2 { grid-column: span 1; } }
</style>

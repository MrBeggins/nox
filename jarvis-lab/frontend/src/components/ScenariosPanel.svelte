<script lang="ts">
    // Сценарии «открыть стакан по новости»: описал одной строкой → «Разобрать» → Нокс сам
    // заполнил ветки, ты задаёшь точку стакана для каждой (наведи мышь + F2) → сохранить.
    // По нужной новости Нокс откроет и подсветит заготовленный стакан. Купить/Продать жмёшь ТЫ.
    // (Перенесено из Настройки → Сценарии; мозг для разбора — общий, со страницы «Мозг».)
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import InfoDot from "@/components/elements/InfoDot.svelte"

    function parseReader(raw: string): any { try { return JSON.parse(raw) } catch { return {} } }

    let scnList: any[] = []
    let scnText = ""           // описание нового сценария одной строкой
    let parsed: any = null     // разобранный сценарий (задаём точки стаканов)
    let scnMsg = ""
    let capturing = -1         // индекс ветки в режиме захвата F2

    async function loadScn() {
        try { scnList = parseReader(await invoke<string>("scn_list")).scenarios || [] } catch (e) { /* ридер офлайн */ }
    }
    onMount(loadScn)

    async function doParse() {
        scnMsg = "Разбираю…"
        try {
            const r = parseReader(await invoke<string>("scn_parse", { text: scnText }))
            if (r.ok) { parsed = r.scenario; scnMsg = "Готово. Задайте точку стакана для каждой ветки (F2)." }
            else { parsed = null; scnMsg = r.text || "Не понял описание." }
        } catch (e) { scnMsg = "Сервис сценариев офлайн." }
    }
    async function captureCoord(i: number) {
        capturing = i
        scnMsg = "Наведи мышь на нужный стакан и нажми F2…"
        let base = 0
        try { base = (parseReader(await invoke<string>("scn_capture")) || {}).ts || 0 } catch (e) {}
        const t0 = Date.now()
        const poll = async () => {
            if (capturing !== i) return
            try {
                const c = parseReader(await invoke<string>("scn_capture")) || {}
                if (c.ts && c.ts > base) {
                    parsed.branches[i].x = c.x; parsed.branches[i].y = c.y
                    parsed = parsed; capturing = -1; scnMsg = `Ветка «${parsed.branches[i].label}»: точка (${c.x}, ${c.y}).`
                    return
                }
            } catch (e) {}
            if (Date.now() - t0 > 20000) { capturing = -1; scnMsg = "Захват отменён (нет F2)."; return }
            setTimeout(poll, 300)
        }
        poll()
    }
    async function showPoint(x: number, y: number) { try { await invoke("scn_show", { x: String(x), y: String(y) }) } catch (e) {} }
    async function showAll(id: string) { try { await invoke("scn_show_all", { id }) } catch (e) {} }
    async function testScn(s: any) {
        try {
            const r = parseReader(await invoke<string>("scn_test", { id: s.id, value: String(s._tv ?? "") }))
            s._tmsg = (r && r.text) ? r.text : "готово"; scnList = scnList
        } catch (e) { s._tmsg = "ошибка теста"; scnList = scnList }
    }
    async function saveScn() {
        if (!parsed) return
        // точки нужны только веткам с действием (ветка «без действий» — без клика)
        if (parsed.branches.some((b: any) => !b.noop && b.x == null)) { scnMsg = "Задайте точки веткам с действием (кроме «без действий»)."; return }
        try {
            await invoke("scn_save", { jsonData: JSON.stringify(parsed) })
            parsed = null; scnText = ""; scnMsg = "Сценарий сохранён. Сработает один раз и удалится."
            await loadScn()
        } catch (e) { scnMsg = "Не удалось сохранить." }
    }
    async function delScn(id: string) { try { await invoke("scn_del", { id }); await loadScn() } catch (e) {} }
    async function toggleScn(id: string) { try { await invoke("scn_toggle", { id }); await loadScn() } catch (e) {} }
    async function setSchedule(s: any) {
        try { await invoke("scn_schedule", { id: s.id, dt: s._dt || "", prewarm: String(s._pre || 3) }); await loadScn() } catch (e) {}
    }
</script>

<div class="nx-block">
    <label class="nx-lab">Описание одной строкой<InfoDot text="Опиши правило словами — Нокс сам разберёт его на ветки. По нужной новости откроет и подсветит заготовленный стакан (один клик по точке). Купить/Продать жмёшь ТЫ. Пример: «Новатэк дивиденды: нет → стакан 1; 1-30 → стакан 2; больше 30 → стакан 3». Точки задаются наведением мыши + F2." /></label>
    <p class="nx-desc">Пример: «Новатэк дивиденды: нет → стакан 1; от 1 до 30 рублей → стакан 2; больше 30 → стакан 3». Нажми «Разобрать» — ветки заполнятся сами.</p>
    <textarea class="nx-in area" rows="3" placeholder="Новатэк дивиденды: нет → стакан 1; 1-30 → стакан 2; больше 30 → стакан 3" bind:value={scnText}></textarea>
    <div class="nx-row">
        <button class="nx-btn" on:click={doParse} disabled={!scnText.trim()}>Разобрать</button>
        {#if scnMsg}<span class="nx-hint m0">{scnMsg}</span>{/if}
    </div>
</div>

{#if parsed}
    <div class="nx-block">
        <label class="nx-lab">{parsed.instrument} · {parsed.event}</label>
        {#each parsed.branches as b, i}
            <div class="nx-row">
                <span class="chip ph">{b.label}</span>
                <span class="nx-hint m0">
                    {b.type === "none" ? "нет значения" : b.type === "range" ? `${b.min}–${b.max}` : b.type === "gt" ? `> ${b.min}` : b.type === "lt" ? `< ${b.max}` : "любое"}
                </span>
                {#if b.noop}
                    <span class="nx-hint m0 auto muted">без клика</span>
                {:else}
                    <span class="nx-hint m0 auto">{b.x != null ? `✓ (${b.x}, ${b.y})` : "нет точки"}</span>
                    {#if b.x != null}<button class="nx-clr" on:click={() => showPoint(b.x, b.y)}>показать</button>{/if}
                    <button class="nx-btn" on:click={() => captureCoord(i)} disabled={capturing >= 0}>
                        {capturing === i ? "жду F2…" : "Задать (F2)"}
                    </button>
                {/if}
            </div>
        {/each}
        <div class="nx-row">
            <button class="nx-btn" on:click={saveScn}>Сохранить сценарий</button>
            <button class="nx-clr" on:click={() => { parsed = null; scnMsg = "" }}>отмена</button>
        </div>
    </div>
{/if}

{#if scnList.length}
    <div class="nx-block">
        <label class="nx-lab">Сохранённые сценарии<InfoDot text="«показать точки» — красные плюсы на экране; «⏰ ожидается» — время события (за N минут прогрев мозга); «🧪 тест» — прогнать с числом без ожидания новости. Каждый сценарий срабатывает один раз и удаляется." /></label>
        {#each scnList as s}
            <div class="nx-row">
                <span class="dot" class:on={s.enabled}></span>
                <b class="nm">{s.name || s.instrument}</b>
                <span class="nx-hint m0">{s.instrument} · {s.event} · {s.branches?.length || 0} веток</span>
                <button class="nx-clr ml-auto" on:click={() => showAll(s.id)}>показать точки</button>
                <button class="nx-clr" on:click={() => toggleScn(s.id)}>{s.enabled ? "выкл" : "вкл"}</button>
                <button class="nx-clr" on:click={() => delScn(s.id)}>удалить</button>
            </div>
            <div class="nx-row sub">
                {#each (s.branches || []) as b}
                    <span class="nx-hint m0 pill">{b.label}: {b.noop ? "без клика" : (b.x != null ? `(${b.x}, ${b.y})` : "нет точки")}</span>
                    {#if !b.noop && b.x != null}<button class="nx-clr" on:click={() => showPoint(b.x, b.y)}>показать</button>{/if}
                {/each}
            </div>
            <div class="nx-row sub">
                <span class="nx-hint m0">⏰ ожидается:</span>
                <input class="nx-in w170" placeholder="2026-10-29 16:30" value={s.schedule_dt || ""} on:input={(e) => s._dt = e.currentTarget.value} />
                <input class="nx-in w70" type="number" min="1" max="30" value={s.prewarm_min || 3} on:input={(e) => s._pre = e.currentTarget.value} title="за сколько минут прогреть мозг" />
                <span class="nx-hint m0">мин до</span>
                <button class="nx-btn" on:click={() => setSchedule(s)}>назначить</button>
                {#if s.schedule_dt}<span class="nx-hint m0 ok">✓ {s.schedule_dt}</span>{/if}
            </div>
            <div class="nx-row sub">
                <span class="nx-hint m0">🧪 тест: значение</span>
                <input class="nx-in w110" type="number" placeholder="напр. 180" value={s._tv ?? ""} on:input={(e) => s._tv = e.currentTarget.value} />
                <button class="nx-btn" on:click={() => testScn(s)}>прогнать</button>
                {#if s._tmsg}<span class="nx-hint m0 info">{s._tmsg}</span>{/if}
            </div>
        {/each}
    </div>
{/if}

<style>
    .nx-block { margin-bottom: 1.1rem; padding-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.06); }
    .nx-lab { font-weight: 600; font-size: 0.9rem; color: #fff; display: block; margin-bottom: 0.25rem; }
    .nx-desc { font-size: 0.75rem; color: rgba(255,255,255,0.5); margin: 0 0 0.6rem; }
    .nx-hint { font-size: 0.72rem; color: rgba(255,255,255,0.45); margin: 0.4rem 0 0; }
    .nx-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 6px; }
    .nx-in { flex: 1; min-width: 130px; background: rgba(16,22,31,0.9); border: 1px solid rgba(255,255,255,0.12);
        border-radius: 8px; color: #e9eef6; padding: 8px 10px; font-size: 0.85rem; box-sizing: border-box; }
    .nx-in:focus { outline: none; border-color: #35e0d0; }
    .area { width: 100%; min-height: 70px; resize: vertical; line-height: 1.4; }
    .nx-btn { padding: 8px 14px; border-radius: 8px; border: 1px solid #35e0d0; background: rgba(53,224,208,0.14);
        color: #35e0d0; font-weight: 700; font-size: 0.8rem; cursor: pointer; }
    .nx-btn:disabled { opacity: 0.5; cursor: default; }
    .nx-clr { background: none; border: none; color: #8592a6; font-size: 0.72rem; cursor: pointer; text-decoration: underline; }
    .chip { display: inline-flex; align-items: center; font-size: 0.76rem; font-weight: 600; padding: 3px 9px;
        border-radius: 999px; border: 1px solid rgba(53,224,208,0.3); color: #35e0d0; background: rgba(255,255,255,0.03); }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: #475569; display: inline-block; }
    .dot.on { background: #35e0d0; box-shadow: 0 0 8px #35e0d0; }
    .nm { color: #e9eef6; }
    .m0 { margin: 0; }
    .auto { margin-left: auto; }
    .ml-auto { margin-left: auto; }
    .muted { color: #9aa4b2; }
    .ok { color: #7fd1a0; }
    .info { color: #8ab4ff; }
    .pill { background: #1b2330; padding: 2px 7px; border-radius: 6px; }
    .sub { margin-left: 18px; }
    .w170 { max-width: 170px; } .w70 { max-width: 70px; } .w110 { max-width: 110px; }
</style>

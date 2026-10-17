<script lang="ts">
    // Стрелки «Купить» ▲ / «Продать» ▼ на оверлее: ручная корзина заявок на неожиданную новость.
    // Каждая стрелка — свой список заявок (Т-Терминал: виджет на вкладке по объёму; CScalp: стакан по тикеру,
    // по рынку). Жмёте стрелку на оверлее — исполняются ВСЕ её заявки сразу (тумблер исполнения и лимиты — общие).
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"

    type Order = { ticker: string, side: string, lots?: number | string | null, venue?: string, n?: number }
    type Arrow = { enabled: boolean, orders: Order[] }
    type Cfg = { tab: string, cscalp_tab: string, style: string, layout: string, size: number, buy: Arrow, sell: Arrow }
    type CsTab = { tab: string, stacks: string[], open: boolean, why: string, accounts: Record<string, boolean> } | null
    const STYLES = [
        { v: "triangles", l: "А · Треугольники под шаром" },
        { v: "pills", l: "Б · Капсулы с подписью" },
        { v: "ring", l: "В · Сегменты кольца вокруг шара" },
        { v: "chevrons", l: "Г · Двойные шевроны" },
    ]

    const SIDES = [
        { k: "buy" as const, l: "Купить", arrow: "▲", color: "#22c55e" },
        { k: "sell" as const, l: "Продать", arrow: "▼", color: "#ef4444" },
    ]
    let cs: CsTab = null
    let cfg: Cfg = { tab: "нокс", cscalp_tab: "", style: "triangles", layout: "row", size: 100, buy: { enabled: false, orders: [] }, sell: { enabled: false, orders: [] } }
    let msg = ""
    let busy = ""

    function parse(s: string) { try { return JSON.parse(s) } catch { return {} } }
    const pick = (r: any): Cfg => ({ tab: r.tab || "нокс", cscalp_tab: r.cscalp_tab || "", style: r.style || "triangles", layout: r.layout || "row",
                                     size: Number(r.size) || 100, buy: r.buy || { enabled: false, orders: [] },
                                     sell: r.sell || { enabled: false, orders: [] } })

    async function load() {
        try { const r = parse(await invoke<string>("arrows_get")); if (r.ok) { cfg = pick(r); cs = r.cscalp || null } } catch (e) { msg = "Нокс не отвечает: " + e }
    }
    function addOrder(side: "buy" | "sell") {
        cfg[side].orders = [...cfg[side].orders, { ticker: "", side, lots: 1, venue: "tbank" }]
        cfg = cfg
    }
    function delOrder(side: "buy" | "sell", i: number) {
        cfg[side].orders = cfg[side].orders.filter((_, j) => j !== i)
        cfg = cfg
    }
    async function save(quiet = false): Promise<boolean> {
        try {
            // вкладка CScalp задана — её стаканы берутся сами, ручной список CScalp не нужен
            const strip = (a: Arrow) => ({ ...a, orders: a.orders.filter(o => !(cfg.cscalp_tab && o.venue === "cscalp")) })
            const out = { ...cfg, buy: strip(cfg.buy), sell: strip(cfg.sell) }
            const r = parse(await invoke<string>("arrows_save", { jsonData: JSON.stringify(out) }))
            if (r.ok) { cfg = pick(r); load() }
            if (!quiet) msg = r.text || ""
            return !!r.ok
        } catch (e) { msg = "Не сохранил: " + e; return false }
    }
    async function act(cmd: string, args: Record<string, string> = {}) {
        busy = cmd + (args.side || "")
        msg = cmd === "arrow_layout" ? "Раскладываю виджеты в терминале…" : "Проверяю…"
        if (await save(true)) {
            try { msg = parse(await invoke<string>(cmd, args)).text || "" } catch (e) { msg = "Ошибка: " + e }
        }
        busy = ""
    }
    onMount(load)
</script>

<p class="hint">Для неожиданных новостей: одно нажатие — сразу несколько активов. Стрелки висят рядом с шаром
    оверлея и двигаются вместе с ним; каждая включается отдельно. Заявки — как в ветке наблюдателя:
    Т-Терминал (виджет «Заявка» на вкладке, по объёму — «Разложить в терминале» создаст их) или CScalp (стакан по тикеру,
    по рынку). Тумблер исполнения и лимиты — те же, что у наблюдателей. Оверлей включается на «Пульсе».</p>

<div class="tabrow">
    <label>Вкладка терминала для заявок Т-Терминала
        <input bind:value={cfg.tab} placeholder="нокс" on:change={() => save(true)} />
    </label>
    <label>Вкладка CScalp — жать ВСЕ её стаканы (пусто — списком ниже)
        <input bind:value={cfg.cscalp_tab} placeholder="нокс" on:change={() => save(true)} />
    </label>
    <label>Вид стрелок на оверлее
        <select bind:value={cfg.style} on:change={() => save(true)}>
            {#each STYLES as st}<option value={st.v}>{st.l}</option>{/each}
        </select>
    </label>
    <label>Расположение
        <select bind:value={cfg.layout} on:change={() => save(true)}>
            <option value="row">Обе под шаром, в ряд</option>
            <option value="stack">«Купить» над шаром, «Продать» под шаром</option>
        </select>
    </label>
    <label>Размер виджета: {cfg.size}%
        <input type="range" min="60" max="160" step="5" bind:value={cfg.size} on:change={() => save(true)} />
    </label>
</div>

{#if cfg.cscalp_tab}
    <div class="cstab" class:bad={cs && !cs.open}>
        {#if cs}
            CScalp «{cs.tab}»: {cs.stacks.length ? cs.stacks.join(", ") : "стаканов нет"} ·
            {cs.open ? "вкладка открыта — стрелки нажмут все её стаканы" : (cs.why || "вкладка не открыта") + " — стрелки ничего не нажмут"}
            {#if Object.keys(cs.accounts || {}).length}
                <div class="acc">Счета CScalp: {#each Object.entries(cs.accounts) as [a, on]}<span class:on>{a} {on ? "подключён" : "отключён"}</span>{/each}
                    <span class="mut">— счёт и объём каждого стакана выбираются в самом CScalp</span></div>
            {/if}
        {:else}Состояние вкладки CScalp не получено{/if}
    </div>
{/if}

<div class="arrows">
    {#each SIDES as s}
        <div class="arrowcard" style="border-color:{s.color}">
            <div class="ahead">
                <span class="aname" style="background:{s.color}">{s.arrow} {s.l}</span>
                <label class="chk"><input type="checkbox" bind:checked={cfg[s.k].enabled} on:change={() => save()} />
                    показывать на оверлее</label>
            </div>
            {#if cfg.cscalp_tab}
                <div class="noop">CScalp: {s.k === "buy" ? "купить" : "продать"} по рынку во всех стаканах вкладки «{cfg.cscalp_tab}»
                    ({cs ? cs.stacks.length : "?"}) — сколько их там сейчас, объём — как стоит в стакане.</div>
            {:else if cfg[s.k].orders.length === 0}<div class="noop">Заявок нет — стрелка ничего не нажмёт.</div>{/if}
            {#each cfg[s.k].orders as o, j}
                {#if !(cfg.cscalp_tab && o.venue === "cscalp")}
                <div class="orow" class:cs={o.venue === "cscalp"}>
                    <select class="venue" bind:value={o.venue}>
                        <option value="tbank">Т-Терминал</option><option value="cscalp">CScalp</option>
                    </select>
                    <input class="tk" class:cs={o.venue === "cscalp"} bind:value={o.ticker}
                        placeholder={o.venue === "cscalp" ? "SILVM-12.26" : "SBER"} />
                    <select class="side" class:buy={o.side === "buy"} class:sell={o.side === "sell"} bind:value={o.side}>
                        <option value="buy">Купить</option><option value="sell">Продать</option>
                    </select>
                    {#if o.venue === "cscalp"}
                        <span class="lbl">по рынку, объём — как стоит в стакане</span>
                    {:else}
                        <input class="num" bind:value={o.lots} placeholder="лоты" title="Виджет с ровно таким количеством" />
                        <span class="lbl">лот(ов), по рынку</span>
                    {/if}
                    <button class="btn small danger" on:click={() => delOrder(s.k, j)}>✕</button>
                </div>
                {/if}
            {/each}
            <div class="oadd">
                <button class="btn small" on:click={() => addOrder(s.k)}>+ заявка</button>
                <button class="btn small" disabled={!!busy} on:click={() => act("arrow_dry", { side: s.k })}>
                    {busy === "arrow_dry" + s.k ? "Проверяю…" : "Проверить без нажатий"}</button>
            </div>
        </div>
    {/each}
</div>

<div class="row-actions">
    <button class="btn primary" disabled={!!busy} on:click={() => save()}>Сохранить стрелки</button>
    <button class="btn" disabled={!!busy} on:click={() => act("arrow_layout")}>
        {busy === "arrow_layout" ? "Раскладываю…" : "Разложить в терминале"}</button>
</div>
{#if msg}<div class="res">{msg}</div>{/if}

<style>
    .hint { color: rgba(223,239,240,0.55); font-size: 0.8rem; margin: 0.15rem 0 0.6rem; }
    label { display: block; font-size: 0.78rem; color: rgba(223,239,240,0.75); }
    .tabrow { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 340px)); gap: 0.6rem; margin-bottom: 0.5rem; }
    .tabrow select { width: 100%; }
    input, select {
        margin-top: 0.28rem; padding: 0.45rem 0.55rem; background: rgba(8,16,19,0.85);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 7px; color: #eafeff; font-size: 0.83rem; box-sizing: border-box;
    }
    .tabrow input { width: 100%; }
    input:focus, select:focus { outline: none; border-color: #2fe6e6; }
    .arrows { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 0.6rem; }
    .arrowcard { border: 1px solid; border-left-width: 4px; border-radius: 10px; padding: 0.55rem 0.7rem; background: rgba(15,26,30,0.55); }
    .ahead { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
    .aname { color: #fff; font-weight: 700; font-size: 0.8rem; border-radius: 6px; padding: 0.15rem 0.55rem; }
    .chk { display: flex; align-items: center; gap: 0.35rem; margin: 0; }
    .chk input { width: auto; margin: 0; }
    .noop { font-size: 0.8rem; color: #94a3b8; margin: 0.45rem 0 0.2rem; }
    .orow { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.4rem; flex-wrap: wrap; }
    .orow input, .orow select { margin-top: 0; }
    .venue { width: 118px; }
    .orow.cs .venue { border-color: #a855f7; color: #e9d5ff; }
    .tk { width: 90px; text-transform: uppercase; }
    .tk.cs { width: 130px; }
    .num { width: 70px; }
    .side.buy { border-color: #22c55e; color: #86efac; }
    .side.sell { border-color: #ef4444; color: #fca5a5; }
    .lbl { font-size: 0.75rem; color: rgba(223,239,240,0.6); }
    .oadd { display: flex; gap: 0.4rem; margin-top: 0.5rem; align-items: center; flex-wrap: wrap; }
    .row-actions { display: flex; gap: 0.5rem; align-items: center; margin: 0.6rem 0 0.3rem; flex-wrap: wrap; }
    .btn { padding: 0.45rem 0.85rem; background: rgba(35,50,55,0.7); border: 1px solid rgba(82,254,254,0.2);
        border-radius: 8px; color: #cdeff0; font-size: 0.8rem; cursor: pointer; transition: all 0.15s ease; }
    .btn:hover { background: rgba(82,254,254,0.12); }
    .btn:disabled { opacity: 0.5; cursor: default; }
    .btn.primary { background: rgba(47,230,230,0.18); border-color: #2fe6e6; color: #eafeff; }
    .btn.small { padding: 0.3rem 0.55rem; }
    .btn.danger { color: #ffb4b4; border-color: rgba(255,80,80,0.35); }
    .cstab { font-size: 0.8rem; color: #e9d5ff; background: rgba(168,85,247,0.10); border: 1px solid rgba(168,85,247,0.4);
        border-radius: 8px; padding: 0.45rem 0.6rem; margin-bottom: 0.55rem; }
    .cstab.bad { color: #fecaca; background: rgba(239,68,68,0.10); border-color: rgba(239,68,68,0.45); }
    .acc { margin-top: 0.3rem; color: rgba(223,239,240,0.75); }
    .acc span { margin-left: 0.5rem; color: #fca5a5; }
    .acc span.on { color: #86efac; }
    .acc .mut { color: rgba(223,239,240,0.5); }
    .res { margin-top: 0.45rem; font-size: 0.78rem; color: #cdeff0; background: rgba(47,230,230,0.08);
        border: 1px solid rgba(47,230,230,0.25); border-radius: 7px; padding: 0.35rem 0.55rem; }
</style>

<script lang="ts">
    import { onMount, onDestroy, tick } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import ArrowButton from "@/components/ArrowButton.svelte"
    import { jarvisState, lastAssistantReply } from "@/stores"

    let bubble = ""
    let bubbleTimer: ReturnType<typeof setTimeout>

    // Стрелки «Купить» ▲ / «Продать» ▼ (настройка — «Наблюдатели» → «Стрелки на оверлее»): одно нажатие —
    // все заявки стрелки сразу. Слоты держат место всегда, чтобы шар не прыгал, когда стрелку выключили.
    let arrows: any = { buy: { enabled: false }, sell: { enabled: false } }
    $: style = ["triangles", "pills", "ring", "chevrons"].includes(arrows.style) ? arrows.style : "triangles"
    // расположение: row — обе стрелки под шаром; stack — «купить» над шаром, «продать» под ним
    $: layout = arrows.layout === "stack" ? "stack" : "row"
    // размер виджета (шар + стрелки), 60…160 %
    $: scale = Math.min(160, Math.max(60, Number(arrows.size) || 100)) / 100
    // дуги кольца (стиль В): в ряд — снизу слева/справа, столбиком — сверху/снизу
    const ARC: Record<string, { buy: string, sell: string }> = {
        row: { buy: "M14 44 A50 50 0 0 0 54 109", sell: "M66 109 A50 50 0 0 0 106 44" },
        stack: { buy: "M17 35 A50 50 0 0 1 103 35", sell: "M17 85 A50 50 0 0 0 103 85" },
    }

    // окно оверлея подстраивается под виджет; шар остаётся на месте (низ и центр окна неподвижны)
    let hudEl: HTMLDivElement
    let fitted = ""
    async function fitWindow(_s: string, _l: string, _k: number) {
        await tick()
        if (!hudEl) return
        const r = hudEl.getBoundingClientRect()
        const w = Math.max(210, Math.ceil(r.width) + 24), h = Math.ceil(r.height) + 140
        if (fitted === `${w}x${h}`) return
        try {
            const { getCurrentWindow, LogicalSize, PhysicalPosition } = await import("@tauri-apps/api/window")
            const win = getCurrentWindow()
            const pos = await win.outerPosition(), old = await win.outerSize()
            await win.setSize(new LogicalSize(w, h))
            const now = await win.outerSize()
            await win.setPosition(new PhysicalPosition(Math.round(pos.x + (old.width - now.width) / 2),
                                                       Math.round(pos.y + old.height - now.height)))
            fitted = `${w}x${h}`
        } catch (e) { /* не в Tauri или нет прав на размер окна */ }
    }
    $: if (hudEl) fitWindow(style, layout, scale)
    let tips = { buy: "", sell: "" }
    let firing = ""
    let arrowsTimer: ReturnType<typeof setInterval>

    function showBubble(text: string, ms: number) {
        bubble = text
        clearTimeout(bubbleTimer)
        bubbleTimer = setTimeout(() => { bubble = "" }, ms)
    }
    function tipOf(side: string, r: any) {
        const l: string[] = (r.labels || {})[side] || []
        return (side === "buy" ? "▲ Купить: " : "▼ Продать: ") + (l.length ? l.join(", ") : "заявок нет")
    }
    async function loadArrows() {
        try {
            const r = JSON.parse(await invoke<string>("arrows_get"))
            if (r.ok) { arrows = r; tips = { buy: tipOf("buy", r), sell: tipOf("sell", r) } }
        } catch (e) { /* Нокс не отвечает — стрелки остаются как были */ }
    }
    async function fire(side: "buy" | "sell") {
        if (firing || !arrows[side]?.enabled) return
        firing = side
        const mark = side === "buy" ? "▲ " : "▼ "
        showBubble(mark + (side === "buy" ? "Покупаю…" : "Продаю…"), 30000)
        try {
            const r = JSON.parse(await invoke<string>("arrow_fire", { side }))
            showBubble(mark + (r.text || "готово"), 10000)
        } catch (e) { showBubble(mark + "ошибка: " + e, 8000) }
        firing = ""
    }

    // показать короткий ответ текстом, скрыть через паузу
    const unsub = lastAssistantReply.subscribe((t) => {
        const clean = (t || "").trim()
        if (!clean) return
        bubble = clean
        clearTimeout(bubbleTimer)
        // длиннее текст — дольше висит
        const ms = Math.min(14000, 4000 + clean.length * 45)
        bubbleTimer = setTimeout(() => { bubble = "" }, ms)
    })

    $: active = $jarvisState === "listening"
    $: busy = $jarvisState === "processing"

    async function setupWindow() {
        try {
            const { getCurrentWindow, PhysicalPosition, currentMonitor } = await import("@tauri-apps/api/window")
            const win = getCurrentWindow()
            // позиция в правый нижний угол при первом запуске
            try {
                const mon = await currentMonitor()
                const size = await win.outerSize()
                if (mon) {
                    const x = mon.size.width - size.width - 24
                    const y = mon.size.height - size.height - 70
                    await win.setPosition(new PhysicalPosition(Math.max(0, x), Math.max(0, y)))
                }
            } catch (e) { /* ignore */ }
        } catch (e) { /* не в Tauri */ }
    }

    // Перетаскивание шара мышью
    async function startDrag(e: MouseEvent) {
        if (e.button !== 0) return
        try {
            const { getCurrentWindow } = await import("@tauri-apps/api/window")
            await getCurrentWindow().startDragging()
        } catch (err) { /* ignore */ }
    }

    onMount(() => {
        // фон окна прозрачный
        try {
            document.documentElement.style.background = "transparent"
            document.body.style.background = "transparent"
        } catch (e) { /* ignore */ }
        setupWindow()
        loadArrows()
        arrowsTimer = setInterval(loadArrows, 3000)
    })
    onDestroy(() => { unsub(); clearTimeout(bubbleTimer); clearInterval(arrowsTimer) })
</script>

<div class="wrap">
    {#if bubble}
        <div class="bubble">{bubble}</div>
    {/if}
    <div class="hud st-{style} lay-{layout}" style="zoom:{scale}" bind:this={hudEl}>
        {#if layout === "stack" && style !== "ring"}
            <ArrowButton side="buy" look={style} enabled={!!arrows.buy?.enabled} firing={firing === "buy"}
                busy={!!firing} tip={tips.buy} on:fire={(e) => fire(e.detail)} />
        {/if}
        <div class="orbbox">
            <div class="orb" class:active class:busy on:mousedown={startDrag} title="Перетащите, чтобы переместить">
                <span class="glow"></span>
                <span class="core"></span>
                <span class="ring"></span>
            </div>
            {#if style === "ring"}
                <!-- В: дуги вокруг шара — нажимается сама дуга -->
                <svg class="arcs" viewBox="0 0 120 120" aria-hidden="true">
                    <g class="arc buy" class:off={!arrows.buy?.enabled} class:firing={firing === "buy"}
                        on:click={() => fire("buy")} role="button" tabindex="-1" on:keydown={() => {}}>
                        <title>{tips.buy}</title><path class="band" d={ARC[layout].buy} />
                    </g>
                    <g class="arc sell" class:off={!arrows.sell?.enabled} class:firing={firing === "sell"}
                        on:click={() => fire("sell")} role="button" tabindex="-1" on:keydown={() => {}}>
                        <title>{tips.sell}</title><path class="band" d={ARC[layout].sell} />
                    </g>
                </svg>
            {/if}
        </div>
        {#if style !== "ring"}
            {#if layout === "stack"}
                <ArrowButton side="sell" look={style} enabled={!!arrows.sell?.enabled} firing={firing === "sell"}
                    busy={!!firing} tip={tips.sell} on:fire={(e) => fire(e.detail)} />
            {:else}
                <div class="arrows">
                    <ArrowButton side="buy" look={style} enabled={!!arrows.buy?.enabled} firing={firing === "buy"}
                        busy={!!firing} tip={tips.buy} on:fire={(e) => fire(e.detail)} />
                    <ArrowButton side="sell" look={style} enabled={!!arrows.sell?.enabled} firing={firing === "sell"}
                        busy={!!firing} tip={tips.sell} on:fire={(e) => fire(e.detail)} />
                </div>
            {/if}
        {/if}
    </div>
</div>

<style lang="scss">
    :global(html), :global(body) { background: transparent !important; overflow: hidden; margin: 0; }
    .wrap { width: 100vw; height: 100vh; display: flex; flex-direction: column; align-items: center;
        justify-content: flex-end; gap: 10px; padding: 8px; box-sizing: border-box; user-select: none; }

    .bubble {
        max-width: 190px; align-self: center;
        background: rgba(18, 24, 34, 0.78);
        color: #eaf6f4; font-family: "Manrope", "Segoe UI", sans-serif; font-size: 13px; line-height: 1.4;
        padding: 10px 13px; border-radius: 14px; border: 1px solid rgba(53, 224, 208, 0.35);
        box-shadow: 0 10px 30px -12px rgba(0,0,0,.6);
        backdrop-filter: blur(6px);
        overflow-wrap: anywhere;
        animation: pop .18s ease;
    }
    @keyframes pop { from { opacity: 0; transform: translateY(6px) scale(.97); } to { opacity: 1; transform: none; } }

    /* стрелки у шара: вид кнопок — ArrowButton.svelte, дуги кольца — здесь */
    .hud { display: flex; flex-direction: column; align-items: center; gap: 8px; }
    .orbbox { position: relative; display: grid; place-items: center; }
    .arrows { display: flex; gap: 18px; align-items: center; }
    .st-pills .arrows { gap: 8px; }
    .st-ring .orbbox { width: 118px; height: 118px; }
    .arcs { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; }
    .arc { cursor: pointer; pointer-events: auto; transition: filter .15s; }
    .arc.buy { color: #22c55e; }
    .arc.sell { color: #ef4444; }
    .arc .band { fill: none; stroke: currentColor; stroke-width: 11; stroke-linecap: round; stroke-opacity: .65; }
    .arc:hover .band { stroke-opacity: .95; }
    .arc:hover { filter: drop-shadow(0 0 7px currentColor); }
    .arc.off { visibility: hidden; }
    .arc.firing { animation: pulse .8s ease-in-out infinite; filter: drop-shadow(0 0 9px currentColor); }
    .orb { position: relative; width: 72px; height: 72px; cursor: grab; }
    .orb:active { cursor: grabbing; }
    /* полупрозрачный шар (как оверлей: сквозь него слегка виден фон) */
    .orb .core { position: absolute; inset: 12px; border-radius: 50%;
        background: radial-gradient(circle at 36% 32%, rgba(255,255,255,.85), transparent 42%),
                    radial-gradient(circle at 60% 66%, rgba(53,224,208,.85), rgba(6,48,46,.72) 92%);
        box-shadow: 0 0 26px -6px rgba(53,224,208,.7), inset 0 0 22px -6px rgba(255,255,255,.4);
        opacity: .78; transition: opacity .25s, box-shadow .25s, transform .25s; }
    .orb .glow { position: absolute; inset: -10px; border-radius: 50%; opacity: 0;
        background: radial-gradient(circle, rgba(53,224,208,.5) 0%, transparent 68%); transition: opacity .2s; }
    .orb .ring { position: absolute; inset: 5px; border-radius: 50%; border: 1.5px solid rgba(53,224,208,.4);
        animation: spin 16s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* СЛУШАЮ: ярче + пульс + сильный ореол */
    .orb.active .core { opacity: 1; box-shadow: 0 0 60px 4px rgba(53,224,208,.9), inset 0 0 26px -4px rgba(255,255,255,.6);
        animation: breathe 1.1s ease-in-out infinite; }
    .orb.active .glow { opacity: 1; animation: pulse 1.1s ease-in-out infinite; }
    .orb.active .ring { border-color: rgba(53,224,208,.95); animation-duration: 5s; }

    /* ДУМАЮ: янтарный оттенок */
    .orb.busy .core { opacity: .95; box-shadow: 0 0 46px 2px rgba(246,179,82,.8), inset 0 0 22px -6px rgba(255,255,255,.4);
        background: radial-gradient(circle at 36% 32%, rgba(255,255,255,.8), transparent 42%),
                    radial-gradient(circle at 60% 66%, rgba(246,179,82,.85), rgba(60,40,6,.7) 92%); }
    .orb.busy .glow { opacity: .8; background: radial-gradient(circle, rgba(246,179,82,.45) 0%, transparent 68%); }

    @keyframes breathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.06); } }
    @keyframes pulse { 0%,100% { opacity: .5; transform: scale(1); } 50% { opacity: 1; transform: scale(1.12); } }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; } }
</style>

<script lang="ts">
    import { onMount, onDestroy } from "svelte"
    import { jarvisState, lastAssistantReply } from "@/stores"

    let bubble = ""
    let bubbleTimer: ReturnType<typeof setTimeout>

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
    })
    onDestroy(() => { unsub(); clearTimeout(bubbleTimer) })
</script>

<div class="wrap">
    {#if bubble}
        <div class="bubble">{bubble}</div>
    {/if}
    <div class="orb" class:active class:busy on:mousedown={startDrag} title="Перетащите, чтобы переместить">
        <span class="glow"></span>
        <span class="core"></span>
        <span class="ring"></span>
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

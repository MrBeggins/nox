<script lang="ts">
    import { tick } from "svelte"
    // Кружок «!» со всплывающей подсказкой. position:fixed + измерение после показа
    // и ПРИЖАТИЕ в пределах экрана, чтобы подсказка не вылезала за край.
    export let text: string = ""
    let show = false
    let placed = false
    let tipEl: HTMLElement
    let left = 0, top = 0
    let dotRect: { cx: number; top: number; bottom: number } | null = null

    async function enter(e: MouseEvent | FocusEvent) {
        const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
        dotRect = { cx: r.left + r.width / 2, top: r.top, bottom: r.bottom }
        show = true; placed = false
        await tick()
        position()
    }
    function position() {
        if (!tipEl || !dotRect) return
        const w = tipEl.offsetWidth, h = tipEl.offsetHeight
        const vw = window.innerWidth, vh = window.innerHeight
        const m = 8
        left = Math.max(m, Math.min(dotRect.cx - w / 2, vw - w - m))
        const above = dotRect.top > h + 12
        top = above ? dotRect.top - h - 6 : dotRect.bottom + 6
        top = Math.max(m, Math.min(top, vh - h - m))
        placed = true
    }
    function leave() { show = false; placed = false }
</script>

<span class="idot" on:mouseenter={enter} on:mouseleave={leave} on:focus={enter} on:blur={leave}
      role="button" tabindex="0" aria-label={text}>!</span>

{#if show && text}
    <span class="itip" bind:this={tipEl}
          style="left:{left}px; top:{top}px; visibility:{placed ? 'visible' : 'hidden'}">{text}</span>
{/if}

<style>
    .idot {
        display: inline-flex; align-items: center; justify-content: center;
        width: 15px; height: 15px; border-radius: 50%;
        border: 1px solid #4a90d9; color: #8ab4ff;
        font-size: 11px; font-weight: 700; line-height: 1;
        cursor: help; margin-left: 6px; vertical-align: middle; user-select: none;
        flex: 0 0 auto;
    }
    .idot:hover, .idot:focus { background: #1b2a3f; outline: none; color: #cfe0ff; }
    .itip {
        position: fixed; z-index: 99999;
        max-width: 300px; width: max-content; white-space: normal;
        background: #0f1722; color: #dfe8f3; border: 1px solid #2a3a4f;
        border-radius: 8px; padding: 8px 11px;
        font-size: 12px; font-weight: 400; line-height: 1.45; text-align: left;
        box-shadow: 0 8px 24px rgba(0, 0, 0, .55); pointer-events: none;
    }
</style>

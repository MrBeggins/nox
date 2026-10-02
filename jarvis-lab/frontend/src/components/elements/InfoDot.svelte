<script lang="ts">
    // Маленький кружок «!» с всплывающей подсказкой при наведении.
    // Использует position:fixed, чтобы подсказку не обрезали контейнеры со скроллом.
    export let text: string = ""
    let show = false
    let x = 0, y = 0, above = true

    function place(el: HTMLElement) {
        const r = el.getBoundingClientRect()
        x = r.left + r.width / 2
        above = r.top > 150            // если места сверху мало — показать снизу
        y = above ? r.top - 8 : r.bottom + 8
        show = true
    }
    function enter(e: MouseEvent | FocusEvent) { place(e.currentTarget as HTMLElement) }
    function leave() { show = false }
</script>

<span class="idot" on:mouseenter={enter} on:mouseleave={leave} on:focus={enter} on:blur={leave}
      role="button" tabindex="0" aria-label={text}>!</span>

{#if show && text}
    <span class="itip" class:below={!above} style="left:{x}px;top:{y}px">{text}</span>
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
        transform: translate(-50%, -100%);
        max-width: 300px; width: max-content; white-space: normal;
        background: #0f1722; color: #dfe8f3; border: 1px solid #2a3a4f;
        border-radius: 8px; padding: 8px 11px;
        font-size: 12px; font-weight: 400; line-height: 1.45; text-align: left;
        box-shadow: 0 8px 24px rgba(0, 0, 0, .55); pointer-events: none;
    }
    .itip.below { transform: translate(-50%, 0); }
</style>

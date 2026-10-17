<script lang="ts">
    // Кнопка-стрелка оверлея (▲ купить / ▼ продать) в одном из стилей: triangles | pills | chevrons.
    // Стиль «ring» (дуги вокруг шара) рисует сам Overlay.svelte.
    import { createEventDispatcher } from "svelte"
    export let side: "buy" | "sell" = "buy"
    export let look = "triangles"
    export let enabled = false
    export let firing = false
    export let busy = false
    export let tip = ""
    const dispatch = createEventDispatcher()

    // треугольники со скруглёнными углами (r = 7) — 56×50
    const TRI = {
        buy: "M31.6 10.0 L49.4 40.0 Q53.0 46.0 46.0 46.0 L10.0 46.0 Q3.0 46.0 6.6 40.0 L24.4 10.0 Q28.0 4.0 31.6 10.0 Z",
        sell: "M10.0 4.0 L46.0 4.0 Q53.0 4.0 49.4 10.0 L31.6 40.0 Q28.0 46.0 24.4 40.0 L6.6 10.0 Q3.0 4.0 10.0 4.0 Z",
    }
    // двойной шеврон, отцентрован в 24×24 (y 6…18)
    const CHEV = { buy: "M6 12 12 6 18 12 M6 18 12 12 18 18", sell: "M6 6 12 12 18 6 M6 12 12 18 18 12" }
</script>

<button class="arrow {side} {look}" class:off={!enabled} class:firing title={tip}
    disabled={busy || !enabled} on:click={() => dispatch("fire", side)}>
    {#if look === "pills"}
        <svg viewBox="0 0 12 12" aria-hidden="true"><path d={side === "buy" ? "M1 10 6 2 11 10Z" : "M1 2 11 2 6 10Z"} /></svg>
        <span>{side === "buy" ? "купить" : "продать"}</span>
    {:else if look === "chevrons"}
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d={CHEV[side]} /></svg>
    {:else}
        <svg viewBox="0 0 56 50" aria-hidden="true"><path d={TRI[side]} /></svg>
    {/if}
</button>

<style>
    .arrow { padding: 0; border: 0; background: none; cursor: pointer; color: inherit;
        transition: transform .12s, filter .15s, opacity .2s; }
    .arrow.buy { color: #22c55e; }
    .arrow.sell { color: #ef4444; }
    .arrow.off { visibility: hidden; }
    .arrow:disabled:not(.off) { opacity: .55; cursor: default; }
    .arrow:hover:not(:disabled) { transform: scale(1.07); filter: drop-shadow(0 0 7px currentColor); }
    .arrow:active:not(:disabled) { transform: scale(.94); }
    .arrow.firing { animation: pulse .8s ease-in-out infinite; filter: drop-shadow(0 0 9px currentColor); }
    @keyframes pulse { 0%,100% { opacity: .6; } 50% { opacity: 1; } }
    svg { display: block; }

    /* А: скруглённые полупрозрачные треугольники */
    .triangles svg { width: 56px; height: 50px; }
    .triangles path { fill: currentColor; fill-opacity: .2; stroke: currentColor; stroke-opacity: .7;
        stroke-width: 2.2; stroke-linejoin: round; }
    .triangles:hover:not(:disabled) path { fill-opacity: .42; stroke-opacity: .95; }

    /* Б: стеклянные капсулы с подписью */
    .pills { display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 12px 0 10px; border-radius: 14px;
        border: 1.5px solid; font: 600 11.5px "Manrope", "Segoe UI", sans-serif; }
    .pills.buy { border-color: rgba(34, 197, 94, .8); background: rgba(34, 197, 94, .18); }
    .pills.sell { border-color: rgba(239, 68, 68, .8); background: rgba(239, 68, 68, .18); }
    .pills span { color: #eafff3; }
    .pills.sell span { color: #ffecec; }
    .pills svg { width: 11px; height: 11px; fill: currentColor; }

    /* Г: двойные шевроны в лёгком круге */
    .chevrons { width: 52px; height: 52px; border-radius: 50%; border: 1px solid; display: grid; place-items: center; }
    .chevrons.buy { border-color: rgba(34, 197, 94, .5); background: rgba(34, 197, 94, .12); }
    .chevrons.sell { border-color: rgba(239, 68, 68, .5); background: rgba(239, 68, 68, .12); }
    .chevrons svg { width: 28px; height: 28px; fill: none; stroke: currentColor; stroke-width: 2.6;
        stroke-linecap: round; stroke-linejoin: round; }
</style>

<script lang="ts">
    import { onMount, onDestroy } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import {
        isJarvisRunning, updateJarvisStats, enableIpc, disableIpc,
        translate, translations, lastAssistantReply, setMuted,
        recentReplies, sendTextCommand, setDnd, jarvisState, abortSpeech
    } from "@/stores"

    function stopSpeaking() {
        try { abortSpeech() } catch (e) { console.error(e) }
    }

    // краткая подпись состояния под орбом
    $: stateCaption =
        $jarvisState === "listening"  ? "Слушаю…" :
        $jarvisState === "processing" ? "Думаю…" :
        ($lastAssistantReply || "Готов слушать")

    let micMuted = false
    let news = true
    let dnd = false
    let overlayOn = false
    let processRunning = false
    let launching = false
    let wasRunning = false

    $: t = (key: string) => translate($translations, key)

    function toggleMic() {
        micMuted = !micMuted
        try { setMuted(micMuted) } catch (e) { console.error(e) }
    }

    function toggleNews() {
        news = !news
        try { sendTextCommand(news ? "включи новости" : "выключи новости") } catch (e) { console.error(e) }
    }

    function toggleDnd() {
        dnd = !dnd
        try { setDnd(dnd) } catch (e) { console.error(e) }
    }

    async function toggleOverlay() {
        overlayOn = !overlayOn
        try {
            await invoke("db_write", { key: "overlay_enabled", val: overlayOn ? "true" : "false" })
            await invoke("set_overlay_visible", { visible: overlayOn })
        } catch (e) { console.error(e) }
    }

    isJarvisRunning.subscribe((value) => {
        processRunning = value
        if (value) { enableIpc(); wasRunning = true }
        else if (wasRunning) { disableIpc(); wasRunning = false }
    })

    onMount(async () => {
        updateJarvisStats()
        try { const ov = await invoke<string>("db_read", { key: "overlay_enabled" }); overlayOn = ov === "true" } catch (e) {}
    })
    onDestroy(() => disableIpc())

    async function runAssistant() {
        launching = true
        try {
            await invoke("run_jarvis_app")
            setTimeout(async () => { await updateJarvisStats(); launching = false }, 2500)
        } catch (err) { console.error(err); launching = false }
    }

</script>

<div class="puls">
    <div class="orbwrap" class:live={$jarvisState === "listening"} class:busy={$jarvisState === "processing"}>
        <div class="orb"
             class:idle={!processRunning}
             class:listening={$jarvisState === "listening"}
             class:thinking={$jarvisState === "processing"}>
            <span class="halo"></span>
            <span class="ring"></span><span class="ring r2"></span><span class="core"></span>
        </div>
        <div class="orbcap">
            {#if processRunning}
                <b class:accent={$jarvisState === "listening"}>{stateCaption}</b>
                {#if $jarvisState === "listening"}
                    активирован — говорите команду
                {:else if $jarvisState === "processing"}
                    обрабатываю запрос…
                {:else}
                    скажите «Нокс» или нажмите хоткей
                {/if}
                <button class="stopbtn" on:click={stopSpeaking} title="Прервать ответ/команду">⏹ Стоп</button>
            {:else}
                <b>Ассистент не запущен</b>
                <button class="start" on:click={runAssistant} disabled={launching}>
                    {launching ? "Запуск…" : "Запустить"}
                </button>
            {/if}
        </div>
    </div>

    <aside class="aside">
        <h4>Последнее в эфире</h4>
        <div class="feedscroll">
            {#if $recentReplies.length}
                {#each $recentReplies as f}
                    <div class="feedline">
                        <time>{f.time}</time>
                        <span>{f.text}</span>
                    </div>
                {/each}
            {:else}
                <div class="feedempty">Пока тихо — озвученные ответы и алерты появятся здесь</div>
            {/if}
        </div>

        <div class="quick">
            <button class="qbtn" class:on={!micMuted} on:click={toggleMic}>
                <span class="lab">Микрофон</span><span class="sw"></span><span class="st">{micMuted ? "выкл" : "включён"}</span>
            </button>
            <button class="qbtn" class:on={news} on:click={toggleNews}>
                <span class="lab">Новости</span><span class="sw"></span><span class="st">{news ? "читает" : "выкл"}</span>
            </button>
            <button class="qbtn" class:on={dnd} on:click={toggleDnd}>
                <span class="lab">Не беспокоить</span><span class="sw"></span><span class="st">{dnd ? "тихо" : "выкл"}</span>
            </button>
            <button class="qbtn" class:on={overlayOn} on:click={toggleOverlay}>
                <span class="lab">Оверлей</span><span class="sw"></span><span class="st">{overlayOn ? "показан" : "выкл"}</span>
            </button>
        </div>
    </aside>
</div>

<style lang="scss">
    $accent: #35e0d0; $muted: #8592a6; $ink: #e9eef6; $line: #1e2736;
    .puls { display: grid; grid-template-columns: 1.15fr .85fr; height: 100%; overflow: hidden; }
    .orbwrap { position: relative; display: grid; place-items: center; padding: 26px;
        background: radial-gradient(120% 90% at 50% 32%, rgba(53,224,208,.12), transparent 60%); }
    .orb { position: relative; width: 200px; height: 200px; border-radius: 50%; transition: opacity .3s; }
    .orb.idle { opacity: .4; filter: grayscale(.4); }
    .orb .core { position: absolute; inset: 40px; border-radius: 50%;
        background: radial-gradient(circle at 36% 32%, rgba(255,255,255,.8), transparent 42%),
                    radial-gradient(circle at 62% 66%, #{$accent}, #06302e 92%);
        box-shadow: 0 0 50px -6px $accent, inset 0 0 34px -6px rgba(255,255,255,.4); }
    .orb .ring { position: absolute; inset: 0; border-radius: 50%; border: 1.5px solid rgba(53,224,208,.45); animation: spin 14s linear infinite; }
    .orb .ring.r2 { inset: 18px; border-style: dashed; opacity: .5; animation-duration: 9s; animation-direction: reverse; }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* Свечение активации — большой мягкий ореол вокруг орба */
    .orb .halo { position: absolute; inset: -60px; border-radius: 50%; pointer-events: none; opacity: 0;
        background: radial-gradient(circle, rgba(53,224,208,.55) 0%, rgba(53,224,208,.22) 38%, transparent 70%);
        transition: opacity .18s ease; }

    /* СЛУШАЮ: ярко пульсирует, кольца ускоряются, ядро горит сильнее */
    .orb.listening .halo { opacity: 1; animation: halo-pulse 1.1s ease-in-out infinite; }
    .orb.listening .core {
        box-shadow: 0 0 90px 6px $accent, 0 0 160px 20px rgba(53,224,208,.5), inset 0 0 40px -4px rgba(255,255,255,.6);
        animation: core-breathe 1.1s ease-in-out infinite; }
    .orb.listening .ring { border-color: rgba(53,224,208,.95); animation-duration: 4s; }
    .orb.listening .ring.r2 { opacity: .9; animation-duration: 3s; }

    /* ДУМАЮ: янтарное свечение, кольца быстро крутятся */
    .orb.thinking .halo { opacity: .8; background: radial-gradient(circle, rgba(246,179,82,.45) 0%, rgba(246,179,82,.18) 40%, transparent 72%); }
    .orb.thinking .core { box-shadow: 0 0 70px 2px #f6b352, inset 0 0 34px -6px rgba(255,255,255,.4); }
    .orb.thinking .ring { border-color: rgba(246,179,82,.8); animation-duration: 2.2s; }

    @keyframes halo-pulse { 0%,100% { opacity: .55; transform: scale(1); } 50% { opacity: 1; transform: scale(1.12); } }
    @keyframes core-breathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.06); } }

    /* фон всей области Пульса подсвечивается при активации */
    .orbwrap.live { background: radial-gradient(120% 90% at 50% 40%, rgba(53,224,208,.28), transparent 62%); }
    .orbwrap.busy { background: radial-gradient(120% 90% at 50% 40%, rgba(246,179,82,.16), transparent 62%); }
    .orbcap { position: absolute; bottom: 20px; left: 0; right: 0; text-align: center; color: $muted; font-size: 12.5px; }
    .orbcap b { display: block; color: $ink; font-family: "Sora", sans-serif; font-size: 15px; margin-bottom: 4px; max-width: 80%; margin-inline: auto; }
    .orbcap b.accent { color: $accent; text-shadow: 0 0 18px rgba(53,224,208,.7); font-size: 17px; }
    .start { margin-top: 4px; padding: 7px 16px; border-radius: 999px; border: 1px solid $accent; background: rgba(53,224,208,.14);
        color: $accent; font-weight: 700; cursor: pointer; }
    .stopbtn { display: inline-block; margin-top: 10px; padding: 6px 16px; border-radius: 999px;
        border: 1px solid #ff6b6b; background: rgba(255,80,80,.12); color: #ff9a9a; font-weight: 700; cursor: pointer; }
    .stopbtn:hover { background: rgba(255,80,80,.22); }

    .aside { border-left: 1px solid $line; padding: 18px; display: flex; flex-direction: column; gap: 12px; height: 100%; min-height: 0; }
    h4 { font-size: 11px; letter-spacing: 1.6px; text-transform: uppercase; color: $muted; font-weight: 700; margin: 0; font-family: "JetBrains Mono", monospace; flex: none; }
    /* Только лента скроллится — орб и кнопки остаются на месте */
    .feedscroll { flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column;
        scrollbar-width: thin; }
    .feedline { display: flex; gap: 10px; align-items: baseline; font-size: 13px; padding: 9px 0; border-bottom: 1px dashed $line; }
    .feedline time { font-family: "JetBrains Mono", monospace; color: $muted; font-size: 11.5px; flex: none; }
    .feedempty { color: $muted; font-size: 12.5px; padding: 10px 0; line-height: 1.5; opacity: .8; }
    .tkr { font-family: "JetBrains Mono", monospace; font-weight: 700; color: #f6b352; font-size: 12px; }
    .ev { color: $accent; font-weight: 600; }

    .quick { display: flex; gap: 9px; flex: none; flex-wrap: wrap; padding-top: 6px; border-top: 1px solid $line; }
    .qbtn { flex: 1; min-width: 96px; border: 1px solid #253045; background: #1b2434; border-radius: 11px; padding: 11px;
        display: flex; flex-direction: column; gap: 7px; align-items: flex-start; cursor: pointer; color: $ink;
        .lab { font-size: 12.5px; font-weight: 700; } .st { font-size: 11px; color: $muted; }
        &.on { border-color: $accent; background: rgba(53,224,208,.14); .st { color: $accent; } } }
    .sw { width: 32px; height: 18px; border-radius: 999px; background: #253045; position: relative;
        &::after { content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: #fff; transition: .2s; } }
    .qbtn.on .sw { background: $accent; &::after { left: 16px; } }

    @media (max-width: 760px) { .puls { grid-template-columns: 1fr; } .aside { border-left: 0; border-top: 1px solid $line; } }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; } }
</style>

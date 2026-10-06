<script lang="ts">
    import { onMount, onDestroy } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import { Router } from "@roxi/routify"
    import routes from "../.routify/routes.default.js"
    import { SvelteUIProvider } from "@svelteuidev/core"
    import Events from "./Events.svelte"
    import Overlay from "@/components/Overlay.svelte"

    import {
        loadVoiceSetting,
        loadAppInfo,
        startStatsPolling,
        stopStatsPolling,
        enableIpc,
        disconnectIpc,
        loadTranslations
    } from "@/stores"

    // Оверлей-режим: то же окно бандла, но открытое с ?overlay=1 (прозрачный шар-HUD)
    const isOverlay = typeof window !== "undefined" && window.location.search.includes("overlay")

    onMount(() => {
        if (isOverlay) {
            // оверлею нужны только живые события ассистента
            enableIpc()
            return
        }
        loadVoiceSetting()
        loadAppInfo()
        startStatsPolling(5000)
        enableIpc()
        loadTranslations()

        // синхронизировать видимость оверлея с настройкой (по умолчанию ВЫКЛ)
        invoke<string>("db_read", { key: "overlay_enabled" })
            .then((v) => invoke("set_overlay_visible", { visible: v === "true" }))
            .catch(() => {})
    })

    onDestroy(() => {
        if (!isOverlay) stopStatsPolling()
        disconnectIpc()
    })
</script>

{#if isOverlay}
    <Overlay />
{:else}
    <SvelteUIProvider themeObserver="dark" withNormalizeCSS withGlobalStyles>
        <Router {routes} />
    </SvelteUIProvider>
    <Events />
{/if}

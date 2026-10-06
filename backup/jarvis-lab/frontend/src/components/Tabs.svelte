<script lang="ts">
    import { goto } from "@roxi/routify"
    import { onMount } from "svelte"
    import { translations, translate } from "@/stores"

    $: t = (key: string) => translate($translations, key)

    // active path tracking (no routify-internal deps)
    let active = "/"
    function syncActive() {
        if (typeof window !== "undefined") active = window.location.pathname || "/"
    }
    onMount(syncActive)

    const tabs = [
        { path: "/",          key: "tab-home",      fallback: "Главная",    icon: "◉" },
        { path: "/commands",  key: "tab-commands",  fallback: "Команды",    icon: "⌘" },
        { path: "/modes",     key: "tab-modes",     fallback: "Режимы",     icon: "⚙" },
        { path: "/models",    key: "tab-models",    fallback: "ИИ-модели",  icon: "✦" },
        { path: "/watchers",  key: "tab-watchers",  fallback: "Наблюдатели", icon: "⊙" },
        { path: "/tterminal", key: "tab-tterminal", fallback: "Т-Терминал", icon: "₮" },
        { path: "/settings",  key: "tab-settings",  fallback: "Настройки",  icon: "⚙" },
    ]

    function go(path: string) {
        active = path
        $goto(path)
    }
    function label(tab: {key: string, fallback: string}) {
        const tr = t(tab.key)
        return (!tr || tr === tab.key) ? tab.fallback : tr
    }
</script>

<svelte:window on:popstate={syncActive} />

<nav class="tabs">
    {#each tabs as tab}
        <button
            class="tab"
            class:active={active === tab.path}
            on:click={() => go(tab.path)}
        >
            <span class="tab-icon">{tab.icon}</span>
            <span class="tab-label">{label(tab)}</span>
        </button>
    {/each}
</nav>

<style>
    .tabs {
        display: flex;
        gap: 0.25rem;
        padding: 0.4rem 0.6rem;
        margin: 0 0 0.5rem;
        border-bottom: 1px solid rgba(82, 254, 254, 0.14);
        overflow-x: auto;
    }
    .tab {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.45rem 0.8rem;
        background: transparent;
        border: none;
        border-radius: 8px;
        color: rgba(255, 255, 255, 0.62);
        font-size: 0.8rem;
        font-weight: 500;
        white-space: nowrap;
        cursor: pointer;
        transition: all 0.15s ease;
    }
    .tab:hover {
        background: rgba(82, 254, 254, 0.08);
        color: #cdeff0;
    }
    .tab.active {
        background: rgba(47, 230, 230, 0.14);
        color: #52fefe;
        box-shadow: inset 0 -2px 0 #2fe6e6;
    }
    .tab-icon { font-size: 0.9rem; opacity: 0.9; }
</style>

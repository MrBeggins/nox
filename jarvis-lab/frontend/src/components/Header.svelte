<script lang="ts">
    import { goto } from "@roxi/routify"
    import { invoke } from "@tauri-apps/api/core"
    import { onMount } from "svelte"
    import { currentLanguage, setLanguage, translations, translate } from "@/stores"

    let appVersion = ""
    let menuOpen = false
    let langDropdownOpen = false
    let path = "/"

    const languages = [
        { code: "ru", label: "RU", flag: "🇷🇺", name: "Русский" },
        { code: "en", label: "EN", flag: "🇬🇧", name: "English" },
        { code: "ua", label: "UA", flag: "🇺🇦", name: "Українська" },
    ]

    // Окна Nox
    const views = [
        { path: "/",          name: "Пульс",          icon: "◉" },
        { path: "/calendar",  name: "Календарь",      icon: "🗓" },
        { path: "/widgets",   name: "Сетка виджетов", icon: "▦" },
        { path: "/settings",  name: "Настройки",      icon: "⚙" },
    ]
    const extra = [
        { path: "/commands",  name: "Команды",   icon: "⌘" },
        { path: "/modes",     name: "Режимы",    icon: "⚙" },
        { path: "/models",    name: "ИИ-модели", icon: "✦" },
        { path: "/tterminal", name: "Т-Терминал", icon: "₮" },
    ]

    function syncPath() {
        if (typeof window !== "undefined") path = window.location.pathname || "/"
    }
    onMount(async () => {
        syncPath()
        try { appVersion = await invoke<string>("get_app_version") } catch {}
    })

    function go(p: string) { path = p; menuOpen = false; $goto(p) }

    $: allViews = [...views, ...extra]
    $: currentName = (allViews.find(v => v.path === path) || views[0]).name

    async function selectLanguage(code: string) { await setLanguage(code); langDropdownOpen = false }

    function onWindowClick(e: MouseEvent) {
        const t = e.target as HTMLElement
        if (!t.closest(".nox-switch")) menuOpen = false
        if (!t.closest(".lang-selector")) langDropdownOpen = false
    }

    $: currentLang = languages.find(l => l.code === $currentLanguage) || languages[0]
    $: t = (key: string) => translate($translations, key)
</script>

<svelte:window on:click={onWindowClick} on:popstate={syncPath} />

<header class="nox-header">
    <div class="nox-left">
        <!-- троеточие: переключатель окон -->
        <div class="nox-switch">
            <button class="dots" title="Сменить окно" aria-label="Сменить окно"
                on:mouseenter={() => menuOpen = true}
                on:click|stopPropagation={() => menuOpen = !menuOpen}>
                <i></i><i></i><i></i>
            </button>
            {#if menuOpen}
                <div class="menu" role="menu" on:mouseleave={() => menuOpen = false}>
                    <div class="mlabel">Окно</div>
                    {#each views as v}
                        <button class="mitem" class:sel={path === v.path} on:click={() => go(v.path)}>
                            <span class="ic">{v.icon}</span>{v.name}<span class="chk">✓</span>
                        </button>
                    {/each}
                    <div class="mdiv"></div>
                    <div class="mlabel">Ещё</div>
                    {#each extra as v}
                        <button class="mitem" class:sel={path === v.path} on:click={() => go(v.path)}>
                            <span class="ic">{v.icon}</span>{v.name}<span class="chk">✓</span>
                        </button>
                    {/each}
                </div>
            {/if}
        </div>

        <div class="brand">
            <span class="orb-mini"></span>
            <span class="bname">NOX</span>
            <span class="bsep">·</span>
            <span class="bview">{currentName}</span>
        </div>
    </div>

    <div class="nox-right">
        <span class="statuspill"><span class="pulse"></span>Слушаю</span>
        <span class="ver">v{appVersion}</span>
    </div>
</header>

<style lang="scss">
    $accent: #35e0d0;
    $muted: #8592a6;
    $line: #253045;
    $elev: #1b2434;
    $surface: #131a27;

    .nox-header {
        display: flex; align-items: center; gap: 14px;
        padding: 10px 14px; border-bottom: 1px solid $line;
        background: $elev;
    }
    .nox-left { display: flex; align-items: center; gap: 12px; position: relative; }
    .nox-right { margin-left: auto; display: flex; align-items: center; gap: 12px; }

    .nox-switch { position: relative; }
    .dots {
        display: flex; gap: 6px; padding: 6px 5px; border-radius: 8px;
        background: transparent; border: none; cursor: pointer;
        i { width: 11px; height: 11px; border-radius: 50%; background: $line; display: block; transition: .15s; }
        &:hover i { background: $accent; }
    }
    .menu {
        position: absolute; top: 40px; left: 0; z-index: 200;
        background: $surface; border: 1px solid $line; border-radius: 12px;
        box-shadow: 0 22px 54px -20px rgba(0,0,0,.65); padding: 6px; min-width: 220px;
    }
    .mlabel {
        font-size: 10px; letter-spacing: 1.6px; text-transform: uppercase;
        color: $muted; font-weight: 700; padding: 6px 10px 4px;
        font-family: "JetBrains Mono", monospace;
    }
    .mdiv { height: 1px; background: $line; margin: 5px 4px; }
    .mitem {
        display: flex; align-items: center; gap: 11px; width: 100%; text-align: left;
        border: none; background: none; color: #e9eef6; font: inherit; font-weight: 600;
        padding: 9px 10px; border-radius: 9px; cursor: pointer;
        .ic { width: 18px; text-align: center; }
        .chk { margin-left: auto; color: $accent; opacity: 0; }
        &:hover { background: $elev; }
        &.sel { background: rgba(53,224,208,.14); color: $accent; }
        &.sel .chk { opacity: 1; }
    }

    .brand { display: flex; align-items: center; gap: 9px; font-weight: 700; letter-spacing: 1px; }
    .orb-mini {
        width: 22px; height: 22px; border-radius: 50%;
        background:
            radial-gradient(circle at 33% 30%, rgba(255,255,255,.6), transparent 45%),
            radial-gradient(circle at 62% 66%, #{$accent}, #0b3f3c 92%);
        box-shadow: 0 0 14px -2px $accent;
    }
    .bname { font-family: "Sora", sans-serif; font-size: 15px; }
    .bsep { color: $muted; font-weight: 400; }
    .bview { color: $muted; font-weight: 600; font-size: 13px; }

    .statuspill {
        display: inline-flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 700;
        border-radius: 999px; padding: 4px 10px; background: rgba(53,224,208,.16); color: $accent;
        .pulse { width: 7px; height: 7px; border-radius: 50%; background: $accent; }
    }
    .ver { color: $muted; font-size: 11px; }

    .lang-selector { position: relative; }
    .lang-btn {
        display: flex; align-items: center; padding: 5px 8px; background: transparent;
        border: none; border-radius: 7px; cursor: pointer;
        &:hover { background: rgba(53,224,208,.1); }
    }
    .lang-dropdown {
        position: absolute; top: calc(100% + .35rem); right: 0; background: $surface;
        border: 1px solid $line; border-radius: 9px; overflow: hidden; z-index: 200;
        min-width: 140px; box-shadow: 0 4px 20px rgba(0,0,0,.5);
    }
    .lang-option {
        display: flex; align-items: center; gap: .5rem; width: 100%; padding: .6rem .85rem;
        background: transparent; border: none; color: rgba(233,238,246,.8); font-size: .78rem;
        cursor: pointer; text-align: left;
        &:hover { background: rgba(53,224,208,.1); color: #fff; }
        &.active { background: rgba(53,224,208,.15); color: $accent; }
    }
    .lang-name { font-weight: 500; }
</style>

<script lang="ts">
    import { Container } from "@svelteuidev/core"
    import Header from "@/components/Header.svelte"
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import { goto } from "@roxi/routify"

    // первый запуск: пока не задано имя (и окно не пропущено) — открыть «Регистрацию»
    onMount(async () => {
        try {
            if (localStorage.getItem("nox_reg_skip") === "1") return
            const p = JSON.parse(await invoke<string>("profile_get"))
            if (!p.registered && window.location.pathname !== "/profile") $goto("/profile")
        } catch (e) { /* профиль недоступен — не мешаем */ }
    })
</script>

<Container fluid id="wrapper">
    <Header />
    <main>
        <slot></slot>
    </main>
</Container>

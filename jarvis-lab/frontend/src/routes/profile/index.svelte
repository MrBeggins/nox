<script lang="ts">
    // Окно «Регистрация»: имя-обращение + ключи сервисов в одном месте.
    // Значения ключей сюда НЕ приходят — только «задан, N символов». Пустое поле = не менять.
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import { goto } from "@roxi/routify"
    import { reloadSettings } from "@/stores"

    type Keys = { tinvest: number, apimira: number, bls: number, bea: number, fmp: number, tt: number, stocksi: number }
    let registered = false
    let name = ""
    let keys: Keys = { tinvest: 0, apimira: 0, bls: 0, bea: 0, fmp: 0, tt: 0, stocksi: 0 }
    let input: Record<keyof Keys, string> = { tinvest: "", apimira: "", bls: "", bea: "", fmp: "", tt: "", stocksi: "" }
    let show = false
    let busy = false
    let msg = ""
    let err = ""

    const sections: { title: string, items: { id: keyof Keys, label: string, hint: string, ph: string }[] }[] = [
        { title: "Брокер", items: [
            { id: "tinvest", label: "Т-Терминал — токен T-Invest API", ph: "t.…",
              hint: "Портфель, котировки, стаканы. Т-Инвестиции → Настройки → Токены T-Invest API. Нокс только читает — выпускайте токен «Только для чтения»." },
        ]},
        { title: "Мозг", items: [
            { id: "apimira", label: "APIMIRA — ключ", ph: "am-…",
              hint: "Облачный мозг Нокса (ответы, разбор задач, наблюдатели) и «Чат с Astra». apimira.com → ключи API." },
        ]},
        { title: "Американские сервисы", items: [
            { id: "bea", label: "BEA — UserID", ph: "36 символов",
              hint: "ВВП и PCE США в момент релиза. Бесплатная регистрация: apps.bea.gov/API/signup — UserID придёт на почту." },
            { id: "bls", label: "BLS — ключ (необязательно)", ph: "ключ BLS",
              hint: "Нонфарм, CPI, PPI, JOLTS. Работает и без ключа; ключ поднимает лимит запросов. data.bls.gov/registrationEngine." },
            { id: "fmp", label: "FMP — ключ (необязательно)", ph: "ключ FMP",
              hint: "Резервный экономический календарь, если основной недоступен. financialmodelingprep.com." },
        ]},
        { title: "Новостные сервисы", items: [
            { id: "tt", label: "Trading Tools — токен", ph: "токен TT",
              hint: "Токен выдаёт их Telegram-бот. Пока только хранится — Нокс читает ленту TT из терминала." },
            { id: "stocksi", label: "Stocksi Ultimate — ключ", ph: "ключ Stocksi",
              hint: "Пока только хранится — подключим, когда будет интеграция." },
        ]},
    ]

    async function load() {
        try {
            const p = JSON.parse(await invoke<string>("profile_get"))
            registered = !!p.registered; name = p.name || ""; keys = p.keys || keys
        } catch (e) { err = "Не удалось прочитать профиль" }
    }

    async function save() {
        if (busy) return
        busy = true; msg = ""; err = ""
        try {
            const p = JSON.parse(await invoke<string>("profile_save", { p: { name, ...input } }))
            registered = !!p.registered; keys = p.keys || keys
            input = { tinvest: "", apimira: "", bls: "", bea: "", fmp: "", tt: "", stocksi: "" }   // ключи не держим в окне
            try { reloadSettings() } catch (e) {}
            msg = name.trim() ? `Сохранено. Теперь Нокс будет обращаться: «${name.trim()}».` : "Сохранено."
        } catch (e) { err = typeof e === "string" ? e : "Не удалось сохранить" }
        busy = false
    }

    onMount(load)
</script>

<div class="reg">
    <h2>{registered ? "Профиль" : "Регистрация"}</h2>
    <p class="nx-desc">
        {registered ? "Имя и ключи сервисов. Пустое поле ключа — оставить как есть."
                    : "Пара минут — и Нокс знает, как к вам обращаться, и подключён к вашим сервисам. Любое поле можно заполнить позже."}
    </p>
    {#if err}<div class="nx-err">{err}</div>{/if}

    <div class="nx-block">
        <div class="nx-group">Обращение</div>
        <label class="nx-lab" for="reg-name">Как к вам обращаться</label>
        <input id="reg-name" class="nx-in" placeholder="например, Дамир" bind:value={name} maxlength="40" />
        <p class="nx-hint">Нокс будет говорить это вместо «сэр» — и голосом, и в ленте: «Готово, {name.trim() || "сэр"}.» Пусто — «сэр».</p>
    </div>

    {#each sections as sec}
        <div class="nx-block">
            <div class="nx-group">{sec.title}</div>
            {#each sec.items as it}
                <div class="item">
                    <div class="head">
                        <label class="nx-lab" for={"reg-" + it.id}>{it.label}</label>
                        <span class="state" class:set={keys[it.id] > 0}>{keys[it.id] > 0 ? `✓ задан · ${keys[it.id]} симв.` : "не задан"}</span>
                    </div>
                    {#if show}
                        <input id={"reg-" + it.id} class="nx-in" type="text" autocomplete="off" spellcheck="false"
                               placeholder={keys[it.id] > 0 ? "оставьте пустым — не менять" : it.ph} bind:value={input[it.id]} />
                    {:else}
                        <input id={"reg-" + it.id} class="nx-in" type="password" autocomplete="new-password"
                               placeholder={keys[it.id] > 0 ? "оставьте пустым — не менять" : it.ph} bind:value={input[it.id]} />
                    {/if}
                    <p class="nx-hint">{it.hint}</p>
                </div>
            {/each}
        </div>
    {/each}

    <div class="nx-row actions">
        <button class="nx-btn" on:click={save} disabled={busy}>{busy ? "Сохраняю…" : "Сохранить"}</button>
        <label class="toggle"><input type="checkbox" bind:checked={show} /> показывать вводимое</label>
        <span class="spacer"></span>
        <button class="nx-clr" on:click={() => { if (!registered) { try { localStorage.setItem("nox_reg_skip", "1") } catch (e) {} } $goto("/") }}>{registered ? "На Пульс" : "Пропустить"}</button>
    </div>
    {#if msg}<p class="nx-ok">{msg}</p>{/if}
    <p class="nx-hint">Ключи хранятся только на этом компьютере (настройки Нокса и папка C:\jarvis-voice) и никуда не отправляются, кроме самих сервисов.</p>
</div>

<style lang="scss">
$nx-accent: #35e0d0; $nx-muted: #8592a6;
.reg { max-width: 720px; margin: 0 auto; padding: 1.2rem 1rem 2rem; }
h2 { color: #fff; font-size: 1.35rem; margin: 0 0 0.3rem; }
.nx-block { margin-bottom: 1.1rem; padding-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.06); }
.nx-group { font-size: 0.7rem; letter-spacing: 1.4px; text-transform: uppercase; color: $nx-accent; font-weight: 700; margin: 0.4rem 0 0.8rem; }
.nx-lab { font-weight: 600; font-size: 0.9rem; color: #fff; display: block; margin-bottom: 0.25rem; }
.nx-desc { font-size: 0.8rem; color: rgba(255,255,255,0.55); margin: 0 0 1rem; }
.nx-hint { font-size: 0.72rem; color: rgba(255,255,255,0.38); margin: 0.4rem 0 0; }
.nx-ok { font-size: 0.8rem; color: $nx-accent; margin: 0.6rem 0 0; }
.nx-err { font-size: 0.78rem; color: #ff6d6d; background: rgba(255,109,109,0.08); border: 1px solid rgba(255,109,109,0.25); border-radius: 8px; padding: 8px 10px; margin-bottom: 1rem; }
.nx-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.nx-in { width: 100%; box-sizing: border-box; background: rgba(16,22,31,0.9); border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; color: #fff; padding: 9px 11px; font-size: 0.82rem; }
.nx-in:focus { outline: none; border-color: $nx-accent; }
.nx-btn { padding: 9px 18px; border-radius: 8px; border: 1px solid $nx-accent; background: rgba(53,224,208,0.14); color: $nx-accent; font-weight: 700; font-size: 0.82rem; cursor: pointer; }
.nx-btn:disabled { opacity: 0.6; cursor: default; }
.nx-clr { background: none; border: none; color: $nx-muted; font-size: 0.78rem; cursor: pointer; text-decoration: underline; }
.item { margin-bottom: 0.9rem; }
.head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.state { font-size: 0.72rem; color: $nx-muted; white-space: nowrap; }
.state.set { color: $nx-accent; }
.toggle { font-size: 0.75rem; color: rgba(255,255,255,0.55); display: flex; gap: 6px; align-items: center; cursor: pointer; }
.spacer { flex: 1; }
.actions { margin-top: 0.4rem; }
</style>

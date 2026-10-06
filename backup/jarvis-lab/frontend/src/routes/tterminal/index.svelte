<script lang="ts">
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import HDivider from "@/components/elements/HDivider.svelte"
    import InfoDot from "@/components/elements/InfoDot.svelte"

    let url = ""
    let enabled = false
    let token = ""
    // голосовое исполнение заявок (Нокс сам жмёт Купить/Продать); проверяет terminal_reader перед нажатием
    let execEnabled = false
    let maxLots = "10"
    let maxRub = "50000"
    let toast = ""
    function flash(m: string) { toast = m; setTimeout(() => (toast = ""), 1600) }

    async function load() {
        try {
            url = (await invoke<string>("db_read", { key: "tterminal_url" })) || "https://www.tbank.ru/invest/terminal/"
            enabled = (await invoke<string>("db_read", { key: "tterminal_enabled" })) === "true"
            token = await invoke<string>("db_read", { key: "invest_token" })
            execEnabled = (await invoke<string>("db_read", { key: "tterminal_exec_enabled" })) === "true"
            maxLots = (await invoke<string>("db_read", { key: "tterminal_exec_max_lots" })) || "10"
            maxRub = (await invoke<string>("db_read", { key: "tterminal_exec_max_rub" })) || "50000"
        } catch (e) { console.error(e) }
    }

    async function toggleExec() {
        execEnabled = !execEnabled
        await invoke("db_write", { key: "tterminal_exec_enabled", val: String(execEnabled) })
        flash(execEnabled ? "Исполнение голосом включено" : "Исполнение голосом выключено")
    }
    async function saveLimits() {
        const lots = String(maxLots).replace(/\s/g, ""), rub = String(maxRub).replace(/\s/g, "")
        if (!/^\d+$/.test(lots) || !/^\d+$/.test(rub)) { flash("Лимиты — целые числа (0 = без лимита)"); return }
        const ok = (await invoke<boolean>("db_write", { key: "tterminal_exec_max_lots", val: lots }))
            && (await invoke<boolean>("db_write", { key: "tterminal_exec_max_rub", val: rub }))
        flash(ok ? "Лимиты сохранены" : "Не удалось сохранить лимиты")
    }
    onMount(load)

    async function saveToken() {
        await invoke("db_write", { key: "invest_token", val: token })
        flash("Токен сохранён")
    }

    async function toggle() {
        enabled = !enabled
        await invoke("db_write", { key: "tterminal_enabled", val: String(enabled) })
        flash(enabled ? "Доступ включён" : "Доступ выключен")
    }
    async function saveUrl() {
        await invoke("db_write", { key: "tterminal_url", val: url })
        flash("Адрес сохранён")
    }
    async function open() {
        try { await invoke("open_url", { url }) } catch (e) { flash("Не удалось открыть: " + e) }
    }
</script>

<div class="page">
    <h2>Т-Терминал</h2>
    <p class="hint">Подключение Джарвиса к инвест-терминалу для наблюдения и подготовки заявок.</p>
    {#if toast}<div class="toast">{toast}</div>{/if}

    <div class="row">
        <div class="row-info">
            <div class="row-title">Доступ Джарвиса к терминалу</div>
            <div class="row-desc">Разрешить ассистенту читать терминал и готовить заявки.</div>
        </div>
        <button class="switch" class:on={enabled} on:click={toggle} aria-label="доступ">
            <span class="knob"></span>
        </button>
    </div>

    <label>Адрес терминала<InfoDot text="Ссылка на веб-терминал T-Банка. По кнопке «Открыть терминал» Nox открывает его в своём окне браузера (отдельный профиль), откуда читает новости Trading Tools и календарь MindStocks." />
        <input bind:value={url} placeholder="https://www.tbank.ru/invest/terminal/" />
    </label>
    <div class="actions">
        <button class="btn" on:click={saveUrl}>Сохранить адрес</button>
        <button class="btn primary" on:click={open}>Открыть терминал</button>
    </div>

    <HDivider />

    <h3 style="margin:0 0 0.3rem;color:#cdeff0;font-size:0.95rem;">Чтение портфеля (Invest API)</h3>
    <p class="hint">Токен <b>«только чтение»</b> из T-Банка: Инвестиции → Токены Invest API → создать → режим «Только чтение». С ним Джарвис читает портфель и котировки голосом («джарвис, мой портфель», «курс сбера»). Сделки этим токеном невозможны.</p>
    <label>Токен Invest API (только чтение)<InfoDot text="Токен T-Инвест API в режиме «Только чтение» (T-Банк → Инвестиции → Токены Invest API). С ним Nox читает портфель и котировки голосом. ВАЖНО: токен «только чтение» — сделки им невозможны; Купить/Продать всегда жмёшь ты сам." />
        <input type="password" bind:value={token} placeholder="t...." />
    </label>
    <div class="actions">
        <button class="btn primary" on:click={saveToken}>Сохранить токен</button>
    </div>

    <HDivider />

    <h3 style="margin:0 0 0.3rem;color:#cdeff0;font-size:0.95rem;">Исполнение заявок голосом</h3>
    <div class="row" class:danger={execEnabled}>
        <div class="row-info">
            <div class="row-title">Нокс сам нажимает «Купить / Продать»</div>
            <div class="row-desc">На фразы «выставь заявку…», «отправь заявку…», «продай всё …» Нокс заполняет виджет и сразу отправляет заявку — <b>без подтверждения</b>. Выключено — только заполняет, кнопку жмёте вы.</div>
        </div>
        <button class="switch" class:on={execEnabled} on:click={toggleExec} aria-label="исполнение голосом">
            <span class="knob"></span>
        </button>
    </div>
    <div class="limits">
        <label>Максимум лотов в одной заявке<InfoDot text="Если голосовая заявка больше — Нокс заполнит виджет, но кнопку не нажмёт. 0 = без лимита." />
            <input bind:value={maxLots} inputmode="numeric" placeholder="10" />
        </label>
        <label>Максимальная сумма заявки, ₽<InfoDot text="Для рыночной заявки сумма считается по последней цене. Если больше — заявка не отправляется. 0 = без лимита." />
            <input bind:value={maxRub} inputmode="numeric" placeholder="50000" />
        </label>
    </div>
    <div class="actions">
        <button class="btn" on:click={saveLimits}>Сохранить лимиты</button>
    </div>
    <p class="hint">Каждая голосовая заявка (что сказано, что распознано, ушла или нет и почему) пишется в журнал <code>C:\jarvis-voice\order_journal.jsonl</code>.</p>

    <HDivider />

    <div class="boundary">
        <div class="boundary-title">🔒 Граница безопасности</div>
        <ul>
            <li>✅ Нокс может <b>смотреть</b> портфель и котировки, <b>озвучивать</b> их, <b>готовить</b> заявки и предупреждать по цене.</li>
            {#if execEnabled}
                <li>⚠️ Включено <b>исполнение голосом</b>: Нокс сам жмёт «Купить / Продать» в пределах лимитов выше. Ослышка = реальная сделка — держите лимиты небольшими.</li>
            {:else}
                <li>⛔ Нокс <b>не исполняет</b> сделки: финальную кнопку «Купить / Продать» нажимаете <b>вы</b>.</li>
            {/if}
            <li>⛔ Нокс <b>не переводит</b> деньги.</li>
        </ul>
        <p class="hint">Для торговли «пока тебя нет» надёжнее условные заявки самого брокера.</p>
    </div>
</div>

<style>
    .page { padding: 0 0.9rem 2rem; color: #dfeff0; }
    h2 { margin: 0 0 0.2rem; font-size: 1.25rem; color: #eafeff; }
    .hint { color: rgba(223,239,240,0.55); font-size: 0.8rem; margin: 0.15rem 0 0.6rem; }
    .toast { background: rgba(47,230,230,0.14); border: 1px solid #2fe6e6; color: #eafeff;
        padding: 0.4rem 0.7rem; border-radius: 8px; font-size: 0.8rem; margin-bottom: 0.6rem; }
    .row { display: flex; justify-content: space-between; align-items: center; gap: 1rem;
        background: rgba(15,26,30,0.6); border: 1px solid rgba(82,254,254,0.12);
        border-radius: 12px; padding: 0.85rem 1rem; margin-bottom: 0.8rem; }
    .row-title { font-size: 0.95rem; font-weight: 600; color: #eafeff; }
    .row-desc { font-size: 0.8rem; color: rgba(223,239,240,0.6); margin-top: 0.2rem; }
    label { display: block; font-size: 0.78rem; color: rgba(223,239,240,0.75); margin-bottom: 0.6rem; }
    input { width: 100%; margin-top: 0.28rem; padding: 0.5rem 0.6rem;
        background: rgba(8,16,19,0.85); border: 1px solid rgba(82,254,254,0.2);
        border-radius: 7px; color: #eafeff; font-size: 0.85rem; box-sizing: border-box; }
    input:focus { outline: none; border-color: #2fe6e6; }
    .actions { display: flex; gap: 0.5rem; margin-bottom: 0.4rem; }
    .btn { padding: 0.5rem 0.9rem; background: rgba(35,50,55,0.7);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 8px; color: #cdeff0;
        font-size: 0.82rem; cursor: pointer; transition: all 0.15s ease; }
    .btn:hover { background: rgba(82,254,254,0.12); }
    .btn.primary { background: rgba(47,230,230,0.18); border-color: #2fe6e6; color: #eafeff; }

    .switch { position: relative; width: 50px; height: 28px; flex-shrink: 0;
        background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.15);
        border-radius: 999px; cursor: pointer; transition: all 0.2s ease; padding: 0; }
    .switch.on { background: rgba(47,230,230,0.35); border-color: #2fe6e6; }
    .knob { position: absolute; top: 2px; left: 2px; width: 22px; height: 22px;
        background: #eafeff; border-radius: 50%; transition: transform 0.2s ease; }
    .switch.on .knob { transform: translateX(22px); background: #52fefe; }

    .row.danger { border-color: rgba(255,120,90,0.55); background: rgba(60,22,18,0.45); }
    .limits { display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem; }
    code { font-size: 0.75rem; color: #cdeff0; }

    .boundary { background: rgba(15,26,30,0.55); border: 1px solid rgba(82,254,254,0.14);
        border-radius: 12px; padding: 0.9rem 1rem; }
    .boundary-title { font-weight: 600; color: #eafeff; margin-bottom: 0.4rem; }
    .boundary ul { margin: 0.3rem 0 0.5rem; padding-left: 1.1rem; }
    .boundary li { font-size: 0.82rem; color: rgba(223,239,240,0.8); margin-bottom: 0.35rem; line-height: 1.4; }
</style>

<script lang="ts">
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import HDivider from "@/components/elements/HDivider.svelte"
    import InfoDot from "@/components/elements/InfoDot.svelte"

    type Watcher = {
        id: string, name: string, keywords: string[], source: string,
        brain: string, scenario_id: string, scenario_name?: string,
        until: string, once: boolean, enabled: boolean, fired_day?: string
    }
    let watchers: Watcher[] = []
    let brains: { label: string, value: string }[] = []
    let scenarios: { id: string, name: string }[] = []

    let toast = ""
    function flash(m: string) { toast = m; setTimeout(() => (toast = ""), 1800) }
    function parse(s: string) { try { return JSON.parse(s) } catch { return {} } }

    // форма
    let f_name = "", f_keywords = "", f_source = "all", f_brain = "", f_scenario = "", f_until = ""
    let f_once = true

    async function load() {
        try {
            watchers = (parse(await invoke<string>("watch_list")).watchers) || []
        } catch (e) { watchers = [] }
        try {
            const raw = await invoke<string>("db_read", { key: "ai_models" })
            const presets = raw ? JSON.parse(raw) : []
            brains = [{ label: "По умолчанию (быстрый)", value: "" },
                      ...presets.map((p: any) => ({ label: p.name, value: p.model }))]
        } catch (e) { brains = [{ label: "По умолчанию (быстрый)", value: "" }] }
        try {
            const r = parse(await invoke<string>("scn_list"))
            scenarios = (r.scenarios || []).map((s: any) => ({ id: s.id, name: s.name }))
        } catch (e) { scenarios = [] }
    }
    onMount(load)

    async function add() {
        if (!f_keywords.trim()) { flash("Укажите, что ловить (ключевые слова)"); return }
        try {
            await invoke("watch_add", {
                name: f_name.trim(), keywords: f_keywords.trim(), source: f_source,
                brain: f_brain, scenarioId: f_scenario, until: f_until.trim(), once: f_once ? 1 : 0
            })
            f_name = ""; f_keywords = ""; f_until = ""; f_scenario = ""; f_brain = ""; f_source = "all"; f_once = true
            await load(); flash("Наблюдатель создан")
        } catch (e) { flash("Не удалось создать") }
    }
    async function toggle(id: string) { try { await invoke("watch_toggle", { id }); await load() } catch (e) {} }
    async function del(id: string) { try { await invoke("watch_del", { id }); await load() } catch (e) {} }
</script>

<div class="page">
    <h2>Наблюдатели <span class="tagline">«мультимозг»</span></h2>
    <p class="hint">Каждый наблюдатель весь день (или до заданного часа) сторожит ОДНУ новость — и больше ничего не делает.
        У каждого свой мозг. При срабатывании: озвучит новость и запустит привязанный сценарий. Можно завести несколько — работают параллельно.</p>
    {#if toast}<div class="toast">{toast}</div>{/if}

    <HDivider />

    <h3>Новый наблюдатель</h3>
    <div class="grid">
        <label>Название (необязательно)<InfoDot text="Понятное имя, например «Дивиденды Сбера». Если пусто — соберётся из ключевых слов." />
            <input bind:value={f_name} placeholder="Дивиденды Сбера" />
        </label>
        <label>Что ловить (ключевые слова)<InfoDot text="Через пробел или запятую. Срабатывает, когда в новости есть ВСЕ слова (учитываются склонения). Пример: «дивиденд сбер»." />
            <input bind:value={f_keywords} placeholder="дивиденд сбер" />
        </label>
        <label>Источник<InfoDot text="Откуда ждать новость. Сейчас активна лента Trading Tools. «Любой» — из всех подключённых источников." />
            <select bind:value={f_source}>
                <option value="all">Любой</option>
                <option value="trading">Trading Tools</option>
                <option value="telegram">Telegram</option>
            </select>
        </label>
        <label>Мозг<InfoDot text="Какая модель подтвердит новость и извлечёт число. По умолчанию — быстрый мозг. Для сложных формулировок можно выбрать умнее (список из вкладки «ИИ-модели»)." />
            <select bind:value={f_brain}>
                {#each brains as b}<option value={b.value}>{b.label}</option>{/each}
            </select>
        </label>
        <label>Сценарий при срабатывании<InfoDot text="Привязанный сценарий из вкладки «Сценарии»: откроет/подсветит нужный стакан. Кнопку Купить/Продать жмёшь ты сам. Пусто — только озвучка." />
            <select bind:value={f_scenario}>
                <option value="">— только озвучить —</option>
                {#each scenarios as s}<option value={s.id}>{s.name}</option>{/each}
            </select>
        </label>
        <label>Следить до (ЧЧ:ММ, пусто = весь день)<InfoDot text="Во сколько прекратить наблюдение (по МСК). Пусто — до конца дня. Например 19:00." />
            <input bind:value={f_until} placeholder="19:00" />
        </label>
    </div>
    <label class="chk"><input type="checkbox" bind:checked={f_once} /> Сработать один раз и остановиться
        <InfoDot text="Включено: после первой пойманной новости наблюдатель отключается (типично для события дня). Выключено: продолжает ловить (с паузой 15 мин между повторами)." />
    </label>
    <button class="btn primary" on:click={add}>+ Создать наблюдателя</button>

    <HDivider />

    <h3>Активные наблюдатели</h3>
    <p class="hint">Голосом тоже можно: «Нокс, следи весь день за дивидендами Сбера до 19 сценарий Сбер».</p>
    <div class="list">
        {#each watchers as w}
            <div class="card" class:off={!w.enabled}>
                <div class="card-main">
                    <div class="cmd-id">{w.name} {#if !w.enabled}<span class="badge">выкл</span>{/if}</div>
                    <div class="card-desc">
                        🔎 {w.keywords.join(" + ")}
                        · {w.until ? "до " + w.until : "весь день"}
                        · {w.scenario_name ? "сценарий: " + w.scenario_name : "только озвучка"}
                        {w.brain ? " · мозг: " + w.brain : ""}
                        {w.once ? " · один раз" : " · постоянно"}
                        {w.fired_day ? " · сработал " + w.fired_day : ""}
                    </div>
                </div>
                <div class="card-actions">
                    <button class="btn small" on:click={() => toggle(w.id)}>{w.enabled ? "Выкл" : "Вкл"}</button>
                    <button class="btn small danger" on:click={() => del(w.id)}>🗑</button>
                </div>
            </div>
        {/each}
        {#if watchers.length === 0}<p class="hint">Пока нет наблюдателей.</p>{/if}
    </div>
</div>

<style>
    .page { padding: 0 0.9rem 2rem; color: #dfeff0; }
    h2 { margin: 0 0 0.2rem; font-size: 1.25rem; color: #eafeff; }
    .tagline { font-size: 0.8rem; color: #2fe6e6; font-weight: 400; }
    h3 { margin: 0.6rem 0 0.4rem; font-size: 1rem; color: #cdeff0; }
    .hint { color: rgba(223,239,240,0.55); font-size: 0.8rem; margin: 0.15rem 0 0.6rem; }
    .toast { background: rgba(47,230,230,0.14); border: 1px solid #2fe6e6; color: #eafeff;
        padding: 0.4rem 0.7rem; border-radius: 8px; font-size: 0.8rem; margin-bottom: 0.5rem; }

    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.6rem; }
    label { display: block; font-size: 0.78rem; color: rgba(223,239,240,0.75); margin-bottom: 0.4rem; }
    .chk { display: flex; align-items: center; gap: 0.4rem; margin: 0.6rem 0; }
    .chk input { width: auto; margin: 0; }
    input, select {
        width: 100%; margin-top: 0.28rem; padding: 0.5rem 0.6rem;
        background: rgba(8,16,19,0.85); border: 1px solid rgba(82,254,254,0.2);
        border-radius: 7px; color: #eafeff; font-size: 0.85rem; box-sizing: border-box;
    }
    input:focus, select:focus { outline: none; border-color: #2fe6e6; }

    .btn { padding: 0.5rem 0.9rem; background: rgba(35,50,55,0.7);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 8px; color: #cdeff0;
        font-size: 0.82rem; cursor: pointer; transition: all 0.15s ease; }
    .btn:hover { background: rgba(82,254,254,0.12); }
    .btn.primary { background: rgba(47,230,230,0.18); border-color: #2fe6e6; color: #eafeff; margin-top: 0.3rem; }
    .btn.small { padding: 0.35rem 0.6rem; }
    .btn.danger { color: #ffb4b4; border-color: rgba(255,80,80,0.35); }

    .list { display: flex; flex-direction: column; gap: 0.5rem; margin: 0.4rem 0 0.8rem; }
    .card { display: flex; justify-content: space-between; align-items: center; gap: 0.75rem;
        background: rgba(15,26,30,0.6); border: 1px solid rgba(82,254,254,0.12); border-radius: 10px; padding: 0.65rem 0.9rem; }
    .card.off { opacity: 0.5; }
    .card-main { min-width: 0; }
    .cmd-id { font-weight: 600; color: #eafeff; font-size: 0.9rem; }
    .badge { font-size: 0.68rem; color: #ffb4b4; border: 1px solid rgba(255,80,80,0.35); border-radius: 5px; padding: 0 0.3rem; margin-left: 0.3rem; }
    .card-desc { font-size: 0.76rem; color: rgba(223,239,240,0.6); margin-top: 0.2rem; word-break: break-word; }
    .card-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }
</style>

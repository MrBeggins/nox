<script lang="ts">
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import HDivider from "@/components/elements/HDivider.svelte"
    import InfoDot from "@/components/elements/InfoDot.svelte"
    import ScenariosPanel from "@/components/ScenariosPanel.svelte"

    // venue "cscalp" — стакан CScalp по тикеру (горячая клавиша, только по рынку, объём — в стакане)
    type Order = { ticker: string, n: number, side: string, lots?: number | null, price?: number | null, venue?: string }
    type Branch = { type: string, min?: number | null, max?: number | null, orders: Order[] }
    type Watcher = {
        id: string, name: string, keywords: string[], source: string,
        brain: string, scenario_id: string, scenario_name?: string,
        until: string, once: boolean, enabled: boolean, fired_day?: string,
        tab?: string, event?: string, branches?: Branch[], last_result?: string,
        // решение по ставке ЦБ/ФРС: разбор правилами, ветки по изменению в б.п.
        kind?: string, bank?: string, date?: string, from?: string, rate_now?: number | null
    }
    type Widget = { ticker: string, n: number, label: string }

    // цвета веток — те же, что Нокс рисует поверх терминала
    const COLORS = ["#22c55e", "#ef4444", "#f59e0b", "#3b82f6", "#a855f7", "#14b8a6"]
    const CONDS = [
        { v: "gt", l: "больше" }, { v: "ge", l: "не меньше" }, { v: "lt", l: "меньше" },
        { v: "le", l: "не больше" }, { v: "range", l: "от … до" }, { v: "none", l: "не выплачивает (0)" },
        { v: "any", l: "любое значение" },
    ]

    let watchers: Watcher[] = []
    let brains: { label: string, value: string }[] = []
    let scenarios: { id: string, name: string }[] = []
    let scanned: Widget[] = []
    let csDoms: { ticker: string, window: string }[] = []
    let csArmed: Record<string, string> = {}
    let toast = ""
    let busy = ""
    let results: Record<string, string> = {}
    let dryVal: Record<string, string> = {}

    function flash(m: string) { toast = m; setTimeout(() => (toast = ""), 2600) }
    function parse(s: string) { try { return JSON.parse(s) } catch { return {} } }

    // описание одной строкой -> мозг заполняет ВСЁ: поля наблюдения и ветки с заявками (как голосом).
    // Ничего не сохраняется — пользователь проверяет и жмёт «Создать».
    let brief = ""
    let parsing = false
    async function doBrief() {
        if (!brief.trim()) return
        parsing = true
        try {
            const r = parse(await invoke<string>("watch_parse", { text: brief }))
            if (r.ok) {
                ed.name = r.name || ed.name
                ed.keywords = (r.keywords || []).join(" ")
                ed.until = r.until || ""
                ed.event = r.event || ed.event
                ed.tab = r.tab || ed.tab
                if (r.kind === "rate") {
                    ed.kind = "rate"; ed.bank = r.bank || ed.bank; ed.date = r.date || ""; ed.from = r.from || ""
                }
                if ((r.branches || []).length) ed.branches = r.branches
                const nb = (r.branches || []).length
                const miss = (r.missing || []).length ? ` Не нашёл инструменты: ${r.missing.join(", ")} — впишите тикер сами.` : ""
                const fail = r.brain_failed ? " Мозг не ответил вовремя — ветки НЕ разобраны, нажмите «Разобрать» ещё раз." : ""
                flash((nb ? `Заполнил поля и ветки (${nb}) — проверьте и сохраните.` : "Заполнил поля — проверьте и сохраните.") + miss + fail)
            } else flash(r.text || "Не понял описание")
        } catch (e) { flash("Ридер не отвечает") }
        parsing = false
    }

    function blank() {
        return { id: "", name: "", keywords: "", source: "all", brain: "", scenario_id: "", until: "",
                 once: true, tab: "", event: "", branches: [] as Branch[],
                 kind: "", bank: "cbr", date: "", from: "", rate_now: "" as any }
    }
    const BANKS = [{ v: "cbr", l: "ЦБ РФ (решение в 13:30 МСК)", t: "13:30:00" },
                   { v: "fed", l: "ФРС (заявление в 21:00 МСК)", t: "21:00:00" }]
    // три ветки разом: снижение / сохранение / повышение
    function rateBranches() {
        ed.branches = [...ed.branches,
            { type: "lt", min: null, max: 0, orders: [] },
            { type: "range", min: 0, max: 0, orders: [] },
            { type: "gt", min: 0, max: null, orders: [] }]
    }
    function rateCondText(b: Branch) {
        const f = (v: any) => (v === null || v === undefined || v === "" ? "?" : Number(v))
        if (b.type === "range" && f(b.min) === 0 && f(b.max) === 0) return "сохранение"
        if (b.type === "lt" && f(b.max) === 0) return "любое снижение"
        if (b.type === "gt" && f(b.min) === 0) return "любое повышение"
        if (b.type === "le" && typeof f(b.max) === "number" && (f(b.max) as number) < 0) return `снижение на ${-(f(b.max) as number)}+ б.п.`
        if (b.type === "ge" && typeof f(b.min) === "number" && (f(b.min) as number) > 0) return `повышение на ${f(b.min)}+ б.п.`
        if (b.type === "range" && f(b.min) === f(b.max)) {
            const v = f(b.min) as number
            return v < 0 ? `снижение на ${-v} б.п.` : `повышение на ${v} б.п.`
        }
        return condText(b) + " б.п."
    }
    let ed = blank()

    async function load() {
        try { watchers = (parse(await invoke<string>("watch_list")).watchers) || [] } catch (e) { watchers = [] }
        try {
            const raw = await invoke<string>("db_read", { key: "ai_models" })
            const presets = raw ? JSON.parse(raw) : []
            brains = [{ label: "Как на странице «Мозг»", value: "" },
                      ...presets.map((p: any) => ({ label: p.name, value: p.model }))]
        } catch (e) { brains = [{ label: "Как на странице «Мозг»", value: "" }] }
        try {
            const r = parse(await invoke<string>("scn_list"))
            scenarios = (r.scenarios || []).map((s: any) => ({ id: s.id, name: s.name }))
        } catch (e) { scenarios = [] }
    }
    onMount(load)

    // ---- редактор веток
    function addBranch() { ed.branches = [...ed.branches, { type: "gt", min: null, max: null, orders: [] }] }
    function delBranch(i: number) { ed.branches = ed.branches.filter((_, k) => k !== i) }
    function addOrder(i: number, o?: Order) {
        ed.branches[i].orders = [...ed.branches[i].orders, o || { ticker: "", n: 1, side: "buy", lots: null, price: null }]
    }
    function delOrder(i: number, j: number) { ed.branches[i].orders = ed.branches[i].orders.filter((_, k) => k !== j) }
    function setVenue(o: Order, v: string) {
        o.venue = v === "cscalp" ? "cscalp" : undefined
        if (o.venue) { o.lots = null; o.price = null; o.n = 1 }
        ed = ed
    }
    function fromCs(i: number, e: Event) {
        const sel = e.target as HTMLSelectElement
        const d = csDoms[Number(sel.value)]
        if (d) addOrder(i, { ticker: d.ticker, n: 1, side: "buy", lots: null, price: null, venue: "cscalp" })
        sel.value = ""
    }
    async function loadCs() {
        busy = "cs"
        try {
            const r = parse(await invoke<string>("cscalp_layout"))
            csDoms = r.doms || []
            csArmed = r.armed || {}
            flash(r.ok ? `CScalp: стаканов на экране ${csDoms.length}` : (r.error || "CScalp не найден"))
        } catch (e) { flash("Ридер не отвечает") }
        busy = ""
    }
    function fromTab(i: number, e: Event) {
        const sel = e.target as HTMLSelectElement
        const w = scanned[Number(sel.value)]
        if (w) addOrder(i, { ticker: w.ticker, n: w.n, side: "buy", lots: null, price: null })
        sel.value = ""
    }

    function condText(b: Branch) {
        const f = (v: any) => (v === null || v === undefined || v === "" ? "?" : String(v))
        return ({ gt: `> ${f(b.min)}`, ge: `≥ ${f(b.min)}`, lt: `< ${f(b.max)}`, le: `≤ ${f(b.max)}`,
                  range: `${f(b.min)} … ${f(b.max)}`, none: "не выплачивает", any: "любое" } as any)[b.type] || b.type
    }
    function orderText(o: Order) {
        const act = o.side === "buy" ? "КУПИТЬ" : "ПРОДАТЬ"
        return o.venue === "cscalp" ? `${act} ${o.ticker} в CScalp` : `${act} ${o.ticker} №${o.n}`
    }

    async function scan() {
        const tab = (ed.tab || "нокс").trim()
        if (!tab) { flash("Укажите вкладку терминала"); return }
        busy = "scan"
        try {
            const r = parse(await invoke<string>("watch_scan", { tab }))
            scanned = r.widgets || []
            flash(r.ok ? `На вкладке «${tab}» виджетов: ${scanned.length}` : (r.text || "Не удалось прочитать вкладку"))
        } catch (e) { flash("Ридер терминала не отвечает") }
        busy = ""
    }

    async function saveEd() {
        if (ed.kind !== "rate" && !ed.keywords.trim()) { flash("Укажите, что ловить (ключевые слова)"); return }
        const data = { ...ed, tab: (ed.tab || "нокс").trim() }
        try {
            const r = parse(await invoke<string>("watch_save", { jsonData: JSON.stringify(data) }))
            flash(r.text || "Сохранено")
            if (r.ok) { ed = blank(); scanned = [] }
            await load()
        } catch (e) { flash("Не удалось сохранить") }
    }

    function edit(w: Watcher) {
        ed = { id: w.id, name: w.name, keywords: w.keywords.join(" "), source: w.source || "all", brain: w.brain || "",
               scenario_id: w.scenario_id || "", until: w.until || "", once: w.once, tab: w.tab || "",
               event: w.event || "", branches: JSON.parse(JSON.stringify(w.branches || [])),
               kind: w.kind || "", bank: w.bank || "cbr", date: w.date || "", from: w.from || "",
               rate_now: (w.rate_now ?? "") as any }
        scanned = []
        window.scrollTo({ top: 0, behavior: "smooth" })
    }

    // ---- действия с наблюдателем
    async function act(cmd: string, w: Watcher, args: any = {}) {
        busy = cmd + w.id
        try {
            const r = parse(await invoke<string>(cmd, { id: w.id, ...args }))
            results[w.id] = r.text || ""
        } catch (e) { results[w.id] = "Ридер терминала не отвечает" }
        busy = ""
        await load()
    }
    async function toggle(id: string) { try { await invoke("watch_toggle", { id }); await load() } catch (e) {} }
    async function del(id: string) { try { await invoke("watch_del", { id }); await load() } catch (e) {} }
</script>

<div class="page">
    <h2>Наблюдатели <span class="tagline">«мультимозг»</span></h2>
    <p class="hint">Наблюдатель ждёт одну новость, берёт из неё число и выбирает ветку. У каждой ветки — список заявок:
        какой виджет «Заявка» на вкладке наблюдателя и какую кнопку нажать. Виджеты заполняете вы утром — или Нокс по описанию
        («Разложить в терминале»). Заявка может идти и в CScalp — в стакан по тикеру, по рынку («…в скальпе продай SILVM-12.26»).
        Нажатие подчиняется тумблеру исполнения (и лимитам) в «Т-Терминал».</p>
    {#if toast}<div class="toast">{toast}</div>{/if}

    <HDivider />

    <h3>{ed.id ? `Изменить «${ed.name}»` : "Новый наблюдатель"}</h3>
    <label>Опишите одной строкой<InfoDot text="Обычными словами: за чем следить и что делать при каком значении. Нокс заполнит название, ключевые слова, время, вкладку и ветки с заявками (тикеры, фьючерсы, купить/продать, лоты, цену). Ничего не сохраняется, пока вы не нажмёте «Создать»." /></label>
    <div class="row-actions">
        <input class="brief" bind:value={brief} placeholder="дивиденды Лукойла на вкладке Нокс: больше 600 — купи 1 лот по рынку; от 480 до 600 — купи фьючерс Лукойла 12; меньше 480 — продай 1 лот"
            on:keydown={(e) => { if (e.key === 'Enter') doBrief() }} />
        <button class="btn" on:click={doBrief} disabled={parsing || !brief.trim()}>{parsing ? "Разбираю…" : "Разобрать"}</button>
    </div>
    <div class="grid">
        <label>Тип<InfoDot text="«Решение по ставке» — для ЦБ РФ и ФРС: заголовок разбирается правилами без мозга (~0,1 мс, проверено на официальных формулировках cbr.ru и federalreserve.gov), ветки — по изменению ставки в б.п. Новости до заданного времени не принимаются: анонсы и прогнозы не сработают." />
            <select bind:value={ed.kind}>
                <option value="">Новость: слова / число</option>
                <option value="rate">Решение по ставке ЦБ / ФРС</option>
            </select>
        </label>
        <label>Название<InfoDot text="Например «Новатэк · дивиденды». Если вкладка не указана — так же назовётся вкладка терминала." />
            <input bind:value={ed.name} placeholder={ed.kind === "rate" ? "ЦБ · ставка 23.10" : "Новатэк · дивиденды"} />
        </label>
        {#if ed.kind === "rate"}
            <label>Банк
                <select bind:value={ed.bank} on:change={() => { ed.from = "" }}>
                    {#each BANKS as b}<option value={b.v}>{b.l}</option>{/each}
                </select>
            </label>
            <label>Дата решения<InfoDot text="В другие дни наблюдатель не сработает. Пусто — любой день." />
                <input type="date" bind:value={ed.date} />
            </label>
            <label>Принимать новости с (МСК)<InfoDot text="Раньше этого времени — ни анонсы, ни прогнозы, ни даже похожий заголовок не сработают. С −5 с до +3 мин от него Нокс читает ленту в 5 раз чаще (0,1 с)." />
                <input bind:value={ed.from} placeholder={(BANKS.find((b) => b.v === ed.bank) || BANKS[0]).t} />
            </label>
            <label>Текущая ставка, %<InfoDot text="Нужна, только если в заголовке нет «на N б.п.» (например «ЦБ повысил ставку до 15%»): тогда изменение = новая − текущая. Для ФРС — верхняя граница диапазона." />
                <input bind:value={ed.rate_now} placeholder={ed.bank === "fed" ? "4,00" : "14,00"} />
            </label>
        {:else}
        <label>Что ловить (ключевые слова)<InfoDot text="Срабатывает, когда в новости есть ВСЕ слова (склонения учитываются). Пример: «дивиденд новатэк»." />
            <input bind:value={ed.keywords} placeholder="дивиденд новатэк" />
        </label>
        <label>Какое число брать из новости<InfoDot text="Что мозг ищет в тексте: «дивиденд» (руб. на акцию), «ставка», «нонфарм»… Пусто — по ключевым словам." />
            <input bind:value={ed.event} placeholder="дивиденд" />
        </label>
        {/if}
        <label>Вкладка терминала<InfoDot text="Вкладка T-терминала с виджетами «Заявка» этого наблюдателя. На неё же стоит добавить ленту новостей — Нокс читает её оттуда." />
            <input bind:value={ed.tab} placeholder="нокс (общая рабочая вкладка)" />
        </label>
        {#if ed.kind !== "rate"}
        <label>Мозг<InfoDot text="Модель, которая извлечёт число из новости (список со страницы «Мозг»; пусто — общий выбранный мозг)." />
            <select bind:value={ed.brain}>{#each brains as b}<option value={b.value}>{b.label}</option>{/each}</select>
        </label>
        {/if}
        <label>Следить до (ЧЧ:ММ, пусто = весь день)
            <input bind:value={ed.until} placeholder="19:00" />
        </label>
    </div>
    <label class="chk"><input type="checkbox" bind:checked={ed.once} /> Сработать один раз и выключиться</label>

    <div class="branches">
        {#each ed.branches as b, i}
            <div class="branch" style="border-color:{COLORS[i % COLORS.length]}">
                <div class="bhead">
                    <span class="bname" style="background:{COLORS[i % COLORS.length]}">Ветка {i + 1}</span>
                    <span class="lbl" title={ed.kind === "rate" ? "Изменение ставки в базисных пунктах: снижение — минус (−50 = снижение на 0,5 п.п.), сохранение — 0" : ""}>
                        {ed.kind === "rate" ? "изменение, б.п." : "значение"}</span>
                    <select class="cond" bind:value={b.type}>{#each CONDS as c}<option value={c.v}>{c.l}</option>{/each}</select>
                    {#if ["gt", "ge", "range"].includes(b.type)}<input class="num" bind:value={b.min} placeholder={b.type === "range" ? "от" : "35"} />{/if}
                    {#if ["lt", "le", "range"].includes(b.type)}<input class="num" bind:value={b.max} placeholder={b.type === "range" ? "до" : "35"} />{/if}
                    {#if ed.kind === "rate"}<span class="lbl rate">= {rateCondText(b)}</span>{/if}
                    <button class="btn small danger right" on:click={() => delBranch(i)}>убрать ветку</button>
                </div>
                {#if b.orders.length === 0}<div class="noop">∅ Ничего не делать (только озвучить)</div>{/if}
                {#each b.orders as o, j}
                    <div class="orow" class:cs={o.venue === "cscalp"}>
                        <select class="venue" value={o.venue === "cscalp" ? "cscalp" : "tbank"}
                            on:change={(e) => setVenue(o, e.currentTarget.value)}
                            title="Т-Терминал — виджет «Заявка» на вкладке; CScalp — стакан по тикеру (горячая клавиша, по рынку)">
                            <option value="tbank">Т-Терминал</option><option value="cscalp">CScalp</option>
                        </select>
                        {#if o.venue === "cscalp"}
                            <input class="tk cs" list="cs-tickers" bind:value={o.ticker} placeholder="SILVM-12.26" />
                        {:else}
                            <input class="tk" bind:value={o.ticker} placeholder="NVTK" />
                            <span class="lbl">№</span><input class="nn" type="number" min="1" bind:value={o.n} />
                        {/if}
                        <select class="side" class:buy={o.side === "buy"} class:sell={o.side === "sell"} bind:value={o.side}>
                            <option value="buy">Купить</option><option value="sell">Продать</option>
                        </select>
                        {#if o.venue === "cscalp"}
                            <span class="lbl">по рынку, объём — как стоит в стакане
                                {#if csArmed[o.ticker.toUpperCase()]} · {csArmed[o.ticker.toUpperCase()]}{/if}</span>
                        {:else}
                            <input class="num" bind:value={o.lots} placeholder="лоты" title="Только для «Разложить в терминале»" />
                            <input class="num" bind:value={o.price} placeholder="цена/рынок" title="Пусто — по рынку. Только для «Разложить в терминале»" />
                        {/if}
                        <button class="btn small danger" on:click={() => delOrder(i, j)}>✕</button>
                    </div>
                {/each}
                <div class="oadd">
                    <button class="btn small" on:click={() => addOrder(i)}>+ заявка</button>
                    {#if scanned.length}
                        <select class="fromtab" on:change={(e) => fromTab(i, e)}>
                            <option value="">+ из вкладки…</option>
                            {#each scanned as w, k}<option value={k}>{w.label}</option>{/each}
                        </select>
                    {/if}
                    {#if csDoms.length}
                        <select class="fromtab" on:change={(e) => fromCs(i, e)}>
                            <option value="">+ стакан CScalp…</option>
                            {#each csDoms as d, k}<option value={k}>{d.ticker} · окно «{d.window}»</option>{/each}
                        </select>
                    {/if}
                </div>
            </div>
        {/each}
        <div class="row-actions">
            <button class="btn" on:click={addBranch}>+ ветка</button>
            {#if ed.kind === "rate"}
                <button class="btn" on:click={rateBranches}>+ снижение / сохранение / повышение</button>
                <InfoDot text="Добавит три ветки: любое снижение (< 0), сохранение (= 0), любое повышение (> 0). Пороги можно уточнить: «меньше или равно −50» = снижение на 50 б.п. и больше." />
            {/if}
            <button class="btn" on:click={scan} disabled={busy === "scan"}>{busy === "scan" ? "Читаю…" : "Прочитать вкладку"}</button>
            <InfoDot text="Откроет вкладку в терминале и покажет её виджеты «Заявка» — их можно добавить в ветки из списка «+ из вкладки…». № — порядковый номер виджета с этим тикером слева направо." />
            <button class="btn" on:click={loadCs} disabled={busy === "cs"}>{busy === "cs" ? "Смотрю CScalp…" : "Стаканы CScalp"}</button>
            <InfoDot text="Покажет стаканы, открытые в CScalp (тикер и окно) — их можно добавить в ветки из списка «+ стакан CScalp…». В CScalp Нокс жмёт ваши горячие клавиши над стаканом (T — купить по рынку, Y — продать по рынку), объём — тот, что стоит в стакане. Стаканы можно двигать: перед нажатием Нокс сверяет, что под курсором нужный тикер." />
        </div>
        <datalist id="cs-tickers">{#each csDoms as d}<option value={d.ticker}></option>{/each}</datalist>
    </div>

    {#if ed.branches.length === 0}
        <label>Открыть и подсветить стакан<InfoDot text="Если веток нет — при новости наблюдатель озвучит её и откроет/подсветит стакан из сценария (раздел «Стакан по новости» внизу этой страницы). Без сценария — только озвучит." />
            <select bind:value={ed.scenario_id}>
                <option value="">— только озвучить —</option>
                {#each scenarios as s}<option value={s.id}>{s.name}</option>{/each}
            </select>
        </label>
    {/if}
    <div class="row-actions">
        <button class="btn primary" on:click={saveEd}>{ed.id ? "Сохранить изменения" : "+ Создать наблюдателя"}</button>
        {#if ed.id}<button class="btn" on:click={() => { ed = blank(); scanned = [] }}>Отмена</button>{/if}
    </div>

    <HDivider />

    <h3>Наблюдатели</h3>
    <p class="hint">Голосом: «Нокс, наблюдай дивиденды Новатэка: больше 35 — купи 10 лотов по рынку, от 0 до 35 — ничего,
        не выплатит — продай 5 лотов Новатэка по 1057 и 20 лотов Газпрома по рынку». Нокс создаст ветки и разложит заявки во вкладке.</p>
    <div class="list">
        {#each watchers as w}
            <div class="card" class:off={!w.enabled}>
                <div class="card-top">
                    <div class="card-main">
                        <div class="cmd-id">{w.name} {#if !w.enabled}<span class="badge">выкл</span>{/if}</div>
                        <div class="card-desc">
                            {#if w.kind === "rate"}
                                🏦 {w.bank === "fed" ? "ФРС" : "ЦБ РФ"} · {w.date || "любой день"} с {w.from}
                                {#if w.rate_now !== null && w.rate_now !== undefined} · текущая {w.rate_now}%{/if}
                                · разбор правилами
                            {:else}
                            🔎 {w.keywords.join(" + ")} · {w.until ? "до " + w.until : "весь день"}
                            {/if}
                            {#if w.tab} · вкладка «{w.tab}»{/if}
                            {#if !(w.branches || []).length} · {w.scenario_name ? "сценарий: " + w.scenario_name : "только озвучка"}{/if}
                            {w.once ? " · один раз" : " · постоянно"}
                        </div>
                    </div>
                    <div class="card-actions">
                        <button class="btn small" on:click={() => edit(w)}>Изменить</button>
                        <button class="btn small" on:click={() => toggle(w.id)}>{w.enabled ? "Выкл" : "Вкл"}</button>
                        <button class="btn small danger" on:click={() => del(w.id)}>🗑</button>
                    </div>
                </div>
                {#if (w.branches || []).length}
                    <div class="bsum">
                        {#each w.branches || [] as b, i}
                            <div><span class="dot" style="background:{COLORS[i % COLORS.length]}"></span>
                                <b>Ветка {i + 1}</b> ({w.kind === "rate" ? rateCondText(b) : condText(b)}): {b.orders.length ? b.orders.map(orderText).join(", ") : "ничего не делать"}</div>
                        {/each}
                    </div>
                    <div class="wact">
                        <button class="btn small" on:click={() => act("watch_show", w)} disabled={!!busy}>Показать в терминале</button>
                        <button class="btn small" on:click={() => act("watch_layout", w)} disabled={!!busy}>
                            {busy === "watch_layout" + w.id ? "Раскладываю…" : "Разложить в терминале"}</button>
                        <InfoDot text="Нокс создаст вкладку (если её нет) и добавит недостающие виджеты «Заявка» по веткам — с лотами и ценой из веток. Уже существующие виджеты не трогает. Кнопки не нажимаются." />
                        <input class={w.kind === "rate" ? "dryhead" : "num"} bind:value={dryVal[w.id]}
                            placeholder={w.kind === "rate" ? "−50 или заголовок: ЦБ РФ СНИЗИЛ СТАВКУ НА 50 Б.П." : "значение"} />
                        <button class="btn small" on:click={() => act("watch_dry", w, { value: dryVal[w.id] || "" })} disabled={!!busy}>Проверить</button>
                        <InfoDot text="Без нажатий: какая ветка сработает при этом значении и какие виджеты пройдут проверку (тикер, количество, лимиты). 0 — «не выплачивает»." />
                    </div>
                {/if}
                {#if results[w.id]}<div class="res">{results[w.id]}</div>{/if}
                {#if w.last_result}<div class="res last">Последнее срабатывание: {w.last_result}</div>{/if}
            </div>
        {/each}
        {#if watchers.length === 0}<p class="hint">Пока нет наблюдателей.</p>{/if}
    </div>

    <HDivider />

    <h3>Стакан по новости — сценарий одной строкой</h3>
    <p class="hint">Опишите правило словами → «Разобрать» → задайте точку стакана для каждой ветки (мышь + F2) → сохранить.
        По новости Нокс откроет и подсветит нужный стакан и скажет вслух. Срабатывает один раз.</p>
    <ScenariosPanel />
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
    .brief { flex: 1; min-width: 240px; margin-top: 0 !important; }
    label { display: block; font-size: 0.78rem; color: rgba(223,239,240,0.75); margin-bottom: 0.4rem; }
    .chk { display: flex; align-items: center; gap: 0.4rem; margin: 0.4rem 0 0.6rem; }
    .chk input { width: auto; margin: 0; }
    input, select {
        width: 100%; margin-top: 0.28rem; padding: 0.45rem 0.55rem;
        background: rgba(8,16,19,0.85); border: 1px solid rgba(82,254,254,0.2);
        border-radius: 7px; color: #eafeff; font-size: 0.83rem; box-sizing: border-box;
    }
    input:focus, select:focus { outline: none; border-color: #2fe6e6; }

    .branches { display: flex; flex-direction: column; gap: 0.55rem; margin: 0.4rem 0 0.6rem; }
    .branch { border: 1px solid; border-left-width: 4px; border-radius: 10px; padding: 0.55rem 0.7rem; background: rgba(15,26,30,0.55); }
    .bhead { display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap; }
    .bhead input, .bhead select, .orow input, .orow select, .oadd select, .wact input { margin-top: 0; width: auto; }
    .bname { color: #fff; font-weight: 700; font-size: 0.78rem; border-radius: 6px; padding: 0.15rem 0.5rem; }
    .lbl { font-size: 0.75rem; color: rgba(223,239,240,0.6); }
    .cond { min-width: 170px; }
    .num { width: 92px !important; }
    .right { margin-left: auto; }
    .noop { font-size: 0.8rem; color: #94a3b8; margin: 0.45rem 0 0.2rem; }
    .orow { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.4rem; flex-wrap: wrap; }
    .tk { width: 90px !important; text-transform: uppercase; }
    .tk.cs { width: 130px !important; }
    .venue { width: 118px !important; }
    .lbl.rate { color: #fde68a; }
    .dryhead { flex: 1; min-width: 260px; }
    .orow.cs .venue { border-color: #a855f7; color: #e9d5ff; }
    .nn { width: 58px !important; }
    .side.buy { border-color: #22c55e; color: #86efac; }
    .side.sell { border-color: #ef4444; color: #fca5a5; }
    .oadd { display: flex; gap: 0.4rem; margin-top: 0.45rem; align-items: center; }
    .fromtab { max-width: 340px; }
    .row-actions { display: flex; gap: 0.5rem; align-items: center; margin: 0.3rem 0; flex-wrap: wrap; }

    .btn { padding: 0.45rem 0.85rem; background: rgba(35,50,55,0.7);
        border: 1px solid rgba(82,254,254,0.2); border-radius: 8px; color: #cdeff0;
        font-size: 0.8rem; cursor: pointer; transition: all 0.15s ease; }
    .btn:hover { background: rgba(82,254,254,0.12); }
    .btn:disabled { opacity: 0.5; cursor: default; }
    .btn.primary { background: rgba(47,230,230,0.18); border-color: #2fe6e6; color: #eafeff; }
    .btn.small { padding: 0.3rem 0.55rem; }
    .btn.danger { color: #ffb4b4; border-color: rgba(255,80,80,0.35); }

    .list { display: flex; flex-direction: column; gap: 0.55rem; margin: 0.4rem 0 0.8rem; }
    .card { background: rgba(15,26,30,0.6); border: 1px solid rgba(82,254,254,0.12); border-radius: 10px; padding: 0.65rem 0.9rem; }
    .card.off { opacity: 0.6; }
    .card-top { display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; }
    .card-main { min-width: 0; }
    .cmd-id { font-weight: 600; color: #eafeff; font-size: 0.9rem; }
    .badge { font-size: 0.68rem; color: #ffb4b4; border: 1px solid rgba(255,80,80,0.35); border-radius: 5px; padding: 0 0.3rem; margin-left: 0.3rem; }
    .card-desc { font-size: 0.76rem; color: rgba(223,239,240,0.6); margin-top: 0.2rem; word-break: break-word; }
    .card-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }
    .bsum { margin-top: 0.45rem; font-size: 0.78rem; color: rgba(223,239,240,0.85); display: flex; flex-direction: column; gap: 0.2rem; }
    .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 0.35rem; }
    .wact { display: flex; gap: 0.4rem; align-items: center; margin-top: 0.5rem; flex-wrap: wrap; }
    .res { margin-top: 0.45rem; font-size: 0.78rem; color: #cdeff0; background: rgba(47,230,230,0.08);
        border: 1px solid rgba(47,230,230,0.25); border-radius: 7px; padding: 0.35rem 0.55rem; }
    .res.last { color: #fde68a; background: rgba(245,158,11,0.08); border-color: rgba(245,158,11,0.3); }
</style>

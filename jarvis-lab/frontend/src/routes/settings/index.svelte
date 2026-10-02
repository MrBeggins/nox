<script lang="ts">
    import { onMount } from "svelte"
    import { invoke } from "@tauri-apps/api/core"
    import { goto } from "@roxi/routify"
    import { setTimeout } from "worker-timers"

    import { showInExplorer } from "@/functions"
    import { appInfo, assistantVoice, translations, translate } from "@/stores"


    import {
        Notification,
        Button,
        Text,
        Tabs,
        Space,
        Alert,
        Input,
        InputWrapper,
        NativeSelect,
        Switch
    } from "@svelteuidev/core"

    import {
        Check,
        Mix,
        Cube,
        Code,
        Gear,
        QuestionMarkCircled,
        CrossCircled
    } from "radix-icons-svelte"

    $: t = (key: string) => translate($translations, key)

    interface VoiceMeta {
        id: string
        name: string
        author: string
        languages: string[]
    }

    interface VoiceConfig {
        voice: VoiceMeta
    }
    
    let availableVoices: VoiceMeta[] = []

    async function selectVoice(voiceId: string) {
        voiceVal = voiceId
        
        // play preview sound
        try {
            await invoke("preview_voice", { voiceId })
        } catch (err) {
            console.error("Failed to preview voice:", err)
        }
    }

    // ### STATE
    interface MicrophoneOption {
        label: string
        value: string
    }

    let availableMicrophones: MicrophoneOption[] = []
    let availableVoskModels: { label: string; value: string }[] = []
    let availableGlinerModels: { label: string; value: string }[] = []
    let settingsSaved = false
    let saveButtonDisabled = false

    // form values (state vars)
    let voiceVal = ""
    let selectedMicrophone = ""
    let selectedWakeWordEngine = ""
    let selectedIntentRecognitionEngine = ""
    let selectedSlotExtractionEngine = ""
    let selectedGlinerModel = ""
    let selectedVoskModel = ""
    let selectedNoiseSuppression = ""
    let selectedVad = ""
    let gainNormalizerEnabled = false
    let apiKeyPicovoice = ""
    let apiKeyOpenai = ""

    // ### НОВОСТИ / СЛЕЖКА / АКТИВАЦИЯ
    let wakeWord = ""
    let wakeWordEnabled = true
    let wakeSaved = false
    let newsMonOn = false
    let smartOn = false
    let filter: { tickers: string[]; phrases: string[]; sources: string[] } = { tickers: [], phrases: [], sources: [] }
    let filterError = ""
    let addTicker = ""
    let addPhrase = ""
    let addSource = ""

    // активация вручную + оверлей
    let activationHotkey = "ctrl_alt_j"
    let overlayEnabled = false
    const hotkeyOptions = [
        { label: "Ctrl + Alt + J", value: "ctrl_alt_j" },
        { label: "Ctrl + Alt + Пробел", value: "ctrl_alt_space" },
        { label: "Правый Ctrl", value: "right_ctrl" },
        { label: "Правый Shift", value: "right_shift" },
        { label: "Pause / Break", value: "pause" },
        { label: "F8", value: "f8" },
        { label: "F9", value: "f9" },
        { label: "Боковая кнопка мыши 1", value: "mouse_x1" },
        { label: "Боковая кнопка мыши 2", value: "mouse_x2" },
    ]

    async function saveActivation() {
        try {
            await invoke("db_write", { key: "activation_hotkey", val: activationHotkey })
            // применить в запущенном ассистенте без перезапуска
            try { const { reloadSettings } = await import("@/stores"); reloadSettings() } catch (e) {}
            wakeSaved = true
            setTimeout(() => { wakeSaved = false }, 4000)
        } catch (e) { console.error(e) }
    }

    async function toggleOverlay() {
        overlayEnabled = !overlayEnabled
        try {
            await invoke("db_write", { key: "overlay_enabled", val: overlayEnabled ? "true" : "false" })
            await invoke("set_overlay_visible", { visible: overlayEnabled })
        } catch (e) { console.error(e) }
    }

    // ### МАГЕЛЛАН (order flow)
    let magTickers: string[] = []
    let magMon = false
    let magImb = 15
    let magPrice = 2.0
    let magTurn = 300
    let magAdd = ""
    let magError = ""

    async function loadMagellan() {
        try {
            const f = parseReader(await invoke<string>("magellan_filter_get"))
            if (f.filter) {
                magTickers = f.filter.tickers || []
                magImb = f.filter.min_imbalance ?? 15
                magPrice = f.filter.min_price ?? 2.0
                magTurn = f.filter.min_turnover ?? 300
            }
            magMon = !!f.mon
            magError = ""
        } catch (e) { magError = "Сервис Магеллана офлайн (запустите magellan_reader)" }
    }
    async function magRefresh() {
        try { const f = parseReader(await invoke<string>("magellan_filter_get")); if (f.filter) magTickers = f.filter.tickers || [] } catch (e) {}
    }
    async function magAddTicker() {
        if (!magAdd.trim()) return
        try { await invoke("magellan_add", { value: magAdd.trim() }); magAdd = ""; await magRefresh() }
        catch (e) { magError = String(e) }
    }
    async function magRemoveTicker(v: string) {
        try { await invoke("magellan_remove", { value: v }); await magRefresh() } catch (e) { magError = String(e) }
    }
    async function magClearAll() {
        try { await invoke("magellan_clear"); await magRefresh() } catch (e) { magError = String(e) }
    }
    async function magToggleMon() {
        try { const r = parseReader(await invoke<string>("magellan_mon", { on: magMon ? 0 : 1 })); magMon = !!r.on }
        catch (e) { magError = String(e) }
    }
    async function magSaveThresholds() {
        try { await invoke("magellan_settings", { minImbalance: magImb, minPrice: magPrice, minTurnover: magTurn }) }
        catch (e) { magError = String(e) }
    }

    // ### СЦЕНАРИИ (авто-клик стакана по новости)
    let scnList: any[] = []
    let scnBrain = "ollama"
    let scnHasClaude = false
    let scnHasOpenai = false
    let claudeKey = ""
    let openaiKey = ""
    let scnText = ""           // описание нового сценария (NL)
    let parsed: any = null     // разобранный сценарий (настраиваем координаты)
    let scnMsg = ""
    let capturing = -1         // индекс ветки в режиме захвата F2

    async function loadScn() {
        try {
            const r = parseReader(await invoke<string>("scn_list"))
            scnList = r.scenarios || []
            scnBrain = r.brain || "ollama"
            scnHasClaude = !!r.has_claude
            scnHasOpenai = !!r.has_openai
        } catch (e) { /* reader offline */ }
    }
    async function doParse() {
        scnMsg = "Разбираю…"
        try {
            const r = parseReader(await invoke<string>("scn_parse", { text: scnText }))
            if (r.ok) { parsed = r.scenario; scnMsg = "Задайте координаты для каждой ветки (F2)." }
            else { parsed = null; scnMsg = r.text || "Не понял сценарий." }
        } catch (e) { scnMsg = "Сервис сценариев офлайн." }
    }
    async function captureCoord(i: number) {
        capturing = i
        scnMsg = "Наведи мышь на нужный стакан и нажми F2…"
        let base = 0
        try { base = (parseReader(await invoke<string>("scn_capture")) || {}).ts || 0 } catch (e) {}
        const t0 = Date.now()
        const poll = async () => {
            if (capturing !== i) return
            try {
                const c = parseReader(await invoke<string>("scn_capture")) || {}
                if (c.ts && c.ts > base) {
                    parsed.branches[i].x = c.x; parsed.branches[i].y = c.y
                    parsed = parsed; capturing = -1; scnMsg = `Ветка «${parsed.branches[i].label}»: точка (${c.x}, ${c.y}).`
                    return
                }
            } catch (e) {}
            if (Date.now() - t0 > 20000) { capturing = -1; scnMsg = "Захват отменён (нет F2)."; return }
            setTimeout(poll, 300)
        }
        poll()
    }
    async function showPoint(x: number, y: number) {
        try { await invoke("scn_show", { x: String(x), y: String(y) }) } catch (e) {}
    }
    async function showAll(id: string) {
        try { await invoke("scn_show_all", { id }) } catch (e) {}
    }
    async function testScn(s: any) {
        try {
            const r = parseReader(await invoke<string>("scn_test", { id: s.id, value: String(s._tv ?? "") }))
            s._tmsg = (r && r.text) ? r.text : "готово"; scnList = scnList
        } catch (e) { s._tmsg = "ошибка теста"; scnList = scnList }
    }
    async function saveScn() {
        if (!parsed) return
        // координаты нужны только веткам с ДЕЙСТВИЕМ (noop — без клика)
        if (parsed.branches.some((b: any) => !b.noop && b.x == null)) { scnMsg = "Задайте координаты веткам с действием (кроме «без действий»)."; return }
        try {
            await invoke("scn_save", { jsonData: JSON.stringify(parsed) })
            parsed = null; scnText = ""; scnMsg = "Сценарий сохранён."
            await loadScn()
        } catch (e) { scnMsg = "Не удалось сохранить." }
    }
    async function delScn(id: string) { try { await invoke("scn_del", { id }); await loadScn() } catch (e) {} }
    async function toggleScn(id: string) { try { await invoke("scn_toggle", { id }); await loadScn() } catch (e) {} }
    async function setSchedule(s: any) {
        try {
            await invoke("scn_schedule", { id: s.id, dt: s._dt || "", prewarm: String(s._pre || 3) })
            await loadScn()
        } catch (e) {}
    }
    async function saveBrain() {
        try {
            const r = parseReader(await invoke<string>("scn_brain",
                { brain: scnBrain, claudeKey: claudeKey || "__keep__", openaiKey: openaiKey || "__keep__" }))
            scnHasClaude = !!r.has_claude; scnHasOpenai = !!r.has_openai; claudeKey = ""; openaiKey = ""
            scnMsg = "Мозг сценариев сохранён."
        } catch (e) { scnMsg = "Не удалось сохранить мозг." }
    }

    // Полная остановка Nox (в два клика)
    let stopConfirm = false
    async function stopNox() {
        if (!stopConfirm) { stopConfirm = true; setTimeout(() => stopConfirm = false, 4000); return }
        try { await invoke("stop_nox") } catch (e) { console.error(e) }
    }

    function parseReader(raw: string): any { try { return JSON.parse(raw) } catch { return {} } }

    async function loadNews() {
        try {
            wakeWord = await invoke<string>("db_read", { key: "wake_word" })
            const we = await invoke<string>("db_read", { key: "wake_word_enabled" })
            wakeWordEnabled = we !== "false"
            const hk = await invoke<string>("db_read", { key: "activation_hotkey" })
            if (hk && hk.trim()) activationHotkey = hk.trim()
            const ov = await invoke<string>("db_read", { key: "overlay_enabled" })
            overlayEnabled = ov === "true"
        } catch (e) { console.error(e) }
        try {
            const f = parseReader(await invoke<string>("reader_filter_get"))
            if (f.filter) { filter = { tickers: f.filter.tickers||[], phrases: f.filter.phrases||[], sources: f.filter.sources||[] }; smartOn = !!f.filter.smart }
            const m = parseReader(await invoke<string>("reader_mon", { on: -1 }))
            newsMonOn = !!m.on
            filterError = ""
        } catch (e) { filterError = "Ридер новостей офлайн (запустите terminal_reader)" }
    }

    async function refreshFilter() {
        try {
            const f = parseReader(await invoke<string>("reader_filter_get"))
            if (f.filter) filter = { tickers: f.filter.tickers||[], phrases: f.filter.phrases||[], sources: f.filter.sources||[] }
        } catch (e) { console.error(e) }
    }

    async function filterAdd(kind: string, value: string) {
        if (!value.trim()) return
        try { await invoke("reader_filter_add", { kind, value: value.trim() }); await refreshFilter() }
        catch (e) { filterError = String(e) }
    }
    async function filterRemove(kind: string, value: string) {
        try { await invoke("reader_filter_remove", { kind, value }); await refreshFilter() }
        catch (e) { filterError = String(e) }
    }
    async function filterClear(kind: string) {
        try { await invoke("reader_filter_clear", { kind }); await refreshFilter() }
        catch (e) { filterError = String(e) }
    }
    async function toggleMon() {
        try { const r = parseReader(await invoke<string>("reader_mon", { on: newsMonOn ? 0 : 1 })); newsMonOn = !!r.on }
        catch (e) { filterError = String(e) }
    }
    async function toggleSmart() {
        try { const r = parseReader(await invoke<string>("reader_smart", { on: smartOn ? 0 : 1 })); smartOn = !!r.smart }
        catch (e) { filterError = String(e) }
    }
    async function saveWake() {
        try {
            await invoke("db_write", { key: "wake_word", val: wakeWord.trim().toLowerCase() })
            await invoke("db_write", { key: "wake_word_enabled", val: wakeWordEnabled ? "true" : "false" })
            wakeSaved = true
            setTimeout(() => { wakeSaved = false }, 4000)
        } catch (e) { console.error(e) }
    }

    // subscribe to stores
    assistantVoice.subscribe(value => {
        voiceVal = value
    })

    let feedbackLink = ""
    let logFilePath = ""
    appInfo.subscribe(info => {
        feedbackLink = info.feedbackLink
        logFilePath = info.logFilePath
    })

    // ### FUNCTIONS
    async function saveSettings() {
        saveButtonDisabled = true
        settingsSaved = false

        try {
            // derive the single backend key the engine actually reads (slots_backend):
            // "none" when disabled, otherwise the GLiNER model id. Empty model = auto-detect first available.
            let slotsBackend = "none"
            if (selectedSlotExtractionEngine === "GLiNER") {
                let modelId = selectedGlinerModel
                if (!modelId && availableGlinerModels.length > 0) {
                    modelId = availableGlinerModels[0].value
                }
                slotsBackend = modelId || "none"
            }

            await Promise.all([
                invoke("db_write", { key: "assistant_voice", val: voiceVal }),
                invoke("db_write", { key: "selected_microphone", val: selectedMicrophone }),
                invoke("db_write", { key: "selected_wake_word_engine", val: selectedWakeWordEngine }),
                invoke("db_write", { key: "selected_intent_recognition_engine", val: selectedIntentRecognitionEngine }),
                invoke("db_write", { key: "selected_slot_extraction_engine", val: selectedSlotExtractionEngine }),
                invoke("db_write", { key: "selected_gliner_model", val: selectedGlinerModel }),
                invoke("db_write", { key: "slots_backend", val: slotsBackend }),
                invoke("db_write", { key: "selected_vosk_model", val: selectedVoskModel }),

                invoke("db_write", { key: "noise_suppression", val: selectedNoiseSuppression }),
                invoke("db_write", { key: "vad", val: selectedVad }),
                invoke("db_write", { key: "gain_normalizer", val: gainNormalizerEnabled.toString() }),

                invoke("db_write", { key: "api_key__picovoice", val: apiKeyPicovoice }),
                invoke("db_write", { key: "api_key__openai", val: apiKeyOpenai })
            ])

            // update shared store
            assistantVoice.set(voiceVal)
            settingsSaved = true

            // hide alert after 5 seconds
            setTimeout(() => {
                settingsSaved = false
            }, 5000)

            // restart listening with new settings
            // stopListening(() => startListening())
        } catch (err) {
            console.error("failed to save settings:", err)
        }

        setTimeout(() => {
            saveButtonDisabled = false
        }, 1000)
    }

    // ### INIT
    onMount(async () => {
        loadNews()
        loadMagellan()
        loadScn()
        // load voices
        try {
            const voices = await invoke<VoiceConfig[]>("list_voices")
            availableVoices = voices.map(v => v.voice)
        } catch (err) {
            console.error("Failed to load voices:", err)
            availableVoices = []
        }

        try {
            // load microphones
            const mics = await invoke<string[]>("pv_get_audio_devices")
            availableMicrophones = [
                { label: t('settings-mic-default'), value: "-1" },  // system default
                ...mics.map((name, idx) => ({
                    label: name,
                    value: String(idx)
                }))
            ]

            // load vosk models
            const languageNames: Record<string, string> = {
                us: 'English',
                ru: 'Русский',
                uk: 'Українська',
                de: 'German',
                fr: 'French',
                es: 'Spanish',
                // ..
            };
            const voskModels = await invoke<{ name: string; language: string; size: string }[]>("list_vosk_models")
            availableVoskModels = voskModels.map(m => ({
                label: `${m.name} (${languageNames[m.language] ?? m.language}, ${m.size})`,
                value: m.name
            }))

            // load gliner models
            const glinerModels = await invoke<{ display_name: string; value: string }[]>("list_gliner_models")
            availableGlinerModels = glinerModels.map(m => ({
                label: m.display_name,
                value: m.value,
            }))

            // load settings from db
            const [mic, wakeWord, intentReco, slotEngine, glinerModel, voskModel,
                   noiseSuppression, vad, gainNormalizer,
                   pico, openai] = await Promise.all([
                invoke<string>("db_read", { key: "selected_microphone" }),
                invoke<string>("db_read", { key: "selected_wake_word_engine" }),
                invoke<string>("db_read", { key: "selected_intent_recognition_engine" }),
                invoke<string>("db_read", { key: "selected_slot_extraction_engine" }),
                invoke<string>("db_read", { key: "selected_gliner_model" }),
                invoke<string>("db_read", { key: "selected_vosk_model" }),

                invoke<string>("db_read", { key: "noise_suppression" }),
                invoke<string>("db_read", { key: "vad" }),
                invoke<string>("db_read", { key: "gain_normalizer" }),

                invoke<string>("db_read", { key: "api_key__picovoice" }),
                invoke<string>("db_read", { key: "api_key__openai" })
            ])

            selectedMicrophone = mic
            selectedWakeWordEngine = wakeWord
            selectedIntentRecognitionEngine = intentReco
            selectedSlotExtractionEngine = slotEngine
            selectedVoskModel = voskModel
            selectedGlinerModel = glinerModel
            selectedNoiseSuppression = noiseSuppression
            selectedVad = vad
            gainNormalizerEnabled = gainNormalizer === "true"
            apiKeyPicovoice = pico
            apiKeyOpenai = openai
        } catch (err) {
            console.error("failed to load settings:", err)
        }
    })
</script>

<Space h="xl" />

{#if settingsSaved}
    <Notification
        title={t('notification-saved')}
        icon={Check}
        color="teal"
        on:close={() => { settingsSaved = false }}
    />
    <Space h="xl" />
{/if}

<Tabs class="form" color="#35e0d0" position="left">
    <Tabs.Tab label={t('settings-general')} icon={Gear}>
        <Space h="sm" />
        <div class="voice-select">
            <label>{t('settings-voice')}</label>
            <p class="description">{t('settings-voice-desc')}</p>
            
            <div class="voice-options">
                {#each availableVoices as voice}
                    <button 
                        type="button"
                        class="voice-option"
                        class:selected={voiceVal === voice.id}
                        on:click={() => selectVoice(voice.id)}
                    >
                        <div class="voice-info">
                            <span class="voice-name">{voice.name}</span>
                            {#if voice.author}
                                <span class="voice-author">by {voice.author}</span>
                            {/if}
                        </div>
                        <div class="voice-languages">
                            {#each voice.languages.filter(l => l.toLowerCase() === "ru") as lang}
                                <img
                                    src="/media/flags/{lang.toUpperCase()}.png"
                                    alt={lang}
                                    width="20"
                                    title={lang}
                                />
                            {/each}
                        </div>
                    </button>
                {/each}
                
                {#if availableVoices.length === 0}
                    <p class="no-voices">{t('settings-no-voices')}</p>
                {/if}
            </div>
        </div>
    </Tabs.Tab>

    <Tabs.Tab label={t('settings-devices')} icon={Mix}>
        <Space h="sm" />
        <NativeSelect
            data={availableMicrophones}
            label={t('settings-microphone')}
            description={t('settings-microphone-desc')}
            variant="filled"
            bind:value={selectedMicrophone}
        />
    </Tabs.Tab>

    <Tabs.Tab label={t('settings-neural-networks')} icon={Cube}>
        <Space h="sm" />
        <NativeSelect
            data={[
                { label: "Rustpotter", value: "Rustpotter" },
                { label: "Vosk", value: "Vosk" },
                { label: "Picovoice Porcupine", value: "Picovoice" }
            ]}
            label={t('settings-wake-word-engine')}
            description={t('settings-wake-word-desc')}
            variant="filled"
            bind:value={selectedWakeWordEngine}
        />

        {#if selectedWakeWordEngine === "picovoice"}
            <Space h="sm" />
            <Alert title={t('settings-attention')} color="#868E96" variant="outline">
                <Notification
                    title={t('settings-picovoice-warning')}
                    icon={CrossCircled}
                    color="orange"
                    withCloseButton={false}
                >
                    {t('settings-picovoice-waiting')}
                </Notification>
                <Space h="sm" />
                <Text size="sm" color="gray">
                    {t('settings-picovoice-key-desc')}
                    <a href="https://console.picovoice.ai/" target="_blank">Picovoice Console</a>.
                </Text>
                <Space h="sm" />
                <Input
                    icon={Code}
                    placeholder={t('settings-picovoice-key')}
                    variant="filled"
                    autocomplete="off"
                    bind:value={apiKeyPicovoice}
                />
            </Alert>
        {/if}

        <Space h="xl" />
        {#key availableVoskModels}
        <NativeSelect
            data={[
                { label: t('settings-auto-detect'), value: "" },
                ...availableVoskModels
            ]}
            label={t('settings-vosk-model')}
            description={t('settings-vosk-model-desc')}
            variant="filled"
            bind:value={selectedVoskModel}
        />
        {/key}

        {#if availableVoskModels.length === 0}
            <Space h="sm" />
            <Alert title={t('settings-models-not-found')} color="orange" variant="outline">
                <Text size="sm" color="gray">
                    {t('settings-models-hint')}
                </Text>
            </Alert>
        {/if}

        <Space h="xl" />
        <NativeSelect
            data={[
                { label: "Intent Classifier", value: "IntentClassifier" },
                { label: "Embedding Classifier", value: "EmbeddingClassifier" }
            ]}
            label={t('settings-intent-engine')}
            description={t('settings-intent-engine-desc')}
            variant="filled"
            bind:value={selectedIntentRecognitionEngine}
        />

        <Space h="xl" />
        <NativeSelect
            data={[
                { label: t('settings-disabled'), value: "None" },
                { label: "GLiNER (NER)", value: "GLiNER" }
            ]}
            label={t('settings-slot-engine')}
            description={t('settings-slot-engine-desc')}
            variant="filled"
            bind:value={selectedSlotExtractionEngine}
        />

        {#if selectedSlotExtractionEngine === "GLiNER"}
            <Space h="sm" />
            {#key availableGlinerModels}
            <NativeSelect
                data={[
                    { label: t('settings-auto-detect'), value: "" },
                    ...availableGlinerModels
                ]}
                label={t('settings-gliner-model')}
                description={t('settings-gliner-model-desc')}
                variant="filled"
                bind:value={selectedGlinerModel}
            />
            {/key}

            {#if availableGlinerModels.length === 0}
                <Space h="sm" />
                <Alert title={t('settings-models-not-found')} color="orange" variant="outline">
                    <Text size="sm" color="gray">
                        {t('settings-gliner-models-hint')}
                    </Text>
                </Alert>
            {/if}
        {/if}

        <Space h="xl" />
        <NativeSelect
            data={[
                { label: t('settings-disabled'), value: "None" },
                { label: "Nnnoiseless", value: "Nnnoiseless" }
            ]}
            label={t('settings-noise-suppression')}
            description={t('settings-noise-suppression-desc')}
            variant="filled"
            bind:value={selectedNoiseSuppression}
        />

        <Space h="md" />

        <NativeSelect
            data={[
                { label: t('settings-disabled'), value: "None" },
                { label: "Energy", value: "Energy" },
                { label: "Nnnoiseless", value: "Nnnoiseless" }
            ]}
            label={t('settings-vad')}
            description={t('settings-vad-desc')}
            variant="filled"
            bind:value={selectedVad}
        />

        <Space h="md" />

        <InputWrapper label={t('settings-gain-normalizer')}>
            <Text size="sm" color="gray">
                {t('settings-gain-normalizer-desc')}
            </Text>
            <Space h="xs" />
            <Switch
                label={gainNormalizerEnabled ? t('settings-enabled') : t('settings-disabled')}
                bind:checked={gainNormalizerEnabled}
            />
        </InputWrapper>

        <Space h="xl" />

        <InputWrapper label={t('settings-openai-key')}>
            <Text size="sm" color="gray">
                {t('settings-openai-not-supported')}
            </Text>
            <Space h="sm" />
            <Input
                icon={Code}
                placeholder={t('settings-openai-key')}
                variant="filled"
                autocomplete="off"
                bind:value={apiKeyOpenai}
                disabled
            />
        </InputWrapper>
    </Tabs.Tab>

    <Tabs.Tab label="Новости и слежка" icon={Mix}>
        <Space h="sm" />

        <div class="nx-block">
            <label class="nx-lab">Слово активации</label>
            <p class="nx-desc">Каким словом звать ассистента голосом. Применяется после перезапуска ассистента (кнопка «Запустить» на Пульсе).</p>
            <div class="nx-row">
                <input class="nx-in" placeholder="нокс" bind:value={wakeWord} />
                <label class="nx-sw"><input type="checkbox" bind:checked={wakeWordEnabled} /> реагировать на слово</label>
                <button class="nx-btn" on:click={saveWake}>Сохранить</button>
            </div>
            {#if wakeSaved}<p class="nx-ok">Сохранено. Перезапустите ассистента, чтобы слово вступило в силу.</p>{/if}
            <p class="nx-hint">Если слово выключено — активация только хоткеем/кнопкой.</p>
        </div>

        <div class="nx-block">
            <label class="nx-lab">Клавиша активации вручную</label>
            <p class="nx-desc">Нажми и держи — Nox начнёт слушать команду сразу, без слова «нокс». Работает даже при выключенном микрофоне и без голосового ввода.</p>
            <div class="nx-row">
                <select class="nx-in" bind:value={activationHotkey}>
                    {#each hotkeyOptions as o}<option value={o.value}>{o.label}</option>{/each}
                </select>
                <button class="nx-btn" on:click={saveActivation}>Применить</button>
            </div>
        </div>

        <div class="nx-block">
            <div class="nx-row">
                <label class="nx-lab" style="margin:0">Оверлей-шар поверх окон</label>
                <label class="nx-sw" style="margin-left:auto"><input type="checkbox" checked={overlayEnabled} on:change={toggleOverlay} /> {overlayEnabled ? "показан" : "скрыт"}</label>
            </div>
            <p class="nx-desc">Полупрозрачный шар в углу экрана: ярче при обращении, показывает короткий ответ/напоминание. Клики проходят сквозь него.</p>
        </div>

        <div class="nx-block">
            <div class="nx-row">
                <label class="nx-lab" style="margin:0">Автоматическое чтение ленты</label>
                <label class="nx-sw" style="margin-left:auto"><input type="checkbox" checked={newsMonOn} on:change={toggleMon} /> {newsMonOn ? "читает" : "выключено"}</label>
            </div>
            <p class="nx-desc">Nox следит за лентой Trading Tools и озвучивает только то, что попадает под фильтр ниже.</p>
        </div>

        <div class="nx-block">
            <div class="nx-row">
                <label class="nx-lab" style="margin:0">Умный отбор (Ollama, локально)</label>
                <label class="nx-sw" style="margin-left:auto"><input type="checkbox" checked={smartOn} on:change={toggleSmart} /> {smartOn ? "вкл" : "выкл"}</label>
            </div>
            <p class="nx-desc">Локальная модель сама решает, важна ли новость для инвестора, и озвучивает только суть в пару слов. Работает без интернета и бесплатно. Когда включено — фильтр по тикерам/фразам ниже не используется.</p>
        </div>

        {#if filterError}<div class="nx-err">{filterError}</div>{/if}

        <div class="nx-block">
            <label class="nx-lab">Тикеры</label>
            <div class="chips">
                {#each filter.tickers as v}
                    <span class="chip tk">{v}<button on:click={() => filterRemove("ticker", v)}>✕</button></span>
                {/each}
                {#if !filter.tickers.length}<span class="chip-empty">пусто</span>{/if}
            </div>
            <div class="nx-row">
                <input class="nx-in" placeholder="SBER, GAZP…" bind:value={addTicker}
                    on:keydown={(e) => { if (e.key==='Enter') { filterAdd('ticker', addTicker); addTicker='' } }} />
                <button class="nx-btn" on:click={() => { filterAdd('ticker', addTicker); addTicker='' }}>Добавить</button>
                {#if filter.tickers.length}<button class="nx-clr" on:click={() => filterClear('ticker')}>очистить</button>{/if}
            </div>
        </div>

        <div class="nx-block">
            <label class="nx-lab">Ключевые фразы</label>
            <div class="chips">
                {#each filter.phrases as v}
                    <span class="chip ph">{v}<button on:click={() => filterRemove("phrase", v)}>✕</button></span>
                {/each}
                {#if !filter.phrases.length}<span class="chip-empty">пусто</span>{/if}
            </div>
            <div class="nx-row">
                <input class="nx-in" placeholder="дивиденды, оферта, buyback…" bind:value={addPhrase}
                    on:keydown={(e) => { if (e.key==='Enter') { filterAdd('phrase', addPhrase); addPhrase='' } }} />
                <button class="nx-btn" on:click={() => { filterAdd('phrase', addPhrase); addPhrase='' }}>Добавить</button>
                {#if filter.phrases.length}<button class="nx-clr" on:click={() => filterClear('phrase')}>очистить</button>{/if}
            </div>
        </div>

        <div class="nx-block">
            <label class="nx-lab">Источники</label>
            <div class="chips">
                {#each filter.sources as v}
                    <span class="chip src">{v}<button on:click={() => filterRemove("source", v)}>✕</button></span>
                {/each}
                {#if !filter.sources.length}<span class="chip-empty">пусто</span>{/if}
            </div>
            <div class="nx-row">
                <input class="nx-in" placeholder="НКЦ, e-disclosure, Мосбиржа…" bind:value={addSource}
                    on:keydown={(e) => { if (e.key==='Enter') { filterAdd('source', addSource); addSource='' } }} />
                <button class="nx-btn" on:click={() => { filterAdd('source', addSource); addSource='' }}>Добавить</button>
                {#if filter.sources.length}<button class="nx-clr" on:click={() => filterClear('source')}>очистить</button>{/if}
            </div>
        </div>
    </Tabs.Tab>

    <Tabs.Tab label="Магеллан" icon={Cube}>
        <Space h="sm" />

        <div class="nx-block">
            <div class="nx-row">
                <label class="nx-lab" style="margin:0">Слежка за Order Flow (Russian Magellan)</label>
                <label class="nx-sw" style="margin-left:auto"><input type="checkbox" checked={magMon} on:change={magToggleMon} /> {magMon ? "следит" : "выключено"}</label>
            </div>
            <p class="nx-desc">Nox читает поток сделок MOEX с russianmagellan.pro и уведомляет по выбранным бумагам: перекос покупатели/продавцы, резкое движение цены, крупный игрок.</p>
        </div>

        {#if magError}<div class="nx-err">{magError}</div>{/if}

        <div class="nx-block">
            <label class="nx-lab">Бумаги под наблюдением</label>
            <div class="chips">
                {#each magTickers as v}
                    <span class="chip tk">{v}<button on:click={() => magRemoveTicker(v)}>✕</button></span>
                {/each}
                {#if !magTickers.length}<span class="chip-empty">пусто — добавьте тикеры</span>{/if}
            </div>
            <div class="nx-row">
                <input class="nx-in" placeholder="SMLT, SBER, LKOH…" bind:value={magAdd}
                    on:keydown={(e) => { if (e.key==='Enter') magAddTicker() }} />
                <button class="nx-btn" on:click={magAddTicker}>Добавить</button>
                {#if magTickers.length}<button class="nx-clr" on:click={magClearAll}>очистить</button>{/if}
            </div>
        </div>

        <div class="nx-block">
            <label class="nx-lab">Пороги уведомлений</label>
            <div class="nx-row" style="gap:16px; flex-wrap:wrap">
                <label class="nx-fld">Дисбаланс, %<input class="nx-num" type="number" min="1" max="50" bind:value={magImb} on:change={magSaveThresholds} /></label>
                <label class="nx-fld">Движение цены, %<input class="nx-num" type="number" min="0.1" step="0.1" bind:value={magPrice} on:change={magSaveThresholds} /></label>
                <label class="nx-fld">Мин. оборот, млн ₽<input class="nx-num" type="number" min="0" step="50" bind:value={magTurn} on:change={magSaveThresholds} /></label>
            </div>
            <p class="nx-hint">Уведомление сработает, если по бумаге под наблюдением перекос ≥ дисбаланса ИЛИ движение цены ≥ порога (при обороте выше минимума). Повтор — не чаще раза в 20 минут.</p>
        </div>
    </Tabs.Tab>

    <Tabs.Tab label="Сценарии" icon={Code}>
        <Space h="sm" />
        <div class="nx-block">
            <label class="nx-lab">Авто-клик стакана по новости</label>
            <p class="nx-desc">Опиши правило словами — Nox по нужной новости сам откроет заготовленный стакан (клик по координате). Купить/Продать жмёшь ты. Пример: «Новатэк дивиденды: нет → стакан 1; от 1 до 30 рублей → стакан 2; больше 30 → стакан 3».</p>
            <textarea class="nx-in" rows="5" style="width:100%;min-height:120px;resize:vertical;line-height:1.4;font-size:14px" placeholder="Новатэк дивиденды: нет → стакан 1; 1-30 → стакан 2; больше 30 → стакан 3&#10;&#10;или: нонфарм 02.10.2026 — больше 99к → сценарий 1; меньше 75 → сценарий 2; 75-99к → без действий" bind:value={scnText}></textarea>
            <div class="nx-row">
                <button class="nx-btn" on:click={doParse} disabled={!scnText.trim()}>Разобрать</button>
                {#if scnMsg}<span class="nx-hint" style="margin:0">{scnMsg}</span>{/if}
            </div>
        </div>

        {#if parsed}
            <div class="nx-block">
                <label class="nx-lab">{parsed.instrument} · {parsed.event}</label>
                {#each parsed.branches as b, i}
                    <div class="nx-row" style="align-items:center">
                        <span class="chip ph">{b.label}</span>
                        <span class="nx-hint" style="margin:0">
                            {b.type === "none" ? "нет значения" : b.type === "range" ? `${b.min}–${b.max}` : b.type === "gt" ? `> ${b.min}` : b.type === "lt" ? `< ${b.max}` : "любое"}
                        </span>
                        {#if b.noop}
                            <span class="nx-hint" style="margin:0 auto 0 8px;color:#9aa4b2">без клика</span>
                        {:else}
                            <span class="nx-hint" style="margin:0 auto 0 8px">{b.x != null ? `✓ (${b.x}, ${b.y})` : "нет координаты"}</span>
                            {#if b.x != null}<button class="nx-clr" on:click={() => showPoint(b.x, b.y)} title="курсор прыгнет в точку">показать</button>{/if}
                            <button class="nx-btn" on:click={() => captureCoord(i)} disabled={capturing >= 0}>
                                {capturing === i ? "жду F2…" : "Задать (F2)"}
                            </button>
                        {/if}
                    </div>
                {/each}
                <div class="nx-row">
                    <button class="nx-btn" on:click={saveScn}>Сохранить сценарий</button>
                    <button class="nx-clr" on:click={() => { parsed = null; scnMsg = "" }}>отмена</button>
                </div>
            </div>
        {/if}

        {#if scnList.length}
            <div class="nx-block">
                <label class="nx-lab">Активные сценарии</label>
                {#each scnList as s}
                    <div class="nx-row" style="align-items:center">
                        <span class="dot" class:on={s.enabled}></span>
                        <b style="color:#e9eef6">{s.name || s.instrument}</b>
                        <span class="nx-hint" style="margin:0">{s.instrument} · {s.event} · {s.branches?.length || 0} веток</span>
                        <button class="nx-clr" style="margin-left:auto" on:click={() => showAll(s.id)} title="показать все точки красными плюсами">показать точки</button>
                        <button class="nx-clr" on:click={() => toggleScn(s.id)}>{s.enabled ? "выкл" : "вкл"}</button>
                        <button class="nx-clr" on:click={() => delScn(s.id)}>удалить</button>
                    </div>
                    <div class="nx-row" style="flex-wrap:wrap;gap:6px;margin:2px 0 0 18px">
                        {#each (s.branches || []) as b}
                            <span class="nx-hint" style="margin:0;background:#1b2330;padding:2px 7px;border-radius:6px">
                                {b.label}: {b.noop ? "без клика" : (b.x != null ? `(${b.x}, ${b.y})` : "нет точки")}
                            </span>
                            {#if !b.noop && b.x != null}<button class="nx-clr" style="padding:1px 7px" on:click={() => showPoint(b.x, b.y)}>показать</button>{/if}
                        {/each}
                    </div>
                    <div class="nx-row" style="align-items:center;margin-top:4px">
                        <span class="nx-hint" style="margin:0">⏰ ожидается:</span>
                        <input class="nx-in" style="max-width:170px" placeholder="2026-10-29 16:30"
                               value={s.schedule_dt || ""} on:input={(e) => s._dt = e.currentTarget.value} />
                        <input class="nx-in" type="number" min="1" max="30" style="max-width:70px"
                               value={s.prewarm_min || 3} on:input={(e) => s._pre = e.currentTarget.value} title="за сколько минут прогреть Claude" />
                        <span class="nx-hint" style="margin:0">мин до</span>
                        <button class="nx-btn" on:click={() => setSchedule(s)}>назначить</button>
                        {#if s.schedule_dt}<span class="nx-hint" style="margin:0;color:#7fd1a0">✓ {s.schedule_dt}</span>{/if}
                    </div>
                    <div class="nx-row" style="align-items:center;margin-top:4px">
                        <span class="nx-hint" style="margin:0">🧪 тест: значение</span>
                        <input class="nx-in" type="number" style="max-width:110px" placeholder="напр. 180"
                               value={s._tv ?? ""} on:input={(e) => s._tv = e.currentTarget.value} />
                        <button class="nx-btn" on:click={() => testScn(s)}>прогнать</button>
                        {#if s._tmsg}<span class="nx-hint" style="margin:0;color:#8ab4ff">{s._tmsg}</span>{/if}
                    </div>
                {/each}
            </div>
        {/if}

        <div class="nx-block">
            <label class="nx-lab">Мозг сценариев (разбор новости)</label>
            <div class="nx-row">
                <select class="nx-in" bind:value={scnBrain} on:change={saveBrain}>
                    <option value="ollama">Ollama (локально, бесплатно)</option>
                    <option value="claude_cli">Claude (моя подписка, без ключа)</option>
                    <option value="claude">Claude API (нужен ключ)</option>
                    <option value="openai">ChatGPT API (нужен ключ)</option>
                </select>
            </div>
            {#if scnBrain === "claude_cli"}
                <div class="nx-hint" style="margin-top:4px">
                    Использует твою подписку через локальный Claude. Нужен разовый вход:
                    запусти <b>claude-login.cmd</b> (в папке C:\jarvis-lab) и включи VPN. Токенов тратит мало.
                </div>
            {/if}
            {#if scnBrain === "claude"}
                <div class="nx-row">
                    <input class="nx-in" type="password" placeholder={scnHasClaude ? "ключ сохранён — впиши, чтобы заменить" : "Anthropic API ключ (sk-ant-…)"} bind:value={claudeKey} />
                    <button class="nx-btn" on:click={saveBrain}>Сохранить</button>
                </div>
            {/if}
            {#if scnBrain === "openai"}
                <div class="nx-row">
                    <input class="nx-in" type="password" placeholder={scnHasOpenai ? "ключ сохранён — впиши, чтобы заменить" : "OpenAI API ключ (sk-…)"} bind:value={openaiKey} />
                    <button class="nx-btn" on:click={saveBrain}>Сохранить</button>
                </div>
            {/if}
            <p class="nx-hint">Ollama — бесплатно и локально. Claude/ChatGPT — точнее разбирают сложные формулировки, но нужен API-ключ (оплата по токенам). Ключи хранятся локально.</p>
        </div>
    </Tabs.Tab>
</Tabs>

<Space h="xl" />

<Button
    color="teal"
    radius="md"
    size="sm"
    uppercase
    ripple
    fullSize
    on:click={saveSettings}
    disabled={saveButtonDisabled}
>
    {t('settings-save')}
</Button>

<Space h="sm" />

<Button
    color="gray"
    radius="md"
    size="sm"
    uppercase
    fullSize
    on:click={() => $goto("/")}
>
    {t('settings-back')}
</Button>

<Space h="xl" />

<button class="stop-nox" class:armed={stopConfirm} on:click={stopNox}>
    {stopConfirm ? "Точно остановить? Нажмите ещё раз" : "⏻ Остановить Nox полностью"}
</button>
<p class="stop-hint">Гасит сторож, все сервисы, голосовой движок и интерфейс. Запустить снова — ярлык Nox.</p>

<Space h="xl" />

<style lang="scss">
.voice-select {
    margin-bottom: 1rem;
    
    label {
        font-weight: 600;
        font-size: 0.9rem;
        color: #fff;
        display: block;
        margin-bottom: 0.25rem;
    }
    
    .description {
        font-size: 0.75rem;
        color: rgba(255,255,255,0.5);
        margin: 0 0 0.75rem;
        white-space: pre-line;
    }
}

$voice-item-height: 70px;
$voice-item-gap: 0.5rem;
$voice-max-visible: 3;

.voice-options {
    display: flex;
    flex-direction: column;
    gap: $voice-item-gap;
    max-height: $voice-item-height * $voice-max-visible;
    overflow-y: auto;
    
    &::-webkit-scrollbar {
        width: 6px;
    }
    
    &::-webkit-scrollbar-track {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 3px;
    }
    
    &::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.2);
        border-radius: 3px;
        
        &:hover {
            background: rgba(255, 255, 255, 0.3);
        }
    }
}

.voice-option {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.75rem 1rem;
    background: rgba(30, 40, 45, 0.8);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s ease;
    text-align: left;
    width: 100%;
    
    &:hover {
        background: rgba(40, 55, 60, 0.9);
        border-color: rgba(255,255,255,0.2);
    }
    
    &.selected {
        background: rgba(82, 254, 254, 0.1);
        border-color: rgba(82, 254, 254, 0.4);
    }
}

.voice-info {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.15rem;
}

.voice-name {
    font-size: 0.85rem;
    color: #fff;
    font-weight: 500;
}

.voice-author {
    font-size: 0.7rem;
    color: rgba(255,255,255,0.4);
}

.voice-languages {
    display: flex;
    gap: 0.35rem;
    
    img {
        opacity: 0.8;
        border-radius: 2px;
    }
}

.no-voices {
    font-size: 0.8rem;
    color: rgba(255,255,255,0.4);
    font-style: italic;
}

/* ### Новости и слежка */
$nx-accent: #35e0d0; $nx-amber: #f6b352; $nx-muted: #8592a6;
.nx-block { margin-bottom: 1.1rem; padding-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.06); }
.nx-lab { font-weight: 600; font-size: 0.9rem; color: #fff; display: block; margin-bottom: 0.25rem; }
.nx-desc { font-size: 0.75rem; color: rgba(255,255,255,0.5); margin: 0 0 0.6rem; }
.nx-hint { font-size: 0.72rem; color: rgba(255,255,255,0.38); margin: 0.4rem 0 0; }
.nx-ok { font-size: 0.75rem; color: $nx-accent; margin: 0.4rem 0 0; }
.nx-err { font-size: 0.78rem; color: #ff6d6d; background: rgba(255,109,109,0.08); border: 1px solid rgba(255,109,109,0.25);
    border-radius: 8px; padding: 8px 10px; margin-bottom: 1rem; }
.nx-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.nx-in { flex: 1; min-width: 130px; background: rgba(16,22,31,0.9); border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px; color: #fff; padding: 9px 11px; font-size: 0.82rem; }
.nx-in:focus { outline: none; border-color: $nx-accent; }
.nx-btn { padding: 9px 14px; border-radius: 8px; border: 1px solid $nx-accent; background: rgba(53,224,208,0.14);
    color: $nx-accent; font-weight: 700; font-size: 0.78rem; cursor: pointer; }
.nx-clr { background: none; border: none; color: $nx-muted; font-size: 0.72rem; cursor: pointer; text-decoration: underline; }
.nx-fld { display: flex; flex-direction: column; gap: 5px; font-size: 0.72rem; color: rgba(255,255,255,0.55); }
.nx-num { width: 110px; background: rgba(16,22,31,0.9); border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px; color: #fff; padding: 8px 10px; font-size: 0.85rem; }
.nx-num:focus { outline: none; border-color: $nx-accent; }
.nx-sw { display: inline-flex; align-items: center; gap: 6px; color: rgba(255,255,255,0.7); font-size: 0.78rem; cursor: pointer;
    input { accent-color: $nx-accent; } }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 0.55rem; }
.chip { display: inline-flex; align-items: center; gap: 6px; font-size: 0.76rem; font-weight: 600;
    border-radius: 999px; padding: 4px 6px 4px 10px; color: #fff; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);
    button { background: none; border: none; color: rgba(255,255,255,0.4); cursor: pointer; font-size: 0.72rem; padding: 0; line-height: 1; }
    button:hover { color: #ff6d6d; } }
.chip.tk { color: $nx-amber; border-color: rgba(246,179,82,0.3); font-family: "JetBrains Mono", monospace; }
.chip.ph { color: $nx-accent; border-color: rgba(53,224,208,0.3); }
.chip.src { color: rgba(255,255,255,0.85); }
.chip-empty { font-size: 0.74rem; color: rgba(255,255,255,0.3); font-style: italic; }
.stop-nox { width: 100%; padding: 11px; border-radius: 10px; cursor: pointer; font-weight: 700; font-size: 0.82rem;
    background: rgba(255,109,109,0.12); color: #ff6d6d; border: 1px solid rgba(255,109,109,0.4); }
.stop-nox:hover { background: rgba(255,109,109,0.2); }
.stop-nox.armed { background: #ff6d6d; color: #141b28; border-color: #ff6d6d; }
.stop-hint { font-size: 0.72rem; color: rgba(255,255,255,0.4); margin: 0.4rem 0 0; text-align: center; }
</style>
use std::sync::mpsc::Receiver;
use std::time::SystemTime;

use jarvis_core::{audio_buffer::AudioRingBuffer, audio_processing, commands, config, listener, recorder, stt, COMMANDS_LIST, intent, voices, ipc::{self, IpcEvent}, i18n, slots};
use rand::seq::SliceRandom;

use crate::should_stop;

// VAD state machine
#[derive(Debug, Clone, Copy, PartialEq)]
enum VadState {
    WaitingForVoice,
    VoiceActive,
}

pub fn start(text_cmd_rx: Receiver<String>, rt: &tokio::runtime::Runtime) -> Result<(), ()> {
    main_loop(text_cmd_rx, rt)
}

fn main_loop(text_cmd_rx: Receiver<String>, rt: &tokio::runtime::Runtime) -> Result<(), ()> {
    let frame_length: usize = 512;
    let sample_rate: usize = 16000;
    let mut frame_buffer: Vec<i16> = vec![0; frame_length];
    
    // ring buffer: keeps last 5 seconds of audio (pre-roll)
    let mut audio_buffer = AudioRingBuffer::new(5.0, frame_length, sample_rate);

    // VAD state
    let mut vad_state = VadState::WaitingForVoice;
    let mut silence_frames: u32 = 0;
    
    // how many frames of silence before we consider speech ended
    // 1.5 seconds = 1.5 * (16000 / 512) ≈ 47 frames
    let silence_threshold: u32 = ((1.5 * sample_rate as f32) / frame_length as f32) as u32;
    
    voices::play_greet();

    match recorder::start_recording() {
        Ok(_) => info!("Recording started. Microphone: {}", 
            recorder::get_audio_device_name(recorder::get_selected_microphone_index())),
        Err(_) => {
            error!("Cannot start recording.");
            return Err(());
        }
    }

    ipc::send(IpcEvent::Idle);

    // ### WAKE WORD DETECTION LOOP
    'wake_word: loop {
        if should_stop() {
            info!("Stop signal received, shutting down...");
            voices::play_goodbye();
            ipc::send(IpcEvent::Stopping);
            break;
        }

        if let Ok(text) = text_cmd_rx.try_recv() {
            process_text_command(&text, &rt);
            continue 'wake_word;
        }

        // Активация «кнопкой» (глобальный хоткей / трей): слушаем команду СРАЗУ,
        // без слова-активации. Работает даже при mute и при выключенном wake word.
        if crate::ACTIVATE.swap(false, std::sync::atomic::Ordering::SeqCst) {
            if crate::MUTED.load(std::sync::atomic::Ordering::Relaxed) {
                // микрофон выключен — не слушаем даже по кнопке, только подсказка в ленту
                ipc::send(IpcEvent::AssistantReply { text: "Микрофон выключен — включите его на Пульсе.".to_string() });
                ipc::send(IpcEvent::Idle);
                continue 'wake_word;
            }
            info!("Activation by hotkey/button");
            ipc::send(IpcEvent::WakeWordDetected);
            voices::play_reply();
            stt::reset_wake_recognizer();
            stt::reset_speech_recognizer();
            audio_processing::reset();
            ipc::send(IpcEvent::Listening);
            recognize_command(&mut frame_buffer, &rt, frame_length, sample_rate, false);
            vad_state = VadState::WaitingForVoice;
            silence_frames = 0;
            audio_buffer.clear();
            stt::reset_wake_recognizer();
            stt::reset_speech_recognizer();
            audio_processing::reset();
            ipc::send(IpcEvent::Idle);
            continue 'wake_word;
        }

        recorder::read_microphone(&mut frame_buffer);

        // Микрофон отключён (mute) или «Не беспокоить»: читаем, но не слушаем и не реагируем
        // на голос. Текстовые команды и хоткей (осознанные действия) работают (выше по циклу).
        if crate::MUTED.load(std::sync::atomic::Ordering::Relaxed)
            || crate::DND.load(std::sync::atomic::Ordering::Relaxed)
        {
            continue 'wake_word;
        }

        // Слово-активация выключена в настройках -> не реагируем на wake word
        // (активация только хоткеем/текстом, обработанными выше). Убирает ложные
        // срабатывания движка на шум.
        let wake_enabled = jarvis_core::DB.get()
            .and_then(|db| db.read().get("wake_word_enabled"))
            .map(|v| v == "true")
            .unwrap_or(true);
        if !wake_enabled {
            continue 'wake_word;
        }

        let processed = audio_processing::process(&frame_buffer);

        match vad_state {
            VadState::WaitingForVoice => {
                // always buffer audio
                audio_buffer.push(&frame_buffer);
                
                if processed.is_voice {
                    // voice started! flush buffer to Vosk
                    info!("VAD: Voice started, flushing {} buffered frames", audio_buffer.len());
                    
                    for buffered_frame in audio_buffer.drain_all() {
                        listener::data_callback(&buffered_frame);
                    }
                    
                    vad_state = VadState::VoiceActive;
                    silence_frames = 0;
                }
            }
            
            VadState::VoiceActive => {
                // dual-feed: speech recognizer gets frames in parallel with wake word detector
                let _ = stt::recognize(&frame_buffer, false);

                // feed to wake word detector
                if let Some(_keyword_index) = listener::data_callback(&frame_buffer) {
                    // WAKE WORD DETECTED!
                    info!("Wake word activated!");
                    ipc::send(IpcEvent::WakeWordDetected);
                    
                    stt::reset_wake_recognizer();
                    audio_processing::reset();

                    // brief sniff to keep feeding STT while transitioning
                    let sniff_frames = ((0.3 * sample_rate as f32) / frame_length as f32) as u32;
                    for _ in 0..sniff_frames {
                        recorder::read_microphone(&mut frame_buffer);
                        audio_processing::process(&frame_buffer);
                        stt::recognize(&frame_buffer, false);
                    }

                    ipc::send(IpcEvent::Listening);
                    recognize_command(&mut frame_buffer, &rt, frame_length, sample_rate, true);

                    // reset state after command
                    vad_state = VadState::WaitingForVoice;
                    silence_frames = 0;
                    audio_buffer.clear();
                    stt::reset_wake_recognizer();
                    stt::reset_speech_recognizer(); // NOW reset, after command is done
                    audio_processing::reset();
                    ipc::send(IpcEvent::Idle);
                    
                    continue 'wake_word;
                }
                
                // track silence
                if processed.is_voice {
                    silence_frames = 0;
                } else {
                    silence_frames += 1;
                    
                    if silence_frames > silence_threshold {
                        debug!("VAD: Silence timeout, returning to wait state");
                        vad_state = VadState::WaitingForVoice;
                        silence_frames = 0;
                        stt::reset_wake_recognizer();
                        stt::reset_speech_recognizer(); // reset since we were dual-feeding
                    }
                }
            }
        }
    }

    recorder::stop_recording().ok();
    ipc::send(IpcEvent::Stopping);

    Ok(())
}


/// Итог обработки одной законченной фразы.
enum PhraseOutcome {
    /// пустое/слишком короткое — слушаем дальше
    Ignore,
    /// произнесено только слово активации — отклик и слушаем заново
    Reactivate,
    /// команда исполнена; true — можно дослушать уточнение
    Executed(bool),
}

/// Склеить кусок распознанного текста с уже накопленным.
fn append_text(acc: &mut String, piece: &str) {
    let p = piece.trim();
    if p.is_empty() {
        return;
    }
    if !acc.is_empty() {
        acc.push(' ');
    }
    acc.push_str(p);
}

/// Выбросить из буфера микрофона то, что накопилось, пока Нокс думал и говорил:
/// там эхо его собственного ответа, его нельзя принимать за команду.
/// Буферизованные кадры читаются мгновенно, «живые» — раз в ~32 мс.
fn drain_mic(frame_buffer: &mut [i16]) {
    for _ in 0..400 {
        let t = std::time::Instant::now();
        recorder::read_microphone(frame_buffer);
        if t.elapsed() > std::time::Duration::from_millis(15) {
            break; // догнали реальное время
        }
    }
}

/// Законченная фраза: снять слово активации/служебные слова и исполнить.
fn handle_phrase(raw: &str, first_recognition: &mut bool, rt: &tokio::runtime::Runtime) -> PhraseOutcome {
    let mut voice = raw.trim().to_lowercase();
    if voice.is_empty() {
        return PhraseOutcome::Ignore;
    }
    info!("Recognized voice: {}", voice);
    ipc::send(IpcEvent::SpeechRecognized { text: voice.clone() });

    let lang = i18n::get_language();
    let wake_phrases = config::get_wake_phrases(&lang);
    if wake_phrases.iter().any(|wp| voice.contains(wp.as_str())) {
        let mut remaining = voice.clone();
        for wp in &wake_phrases {
            remaining = remaining.replace(wp.as_str(), "");
        }
        let remaining = remaining.trim().to_string();
        if remaining.is_empty() {
            *first_recognition = false;
            return PhraseOutcome::Reactivate;
        }
        info!("Wake word + command in one phrase: '{}'", remaining);
        voice = remaining;
    }
    *first_recognition = false;

    for tbr in config::get_phrases_to_remove(&lang) {
        voice = voice.replace(tbr.as_str(), "");
    }
    let voice = voice.trim().to_string();
    if voice.chars().count() < 3 {
        debug!("Ignoring too short recognition: '{}'", voice);
        return PhraseOutcome::Ignore;
    }
    PhraseOutcome::Executed(execute_command(&voice, rt))
}

// Voice recognition for command after wake word / hotkey.
// Vosk режет речь на паузах, поэтому куски КОПЯТСЯ и исполняются одной фразой после
// настоящей паузы ~1.4 с: длинная диктовка сценария не рвётся и не теряется.
fn recognize_command(
    frame_buffer: &mut [i16],
    rt: &tokio::runtime::Runtime,
    frame_length: usize,
    sample_rate: usize,
    prefed_audio: bool
) {
    let mut audio_buffer = AudioRingBuffer::new(2.0, frame_length, sample_rate);
    let mut vad_state = if prefed_audio {
        VadState::VoiceActive
    } else {
        VadState::WaitingForVoice
    };
    let mut silence_frames: u32 = 0;
    let mut first_recognition = prefed_audio;
    let mut pending = String::new();                 // накопленная фраза
    let mut utter_start = SystemTime::now();         // начало фразы (потолок длины)
    let mut last_activity = SystemTime::now();       // последний распознанный текст
    let mut followups_left: u32 = 1;                 // после ответа — одно дослушивание, не бесконечно

    let fps = sample_rate as f32 / frame_length as f32;
    let end_frames = (1.4 * fps) as u32;             // пауза 1.4 с = фраза закончена
    let wait_limit = std::time::Duration::from_secs(15); // ждать начала речи
    let max_utterance = std::time::Duration::from_secs(90);

    loop {
        if crate::should_stop() {
            return;
        }
        // выключили микрофон прямо во время слушания — замолкаем
        if crate::MUTED.load(std::sync::atomic::Ordering::Relaxed) {
            info!("Mic muted during listening — stop listening.");
            stt::reset_speech_recognizer();
            return;
        }

        recorder::read_microphone(frame_buffer);
        let processed = audio_processing::process(frame_buffer);

        match vad_state {
            VadState::WaitingForVoice => {
                audio_buffer.push(frame_buffer);
                if processed.is_voice {
                    for buffered_frame in audio_buffer.drain_all() {
                        if let Some(seg) = stt::recognize(&buffered_frame, false) {
                            append_text(&mut pending, &seg);
                        }
                    }
                    if pending.is_empty() {
                        utter_start = SystemTime::now();
                    }
                    vad_state = VadState::VoiceActive;
                    silence_frames = 0;
                }
            }

            VadState::VoiceActive => {
                if let Some(seg) = stt::recognize(frame_buffer, false) {
                    append_text(&mut pending, &seg); // кусок фразы — копим, не исполняем сразу
                    last_activity = SystemTime::now();
                }
                if processed.is_voice {
                    silence_frames = 0;
                } else {
                    silence_frames += 1;
                }

                let too_long = utter_start.elapsed().map(|e| e > max_utterance).unwrap_or(false);
                if silence_frames >= end_frames || too_long {
                    // фраза закончилась: дочитать хвост и исполнить ЦЕЛИКОМ
                    if let Some(tail) = stt::finalize_speech() {
                        append_text(&mut pending, &tail);
                    }
                    stt::reset_speech_recognizer();
                    let phrase = std::mem::take(&mut pending);
                    vad_state = VadState::WaitingForVoice;
                    silence_frames = 0;
                    audio_buffer.clear();
                    if phrase.trim().is_empty() {
                        continue; // шум без слов
                    }
                    last_activity = SystemTime::now();

                    match handle_phrase(&phrase, &mut first_recognition, rt) {
                        PhraseOutcome::Ignore => continue,
                        PhraseOutcome::Reactivate => {
                            info!("Wake word alone — listening for the command...");
                            voices::play_reply();
                            ipc::send(IpcEvent::Listening);
                            followups_left = 1;
                            continue;
                        }
                        PhraseOutcome::Executed(chain) => {
                            drain_mic(frame_buffer); // эхо собственного ответа — не команда
                            if chain && followups_left > 0 {
                                followups_left -= 1;
                                info!("Waiting for a follow-up question...");
                                stt::reset_speech_recognizer();
                                ipc::send(IpcEvent::Listening);
                                last_activity = SystemTime::now();
                                continue;
                            }
                            info!("Done, returning to wake word mode.");
                            return;
                        }
                    }
                }
            }
        }

        // долго ничего не сказано (а не «долго говорит») — выходим
        if pending.is_empty() {
            if let Ok(idle) = last_activity.elapsed() {
                if idle > wait_limit {
                    info!("No speech for {}s, returning to wake word mode.", wait_limit.as_secs());
                    return;
                }
            }
        }
    }
}


fn process_text_command(text: &str, rt: &tokio::runtime::Runtime) {
    info!("Processing text command: {}", text);
    
    ipc::send(IpcEvent::SpeechRecognized { text: text.to_string() });
    
    let mut filtered = text.to_lowercase();
    // for tbr in config::ASSISTANT_PHRASES_TBR {
    //     filtered = filtered.replace(tbr.as_str(), "");
    // }
    for tbr in config::get_phrases_to_remove(&i18n::get_language()) {
        filtered = filtered.replace(tbr.as_str(), "");
    }

    let filtered = filtered.trim();
    
    if filtered.is_empty() {
        ipc::send(IpcEvent::Idle);
        return;
    }

    // Голосовое управление микрофоном: "не слушай", "отключи микрофон", "слушай"...
    if try_mic_control(filtered) {
        ipc::send(IpcEvent::Idle);
        return;
    }

    // Voice control of the multi-brain: "переключись на gpt", "используй клод", etc.
    if try_switch_brain(filtered) {
        ipc::send(IpcEvent::Idle);
        return;
    }

    // text commands never chain
    execute_command(filtered, rt);
}

/// Detect and handle a spoken request to switch the conversational backend.
/// Returns true if the phrase was a backend switch (and was handled).
/// Голосовое управление микрофоном. Возвращает true, если фраза обработана.
fn try_mic_control(text: &str) -> bool {
    let unmute = text.contains("слушай меня") || text.contains("включи микрофон")
        || text.contains("проснись") || text.contains("активируйся");
    if unmute {
        crate::MUTED.store(false, std::sync::atomic::Ordering::SeqCst);
        let m = "Слушаю вас, сэр.";
        ipc::send(IpcEvent::AssistantReply { text: m.to_string() });
        jarvis_core::tts::speak(m);
        return true;
    }
    let mute = text.contains("не слушай") || text.contains("отключи микрофон")
        || text.contains("выключи микрофон") || text.contains("замолчи")
        || text.contains("перестань слушать") || text.contains("режим тишины")
        || text.contains("уйди в сон") || text == "спи";
    if mute {
        let m = "Отключаю микрофон, сэр. Включите меня в окне, когда понадоблюсь.";
        ipc::send(IpcEvent::AssistantReply { text: m.to_string() });
        jarvis_core::tts::speak(m); // проговариваем ДО отключения
        crate::MUTED.store(true, std::sync::atomic::Ordering::SeqCst);
        return true;
    }
    false
}

fn try_switch_brain(text: &str) -> bool {
    let has_switch_verb = ["переключ", "смени", "использ", "перейд", "включи режим", "поставь режим"]
        .iter()
        .any(|v| text.contains(v));
    if !has_switch_verb {
        return false;
    }

    // два мозга: облако по API-ключу или локальная Ollama (подписка Claude убрана)
    let target = if text.contains("локальн") || text.contains("олам") || text.contains("оллам")
        || text.contains("ollama") || text.contains("офлайн") || text.contains("бесплатн")
    {
        Some("ollama")
    } else if text.contains("облак") || text.contains("gpt") || text.contains("гпт")
        || text.contains("джипити") || text.contains("chatgpt") || text.contains("апи")
    {
        Some("openai")
    } else {
        None
    };

    match target.and_then(jarvis_core::brain::set_backend) {
        Some(name) => {
            // сохранить выбор, чтобы его видела страница «Мозг» и он пережил перезапуск
            if let Some(arc) = jarvis_core::DB.get() {
                let _ = jarvis_core::SettingsManager::from_arc(arc.clone()).write("brain_backend", name);
            }
            let spoken = if name == "ollama" {
                "Переключаюсь на локальный мозг, сэр."
            } else {
                "Переключаюсь на облачный мозг, сэр."
            };
            info!("Brain backend switched to: {}", name);
            ipc::send(IpcEvent::AssistantReply { text: spoken.to_string() });
            jarvis_core::tts::speak(spoken);
            true
        }
        None => false,
    }
}


// Execute command, returns true if chaining should continue
fn execute_command(text: &str, rt: &tokio::runtime::Runtime) -> bool {
    // МОЗГ-ПЕРВЫМ: любой запрос сначала уходит в API-роутер; мозг сам решает —
    // ответить / поискать в сети / завести наблюдателя / объяснить новость, либо вернуть
    // «команду программе», и тогда идём обычным путём ниже (команды/терминал/мозг).
    if let Some(answer) = jarvis_core::terminal::route(text) {
        info!("Router handled request → {}", answer);
        ipc::send(IpcEvent::AssistantReply { text: answer.clone() });
        jarvis_core::tts::speak(&answer);
        ipc::send(IpcEvent::Idle);
        return true; // окно дослушивания 15с
    }

    let commands_list = match COMMANDS_LIST.get() {
        Some(c) => c,
        None => {
            ipc::send(IpcEvent::Error { message: "Commands not loaded".to_string() });
            ipc::send(IpcEvent::Idle);
            return false;
        }
    };
    
    let cmd_result = if let Some((intent_id, confidence)) = 
        rt.block_on(intent::classify(text)) 
    {
        info!("Intent recognized: {} (confidence: {:.2})", intent_id, confidence);
        intent::get_command_by_intent(commands_list, &intent_id)
    } else {
        info!("Intent not recognized, trying levenshtein fallback...");
        commands::fetch_command(text, commands_list)
    };

    // Фильтр по активному режиму-профилю: команда чужого режима игнорируется.
    let active_mode = jarvis_core::DB
        .get()
        .and_then(|db| db.read().get("active_mode"))
        .unwrap_or_default();
    let cmd_result = cmd_result.filter(|(_, c)| commands::command_in_mode(c, &active_mode));

    if let Some((cmd_path, cmd_config)) = cmd_result {
        info!("Command found: {:?}", cmd_path);
        
        // extract slots if needed
        let extracted_slots = if !cmd_config.slots.is_empty() {
            let s = slots::extract(text, &cmd_config.slots);
            if !s.is_empty() {
                info!("Extracted slots: {:?}", s);
            }
            Some(s)
        } else {
            None
        };

        match commands::execute_command(&cmd_path, &cmd_config, Some(&text), extracted_slots.as_ref()) {
            Ok(chain) => {
                info!("Command executed successfully");
                // voices::play_ok();
                voices::play_random_from(cmd_config.get_sounds(&i18n::get_language()).as_slice());
                ipc::send(IpcEvent::CommandExecuted {
                    id: cmd_config.id.clone(),
                    success: true,
                });
                ipc::send(IpcEvent::Idle);
                return chain; // return chain status from command
            }
            Err(msg) => {
                error!("Error executing command: {}", msg);
                voices::play_error();
                ipc::send(IpcEvent::CommandExecuted {
                    id: cmd_config.id.clone(),
                    success: false,
                });
                ipc::send(IpcEvent::Error { message: msg.to_string() });
            }
        }
    } else {
        // T-terminal query (portfolio / quotes)? Read-only, spoken answer.
        if let Some(answer) = jarvis_core::terminal::try_handle(text) {
            info!("Terminal query: {}", answer);
            ipc::send(IpcEvent::AssistantReply { text: answer.clone() });
            jarvis_core::tts::speak(&answer); // обычный чёткий голос (как остальные ответы)
            ipc::send(IpcEvent::Idle);
            return true;   // окно дослушивания: можно сразу спросить уточнение про новость
        }

        info!("No local command matched for: '{}'. Asking the brain...", text);

        // Ни одна локальная команда не подошла -> спросить мозг (облако по ключу или Ollama).
        // Блокировка здесь допустима: это рабочий поток, ассистент «думает».
        match jarvis_core::brain::ask(text) {
            Ok(answer) => {
                info!("Brain reply: {}", answer);
                ipc::send(IpcEvent::AssistantReply { text: answer.clone() });
                // Speak the reply aloud (best-effort; blocks until finished).
                jarvis_core::tts::speak(&answer);
                ipc::send(IpcEvent::Idle);
                return true;   // окно дослушивания 15с: можно задать уточняющий вопрос
            }
            Err(e) => {
                warn!("Brain unavailable ({}). Falling back to 'not found'.", e);
                voices::play_not_found();
                ipc::send(IpcEvent::Error {
                    message: format!("Command not found: {}", text),
                });
            }
        }
    }

    ipc::send(IpcEvent::Idle);
    false // no chain on error or not found
}


pub fn close(code: i32) {
    info!("Closing application.");
    voices::play_goodbye();
    ipc::send(IpcEvent::Stopping);
    std::process::exit(code);
}
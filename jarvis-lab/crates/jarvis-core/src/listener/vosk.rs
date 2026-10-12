use crate::{config, stt, i18n};

pub fn init() -> Result<(), ()> {
    Ok(()) // nothing to init for Vosk
}

pub fn data_callback(frame_buffer: &[i16]) -> Option<i32> {
    if let Some((recognized, _confidence)) = stt::recognize_wake_word(frame_buffer) {
        let recognized = recognized.trim().to_lowercase();
        
        // skip unknown/empty
        if recognized.is_empty() || recognized == "[unk]" {
            return None;
        }
        
        info!("Wake word candidate: '{}'", recognized);
        
        // language-specific wake phrase
        let lang = i18n::get_language();
        let wake_phrases = config::get_wake_phrases(&lang);
        // Своё слово (не «джарвис») распознаётся СВОБОДНО, по частичным результатам —
        // поэтому сверка строже: короткие слова («но», «ок», «он») отбрасываем,
        // порог схожести выше. Иначе будит любая речь вокруг.
        let custom = !config::wake_is_default();
        let min_ratio = if custom { 80.0 } else { config::VOSK_MIN_RATIO };

        // verify with seqdiff ratio
        for word in recognized.split_whitespace() {
            if word == "[unk]" {
                continue;
            }

            let word_chars: Vec<char> = word.chars().collect();
            if custom && (word_chars.len() < 4 || word_chars.len() > 8) {
                continue;
            }

            for wake_phrase in &wake_phrases {
                let wake_chars: Vec<char> = wake_phrase.chars().collect();
                let similarity = seqdiff::ratio(&wake_chars, &word_chars);

                if similarity >= min_ratio {
                    info!("Wake word match: '{}' ~ '{}' ({:.1}%)", word, wake_phrase, similarity);
                    return Some(0);
                }
            }
        }
        
        // info!("Similarity: {:.1}% ('{}' vs '{}')", similarity, recognized, config::VOSK_FETCH_PHRASE);
    }
    
    None
}

// @TODO. Make it better somehow (more accurate or with higher sensitivity).
// pub fn data_callback(frame_buffer: &[i16]) -> Option<i32> {
//     // recognize & convert to sequence
//     let recognized_phrase = stt::recognize(&frame_buffer, true).unwrap_or("".into());

//     if !recognized_phrase.trim().is_empty() {
//         info!("Vosk wake-word debug info:");
//         info!("rec: {}", recognized_phrase);
//         let recognized_phrases = recognized_phrase.split_whitespace();
//         for phrase in recognized_phrases {
//             let recognized_phrase_chars = phrase.trim().to_lowercase().chars().collect::<Vec<_>>();

//             // compare
//             let compare_ratio = seqdiff::ratio(
//                 &config::VOSK_FETCH_PHRASE.chars().collect::<Vec<_>>(),
//                 &recognized_phrase_chars,
//             );
//             info!("og phrase: {:?}", &config::VOSK_FETCH_PHRASE);
//             info!("recognized phrase: {:?}", &recognized_phrase_chars);
//             info!("compare ratio: {}", compare_ratio);

//             if compare_ratio >= config::VOSK_MIN_RATIO {
//                 info!("Phrase activated.");
//                 return Some(0);
//             }
//         }
//     }

//     None
// }

import type { Locale } from "./types";

export type FaqItem = { id: string; q: string; a: string };
export type FaqGroup = { title: string; ids: string[] };

/* The Help view exposes a mix of desktop-workflow questions and
   general-audience questions mirrored verbatim from the landing page FAQ
   (landing/faq.js) — the shared ones use the same wording in both places
   so there is one source of truth. Some app-only items have since been
   trimmed here (e.g. contact_support, own_drum_kit) as redundant with the
   landing copy — that trim is app-FAQ-specific and intentionally not
   mirrored back onto the landing page. */
const ESSENTIAL_EN: FaqItem[] = [
  { id: "fl_on_windows", q: "Do I need FL Studio on Windows for this?", a: "PLG runs on Windows. You need FL Studio to open and refine the FLP project. Guaranteed to work on FL Studio 25 and newer (verified on 25.2.2). Older versions are not supported." },
  { id: "fl_versions", q: "Which FL Studio versions does PLG support?", a: "PLG supports FL Studio 25 and FL Studio 26 — guaranteed to work starting from version 25.2.2. Older versions are not supported. PLG runs on Windows." },
  { id: "first_flp", q: "What do I need to build my first FLP?", a: "On the Home screen, add a MIDI melody and a drum kit — both are required. When the files appear in the panel, press the arrow — PLG will create the FL Studio project." },
  { id: "missing_material", q: "Why does PLG say MIDI is missing?", a: "PLG needs both a readable .mid or .midi melody and a drum kit. Add whichever is missing on Home and try again." },
  { id: "plg_builds", q: "What does PLG build for me?", a: "An FLP project for FL Studio with MIDI and parts arranged on separate tracks. It is not a WAV or MP3 — open it and keep building." },
  { id: "skeleton_or_beat", q: "Is this a finished beat or a starter skeleton?", a: "A starter skeleton. PLG builds an editable FLP with drums, an 808 and a structure — not a final mix. You take it from there in FL Studio: swap sounds, patterns and arrangement." },
  { id: "what_is_generated", q: "What does PLG generate, and what stays mine?", a: "Your MIDI is the melody. PLG generates the kick, 808, hi-hats, clap, snare and snare layer around it, then exports an FL Studio session." },
  { id: "find_files", q: "Where is my generated FLP file?", a: "PLG saves a .flp file (named after your MIDI) in the Projects folder next to the app. Open it in FL Studio to listen, arrange and mix." },
  { id: "open_fl", q: "The FL Studio project did not open — what should I do?", a: "Check the FL Studio path in Settings and select FL64.exe if it was not found automatically. Then create the project again, or open the .flp file from the Projects folder manually." },
  { id: "generation_limit", q: "How does the generation quota work?", a: "A paid 30-day period includes up to 30 generations. Unused quota does not roll over. If it runs out early, you can start a new period right away." },
  { id: "commercial_use", q: "Can I use the result commercially?", a: "Yes. PLG does not claim a share of your release or royalties. You remain responsible for the rights to your source MIDI, samples and final release." },
];

const ESSENTIAL_RU: FaqItem[] = [
  { id: "fl_on_windows", q: "Нужен ли для этого FL Studio на Windows?", a: "PLG работает на Windows. FL Studio нужен, чтобы открыть полученный FLP и доработать проект. Гарантированно работает на FL Studio 25 и новее (проверено на 25.2.2). Более старые версии не поддерживаются." },
  { id: "fl_versions", q: "Какие версии FL Studio поддерживает PLG?", a: "PLG поддерживает FL Studio 25 и FL Studio 26 — гарантированно работает начиная с версии 25.2.2. Более старые версии не поддерживаются. PLG работает на Windows." },
  { id: "first_flp", q: "Что нужно, чтобы собрать первый FLP?", a: "На главном экране добавь MIDI-мелодию и драм-кит — нужны оба. Когда файлы появятся в панели, нажми стрелку — PLG создаст проект FL Studio." },
  { id: "missing_material", q: "Почему PLG пишет, что не хватает MIDI?", a: "Нужны и хотя бы одна читаемая MIDI-мелодия в формате .mid или .midi, и драм-кит. Добавь то, чего не хватает, на главном экране и попробуй ещё раз." },
  { id: "plg_builds", q: "Что PLG собирает на выходе?", a: "FLP-проект для FL Studio: MIDI и партии разложены по дорожкам. Это не WAV и не MP3 — проект можно открыть и продолжить собирать." },
  { id: "skeleton_or_beat", q: "Это готовый бит или стартовый skeleton?", a: "Стартовый skeleton. PLG собирает редактируемый FLP-скелет с барабанами, 808 и структурой — это не финальный микс. Дальше ты сам дорабатываешь его в FL Studio: меняешь звуки, паттерны и аранжировку." },
  { id: "what_is_generated", q: "Что создаёт PLG, а что остаётся моим?", a: "Твоя MIDI остаётся мелодией. PLG генерирует kick, 808, hi-hats, clap, snare и snare layer вокруг неё, затем экспортирует сессию FL Studio." },
  { id: "find_files", q: "Где найти сгенерированный FLP-файл?", a: "PLG сохраняет .flp-файл (назван по имени твоего MIDI) в папке Projects рядом с приложением. Открой его в FL Studio, чтобы послушать, аранжировать и свести трек." },
  { id: "open_fl", q: "Проект FL Studio не открылся — что делать?", a: "Проверь путь к FL Studio в «Настройках» и укажи FL64.exe, если программа не нашла его сама. Затем создай проект ещё раз или открой .flp-файл из папки Projects вручную." },
  { id: "generation_limit", q: "Как работает лимит генераций?", a: "В оплаченный период на 30 дней доступно до 30 генераций. Неиспользованная квота не переносится. Если она закончилась раньше, можно сразу начать новый период." },
  { id: "commercial_use", q: "Можно ли использовать результат в коммерции?", a: "Да. PLG не претендует на долю от релиза или роялти. За права на исходные MIDI и сэмплы, а также за финальный релиз отвечаешь ты." },
];

export const EN: FaqItem[] = [
  {
    id: "how_create",
    q: "How do I create a beat?",
    a: "Home → describe the vibe (style, BPM, mood) → Create beat. Wait 1–3 minutes. When ready, tap Open in FL Studio — that is where you listen and mix.",
  },
  {
    id: "where_listen",
    q: "Why is there no play button in PLG?",
    a: "PLG builds the project; FL Studio is the player. Always preview in FL after Open in FL. The browser UI does not play your full beat.",
  },
  {
    id: "fl_scripts",
    q: "Open in FL does nothing — what now?",
    a: "Settings → install FL Scripts once. Pick your FL64.exe if auto-detect fails. Then Home → Open in FL Studio. Project file: PLG_Session.flp in the PLG folder.",
  },
  {
    id: "rating",
    q: "What are Liked / Disliked for?",
    a: "Listen in FL first, then rate. PLG learns your taste over time (needs a few ratings). It does not block generation if you skip rating.",
  },
  {
    id: "library",
    q: "How do I use my own samples?",
    a: "Library → Import kit (folder with wav/mp3). PLG matches sounds to kick, 808, snare, hats, melody per generation. More variety in the folder = more variety in beats.",
  },
  {
    id: "regenerate",
    q: "Every beat sounds the same?",
    a: "Tap Regenerate on Home for a new variation. Avoid copying the same prompt word-for-word. Import more kits so the matcher is not stuck on one pack.",
  },
  {
    id: "audio_bad",
    q: "Weird audio, crackling, or «broken speaker»?",
    a: "Usually FL output device, not PLG. In FL: Options → Audio → ASIO (ASIO4ALL on Windows). Avoid Bluetooth speakers for checking 808/sub. Wired headphones or monitors.",
  },
  {
    id: "melody",
    q: "Melody lane looks empty or sounds like plinks?",
    a: "Melody is MIDI + a short pluck sample in FL Sampler — it is a hook line, not a full song loop. Open in FL and solo the Melody channel. If it still feels off, Regenerate or try a darker prompt (dark trap, rage, 140 bpm).",
  },
  {
    id: "smart_plan",
    q: "What is Smart plan?",
    a: "Optional extra step before generation: AI drafts BPM, key, and structure (~30 sec). Turn on when you want a tighter brief; leave off for faster random beats.",
  },
  {
    id: "rebake",
    q: "What is Rebake in Tools?",
    a: "Rebuilds the current pattern into a fresh .flp and MIDI without calling the AI again. Use after you changed samples or settings, or if FL did not load the last export.",
  },
  {
    id: "stems",
    q: "What are the .mid names under the beat card?",
    a: "Exported stems: Kick, Snare, 808, Hi-Hats, Melody, etc. They land in output_midi/ inside the PLG folder. FL session already loads the main pattern — stems are for advanced editing.",
  },
  {
    id: "ownership_legal",
    q: "Who owns beats — any royalties to PLG?",
    a: "All generated beats (MIDI, .flp, stems) are yours. Pluggin' claims no authorship, royalties, or revenue share. Licenses on samples you import are your responsibility. Details: Help → Legal → Terms.",
  },
  {
    id: "similarity_legal",
    q: "What if my beat sounds like an existing track?",
    a: "AI may accidentally produce similar melody or groove. The platform does not check for infringement and is not liable for rights-holder claims or takedowns on streaming platforms. Assess clearance yourself before commercial release.",
  },
  {
    id: "privacy_data",
    q: "What data does PLG store in the cloud?",
    a: "Account email, device binding, payment status, beat metadata (BPM, genre, sanitized MIDI pattern without disk paths), ratings, and a prompt hash — not full prompt text. Audio, .flp, and your sample library stay on your PC. Details: Help → Legal → Privacy.",
  },
  {
    id: "consumer_ru",
    q: "Subscription, auto-renewal, and refunds (Russia)?",
    a: "1 trial generation, then a one-time 499 ₽ pack of 30 generations / 30 days via Prodamus (backup: YooKassa) — no auto-renewal, buy another pack when it runs out. Refund within 14 days of first payment if usage is minimal — see Help → Legal → Refunds.",
  },
  {
    id: "subscribe",
    q: "How do I subscribe?",
    a: "Settings → Subscribe. CIS: 499 ₽ via Prodamus (backup: YooKassa), one-time payment for 30 generations / 30 days. International pricing is not available yet. Checkout opens in your browser.",
  },
  {
    id: "quota",
    q: "Beat limits and trial?",
    a: "1 trial generation, then 30 generations per 30 days with no daily cap. Run out early — buy another 30-generation period. The 30-day quota resets with each paid period. Grace access applies if payment fails.",
  },
  {
    id: "samples",
    q: "Who owns samples and beats?",
    a: "You own your beats. Bundled starter sounds are royalty-free for PLG output. Imported kits — check the pack license before release.",
  },
  {
    id: "password",
    q: "Forgot password?",
    a: "On the login screen tap Forgot password — a 6-digit reset code is sent by email (check spam). SLA for support: 24–48 hours.",
  },
  {
    id: "refund",
    q: "Refunds and cancel?",
    a: "Cancel anytime in the payment provider portal. Refund policy is in Help → Legal. Contact help@example.com with your receipt.",
  },
  {
    id: "status",
    q: "Is the service down?",
    a: "Check our Telegram updates channel. Generation may be degraded when AI providers are overloaded.",
  },
];

export const RU: FaqItem[] = [
  {
    id: "how_create",
    q: "Как создать бит?",
    a: "Главная → опиши вайб (стиль, BPM, настроение) → Создать бит. Подожди 1–3 минуты. Когда готово — Открыть в FL. Слушать и миксовать нужно там.",
  },
  {
    id: "where_listen",
    q: "Почему в PLG нет кнопки Play?",
    a: "PLG собирает проект, а проигрыватель — FL Studio. Всегда слушай бит после «Открыть в FL». В браузере полный бит не играет.",
  },
  {
    id: "fl_scripts",
    q: "«Открыть в FL» не работает — что делать?",
    a: "Настройки → один раз установи FL Scripts. Если авто-поиск не нашёл FL — укажи путь к FL64.exe. Потом Главная → Открыть в FL. Файл сессии: PLG_Session.flp в папке PLG.",
  },
  {
    id: "rating",
    q: "Зачем «Зашло» / «Не зашло»?",
    a: "Сначала послушай в FL, потом оцени. PLG подстраивается под твой вкус (нужно несколько оценок). Без оценки бит всё равно создаётся.",
  },
  {
    id: "library",
    q: "Как использовать свои сэмплы?",
    a: "Библиотека → Импорт кита (папка с wav/mp3). PLG подбирает звуки на kick, 808, snare, хэты, мелодию. Чем больше разных паков — тем разнообразнее биты.",
  },
  {
    id: "regenerate",
    q: "Каждый бит звучит одинаково?",
    a: "Нажми Перегенерировать на главной. Не копируй один и тот же промпт слово в слово. Добавь больше китов в библиотеку — иначе матчер застрянет на одном паке.",
  },
  {
    id: "audio_bad",
    q: "Странный звук, треск, «пердеж»?",
    a: "Чаще всего это выход звука FL, не баг PLG. В FL: Options → Audio → ASIO (на Windows ASIO4ALL). Не проверяй 808 на Bluetooth-колонке — только проводные наушники или мониторы.",
  },
  {
    id: "melody",
    q: "Мелодии нет или только «пики»?",
    a: "Melody — это MIDI + короткий pluck в Sampler FL, хук, а не целый луп трека. Открой FL и соло канала Melody. Если не заходит — Перегенерировать или промпт вроде dark trap / rage, 140 bpm.",
  },
  {
    id: "smart_plan",
    q: "Что такое умный план?",
    a: "Опция перед генерацией: ИИ набрасывает BPM, тональность и структуру (~30 сек). Включай для точного ТЗ, выключай — быстрее и свободнее.",
  },
  {
    id: "rebake",
    q: "Что такое Rebake в Инструментах?",
    a: "Пересобирает текущий паттерн в новый .flp и MIDI без повторного вызова ИИ. Нужен после смены сэмплов/настроек или если FL не подхватил последний экспорт.",
  },
  {
    id: "stems",
    q: "Что за .mid внизу карточки бита?",
    a: "Экспорт стемов: Kick, Snare, 808, Hi-Hats, Melody и т.д. Лежат в output_midi/ в папке PLG. В FL уже открывается основная сессия — стемы для продвинутого редактирования.",
  },
  {
    id: "ownership_legal",
    q: "Кому принадлежат биты и есть ли роялти?",
    a: "Все сгенерированные биты (MIDI, .flp, стемы) — ваши. Pluggin' не претендует на авторство, не берёт роялти и долю с релизов. Лицензии на импортированные вами сэмплы — ваша зона ответственности. Подробно: Справка → Legal → Оферта.",
  },
  {
    id: "similarity_legal",
    q: "А если бит похож на чужой трек?",
    a: "ИИ может случайно дать схожую мелодию или грув. Платформа не проверяет плагиат и не отвечает за претензии правообладателей или блокировки на стримингах. Перед коммерческим релизом оцени риски сам (сэмплы, clearance).",
  },
  {
    id: "privacy_data",
    q: "Какие данные PLG хранит в облаке?",
    a: "Email, привязка устройства, статус оплаты, метаданные битов (BPM, жанр, санитизированный MIDI без путей к диску), оценки и хэш промпта — без полного текста. Аудио, .flp и библиотека сэмплов остаются на ПК. Подробно: Справка → Legal → Конфиденциальность.",
  },
  {
    id: "consumer_ru",
    q: "Подписка, автопродление и возврат (РФ)?",
    a: "1 пробная генерация, затем разовый платёж 499 ₽ за пакет 30 генераций / 30 дней через Продамус (резервно — ЮKassa) — без автопродления, новый пакет покупается вручную по мере необходимости. Возврат в течение 14 дней с первой оплаты при минимальном использовании — Справка → Legal → Возвраты.",
  },
  {
    id: "subscribe",
    q: "Как оформить подписку?",
    a: "Настройки → Подписаться. СНГ: 499 ₽ через Продамус (резервно — ЮKassa), разовый платёж за 30 генераций / 30 дней. Международные платежи пока недоступны. Оплата открывается в браузере.",
  },
  {
    id: "quota",
    q: "Лимиты и триал?",
    a: "1 пробная генерация, затем 30 генераций за 30 дней без дневного лимита. Закончились раньше срока — можно купить новый период ещё на 30 генераций. Квота обновляется с каждым оплаченным периодом. При сбое оплаты действует grace-доступ.",
  },
  {
    id: "samples",
    q: "Права на сэмплы и биты?",
    a: "Биты — твои. Стартовые звуки PLG — royalty-free для выхода из приложения. Импортированные киты — смотри лицензию пака.",
  },
  {
    id: "password",
    q: "Забыл пароль?",
    a: "На экране входа — «Забыли пароль?». 6-значный код придёт на почту (проверь спам). Саппорт отвечает за 24–48 ч.",
  },
  {
    id: "refund",
    q: "Возврат и отмена?",
    a: "Отмена в любой момент в кабинете платёжки. Политика возврата — Справка → Legal. Пиши help@example.com с чеком.",
  },
  {
    id: "status",
    q: "Сервис лежит?",
    a: "Смотри Telegram-канал обновлений. Генерация может тормозить при перегрузке AI-провайдеров.",
  },
];

export const FAQ_BY_LOCALE: Record<Locale, FaqItem[]> = {
  en: ESSENTIAL_EN,
  ru: ESSENTIAL_RU,
};

export function faqForLocale(locale: Locale): FaqItem[] {
  return FAQ_BY_LOCALE[locale] ?? ESSENTIAL_EN;
}

// Thematic grouping, purely presentational — question/answer text above is
// untouched. Groups are id-based so they stay correct even if item order in
// EN/RU above ever changes; any id that isn't listed here still renders
// (HelpView appends it, ungrouped) instead of silently disappearing.
const EN_GROUPS: FaqGroup[] = [
  { title: "Generation", ids: ["how_create", "where_listen", "regenerate", "audio_bad", "melody", "smart_plan"] },
  { title: "FL Studio & tools", ids: ["fl_scripts", "rebake", "stems", "library"] },
  { title: "Rating & feedback", ids: ["rating"] },
  { title: "Rights & ownership", ids: ["ownership_legal", "similarity_legal", "samples"] },
  { title: "Subscription & billing", ids: ["consumer_ru", "subscribe", "quota", "refund"] },
  { title: "Account & support", ids: ["privacy_data", "password", "status"] },
];

const RU_GROUPS: FaqGroup[] = [
  { title: "Генерация", ids: ["how_create", "where_listen", "regenerate", "audio_bad", "melody", "smart_plan"] },
  { title: "FL Studio и инструменты", ids: ["fl_scripts", "rebake", "stems", "library"] },
  { title: "Оценка и обратная связь", ids: ["rating"] },
  { title: "Права и авторство", ids: ["ownership_legal", "similarity_legal", "samples"] },
  { title: "Подписка и оплата", ids: ["consumer_ru", "subscribe", "quota", "refund"] },
  { title: "Аккаунт и техподдержка", ids: ["privacy_data", "password", "status"] },
];

export const FAQ_GROUPS_BY_LOCALE: Record<Locale, FaqGroup[]> = {
  en: EN_GROUPS,
  ru: RU_GROUPS,
};

export function faqGroupsForLocale(locale: Locale): FaqGroup[] {
  return FAQ_GROUPS_BY_LOCALE[locale] ?? EN_GROUPS;
}

export type Locale = "en" | "ru";

export type PromptCard = {
  title: string;
  subtitle: string;
  prompt: string;
};

export type Messages = {
  nav: {
    home: string;
    session: string;
    library: string;
    help: string;
    account: string;
    settings: string;
  };
  quota: {
    label: string;
    remaining: string;
    used: string;
    daysReset: string;
    daysUnit: string;
  };
  common: {
    loading: string;
    save: string;
    saving: string;
    open: string;
    openExplorer: string;
    files: string;
    version: string;
    updated: string;
    error: string;
    dash: string;
    cancel: string;
  };
  status: {
    loading: string;
    needApiKey: string;
    readyOpenFl: string;
    readyDescribe: string;
    starting: string;
    generating: string;
    checkNetwork: string;
    longWait: string;
    phases: {
      planning: string;
      generating: string;
      finalizing: string;
      preview: string;
      done: string;
      splitting: string;
    };
    baked: string;
    error: string;
    openingFl: string;
    flError: string;
    flOpened: string;
    generationFailed: string;
  };
  errors: {
    network: string;
    server: string;
    jobLost: string;
    auth: string;
    quota: string;
    rateLimit: string;
    subscription: string;
    device: string;
    validation: string;
    config: string;
    account: string;
    flIntegrationFailed: string;
    somethingWentWrong: string;
    retry: string;
    settingsSaveFailed: string;
    noSupportedFiles: string;
    archiveBusy: string;
    archiveFailed: string;
    archiveFolder: string;
    noLibrary: string;
    genFailed: string;
    genNoFlp: string;
    genNoMidi: string;
    genMidiUnreadable: string;
    genMidiUnusable: string;
    genKitProblem: string;
    genQualityGate: string;
  };
  notice: {
    sampleFallback: string;
    laneKick: string;
    lane808: string;
    laneHats: string;
  };
  offline: {
    title: string;
    desc: string;
    generationFailed: string;
    retry: string;
    checking: string;
    cloudTarget: string;
    switchLocal: string;
    switchLocalFailed: string;
    openSettings: string;
  };
  regenerate: {
    button: string;
    confirm: string;
    minusOne: string;
  };
  flOnboard: {
    noFlTitle: string;
    noFlDesc: string;
    detected: string;
    compatibility: string;
    getFl: string;
    openFailed: string;
    openFailedNamed: string;
  };
  updates: {
    title: string;
    check: string;
    checking: string;
    available: string;
    upToDate: string;
    download: string;
    apply: string;
    failed: string;
  };
  home: {
    eyebrow: string;
    title: string;
    desc: string;
    samplesInLibrary: string;
    createBeat: string;
    generating: string;
    openInFl: string;
    yourPrompt: string;
    describeBeat: string;
    yourBeat: string;
    openFolder: string;
    ideas: string;
    promptPlaceholder: string;
    inspiration: string;
    quickPrompts: string;
    noResults: string;
    smartPlanLabel: string;
    smartPlanHintShort: string;
    smartPlanHintAuto: string;
    smartPlanHintManual: string;
    detailShort: string;
    detailMedium: string;
    detailDetailed: string;
    attachedMidi: string;
    attachedReference: string;
    clearAttachment: string;
    respectUploadedScale: string;
    detectingGenre: string;
    detectedGenreLabel: string;
    genreUnavailable: string;
    genrePickPlaceholder: string;
    genrePickAria: string;
    /* Home hero — optional on purpose. `home` is not one of the namespaces
       merged onto `en` in i18n/index.tsx, so a required key here would force
       all 8 other locale packs to restate it. Optional + t()'s `?? en`
       fallback is what makes this copy render in English everywhere, which is
       the intended design. See the note in locales/en.ts. */
    heroTitle?: string;
    heroEmpty?: string;
    heroAddAria?: string;
    heroMakeBeat?: string;
    heroMaterialCount?: string;
    heroSendAria?: string;
    heroDropHere?: string;
    heroImporting?: string;
    // Real error copy, not set-piece wordmark text — translated per-locale
    // unlike its siblings above. See the note in locales/en.ts.
    heroUnsupported?: string;
    heroImported?: string;
  };
  session: {
    title: string;
    desc: string;
    beatReady: string;
    noBeat: string;
    prompt: string;
    provider: string;
    connected: string;
    offline: string;
    bpm: string;
    style: string;
    sampleChop: string;
    chopDetail: string;
    stemExport: string;
    openStems: string;
    matchedKit: string;
    openInFl: string;
    newBeat: string;
    rateBeat: string;
    rateUp: string;
    rateDown: string;
    rateThanks: string;
    rateFailed: string;
    rateLearning: string;
    rateSubtitle: string;
    rateBeforeFl: string;
    rateAfterFl: string;
    rateCollecting: string;
    rateModelActive: string;
    rateFavoriteGenre: string;
    rateBadgeLive: string;
  };
  library: {
    title: string;
    desc: string;
    folder: string;
    scan: string;
    scanning: string;
    importKit: string;
    importing: string;
    // Optional (not required in every LocalePack) — same reasoning as
    // home.heroTitle etc.: `library` isn't one of the namespaces the i18n
    // provider merges onto `en` at runtime, so t()'s own per-key fallback is
    // what makes an English default work everywhere until each locale adds
    // its own translation.
    openDrumKits?: string;
    openMidi?: string;
    stats: string;
    audio: string;
    totalFiles: string;
    howToImport: string;
    howToImportDesc: string;
    organizeOnImport: string;
    scanDone: string;
    scanFailed: string;
    importFailed: string;
    pickFailed: string;
    midiSection: string;
    midiEmpty: string;
    midiFolderHint: string;
    referenceSection: string;
    referenceEmpty: string;
    attach: string;
    attached: string;
    detach: string;
    goToComposer: string;
    moreFiles: string;
  };
  tools: {
    title: string;
    desc: string;
    descNoRebake: string;
    stemSplit: string;
    stemSplitDesc: string;
    demucsReady: string;
    demucsOffline: string;
    noFile: string;
    pickAudio: string;
    splitStems: string;
    splitting: string;
    sessionLibrary: string;
    rebake: string;
    scanLibrary: string;
    importKit: string;
    rebakeHint: string;
    libraryCount: string;
    referenceReverse: string;
    referenceReverseDesc: string;
    referenceReady: string;
    referenceOffline: string;
    applyReference: string;
    applyingReference: string;
    referenceApplied: string;
    referenceFailed: string;
  };
  help: {
    title: string;
    desc: string;
    documents: string;
    about: string;
    openProject: string;
    docsMissing: string;
    openDocFailed: string;
    legal: string;
    legalDesc: string;
    support: string;
    supportDesc: string;
    supportSla: string;
    faq: string;
    statusPage: string;
    updatesChannel: string;
    docs: Record<string, { title: string }>;
  };
  account: {
    title: string;
    desc: string;
    localDesc: string;
    profile: string;
    access: string;
    accessActive: string;
    accessPaymentFailed: string;
    accessTrial: string;
    accessTrialEnded: string;
    accessInactive: string;
    renewalIn: string;
    purchaseTitle: string;
    trialBeatHint: string;
    purchaseDesc: string;
    purchasePlanLine: string;
    purchaseButton: string;
    retryPaymentButton: string;
    manage: string;
    quotaEnded: string;
    renewAccessDesc: string;
    trialEndedTitle: string;
    trialEndedDesc: string;
    renewAccess: string;
    cancel: string;
    quotaUnavailable: string;
    quotaUnavailableDesc: string;
    retry: string;
    checkoutOpened: string;
    refreshStatus: string;
    checkoutLinkFailed: string;
    checkoutOpenFailed: string;
    checkoutOpenedNotice: string;
    previewTitle: string;
    previewTrialEnded: string;
    previewExpired: string;
    previewActive: string;
    previewCheckout: string;
    previewDesc: string;
  };
  settings: {
    title: string;
    archive: string;
    archiveTitle: string;
    archiveMessage: string;
    archiveMidi: string;
    archiveLibrary: string;
    archiveBoth: string;
    archiveConfirm: string;
    archiveDone: string;
    desc: string;
    language: string;
    accountQuota: string;
    aiProvider: string;
    provider: string;
    geminiKey: string;
    anthropicKey: string;
    flStudio: string;
    flStudioDesc: string;
    flDetected: string;
    flExecutable: string;
    flBrowse: string;
    flBrowsePlaceholder: string;
    flAutoDetect: string;
    flPickFailed: string;
    flNotFound: string;
    flUsingAuto: string;
    flStatusFound: string;
    flStatusFoundVersion: string;
    flStatusMissing: string;
    autoOpenFl: string;
    autoPreview: string;
    advanced: string;
    cloudMode: string;
    cloudModeLabel: string;
    cloudModeDesc: string;
    cloudUrl: string;
    localModeHint: string;
    sampleLibrary: string;
    libraryFolder: string;
    starterSounds: string;
    starterDesc: string;
    bundledPool: string;
    starterKit: string;
    incomingDrops: string;
    about: string;
    logout: string;
    saved: string;
    saveFailed: string;
    installFailed: string;
    scriptsInstalled: string;
    subscription: string;
    subscriptionDesc: string;
    subscribe: string;
    subscribing: string;
    subscriptionActive: string;
    subscriptionTrial: string;
    subscriptionGrace: string;
    subscriptionExpired: string;
    trialRemaining: string;
    graceRemaining: string;
    terms: string;
    privacy: string;
    refund: string;
    deleteAccount: string;
    deleteConfirm: string;
    deleteFailed: string;
    aiDisclaimer: string;
    feedback: string;
    feedbackDesc: string;
    feedbackCategory: string;
    feedbackMessage: string;
    feedbackAttachLog: string;
    feedbackSend: string;
    feedbackSending: string;
    feedbackSent: string;
    feedbackFailed: string;
    feedbackCatBug: string;
    feedbackCatBilling: string;
    feedbackCatFeature: string;
    feedbackCatGeneral: string;
  };
  player: {
    working: string;
    openInFl: string;
    createBeat: string;
    describeBeat: string;
    hint: string;
    preview: string;
    rendering: string;
    playing: string;
    duration: string;
  };
  topbar: {
    placeholder: string;
  };
  kit: {
    title: string;
    matching: string;
    fromLibrary: string;
    starter: string;
    roles: Record<string, string>;
    randomAria: string;
    randomHint: string;
    autoDone: string;
    randomDone: string;
    randomKept: string;
    pinnedReplaced: string;
  };
  tester: {
    badgeHint: string;
    expired: string;
    linksOff: string;
  };
  sounds: {
    title: string;
    openAria: string;
    openHint: string;
    close: string;
    cellsAria: string;
    cells: Record<string, string>;
    cellAuto: string;
    cellAutoHint: string;
    clearCell: string;
    hint: string;
    listAria: string;
    assignAria: string;
    loading: string;
    empty: string;
    loadFailed: string;
    noPreview: string;
    playFailed: string;
    unusable: string;
    backToAuto: string;
    errors: Record<string, string>;
  };
  console: {
    title: string;
    clipperBanner: string;
    bake: string;
    flip: string;
    chaos: string;
    clipperOn: string;
    clipperOff: string;
    clipperTip: string;
    applying: string;
    needBeat: string;
  };
  blueprint: {
    title: string;
  };
  auth: {
    title: string;
    desc: string;
    signupTitle: string;
    signupDesc: string;
    email: string;
    password: string;
    login: string;
    signup: string;
    loggingIn: string;
    signingUp: string;
    forgot: string;
    resetSent: string;
    resetTitle: string;
    passwordChanged: string;
    resetDesc: string;
    newPassword: string;
    resetCode: string;
    sendResetCode: string;
    sendingResetCode: string;
    emailRequired: string;
    resetPassword: string;
    resettingPassword: string;
    passwordMustDiffer: string;
    passwordMin: string;
    signupLogin: string;
    logout: string;
    haveAccount: string;
    newAccount: string;
    acceptTerms: string;
    acceptTermsPrefix: string;
    acceptTermsMiddle: string;
    acceptTermsSuffix: string;
    terms: string;
    privacy: string;
    back: string;
    confirmAge: string;
    legalRequired: string;
    aiDisclaimer: string;
    emailTaken: string;
    emailNotConfirmed: string;
    invalidCreds: string;
    verifyTitle: string;
    verifyDesc: string;
    verificationCode: string;
    verifyEmail: string;
    verifyingEmail: string;
    resendCode: string;
    codeResent: string;
  };
  prompts: Record<string, PromptCard>;
};

/** Locale files may omit keys merged from `en` at runtime. */
export type LocalePack = Omit<
  Messages,
  | "nav"
  | "help"
  | "account"
  | "settings"
  | "tools"
  | "auth"
  | "offline"
  | "regenerate"
  | "flOnboard"
  | "updates"
  | "errors"
  | "status"
> & {
  nav?: Partial<Messages["nav"]>;
  errors?: Partial<Messages["errors"]>;
  status?: Partial<Messages["status"]> & {
    phases?: Partial<Messages["status"]["phases"]>;
  };
  auth?: Partial<Messages["auth"]>;
  help?: Partial<Messages["help"]>;
  account?: Partial<Messages["account"]>;
  settings?: Partial<Messages["settings"]>;
  tools?: Partial<Messages["tools"]>;
  offline?: Partial<Messages["offline"]>;
  regenerate?: Partial<Messages["regenerate"]>;
  flOnboard?: Partial<Messages["flOnboard"]>;
  updates?: Partial<Messages["updates"]>;
};

// PLG deliberately supports only Russian and English (see detectLocale()
// below). The other locale packs (es/pt/zh/ja/fr/de/ar) were removed
// 2026-09-12 — they were unreachable dead code (never offered as a choice,
// several never received translations for the newer auth flows) that still
// shipped inside web/dist, including stale third-party brand mentions
// (Splice/a kit pack) the live en/ru copy had already dropped.
export const LOCALE_OPTIONS: { id: Locale; label: string }[] = [
  { id: "en", label: "English" },
  { id: "ru", label: "Русский" },
];

// No RTL locale is supported any more (ar was the only one).
export const RTL_LOCALES: Locale[] = [];

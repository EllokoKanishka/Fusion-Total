const ELEMENT_IDS = [
  'appRoot', 'dictationToggleBtn', 'dictationWorkspace', 'dictationCloseBtn', 'dictationTitleInput',
  'dictationEditor', 'dictationMicBtn', 'dictationStopSpeechBtn', 'dictationCommandsToggle', 'dictationVoiceCommandBtn',
  'dictationVoiceSelect', 'dictationAssistantSelect', 'dictationAssistantStatus', 'dictationAssistantInstallBtn',
  'dictationUndoBtn', 'dictationRedoBtn', 'dictationBackBtn', 'dictationReadBtn', 'dictationForwardBtn', 'dictationUseReaderBtn',
  'dictationDownloadBtn', 'dictationDownloadPdfBtn', 'dictationPdfPageNumbers', 'dictationNewBtn', 'dictationClearBtn', 'dictationCommandInput', 'dictationCommandBtn',
  'dictationStatus', 'dictationSessions', 'dictationSessionsList', 'dictationPlayer', 'dictationStats', 'dictationSaveStatus',
  'dictationModeSelect', 'dictationReviewStatus', 'dictationReviewPanel', 'dictationReviewTitle', 'dictationReviewOriginal',
  'dictationReviewProposed', 'dictationReviewNote', 'dictationReviewApplyBtn', 'dictationReviewDiscardBtn',
  'quickTextInput', 'quickReadStartBtn', 'quickReadCursorBtn', 'quickClearBtn', 'quickTextInfo',
  'dropzone', 'chooseFileBtn', 'fileInput', 'uploadInfo', 'importProgress', 'autoReadToggle',
  'pdfToWordTool', 'pdfToWordInput', 'pdfToWordInfo', 'pdfToWordDownload', 'referenceModeToggle',
  'prepareBtn', 'cancelPrepareBtn', 'clearDocBtn', 'prepareInfo', 'prepareProgress', 'audioExportMode',
  'audioExportBlockWrap', 'audioExportBlockInput', 'audioExportRangeWrap', 'audioExportStartInput',
  'audioExportEndInput', 'audioExportBtn', 'audioExportCancelBtn', 'audioExportInfo',
  'audioExportDownload', 'notesSummary', 'noteInput', 'saveNoteBtn', 'notesInfo', 'notesList',
  'docTitle', 'docMeta', 'chunk', 'ttsChip', 'ttsDot', 'ttsStatus', 'sttChip', 'sttDot', 'sttStatus',
  'log', 'player', 'readerPlayerToggleBtn', 'readerPlayerSeek', 'readerPlayerTime', 'prevBtn', 'readBtn', 'repeatBtn', 'nextBtn', 'jumpInput', 'jumpBtn',
  'continuousToggle', 'readerPlaybackRate', 'readerPlaybackRateValue', 'readingHighlightStatus', 'readingHighlightColor', 'chatLog', 'chatInput', 'sendChatBtn', 'clearLabHistoryBtn', 'reasoningNormalBtn',
  'reasoningThinkingBtn', 'reasoningSupremeBtn', 'reasoningPensamientoCriticoBtn', 'profileSelect',
  'veilSelect', 'chatProviderSelect', 'freeModeBtn', 'reasoningCaption', 'dialogueBtn', 'dialogueInfo', 'dialoguePlayer',
  'labFocus', 'mainDocTitle', 'mainDocMeta', 'referenceList', 'voiceSelect',
  'mediaTranscribeBtn', 'mediaTranscribeInput', 'mediaTranslateBtn', 'mediaTranslateInput',
  'mediaOriginalPdfToggle', 'mediaTranslatedPdfToggle', 'mediaSpanishAudioToggle',
  'mediaSttPromptInput', 'mediaSttHotwordsInput', 'mediaPostCorrectionToggle',
  'mediaInfo', 'mediaProgress', 'mediaCancelBtn', 'mediaMountBtn',
  'mediaPdfDownload', 'mediaTranslatedPdfDownload', 'mediaAudioDownload'
];

export function collectElements(documentRoot = document) {
  return Object.fromEntries(ELEMENT_IDS.map(id => [id, documentRoot.getElementById(id)]));
}

export { ELEMENT_IDS };

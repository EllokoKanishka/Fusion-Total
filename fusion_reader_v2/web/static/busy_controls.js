function busyControlDocumentLoaded(status) {
  return Boolean(status && status.document && status.document.loaded);
}

function busyControlDocumentHasText(status) {
  return Boolean(String(status && status.text || '').trim());
}

function computeControlAvailability(status, noteText = '') {
  const data = status && typeof status === 'object' ? status : {};
  const documentLoaded = busyControlDocumentLoaded(data);
  const documentHasText = busyControlDocumentHasText(data);
  const noteFilled = Boolean(String(noteText || '').trim());
  const docId = String(data.doc_id || '').trim();
  return {
    prevBtn: documentLoaded,
    nextBtn: documentLoaded,
    jumpBtn: documentLoaded,
    jumpInput: documentLoaded,
    readBtn: documentLoaded && documentHasText,
    repeatBtn: documentLoaded && documentHasText,
    sendChatBtn: true,
    saveNoteBtn: Boolean(docId) && noteFilled,
  };
}

function applyControlState(elements, availability, busyLeaseCount) {
  const busy = Number(busyLeaseCount || 0) > 0;
  const controls = [
    ['prevBtn', 'prevBtn'],
    ['nextBtn', 'nextBtn'],
    ['jumpBtn', 'jumpBtn'],
    ['jumpInput', 'jumpInput'],
    ['readBtn', 'readBtn'],
    ['repeatBtn', 'repeatBtn'],
    ['sendChatBtn', 'sendChatBtn'],
    ['saveNoteBtn', 'saveNoteBtn'],
  ];
  for (const [key, availabilityKey] of controls) {
    const el = elements && elements[key];
    if (!el) {
      continue;
    }
    el.disabled = busy || !Boolean(availability && availability[availabilityKey]);
  }
}

function createBusyControlState(applyFn, initialStatus = null, initialNoteText = '') {
  let status = initialStatus;
  let noteText = String(initialNoteText || '');
  let busyLeaseCount = 0;

  function apply() {
    applyFn(computeControlAvailability(status, noteText), busyLeaseCount);
  }

  function beginBusyLease() {
    busyLeaseCount += 1;
    apply();
    let released = false;
    return () => {
      if (released) {
        return;
      }
      released = true;
      busyLeaseCount = Math.max(0, busyLeaseCount - 1);
      apply();
    };
  }

  function setStatus(nextStatus, nextNoteText = noteText) {
    status = nextStatus;
    noteText = String(nextNoteText || '');
    apply();
  }

  function setNoteText(nextNoteText) {
    noteText = String(nextNoteText || '');
    apply();
  }

  function sync() {
    apply();
  }

  function getBusyLeaseCount() {
    return busyLeaseCount;
  }

  apply();

  return {
    beginBusyLease,
    setStatus,
    setNoteText,
    sync,
    getBusyLeaseCount,
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    busyControlDocumentLoaded,
    busyControlDocumentHasText,
    computeControlAvailability,
    applyControlState,
    createBusyControlState,
  };
}


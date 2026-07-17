#!/usr/bin/env node
const assert = require('node:assert/strict');
const {
  applyControlState,
  createBusyControlState,
} = require('../fusion_reader_v2/web/static/busy_controls.js');

function makeElements() {
  return {
    prevBtn: { disabled: null },
    nextBtn: { disabled: null },
    jumpBtn: { disabled: null },
    jumpInput: { disabled: null },
    readBtn: { disabled: null },
    repeatBtn: { disabled: null },
    sendChatBtn: { disabled: null },
    saveNoteBtn: { disabled: null },
  };
}

function makeStatus(loaded, text = 'Bloque activo', docId = 'doc-1') {
  return {
    document: { loaded: Boolean(loaded) },
    text: loaded ? text : '',
    doc_id: loaded ? docId : '',
  };
}

function snapshotDisabled(els) {
  return {
    prevBtn: Boolean(els.prevBtn.disabled),
    nextBtn: Boolean(els.nextBtn.disabled),
    jumpBtn: Boolean(els.jumpBtn.disabled),
    jumpInput: Boolean(els.jumpInput.disabled),
    readBtn: Boolean(els.readBtn.disabled),
    repeatBtn: Boolean(els.repeatBtn.disabled),
    sendChatBtn: Boolean(els.sendChatBtn.disabled),
    saveNoteBtn: Boolean(els.saveNoteBtn.disabled),
  };
}

function makeController(status, noteText = '') {
  const els = makeElements();
  const controller = createBusyControlState(
    (availability, busyLeaseCount) => applyControlState(els, availability, busyLeaseCount),
    status,
    noteText,
  );
  return { els, controller };
}

function assertReadNavigationEnabled(els, message) {
  const snapshot = snapshotDisabled(els);
  assert.equal(snapshot.prevBtn, false, `${message}: prevBtn`);
  assert.equal(snapshot.nextBtn, false, `${message}: nextBtn`);
  assert.equal(snapshot.jumpBtn, false, `${message}: jumpBtn`);
  assert.equal(snapshot.jumpInput, false, `${message}: jumpInput`);
  assert.equal(snapshot.readBtn, false, `${message}: readBtn`);
  assert.equal(snapshot.repeatBtn, false, `${message}: repeatBtn`);
}

function assertReadNavigationDisabled(els, message) {
  const snapshot = snapshotDisabled(els);
  assert.equal(snapshot.prevBtn, true, `${message}: prevBtn`);
  assert.equal(snapshot.nextBtn, true, `${message}: nextBtn`);
  assert.equal(snapshot.jumpBtn, true, `${message}: jumpBtn`);
  assert.equal(snapshot.jumpInput, true, `${message}: jumpInput`);
  assert.equal(snapshot.readBtn, true, `${message}: readBtn`);
  assert.equal(snapshot.repeatBtn, true, `${message}: repeatBtn`);
}

function assertBusyCount(controller, expected, message) {
  assert.equal(controller.getBusyLeaseCount(), expected, message);
}

function testConcurrentLeasesKeepBusyUntilTheLastRelease() {
  const { els, controller } = makeController(makeStatus(true), 'nota lista');
  const releaseA = controller.beginBusyLease();
  const releaseB = controller.beginBusyLease();

  assertBusyCount(controller, 2, 'dos leases concurrentes');
  assertReadNavigationDisabled(els, 'busy con dos leases');
  assert.equal(snapshotDisabled(els).sendChatBtn, true, 'sendChat queda busy');

  releaseA();
  assertBusyCount(controller, 1, 'liberar el primero conserva busy');
  assertReadNavigationDisabled(els, 'busy con un lease restante');

  releaseB();
  assertBusyCount(controller, 0, 'liberar el segundo termina busy');
  assertReadNavigationEnabled(els, 'control vuelve tras el último release');
}

function testReleaseIsIdempotent() {
  const { els, controller } = makeController(makeStatus(true), 'nota lista');
  const release = controller.beginBusyLease();

  assertBusyCount(controller, 1, 'lease único activo');
  release();
  release();

  assertBusyCount(controller, 0, 'release idempotente no decrementa dos veces');
  assertReadNavigationEnabled(els, 'lease idempotente deja controles semánticamente habilitados');
}

function testClearKeepsReadRepeatNavigationDisabledAfterRelease() {
  const { els, controller } = makeController(makeStatus(false), '');
  const release = controller.beginBusyLease();

  release();

  assertBusyCount(controller, 0, 'clear libera su propio lease');
  assertReadNavigationDisabled(els, 'clear mantiene lectura y navegación deshabilitadas sin documento');
  assert.equal(snapshotDisabled(els).sendChatBtn, false, 'chat sigue disponible aunque no haya documento');
}

function testLoadedDocumentReenablesActionsAfterLeaseRelease() {
  const { els, controller } = makeController(makeStatus(true), '');
  const release = controller.beginBusyLease();

  release();

  assertBusyCount(controller, 0, 'carga termina con busy en cero');
  assertReadNavigationEnabled(els, 'documento cargado habilita acciones semánticas');
  assert.equal(snapshotDisabled(els).saveNoteBtn, true, 'guardar nota sigue deshabilitado sin texto');

  controller.setNoteText('Ya tengo nota');
  assert.equal(snapshotDisabled(els).saveNoteBtn, false, 'guardar nota se habilita con texto');
}

function testOldLeaseDoesNotReleaseNewLease() {
  const { els, controller } = makeController(makeStatus(true), 'nota lista');
  const releaseOld = controller.beginBusyLease();
  const releaseNew = controller.beginBusyLease();

  releaseOld();
  assertBusyCount(controller, 1, 'terminar la operación vieja conserva la nueva');
  assertReadNavigationDisabled(els, 'operación nueva sigue ocupando la UI');

  releaseNew();
  assertBusyCount(controller, 0, 'terminar la operación nueva deja busy en cero');
  assertReadNavigationEnabled(els, 'operación nueva liberada restaura controles semánticos');
}

function testPrepareDoesNotDoubleDecrementOrStealForeignLease() {
  const { els, controller } = makeController(makeStatus(true), 'nota lista');
  const releaseRead = controller.beginBusyLease();
  const releasePrepare = controller.beginBusyLease();

  releasePrepare();
  releasePrepare();

  assertBusyCount(controller, 1, 'prepare no consume el lease ajeno');
  assertReadNavigationDisabled(els, 'lease de lectura sigue vigente durante prepare');

  releaseRead();
  assertBusyCount(controller, 0, 'leer + prepare termina exactamente en cero');
  assertReadNavigationEnabled(els, 'controles vuelven al soltar el último lease');
}

function testErrorFinallyReleasesOnlyOwnLease() {
  const { els, controller } = makeController(makeStatus(true), 'nota lista');
  const releaseRead = controller.beginBusyLease();

  let caught = false;
  try {
    const releaseErroring = controller.beginBusyLease();
    try {
      throw new Error('boom');
    } finally {
      releaseErroring();
    }
  } catch (err) {
    caught = true;
    assert.equal(err.message, 'boom');
  }

  assert.equal(caught, true, 'el flujo con error debe propagarse');
  assertBusyCount(controller, 1, 'finally libera exactamente el lease propio');
  assertReadNavigationDisabled(els, 'lease externo sigue activo después del error');

  releaseRead();
  assertBusyCount(controller, 0, 'el lease externo se puede liberar luego');
  assertReadNavigationEnabled(els, 'sin leases quedan los controles disponibles');
}

function main() {
  testConcurrentLeasesKeepBusyUntilTheLastRelease();
  testReleaseIsIdempotent();
  testClearKeepsReadRepeatNavigationDisabledAfterRelease();
  testLoadedDocumentReenablesActionsAfterLeaseRelease();
  testOldLeaseDoesNotReleaseNewLease();
  testPrepareDoesNotDoubleDecrementOrStealForeignLease();
  testErrorFinallyReleasesOnlyOwnLease();
  console.log('busy-controls: ok');
}

main();

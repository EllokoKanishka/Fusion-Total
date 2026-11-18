// Compatibility entrypoint: the browser and tests share js/busy.mjs.
// CommonJS callers can await loadBusyControls(); no implementation is duplicated.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { loadBusyControls: () => import('./js/busy.mjs') };
}

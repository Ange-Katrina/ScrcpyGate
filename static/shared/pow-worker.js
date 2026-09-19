/* A worker solves one bounded puzzle at a time; parent termination cancels it. */
'use strict';
importScripts('./pow-solver.js');
var busy = false;
self.onmessage = async function (event) {
  if (busy) return;
  busy = true;
  var data = event.data || {}, unreported = 0, last = performance.now();
  try {
    ScrcpyGatePow.validate(data.challenge);
    if (!Number.isInteger(data.index) || data.index < 0 || data.index >= 4) throw new Error('proof_of_work_bad_challenge');
    var result = await ScrcpyGatePow.solvePuzzle(data.challenge, data.index, null,
      performance.now() + Math.min(60000, Math.max(1, data.timeoutMs || 30000)), function (n) {
        unreported += n;
        if (performance.now() - last >= 100) {
          self.postMessage({ type: 'progress', hashes: unreported });
          unreported = 0;
          last = performance.now();
        }
      });
    if (unreported) self.postMessage({ type: 'progress', hashes: unreported });
    self.postMessage({ type: 'solved', index: data.index, number: result.number });
  } catch (error) {
    self.postMessage({ error: error.message === 'proof_of_work_timeout' ? error.message : 'proof_of_work_exhausted' });
  } finally { busy = false; }
};

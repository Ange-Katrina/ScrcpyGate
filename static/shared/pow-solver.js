/* ScrcpyGate PoW v1: bounded SHA-256 preimages, no third-party code. */
(function (global) {
  'use strict';
  var encoder = new TextEncoder();
  var script = typeof document !== 'undefined' && document.currentScript;
  // Capture while the script executes: document.currentScript is null later.
  var workerPath = script && script.src ? new URL('pow-worker.js', script.src).href : '';
  var DOMAIN = 'ScrcpyGate:login:pow:v1';
  function hex(bytes) { return Array.from(bytes, function (n) { return n.toString(16).padStart(2, '0'); }).join(''); }
  function check(challenge) {
    var p = challenge && challenge.parameters;
    if (!challenge || challenge.version !== 1 || challenge.provider !== 'builtin' || challenge.purpose !== 'login'
        || !/^[0-9a-f]{32}$/.test(challenge.id) || !p || p.algorithm !== 'SHA-256'
        || !/^[0-9a-f]{32}$/.test(p.salt) || !Number.isSafeInteger(p.max_number)
        || p.max_number < 0 || p.max_number > 134217727 || !Array.isArray(p.targets) || p.targets.length !== 4
        || !p.targets.every(function (target) { return /^[0-9a-f]{64}$/.test(target); })) {
      throw new Error('proof_of_work_bad_challenge');
    }
    if (!global.crypto || !global.crypto.subtle) throw new Error('webcrypto_unavailable');
    return p;
  }
  function abortError() { return new DOMException('Verification cancelled', 'AbortError'); }
  function checkStop(signal, deadline) {
    if (signal && signal.aborted) throw abortError();
    if (performance.now() >= deadline) throw new Error('proof_of_work_timeout');
  }
  async function puzzle(challenge, index, signal, deadline, progress) {
    var p = challenge.parameters;
    var prefix = DOMAIN + ':' + challenge.id + ':' + p.salt + ':' + index + ':';
    var hashes = 0, lastYield = performance.now();
    for (var start = 0; start <= p.max_number; start += 64) {
      checkStop(signal, deadline);
      var size = Math.min(64, p.max_number - start + 1);
      var batch = Array.from({ length: size }, function (_, offset) {
        return global.crypto.subtle.digest('SHA-256', encoder.encode(prefix + (start + offset)));
      });
      var results = await Promise.all(batch);
      checkStop(signal, deadline);
      hashes += results.length;
      if (progress) progress(results.length);
      for (var j = 0; j < results.length; j++) {
        if (hex(new Uint8Array(results[j])) === p.targets[index]) return { number: start + j, hashes: hashes };
      }
      // Only the main-thread fallback needs to yield for input and painting.
      // Per-batch worker timers incur the browser's nested-timer 4ms clamp.
      if (typeof document !== 'undefined' && performance.now() - lastYield >= 8) {
        await new Promise(function (resolve) { setTimeout(resolve, 0); });
        lastYield = performance.now();
      }
    }
    throw new Error('proof_of_work_exhausted');
  }
  async function inline(challenge, options, progress) {
    var solution = [], hashes = 0;
    for (var i = 0; i < 4; i++) {
      var result = await puzzle(challenge, i, options.signal, options.deadline, function (n) { hashes += n; progress(hashes); });
      solution.push(result.number);
    }
    return { solution: solution, hashes: hashes };
  }
  function workers(challenge, options, progress) {
    return new Promise(function (resolve, reject) {
      var pool = [], done = 0, next = 0, hashes = 0, solution = new Array(4), settled = false;
      var timer;
      function finish(error, result) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        pool.forEach(function (worker) { worker.terminate(); });
        if (options.signal) options.signal.removeEventListener('abort', cancel);
        if (error) reject(error); else resolve(result);
      }
      function cancel() { finish(abortError()); }
      function dispatch(worker) {
        if (next < 4) worker.postMessage({ challenge: challenge, index: next++, timeoutMs: Math.max(1, options.deadline - performance.now()) });
      }
      if (options.signal && options.signal.aborted) { cancel(); return; }
      if (options.signal) options.signal.addEventListener('abort', cancel, { once: true });
      timer = setTimeout(function () { finish(new Error('proof_of_work_timeout')); }, Math.max(1, options.deadline - performance.now()));
      try {
        for (var i = 0; i < Math.min(2, options.workers || 2) && !settled; i++) {
          var worker = new global.Worker(workerPath);
          pool.push(worker);
          worker.onerror = function () { finish(new Error('proof_of_work_worker_unavailable')); };
          worker.onmessage = function (event) {
            if (settled) return;
            var data = event.data || {};
            if (data.error) { finish(new Error(data.error)); return; }
            if (data.type === 'progress') { hashes += data.hashes; progress(hashes); return; }
            solution[data.index] = data.number;
            done += 1;
            if (done === 4) finish(null, { solution: solution, hashes: hashes });
            else dispatch(event.target);
          };
          dispatch(worker);
        }
      } catch (_) { finish(new Error('proof_of_work_worker_unavailable')); }
    });
  }
  async function solve(challenge, onProgress, options) {
    check(challenge);
    options = options || {};
    var started = performance.now(), lastReport = 0;
    var opts = { signal: options.signal, workers: options.workers,
      deadline: started + Math.min(60000, Math.max(1, options.timeoutMs || 30000)) };
    function progress(hashes) {
      if (onProgress && performance.now() - lastReport >= 100) {
        lastReport = performance.now();
        onProgress({ hashes: hashes, elapsedMs: lastReport - started });
      }
    }
    checkStop(opts.signal, opts.deadline);
    var result;
    if (workerPath && typeof global.Worker === 'function' && options.workers !== 0) {
      try { result = await workers(challenge, opts, progress); }
      catch (error) {
        if (error.message !== 'proof_of_work_worker_unavailable') throw error;
        result = await inline(challenge, opts, progress);
      }
    } else result = await inline(challenge, opts, progress);
    return { solution: result.solution, hashes: result.hashes, elapsedMs: performance.now() - started };
  }
  async function benchmark() {
    var challenge = { version: 1, provider: 'builtin', purpose: 'login', id: hex(crypto.getRandomValues(new Uint8Array(16))),
      parameters: { algorithm: 'SHA-256', salt: hex(crypto.getRandomValues(new Uint8Array(16))), max_number: 2047, targets: [] } };
    for (var i = 0; i < 4; i++) {
      var input = DOMAIN + ':' + challenge.id + ':' + challenge.parameters.salt + ':' + i + ':1023';
      challenge.parameters.targets.push(hex(new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(input)))));
    }
    return solve(challenge);
  }
  global.ScrcpyGatePow = { solve: solve, benchmark: benchmark, solvePuzzle: puzzle, validate: check, workerUrl: workerPath };
})(typeof window !== 'undefined' ? window : self);

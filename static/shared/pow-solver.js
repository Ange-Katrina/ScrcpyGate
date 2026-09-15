/* Proof-of-work solver for the login captcha (WebCrypto only, no dependencies).

   Protocol v2: find `number` such that
   SHA-256(challenge + ":" + salt + ":" + number) has `bits` leading zero bits.
   Digests are computed in batches with Promise.all to keep WebCrypto's
   per-call overhead from dominating the measurement.  Exposed as
   window.ScrcpyGatePow so the login page (and future consumers) can share it.
*/
(function (global) {
  "use strict";

  var encoder = new TextEncoder();
  var BATCH = 128;

  function bytesToHex(bytes) {
    var out = "";
    for (var i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, "0");
    return out;
  }

  function hexToBytes(hex) {
    var out = new Uint8Array(hex.length / 2);
    for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  function leadingZeroBits(bytes) {
    var count = 0;
    for (var i = 0; i < bytes.length; i++) {
      if (bytes[i] === 0) { count += 8; continue; }
      for (var bit = 7; bit >= 0; bit--) {
        if (bytes[i] & (1 << bit)) return count;
        count += 1;
      }
      return count;
    }
    return count;
  }

  async function solve(item, onProgress) {
    if (!global.crypto || !global.crypto.subtle) {
      throw new Error("webcrypto_unavailable");
    }
    var challenge = String(item && item.challenge || "");
    var salt = String(item && item.salt || "");
    var bits = parseInt(item && item.bits, 10);
    var maxNumber = parseInt(item && item.maxNumber, 10) || 1000000;
    if (!challenge || !salt || !(bits > 0)) throw new Error("proof_of_work_bad_challenge");
    var prefix = challenge + ":" + salt + ":";
    var nonce = 0;
    var hashes = 0;
    var started = performance.now();
    var lastReport = 0;
    while (nonce <= maxNumber) {
      var buffers = new Array(BATCH);
      for (var i = 0; i < BATCH; i++) buffers[i] = encoder.encode(prefix + (nonce + i));
      var digests = await Promise.all(buffers.map(function (buffer) { return crypto.subtle.digest("SHA-256", buffer); }));
      for (var j = 0; j < BATCH; j++) {
        if (leadingZeroBits(new Uint8Array(digests[j])) >= bits) {
          hashes += j + 1;
          return {
            number: nonce + j,
            hashes: hashes,
            elapsedMs: performance.now() - started,
            digest: bytesToHex(new Uint8Array(digests[j])),
          };
        }
      }
      hashes += BATCH;
      nonce += BATCH;
      var now = performance.now();
      if (onProgress && now - lastReport > 80) {
        lastReport = now;
        onProgress({ hashes: hashes, elapsedMs: now - started });
      }
    }
    throw new Error("proof_of_work_maxnumber");
  }

  global.ScrcpyGatePow = {
    solve: solve,
    leadingZeroBits: leadingZeroBits,
    bytesToHex: bytesToHex,
    hexToBytes: hexToBytes,
  };
})(window);

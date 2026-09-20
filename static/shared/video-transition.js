/* A bounded, local last-frame snapshot for decoder/orientation transitions.
 * Animate only the snapshot: live video geometry and touch mapping stay exact.
 * Pixels never leave the browser. No render loop runs during normal playback. */
(function (global) {
  'use strict';
  function reducedMotion() {
    return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function angleOf(transform) {
    try {
      var matrix = new DOMMatrixReadOnly(transform);
      return Math.atan2(matrix.b, matrix.a) * 180 / Math.PI;
    } catch (error) { return 0; }
  }
  function capture(video) {
    if (!video || !video.parentNode || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return null;
    var computed = global.getComputedStyle(video);
    if (Number(computed.opacity) === 0) return null;
    // getComputedStyle is live: snapshot primitives before decoder teardown or
    // grid layout changes can detach/reuse the source element.
    var style = {};
    ['width', 'height', 'left', 'top', 'objectFit', 'transform', 'transformOrigin', 'borderRadius'].forEach(function (key) {
      style[key] = computed[key];
    });
    var canvas = document.createElement('canvas');
    // Bound memory per viewer even when a device sends a 4K/8K stream.
    var factor = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.max(1, Math.round(video.videoWidth * factor));
    canvas.height = Math.max(1, Math.round(video.videoHeight * factor));
    try {
      var context = canvas.getContext('2d');
      if (!context) return null;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
    } catch (error) { return null; }
    canvas.className = 'sg-video-transition';
    canvas.setAttribute('aria-hidden', 'true');
    canvas.style.cssText = 'position:absolute;pointer-events:none;z-index:2;';
    ['width', 'height', 'left', 'top', 'objectFit', 'transform', 'transformOrigin', 'borderRadius'].forEach(function (key) {
      canvas.style[key] = style[key];
    });
    video.parentNode.appendChild(canvas);
    var animations = [];
    var disposed = false;
    var retiring = false;
    var frameId = null;
    var frameVideo = null;
    var poll = null;
    var fadeTimer = null;
    var layoutKey = '';
    var expiry = global.setTimeout(dispose, 8000);
    function dispose() {
      if (disposed) return;
      disposed = true;
      global.clearTimeout(expiry);
      global.clearTimeout(fadeTimer);
      global.clearInterval(poll);
      if (frameId !== null && frameVideo && frameVideo.cancelVideoFrameCallback) {
        frameVideo.cancelVideoFrameCallback(frameId);
      }
      animations.forEach(function (animation) { animation.cancel(); });
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      canvas.width = canvas.height = 1;
    }
    function moveTo(target, sourceTurn) {
      if (disposed || !target || !target.parentNode) return;
      var next = global.getComputedStyle(target);
      var nextKey = [next.width, next.height, next.transform, sourceTurn || 0].join(':');
      if (layoutKey === nextKey) return;
      layoutKey = nextKey;
      var fromAngle = angleOf(style.transform);
      var toAngle = angleOf(next.transform) + (sourceTurn || 0);
      var delta = ((toAngle - fromAngle + 540) % 360) - 180;
      var transposed = Math.abs(sourceTurn || 0) === 90;
      var width = parseFloat(style.width), height = parseFloat(style.height);
      var scale = Math.min(parseFloat(next.width) / (transposed ? height : width),
        parseFloat(next.height) / (transposed ? width : height));
      if (!(scale > 0) || !isFinite(scale)) return;
      var from = animations.length ? global.getComputedStyle(canvas).transform
        : 'translate(-50%, -50%) rotate(' + fromAngle + 'deg)';
      animations.forEach(function (animation) { animation.cancel(); });
      animations = [];
      var to = 'translate(-50%, -50%) rotate(' + (fromAngle + delta) + 'deg) scale(' + scale + ')';
      // Media elements in both viewers are centered on their containing stage.
      canvas.style.left = '50%';
      canvas.style.top = '50%';
      canvas.style.transformOrigin = 'center';
      canvas.style.transform = to;
      if (!reducedMotion() && canvas.animate) {
        animations.push(canvas.animate([{ transform: from }, { transform: to }], {
          duration: 300, easing: 'cubic-bezier(.22,1,.36,1)'
        }));
      }
    }
    function reveal() {
      if (disposed || retiring) return;
      retiring = true;
      var duration = reducedMotion() ? 0 : 160;
      canvas.style.transition = 'opacity ' + duration + 'ms linear';
      canvas.style.opacity = '0';
      fadeTimer = global.setTimeout(dispose, duration);
    }
    function revealWhenReady(target) {
      if (disposed) return;
      if (frameId !== null && frameVideo && frameVideo.cancelVideoFrameCallback) frameVideo.cancelVideoFrameCallback(frameId);
      global.clearInterval(poll);
      frameVideo = target;
      if (target.requestVideoFrameCallback) {
        frameId = target.requestVideoFrameCallback(function () {
          frameId = null;
          reveal();
        });
      } else {
        // Playback progress, rather than MSE sourceopen, is the fallback signal.
        var baseline = target.currentTime;
        poll = global.setInterval(function () {
          if (target.readyState >= 2 && target.currentTime !== baseline) {
            global.clearInterval(poll);
            reveal();
          }
        }, 50);
      }
    }
    return { element: canvas, landscape: video.videoWidth >= video.videoHeight,
      moveTo: moveTo, reveal: reveal, revealWhenReady: revealWhenReady, dispose: dispose };
  }
  global.ScrcpyGateVideoTransition = { capture: capture };
})(window);

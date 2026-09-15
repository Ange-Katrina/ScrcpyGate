/* 显示状态层：画面方向（自动摆正）与它需要的尺寸输入。

   背景（用户给的方向模型 + 我们自己的实测）：不要把三件事混成一个状态 ——
     1. 设备真实旋转（服务端读 `mCurrentOrientation`，见 ISSUE-153 的跟随逻辑）；
     2. 视频流的方向与尺寸（`sg-raw-v2-video` 解码后的 width/height）；
     3. 网页里画面元素的摆放方向（本模块的 rotation）。
   这个模块只管第 3 件以及它与第 2 件的换算：纯状态 + 纯数学，不碰 DOM。

   方向**完全自动**：设备转屏 → 流尺寸变化 → 按下面的规则选 0/90°；
   反过来的手动旋转按钮（自动 / ↺ / ↻）在 ISSUE-152/154/155 做过，用户实测「不好用、
   太挤太乱」，ISSUE-156 按用户要求整套撤掉，只保留自动。
*/
(function (global) {
  'use strict';

  // 面积增益达不到这个倍数就不动方向，避免接近正方形时来回抖。
  var AUTO_FIT_GAIN = 1.2;

  function normalizeRotation(value) {
    var number = Number(value);
    if (!isFinite(number)) return 0;
    var normalized = ((Math.round(number / 90) * 90) % 360 + 360) % 360;
    if (normalized === 90 || normalized === 180 || normalized === 270) return normalized;
    return 0;
  }

  function transposed(rotation) {
    var value = normalizeRotation(rotation);
    return value === 90 || value === 270;
  }

  function dimensions(size) {
    return {
      width: Math.max(0, Number(size && size.width) || 0),
      height: Math.max(0, Number(size && size.height) || 0)
    };
  }

  function containArea(width, height, boxWidth, boxHeight) {
    if (!(width > 0 && height > 0 && boxWidth > 0 && boxHeight > 0)) return 0;
    var scale = Math.min(boxWidth / width, boxHeight / height);
    return width * scale * height * scale;
  }

  /** 自动方向：只在「竖屏窗口 + 横屏画面」时才转 90° 让画面铺满高度；其余情况保持正向。
   *
   * 为什么不再单纯按面积取大：在宽屏（桌面）窗口里，把竖屏画面转 90° 面积确实更大，
   * 但内容会横过来（实测：工作台默认画面在桌面上变成横向的，用户报「正常应该是竖向的」）。
   * 画面方向应当跟随设备本身；竖屏窗口看横屏设备时旋转 90° 也只是一张「待横放的图」，
   * 全屏时浏览器还会自动锁定横屏，所以只在这一种情况下转。
   */
  function resolveAutoRotation(source, viewport) {
    var src = dimensions(source);
    var box = dimensions(viewport);
    if (!(src.width > 0 && src.height > 0 && box.width > 0 && box.height > 0)) return null;
    if (box.width >= box.height) return 0;
    if (src.width <= src.height) return 0;
    var unrotated = containArea(src.width, src.height, box.width, box.height);
    var swapped = containArea(src.height, src.width, box.width, box.height);
    // 接近正方形时增益很小，转了反而抖，保持正向。
    return swapped > unrotated * AUTO_FIT_GAIN ? 90 : 0;
  }

  function create(options) {
    options = options || {};
    var state = {
      rotation: normalizeRotation(options.rotation),
      source: dimensions(options.source),
      viewport: dimensions(options.viewport)
    };

    function snapshot() {
      return {
        rotation: state.rotation,
        transposed: transposed(state.rotation),
        source: { width: state.source.width, height: state.source.height },
        viewport: { width: state.viewport.width, height: state.viewport.height }
      };
    }

    function setSource(width, height) {
      var next = dimensions({ width: width, height: height });
      if (!(next.width > 0 && next.height > 0)) return state.source;
      state.source = next;
      return state.source;
    }

    function setViewport(width, height) {
      state.viewport = dimensions({ width: width, height: height });
      return state.viewport;
    }

    function setRotation(rotation) {
      state.rotation = normalizeRotation(rotation);
      return state.rotation;
    }

    function resolveAuto() {
      return resolveAutoRotation(state.source, state.viewport);
    }

    return Object.freeze({
      snapshot: snapshot,
      setSource: setSource,
      setViewport: setViewport,
      setRotation: setRotation,
      resolveAuto: resolveAuto
    });
  }

  global.ScrcpyGateDisplay = Object.freeze({
    create: create,
    AUTO_FIT_GAIN: AUTO_FIT_GAIN,
    normalizeRotation: normalizeRotation,
    transposed: transposed,
    containArea: containArea,
    resolveAutoRotation: resolveAutoRotation
  });
}(typeof window !== 'undefined' ? window : globalThis));

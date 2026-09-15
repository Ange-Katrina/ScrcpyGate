/* Raw v2 帧头解析（纯函数）。
 *
 * 该协议由服务端 `app/mirror_runtime.py` 生成：32 字节大端头 + H.264 负载。
 * 这里只做无状态的解析，供工作台单画面适配器（v2-adapter.js）与宫格播放器
 * （mirror-grid.js）共用，避免两份实现漂移。
 *
 * 头部布局：
 *   0..3   magic 'SGV2'
 *   4      版本（当前 1）
 *   5      flags：1=关键帧 2=含配置 4=不连续
 *   6..7   头长度（固定 32）
 *   8..11  帧序号（u32）
 *   12..15 配置代次（u32）
 *   16..19 保留
 *   20..23 保留
 *   24..25 宽（u16，raw 模式下为 0）
 *   26..27 高（u16，raw 模式下为 0）
 *   28..31 负载长度（u32）
 */
(function (global) {
  'use strict';

  var HEADER_LENGTH = 32;
  var MAX_PACKET_BYTES = 25165824;
  var FLAG_KEYFRAME = 0x01;
  var FLAG_CONFIG = 0x02;
  var FLAG_DISCONTINUITY = 0x04;

  function u16(bytes, offset) {
    return ((bytes[offset] << 8) | bytes[offset + 1]) >>> 0;
  }

  function u32(bytes, offset) {
    return ((bytes[offset] * 0x1000000) + ((bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3])) >>> 0;
  }

  function parse(data) {
    var bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    if (bytes.length < HEADER_LENGTH) throw new Error('raw-v2-short-header');
    if (bytes.length > MAX_PACKET_BYTES) throw new Error('raw-v2-packet-too-large');
    if (bytes[0] !== 0x53 || bytes[1] !== 0x47 || bytes[2] !== 0x56 || bytes[3] !== 0x32) throw new Error('raw-v2-magic');
    if (bytes[4] !== 1) throw new Error('raw-v2-version');
    var flags = bytes[5];
    if (flags & ~(FLAG_KEYFRAME | FLAG_CONFIG | FLAG_DISCONTINUITY)) throw new Error('raw-v2-flags');
    var headerLength = u16(bytes, 6);
    if (headerLength !== HEADER_LENGTH) throw new Error('raw-v2-header-length');
    var payloadLength = u32(bytes, 28);
    if (headerLength + payloadLength !== bytes.length) throw new Error('raw-v2-payload-length');
    return {
      sequence: u32(bytes, 8),
      configGeneration: u32(bytes, 12),
      width: u16(bytes, 24),
      height: u16(bytes, 26),
      keyframe: !!(flags & FLAG_KEYFRAME),
      containsConfig: !!(flags & FLAG_CONFIG),
      discontinuity: !!(flags & FLAG_DISCONTINUITY),
      payload: bytes.slice(headerLength)
    };
  }

  function sequenceIsNewer(next, current) {
    var distance = (Number(next) - Number(current)) >>> 0;
    return distance !== 0 && distance < 0x80000000;
  }

  global.ScrcpyGateRawV2 = {
    HEADER_LENGTH: HEADER_LENGTH,
    MAX_PACKET_BYTES: MAX_PACKET_BYTES,
    FLAG_KEYFRAME: FLAG_KEYFRAME,
    FLAG_CONFIG: FLAG_CONFIG,
    FLAG_DISCONTINUITY: FLAG_DISCONTINUITY,
    parse: parse,
    sequenceIsNewer: sequenceIsNewer
  };
})(typeof window !== 'undefined' ? window : this);

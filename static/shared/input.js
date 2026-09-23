/* 与 app/mirror_runtime.py 的 SCRCPY_CLIENT_CLIPBOARD_MAX_BYTES 保持一致：
   单条剪贴板控制消息能携带的 UTF-8 文本上限。 */
const CLIPBOARD_TEXT_MAX_BYTES = 4096;
const KEYBOARD_SENTINEL = '\u200b';

function editKeyForInputType(inputType) {
    if (inputType === 'insertLineBreak' || inputType === 'insertParagraph') return 66;
    if (/^delete(Content|Word|HardLine|SoftLine)Backward$/.test(inputType)) return 67;
    if (/^delete(Content|Word|HardLine|SoftLine)Forward$/.test(inputType)) return 112;
    return null;
}

/* scrcpy server 用 KeyCharacterMap 把文本反解成按键事件，虚拟键盘布局里没有的字符
   （中文、日文、表情等）只会打一条 "Could not inject char" 警告后被丢弃。
   这类文本必须改走设备剪贴板 + 粘贴键（控制消息类型 9），不能走文本注入（类型 1）。 */
function requiresClipboardText(text) {
    for (const character of String(text || '')) {
        if (character.codePointAt(0) > 0x7e || character.codePointAt(0) < 0x20) return true;
    }
    return false;
}

let macLikePlatform = null;

/* macOS 的 Option 是「输入替代字符」（Option+2 = ™），不是命令修饰键；
   这类按键要继续走文本注入，否则 macOS 用户打不出重音和符号。 */
function isMacLikePlatform() {
    if (macLikePlatform !== null) return macLikePlatform;
    macLikePlatform = false;
    try {
        const platform = (typeof navigator !== 'undefined' && navigator && navigator.platform) || '';
        macLikePlatform = /mac|iphone|ipad|ipod/i.test(String(platform));
    } catch (_) { }
    return macLikePlatform;
}

function isCharacterInputEvent(event) {
    if (!event || !event.key) return false;
    if (event.key.length !== 1) return false;
    if (typeof event.getModifierState === 'function' && event.getModifierState('AltGraph')) return true;
    // AltGr（Windows/Linux 的第三层字符）与 macOS Option 都会产出真实字符。
    if (event.altKey && !event.ctrlKey && !event.metaKey && isMacLikePlatform()) return true;
    return !event.ctrlKey && !event.altKey && !event.metaKey;
}

function isImeKey(event) {
    return !!(event && (event.isComposing || event.keyCode === 229 || event.key === 'Process'));
}

function isBrowserPaste(event) {
    return !!event && (((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === 'v') ||
        (event.shiftKey && event.key === 'Insert'));
}

class ScrcpyInput {
    constructor(callback, videoElement, width, height, debug = false, onKeyboardStateChange = null, onInputError = null) {
        this.callback = callback
        this.width = width
        this.height = height
        this.debug = debug
        this.videoElement = videoElement
        this.keyboardActive = false
        this.keyboardInputMode = 'local'
        this.onKeyboardStateChange = typeof onKeyboardStateChange === 'function' ? onKeyboardStateChange : null
        this._isComposingText = false
        this._compositionCommit = null
        this._compositionTimer = null
        this._destroyed = false
        this.onInputError = typeof onInputError === 'function' ? onInputError : null
        this._geometry = null
        this._geometryKey = ''
        this.viewportRotation = 0
        this.fullscreenMode = false
        this._virtualKeyboard = typeof navigator !== 'undefined' ? navigator.virtualKeyboard : null
        if (this._virtualKeyboard && 'overlaysContent' in this._virtualKeyboard) {
            try { this._virtualKeyboard.overlaysContent = false } catch (_) { }
        }
        this._pendingMoveData = null
        this._moveFlushTimer = null
        // 剪贴板控制消息的序号：scrcpy 只把它回显给客户端用来忽略自己写入的内容，
        // 本客户端不读剪贴板，保持单调递增即可。
        this._clipboardSequence = 0
        // 已经按下、还没抬起的 Android 键码：失焦/切标签时要把它们全部抬起，
        // 否则设备端会认为键一直按着（Android 会持续重复该键，修饰键会卡住）。
        this._pressedKeycodes = new Set()
        this._keyRepeatCounts = new Map()
        this._keyboardProxy = this.createKeyboardProxy();
        this._onMobileBeforeInput = null;
        this._onMobileInput = null;
        this._onMobileKeyDown = null;
        this._onMobileKeyUp = null;
        this._onMobilePaste = null;
        this._onMobileCompositionStart = null;
        this._onMobileCompositionEnd = null;
        this._onKeyboardProxyBlur = null;
        this._onVisualViewportChange = null;
        this._releasePressedKeys = null;
        this._onVisibilityChange = null;
        if (document.body && this._keyboardProxy) {
            document.body.appendChild(this._keyboardProxy);
        }
        // 窗口失焦（Alt+Tab、点到浏览器外）与切标签都不会再收到 keyup：
        // 先把按下的键全部抬起，再让设备端保持干净状态。
        this._releasePressedKeys = () => {
            this.releasePointer();
            this.closeKeyboard();
        };
        this._onVisibilityChange = () => {
            if (typeof document !== 'undefined' && document.hidden) this._releasePressedKeys();
        };
        if (typeof window !== 'undefined' && window.addEventListener) {
            window.addEventListener('blur', this._releasePressedKeys);
            window.addEventListener('pagehide', this._releasePressedKeys);
        }
        if (typeof document !== 'undefined' && document.addEventListener) {
            document.addEventListener('visibilitychange', this._onVisibilityChange);
        }
        if (typeof window !== 'undefined' && window.visualViewport && window.visualViewport.addEventListener) {
            this._onVisualViewportChange = () => this._syncKeyboardProxyLayout();
            window.visualViewport.addEventListener('resize', this._onVisualViewportChange);
            window.visualViewport.addEventListener('scroll', this._onVisualViewportChange);
        }
        this._syncKeyboardProxyLayout();
        // Editable focus must exist before the first physical key reaches the IME.
        this._onVideoFocus = () => {
            if (!this._suppressVideoFocus && this.hasPhysicalPointer()) this.openKeyboard(false);
        };
        videoElement.addEventListener('focus', this._onVideoFocus);
        this._onVideoBlur = () => {
            if (this.keyboardInputMode === 'device') this.closeKeyboard();
        };
        videoElement.addEventListener('blur', this._onVideoBlur);
        const isEditableTarget = (target) => {
            if (!target) return false;
            const tag = (target.tagName || '').toLowerCase();
            return target.isContentEditable || tag === 'input' || tag === 'textarea' || tag === 'select';
        };
        // 绑定处理器引用，便于后续解绑
        this._onMouseDown = null;
        this._onMouseUp = null;
        this._onMouseMove = null;
        this._onContextMenu = null;
        this._onWheel = null;
        this._onTouchStart = null;
        this._onTouchMove = null;
        this._onTouchEnd = null;
        this._onTouchCancel = null;
        this._onKeyDown = null;
        this._onKeyUp = null;
        let mouseX = null;
        let mouseY = null;
        let leftButtonIsPressed = false;
        let rightButtonIsPressed = false;
        let middleButtonIsPressed = false;
        let touchIsPressed = false;
        let activeTouchIdentifier = null;
        let suppressMouseUntil = 0;
        this._releasePointer = () => {
            const wasTouching = leftButtonIsPressed || touchIsPressed;
            leftButtonIsPressed = false;
            touchIsPressed = false;
            activeTouchIdentifier = null;
            this._pendingMoveData = null;
            if (wasTouching && mouseX !== null && mouseY !== null) {
                this.sendControlData(this.createTouchProtocolData(1, mouseX, mouseY, this.width, this.height, 0, 0, 0));
            }
            if (rightButtonIsPressed) {
                rightButtonIsPressed = false;
                this.sendControlData(this.createScreenProtocolData(1));
            }
            if (middleButtonIsPressed) {
                middleButtonIsPressed = false;
                this.snedKeyCode(this.syntheticKeyEvent(), 1, 3);
            }
        };
        const findTouch = (touchList, identifier) => {
            if (!touchList) return null;
            for (let i = 0; i < touchList.length; i++) {
                if (touchList[i].identifier === identifier) return touchList[i];
            }
            return null;
        };
        this._onMouseDown = (event) => {
            if (Date.now() < suppressMouseUntil) return;
            if (videoElement.contains(event.target)) {
                if (this.keyboardActive) {
                    if (this.keyboardInputMode === 'device') this.focusVideo();
                    else try { this._keyboardProxy.focus({ preventScroll: true }); } catch (_) { try { this._keyboardProxy.focus(); } catch (__) {} }
                } else if (this.hasPhysicalPointer()) this.openKeyboard(false);
                else this.closeKeyboard(true);
                if (event.button === 0) {
                    const point = this.mapClientToDevice(event.clientX, event.clientY, false);
                    if (!point) return;
                    leftButtonIsPressed = true;

                    mouseX = point.x;
                    mouseY = point.y;

                    this.sendControlData(this.createTouchProtocolData(0, mouseX, mouseY, this.width, this.height, 0, 0, 65535));
                    event.preventDefault();
                } else if (event.button === 2) {
                    rightButtonIsPressed = true;

                    this.sendControlData(this.createScreenProtocolData(0));
                    event.preventDefault();
                } else if (event.button === 1) {
                    middleButtonIsPressed = true;
                    this.snedKeyCode(event, 0, 3);
                    event.preventDefault();
                }
            }
        };
        document.addEventListener('mousedown', this._onMouseDown);

        this._onMouseUp = (event) => {
            if (Date.now() < suppressMouseUntil) return;
            if (event.button === 0 && leftButtonIsPressed) {
                leftButtonIsPressed = false;

                const point = this.mapClientToDevice(event.clientX, event.clientY, true);
                if (point) {
                    mouseX = point.x;
                    mouseY = point.y;
                }

                if (mouseX !== null && mouseY !== null) {
                    this.sendControlData(this.createTouchProtocolData(1, mouseX, mouseY, this.width, this.height, 0, 0, 0));
                    event.preventDefault();
                }

            } else if (event.button === 2 && rightButtonIsPressed) {
                rightButtonIsPressed = false;

                this.sendControlData(this.createScreenProtocolData(1));
                event.preventDefault();
            } else if (event.button === 1 && middleButtonIsPressed) {
                middleButtonIsPressed = false;
                this.snedKeyCode(event, 1, 3);
                event.preventDefault();
            }
        };
        document.addEventListener('mouseup', this._onMouseUp);

        this._onMouseMove = (event) => {
            if (Date.now() < suppressMouseUntil) return;
            if (!leftButtonIsPressed) return;
            if (!(event.buttons & 1)) {
                this.releasePointer();
                return;
            }

            const point = this.mapClientToDevice(event.clientX, event.clientY, true);
            if (!point) return;

            mouseX = point.x;
            mouseY = point.y;

            this.sendMoveData(this.createTouchProtocolData(2, mouseX, mouseY, this.width, this.height, 0, 0, 65535));
            event.preventDefault();
        };
        document.addEventListener('mousemove', this._onMouseMove);

        this._onContextMenu = (event) => {
            event.preventDefault();
        };
        videoElement.addEventListener('contextmenu', this._onContextMenu);

        this._onWheel = (event) => {
            // Browser pinch-to-zoom is a local display action, not a device scroll.
            if (event.ctrlKey) return;
            const point = this.mapClientToDevice(event.clientX, event.clientY, false);
            if (!point) return;
            event.preventDefault();
            // DOM deltas are pixels, lines, or pages; scrcpy 3.1 uses signed
            // 16-bit fixed point in [-1, 1]. Keep fractional trackpad motion.
            const unit = event.deltaMode === 1 ? 1 / 3 : (event.deltaMode === 2 ? 1 : 1 / 100);
            let dx = event.deltaX * unit;
            let dy = event.deltaY * unit;
            if (this.viewportRotation === 90) [dx, dy] = [dy, -dx];
            else if (this.viewportRotation === -90) [dx, dy] = [-dy, dx];
            else if (this.viewportRotation === 180) [dx, dy] = [-dx, -dy];
            if (!dx && !dy) return;
            this.sendControlData(this.createScrollProtocolData(point.x, point.y, this.width, this.height, dx, -dy, event.buttons || 0));
        };
        videoElement.addEventListener('wheel', this._onWheel, { passive: false });

        this._onTouchStart = (event) => {
            if (!event.changedTouches || event.changedTouches.length < 1 || touchIsPressed) return;
            const touch = event.changedTouches[0];
            if (!this.keyboardActive) this.closeKeyboard(true);
            const point = this.mapClientToDevice(touch.clientX, touch.clientY, false);
            if (!point) return;

            touchIsPressed = true;
            activeTouchIdentifier = touch.identifier;
            suppressMouseUntil = Date.now() + 700;
            mouseX = point.x;
            mouseY = point.y;

            this.sendControlData(this.createTouchProtocolData(0, mouseX, mouseY, this.width, this.height, 0, 0, 65535));
            event.preventDefault();
        };
        videoElement.addEventListener('touchstart', this._onTouchStart, { passive: false });

        this._onTouchMove = (event) => {
            if (!touchIsPressed) return;
            const touch = findTouch(event.changedTouches, activeTouchIdentifier) || findTouch(event.touches, activeTouchIdentifier);
            if (!touch) return;
            const point = this.mapClientToDevice(touch.clientX, touch.clientY, true);
            if (!point) return;

            mouseX = point.x;
            mouseY = point.y;
            this.sendMoveData(this.createTouchProtocolData(2, mouseX, mouseY, this.width, this.height, 0, 0, 65535));
            event.preventDefault();
        };
        videoElement.addEventListener('touchmove', this._onTouchMove, { passive: false });

        this._onTouchEnd = (event) => {
            if (!touchIsPressed) return;
            const touch = findTouch(event.changedTouches, activeTouchIdentifier);
            if (!touch && findTouch(event.touches, activeTouchIdentifier)) return;
            if (touch) {
                const point = this.mapClientToDevice(touch.clientX, touch.clientY, true);
                if (point) {
                    mouseX = point.x;
                    mouseY = point.y;
                }
            }

            touchIsPressed = false;
            activeTouchIdentifier = null;
            suppressMouseUntil = Date.now() + 700;
            if (mouseX !== null && mouseY !== null) {
                this.sendControlData(this.createTouchProtocolData(1, mouseX, mouseY, this.width, this.height, 0, 0, 0));
            }
            event.preventDefault();
        };
        videoElement.addEventListener('touchend', this._onTouchEnd, { passive: false });

        this._onTouchCancel = (event) => {
            if (!touchIsPressed) return;
            touchIsPressed = false;
            activeTouchIdentifier = null;
            suppressMouseUntil = Date.now() + 700;
            if (mouseX !== null && mouseY !== null) {
                this.sendControlData(this.createTouchProtocolData(1, mouseX, mouseY, this.width, this.height, 0, 0, 0));
            }
            event.preventDefault();
        };
        videoElement.addEventListener('touchcancel', this._onTouchCancel, { passive: false });

        this._onKeyDown = (event) => {
            if (isEditableTarget(event.target)) return;
            if (document.activeElement !== videoElement && document.activeElement !== this._keyboardProxy) return;
            if (this._isComposingText || isImeKey(event)) return;

            if (isBrowserPaste(event)) {
                return;
            }

            if (this.keyboardInputMode === 'device' && !this.keyboardActive) return;
            if (this.keyboardInputMode !== 'device' && isCharacterInputEvent(event)) {
                this.sendKeyboardText(event.key);
                event.preventDefault();
                return;
            }

            const androidKeyCode = this.mapToAndroidKeyCode(event);
            if (androidKeyCode !== null) {
                this.snedKeyCode(event, 0, androidKeyCode);
                event.preventDefault();
            }
        };
        document.addEventListener('keydown', this._onKeyDown);

        this._onKeyUp = (event) => {
            if (isEditableTarget(event.target)) return;
            if (document.activeElement !== videoElement && document.activeElement !== this._keyboardProxy) return;
            if (this._isComposingText || isImeKey(event)) return;
            const androidKeyCode = this.keyUpKeycodeFor(event);
            if (androidKeyCode !== null) {
                this.snedKeyCode(event, 1, androidKeyCode);
                event.preventDefault();
            }
        };
        document.addEventListener('keyup', this._onKeyUp);

        this._onPaste = (event) => {
            if (isEditableTarget(event.target)) return;
            if (document.activeElement !== videoElement && document.activeElement !== this._keyboardProxy) return;
            const text = event.clipboardData ? event.clipboardData.getData('text/plain') : '';
            if (text) {
                this.sendKeyboardText(text);
                event.preventDefault();
            }
        };
        document.addEventListener('paste', this._onPaste);

        if (this._keyboardProxy) {
            this._onMobileBeforeInput = (event) => {
                if (!this.keyboardActive) return;
                const inputType = event.inputType || '';
                if (this._compositionCommit && /^(insertText|insertFromComposition|insertCompositionText)$/.test(inputType)) {
                    if (event.cancelable) event.preventDefault();
                    return;
                }
                if (this._isComposingText || event.isComposing || inputType === 'insertCompositionText') return;
                // Non-cancelable edits are handled once by the input event.
                if (!event.cancelable) return;

                if (inputType === 'insertText' || inputType === 'insertReplacementText' || inputType === 'insertFromPaste' || inputType === 'insertFromDrop') {
                    const text = event.data || '';
                    if (text) {
                        this.sendKeyboardText(text);
                        event.preventDefault();
                        this.resetKeyboardProxy();
                    }
                    return;
                }

                const editKey = editKeyForInputType(inputType);
                if (editKey !== null) {
                    this.sendKeyCodePress(editKey, event);
                    event.preventDefault();
                    this.resetKeyboardProxy();
                }
            };
            this._keyboardProxy.addEventListener('beforeinput', this._onMobileBeforeInput);

            this._onMobileInput = (event) => {
                if (!this.keyboardActive || this._isComposingText || event.isComposing || this._compositionCommit) return;
                const editKey = editKeyForInputType(event.inputType || '');
                if (editKey !== null) {
                    this.sendKeyCodePress(editKey, event);
                    this.resetKeyboardProxy();
                    return;
                }
                let text = this._keyboardProxy.value || '';
                if (typeof event.data === 'string') text = event.data;
                else {
                    if (text.startsWith(KEYBOARD_SENTINEL)) text = text.slice(1);
                    if (text.endsWith(KEYBOARD_SENTINEL)) text = text.slice(0, -1);
                }
                if (text) {
                    this.sendKeyboardText(text);
                    this.resetKeyboardProxy();
                }
            };
            this._keyboardProxy.addEventListener('input', this._onMobileInput);

            this._onMobileKeyDown = (event) => {
                if (!this.keyboardActive || this._isComposingText || isImeKey(event)) return;
                this.flushCompositionCommit();
                if (isBrowserPaste(event)) return;
                // Text belongs to the browser IME. Physical editing keys must be
                // handled here: an empty textarea may not emit a deletion event.
                if (isCharacterInputEvent(event)) return;

                const androidKeyCode = this.mapToAndroidKeyCode(event);
                if (androidKeyCode !== null) {
                    this.snedKeyCode(event, 0, androidKeyCode);
                    event.preventDefault();
                }
            };
            this._keyboardProxy.addEventListener('keydown', this._onMobileKeyDown);

            this._onMobileKeyUp = (event) => {
                if (!this.keyboardActive || this._isComposingText || isImeKey(event)) return;
                if (isBrowserPaste(event)) return;
                const androidKeyCode = this.keyUpKeycodeFor(event);
                if (androidKeyCode !== null) {
                    this.snedKeyCode(event, 1, androidKeyCode);
                    event.preventDefault();
                }
            };
            this._keyboardProxy.addEventListener('keyup', this._onMobileKeyUp);

            this._onMobilePaste = (event) => {
                if (!this.keyboardActive) return;
                const text = event.clipboardData ? event.clipboardData.getData('text/plain') : '';
                if (text) {
                    this.flushCompositionCommit();
                    this.sendKeyboardText(text);
                    event.preventDefault();
                    this.resetKeyboardProxy();
                }
            };
            this._keyboardProxy.addEventListener('paste', this._onMobilePaste);

            this._onMobileCompositionStart = () => {
                if (!this.keyboardActive) return;
                this.flushCompositionCommit();
                this.releasePressedKeys();
                this._isComposingText = true;
            };
            this._keyboardProxy.addEventListener('compositionstart', this._onMobileCompositionStart);

            this._onMobileCompositionEnd = (event) => {
                const wasComposing = this._isComposingText;
                this._isComposingText = false;
                if (!this.keyboardActive || !wasComposing) {
                    this.resetKeyboardProxy();
                    return;
                }
                // Empty data means cancellation, not the uncommitted phonetic value.
                // Browsers may emit the final input before or after compositionend.
                this._compositionCommit = { text: typeof event.data === 'string' ? event.data : '' };
                this._compositionTimer = setTimeout(() => this.flushCompositionCommit(), 0);
            };
            this._keyboardProxy.addEventListener('compositionend', this._onMobileCompositionEnd);

            this._onKeyboardProxyBlur = () => {
                // Do not steal focus from menus, dialogs, or accessible controls.
                this.flushCompositionCommit();
                this.closeKeyboard();
            };
            this._keyboardProxy.addEventListener('blur', this._onKeyboardProxyBlur);
        }
    }

    hasPhysicalPointer() {
        return typeof window !== 'undefined' && typeof window.matchMedia === 'function' &&
            window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    }

    releasePointer() {
        if (this._releasePointer) this._releasePointer();
    }

    flushCompositionCommit() {
        if (this._compositionTimer !== null) clearTimeout(this._compositionTimer);
        this._compositionTimer = null;
        const pending = this._compositionCommit;
        this._compositionCommit = null;
        if (!pending) return;
        this.resetKeyboardProxy();
        if (this.keyboardActive && !this._destroyed && pending.text) this.sendKeyboardText(pending.text);
    }

    sendKeyboardText(text) {
        try {
            if (this.sendText(text) === false) throw new Error('控制通道不可用，文本未发送');
        } catch (error) {
            if (this.onInputError) this.onInputError(error.message);
        }
    }

    keyUpKeycodeFor(event) {
        const androidKeyCode = this.mapToAndroidKeyCode(event);
        if (androidKeyCode === null) return null;
        // 组合键按下时走 keycode 通路，keyup 必须补齐 —— 即使此刻修饰键已经松开
        // 或者这个键本身是单字符（单字符的 keyup 平时由文本注入省略）。
        if (this._pressedKeycodes && this._pressedKeycodes.has(androidKeyCode)) return androidKeyCode;
        return null;
    }

    releasePressedKeys() {
        if (!this._pressedKeycodes || this._pressedKeycodes.size === 0) return 0;
        const pressed = Array.from(this._pressedKeycodes);
        this._pressedKeycodes.clear();
        const keyEvent = this.syntheticKeyEvent(null);
        for (const keycode of pressed) {
            this.snedKeyCode(keyEvent, 1, keycode);
        }
        return pressed.length;
    }

    _setKeyboardActive(active) {
        const next = !!active;
        if (this.keyboardActive === next) return;
        this.keyboardActive = next;
        if (this.onKeyboardStateChange) {
            try { this.onKeyboardStateChange(next); } catch (_) { }
        }
    }

    focusVideo() {
        if (!this.videoElement) return false;
        this._suppressVideoFocus = true;
        try {
            this.videoElement.focus({ preventScroll: true });
        } catch (e) {
            try { this.videoElement.focus(); } catch (_) { return false; }
        } finally {
            this._suppressVideoFocus = false;
        }
        return typeof document === 'undefined' || document.activeElement === this.videoElement;
    }

    openKeyboard(showVirtualKeyboard = true) {
        if (!this._keyboardProxy || this._destroyed) return false;
        this.closeKeyboard();
        this._setKeyboardActive(true);
        if (this.keyboardInputMode === 'device') {
            if (this.focusVideo()) return true;
            this.closeKeyboard();
            return false;
        }
        try {
            this._keyboardProxy.focus({ preventScroll: true });
        } catch (e) {
            try { this._keyboardProxy.focus(); } catch (_) { }
        }
        if (typeof document !== 'undefined' && document.activeElement !== this._keyboardProxy) {
            this.closeKeyboard();
            return false;
        }
        // Chromium exposes an explicit VirtualKeyboard API on some Android
        // browsers. Focus remains the portable path; show() is only a best
        // effort enhancement and is intentionally ignored when unsupported.
        if (showVirtualKeyboard && this._virtualKeyboard && typeof this._virtualKeyboard.show === 'function') {
            try { this._virtualKeyboard.show() } catch (_) { }
        }
        return true;
    }

    setKeyboardInputMode(mode) {
        if (mode !== 'local' && mode !== 'device') throw new Error('无效的键盘输入方式');
        if (mode === 'device' && !this.hasPhysicalPointer()) throw new Error('设备输入法模式需要电脑实体键盘');
        if (this.keyboardInputMode === mode) return;
        this.closeKeyboard();
        this.keyboardInputMode = mode;
    }

    closeKeyboard(focusVideo = false) {
        this._setKeyboardActive(false);
        this._isComposingText = false;
        if (this._compositionTimer !== null) clearTimeout(this._compositionTimer);
        this._compositionTimer = null;
        this._compositionCommit = null;
        // 离开键盘模式时把按下的键抬起，避免设备端留下卡住的修饰键/长按键。
        this.releasePressedKeys();
        this.resetKeyboardProxy();
        if (this._keyboardProxy) {
            try { this._keyboardProxy.blur(); } catch (_) { }
        }
        if (focusVideo) this.focusVideo();
    }

    createKeyboardProxy() {
        if (typeof document === 'undefined') return null;
        const el = document.createElement('textarea');
        el.setAttribute('aria-hidden', 'true');
        el.setAttribute('autocomplete', 'off');
        el.setAttribute('autocorrect', 'off');
        el.setAttribute('autocapitalize', 'off');
        el.setAttribute('spellcheck', 'false');
        el.inputMode = 'text';
        el.enterKeyHint = 'enter';
        el.tabIndex = -1;
        Object.assign(el.style, {
            position: 'fixed',
            left: '0',
            top: '0',
            width: '1px',
            height: '1px',
            opacity: '0.01',
            border: '0',
            padding: '0',
            margin: '0',
            outline: '0',
            resize: 'none',
            overflow: 'hidden',
            background: 'transparent',
            color: 'transparent',
            caretColor: 'transparent',
            pointerEvents: 'none'
        });
        return el;
    }

    resetKeyboardProxy() {
        if (!this._keyboardProxy) return;
        // Mobile keyboards need editable content on both sides to emit delete
        // events. Sentinels never enter a control message or composition commit.
        this._keyboardProxy.value = KEYBOARD_SENTINEL + KEYBOARD_SENTINEL;
        try {
            this._keyboardProxy.setSelectionRange(1, 1);
        } catch (_) { }
    }

    syntheticKeyEvent(sourceEvent) {
        const event = sourceEvent || {};
        return {
            shiftKey: !!event.shiftKey,
            ctrlKey: !!event.ctrlKey,
            altKey: !!event.altKey,
            metaKey: !!event.metaKey,
            repeat: !!event.repeat,
            getModifierState: (key) => {
                if (event.getModifierState) {
                    try {
                        return event.getModifierState(key);
                    } catch (_) { }
                }
                return false;
            }
        };
    }

    sendKeyCodePress(keycode, sourceEvent) {
        const keyEvent = this.syntheticKeyEvent(sourceEvent);
        if (!this.snedKeyCode(keyEvent, 0, keycode)) return false;
        return this.snedKeyCode(keyEvent, 1, keycode);
    }

    sendDeviceAction(action) {
        const keys = { back: 4, home: 3, tasks: 187, volume_up: 24, volume_down: 25, power: 26 };
        if (Object.prototype.hasOwnProperty.call(keys, action)) return this.sendKeyCodePress(keys[action]);
        if (action === 'screen_off' || action === 'screen_on') {
            return this.sendControlData(this.createPowerProtocolData(action === 'screen_on' ? 1 : 0));
        }
        return false;
    }

    sendControlData(data) {
        if (this._destroyed) return false;
        if (this._moveFlushTimer) {
            // A pending move is scheduled as a microtask when available. It
            // cannot be cancelled, but clearing the marker makes the queued
            // callback a no-op after this important event flushes it now.
            if (typeof this._moveFlushTimer === 'number' && typeof cancelAnimationFrame === 'function') {
                cancelAnimationFrame(this._moveFlushTimer);
            }
            this._moveFlushTimer = null;
        }
        this.flushPendingMove();
        return this.callback(data) !== false;
    }

    sendText(text) {
        const value = String(text || '');
        if (!value) return true;
        if (this._destroyed) return false;
        if (new TextEncoder().encode(value).length > CLIPBOARD_TEXT_MAX_BYTES) {
            throw new RangeError('文本过长，请分次发送（每次最多 4096 UTF-8 字节）');
        }

        // 中文、日文、表情等设备键盘布局反解不出来的字符改走剪贴板 + 粘贴键。
        if (requiresClipboardText(value)) {
            return this.sendClipboardText(value);
        }

        const maxTextBytes = 300; // scrcpy inject-text protocol limit
        const encoder = new TextEncoder();
        let chunk = '';
        let chunkBytes = 0;
        for (const character of value) {
            const characterBytes = encoder.encode(character).length;
            if (chunk && chunkBytes + characterBytes > maxTextBytes) {
                if (!this.sendControlData(this.createTextProtocolData(chunk))) return false;
                chunk = '';
                chunkBytes = 0;
            }
            chunk += character;
            chunkBytes += characterBytes;
        }
        if (chunk) {
            if (!this.sendControlData(this.createTextProtocolData(chunk))) return false;
        }
        return true;
    }

    // One paste per text operation: a timer cannot prove Android consumed a chunk.
    sendClipboardText(text) {
        const value = String(text || '');
        if (!value) return true;
        if (new TextEncoder().encode(value).length > CLIPBOARD_TEXT_MAX_BYTES) {
            throw new RangeError('文本过长，请分次发送（每次最多 4096 UTF-8 字节）');
        }
        return this.sendControlData(this.createSetClipboardProtocolData(value));
    }

    nextClipboardSequence() {
        this._clipboardSequence = (this._clipboardSequence || 0) + 1;
        if (this._clipboardSequence > 0x7fffffff) this._clipboardSequence = 1;
        return this._clipboardSequence;
    }

    sendMoveData(data) {
        this._pendingMoveData = data;
        if (this._moveFlushTimer) return;
        const flush = () => {
            this._moveFlushTimer = null;
            this.flushPendingMove();
        };
        // requestAnimationFrame adds up to one paint interval to every drag
        // and touch move. A microtask still coalesces synchronous bursts while
        // handing the newest coordinate to the socket before the next paint.
        if (typeof queueMicrotask === 'function') {
            this._moveFlushTimer = true;
            queueMicrotask(flush);
        } else if (typeof Promise === 'function') {
            this._moveFlushTimer = true;
            Promise.resolve().then(flush);
        } else if (typeof requestAnimationFrame === 'function') {
            this._moveFlushTimer = requestAnimationFrame(flush);
        } else {
            this._moveFlushTimer = setTimeout(flush, 0);
        }
    }

    flushPendingMove() {
        if (!this._pendingMoveData) return;
        const data = this._pendingMoveData;
        this._pendingMoveData = null;
        this.callback(data);
    }

    invalidateGeometry() {
        this._geometry = null;
        this._geometryKey = '';
    }

    getRenderedVideoRect() {
        const rect = this.videoElement.getBoundingClientRect();
        const rectWidth = rect.width || (rect.right - rect.left);
        const rectHeight = rect.height || (rect.bottom - rect.top);
        const rotation = this.viewportRotation;
        const rotated = rotation === 90 || rotation === -90;
        const elementWidth = rotated ? rectHeight : rectWidth;
        const elementHeight = rotated ? rectWidth : rectHeight;
        const style = window.getComputedStyle ? window.getComputedStyle(this.videoElement) : null;
        const objectFit = style && style.objectFit ? style.objectFit : this.videoElement.style.objectFit || 'contain';
        const key = [
            rect.left, rect.top, rectWidth, rectHeight,
            this.width, this.height, objectFit, rotation
        ].join(':');
        if (this._geometry && this._geometryKey === key) {
            return this._geometry;
        }
        if (!elementWidth || !elementHeight || !this.width || !this.height) {
            this._geometryKey = key;
            this._geometry = { rect, left: 0, top: 0, width: elementWidth, height: elementHeight, elementWidth, elementHeight, rotation };
            return this._geometry;
        }

        const videoAspect = this.width / this.height;
        const elementAspect = elementWidth / elementHeight;
        let renderedWidth = elementWidth;
        let renderedHeight = elementHeight;

        if (objectFit === 'contain' || objectFit === 'scale-down') {
            if (videoAspect > elementAspect) {
                renderedWidth = elementWidth;
                renderedHeight = elementWidth / videoAspect;
            } else {
                renderedHeight = elementHeight;
                renderedWidth = elementHeight * videoAspect;
            }
        } else if (objectFit === 'cover') {
            if (videoAspect > elementAspect) {
                renderedHeight = elementHeight;
                renderedWidth = elementHeight * videoAspect;
            } else {
                renderedWidth = elementWidth;
                renderedHeight = elementWidth / videoAspect;
            }
        }

        this._geometryKey = key;
        this._geometry = {
            rect,
            left: (elementWidth - renderedWidth) / 2,
            top: (elementHeight - renderedHeight) / 2,
            width: renderedWidth,
            height: renderedHeight,
            elementWidth,
            elementHeight,
            rotation
        };
        return this._geometry;
    }

    mapClientToDevice(clientX, clientY, allowOutside = false) {
        const rendered = this.getRenderedVideoRect();
        if (!rendered.width || !rendered.height || !this.width || !this.height) return null;

        const screenX = clientX - rendered.rect.left;
        const screenY = clientY - rendered.rect.top;
        let elementX = screenX;
        let elementY = screenY;
        if (rendered.rotation === 90) {
            elementX = screenY;
            elementY = rendered.elementHeight - screenX;
        } else if (rendered.rotation === -90) {
            elementX = rendered.elementWidth - screenY;
            elementY = screenX;
        } else if (rendered.rotation === 180) {
            elementX = rendered.elementWidth - screenX;
            elementY = rendered.elementHeight - screenY;
        }
        const localX = elementX - rendered.left;
        const localY = elementY - rendered.top;
        let ratioX = localX / rendered.width;
        let ratioY = localY / rendered.height;

        if (!allowOutside && (ratioX < 0 || ratioX > 1 || ratioY < 0 || ratioY > 1)) {
            return null;
        }

        ratioX = Math.max(0, Math.min(1, ratioX));
        ratioY = Math.max(0, Math.min(1, ratioY));

        return {
            x: Math.max(0, Math.min(this.width - 1, Math.round(ratioX * (this.width - 1)))),
            y: Math.max(0, Math.min(this.height - 1, Math.round(ratioY * (this.height - 1))))
        };
    }

    resizeScreen(width, height) {
        if (width !== this.width || height !== this.height) this.releasePointer();
        this.width = width;
        this.height = height;
        this.invalidateGeometry();
    }

    setViewportRotation(rotation) {
        const value = Number(rotation);
        // 0 / 90 / -90 / 180：270 归一化成 -90，其余非法值一律当作 0。
        let normalized = 0;
        if (value === 90) normalized = 90;
        else if (value === -90 || value === 270 || value === -270) normalized = -90;
        else if (value === 180 || value === -180) normalized = 180;
        if (this.viewportRotation === normalized) return;
        this.releasePointer();
        this.viewportRotation = normalized;
        this.invalidateGeometry();
    }

    setFullscreenMode(active) {
        this.fullscreenMode = !!active;
        if (this._keyboardProxy && typeof document !== 'undefined') {
            const fullscreenRoot = document.querySelector ? document.querySelector('.app') : null;
            const target = this.fullscreenMode && fullscreenRoot ? fullscreenRoot : document.body;
            if (target && this._keyboardProxy.parentNode !== target) {
                target.appendChild(this._keyboardProxy);
            }
        }
        this._syncKeyboardProxyLayout();
        this.invalidateGeometry();
    }

    _syncKeyboardProxyLayout() {
        if (!this._keyboardProxy || !this.fullscreenMode || typeof window === 'undefined') return;
        const viewport = window.visualViewport;
        if (!viewport) return;
        const offsetLeft = Number(viewport.offsetLeft) || 0;
        const offsetTop = Number(viewport.offsetTop) || 0;
        // Keep the focus target inside the visible viewport while the browser
        // IME resizes or overlays the fullscreen surface. It remains visually
        // hidden and does not move the mirrored device image.
        this._keyboardProxy.style.left = `${Math.max(0, offsetLeft + 2)}px`;
        this._keyboardProxy.style.top = `${Math.max(0, offsetTop + 2)}px`;
    }

    mapToAndroidKeyCode(event) {
        const codeToAndroidKeyCode = {
            'KeyA': 29,  // KEYCODE_A
            'KeyB': 30,  // KEYCODE_B
            'KeyC': 31,  // KEYCODE_C
            'KeyD': 32,  // KEYCODE_D
            'KeyE': 33,  // KEYCODE_E
            'KeyF': 34,  // KEYCODE_F
            'KeyG': 35,  // KEYCODE_G
            'KeyH': 36,  // KEYCODE_H
            'KeyI': 37,  // KEYCODE_I
            'KeyJ': 38,  // KEYCODE_J
            'KeyK': 39,  // KEYCODE_K
            'KeyL': 40,  // KEYCODE_L
            'KeyM': 41,  // KEYCODE_M
            'KeyN': 42,  // KEYCODE_N
            'KeyO': 43,  // KEYCODE_O
            'KeyP': 44,  // KEYCODE_P
            'KeyQ': 45,  // KEYCODE_Q
            'KeyR': 46,  // KEYCODE_R
            'KeyS': 47,  // KEYCODE_S
            'KeyT': 48,  // KEYCODE_T
            'KeyU': 49,  // KEYCODE_U
            'KeyV': 50,  // KEYCODE_V
            'KeyW': 51,  // KEYCODE_W
            'KeyX': 52,  // KEYCODE_X
            'KeyY': 53,  // KEYCODE_Y
            'KeyZ': 54,  // KEYCODE_Z

            'Digit0': 7,   // KEYCODE_0
            'Digit1': 8,   // KEYCODE_1
            'Digit2': 9,   // KEYCODE_2
            'Digit3': 10,  // KEYCODE_3
            'Digit4': 11,  // KEYCODE_4
            'Digit5': 12,  // KEYCODE_5
            'Digit6': 13,  // KEYCODE_6
            'Digit7': 14,  // KEYCODE_7
            'Digit8': 15,  // KEYCODE_8
            'Digit9': 16,  // KEYCODE_9

            'Enter': 66,       // KEYCODE_ENTER
            'Backspace': 67,   // KEYCODE_DEL
            'Tab': 61,         // KEYCODE_TAB
            'Space': 62,       // KEYCODE_SPACE
            'Escape': 111,     // KEYCODE_ESCAPE
            'CapsLock': 115,   // KEYCODE_CAPS_LOCK
            'NumLock': 143,    // KEYCODE_NUM_LOCK
            'ScrollLock': 116, // KEYCODE_SCROLL_LOCK
            // PC 键盘上的 Home/End/PageUp/PageDown/Insert 是「移动插入点 / 翻页」，
            // 所以映射到编辑键码；Android 的主屏键（KEYCODE_HOME=3）由工作台的
            // 「主页」按钮发送，不再占用物理 Home 键。
            'Home': 122,       // KEYCODE_MOVE_HOME
            'End': 123,        // KEYCODE_MOVE_END
            'PageUp': 92,      // KEYCODE_PAGE_UP
            'PageDown': 93,    // KEYCODE_PAGE_DOWN
            'Insert': 124,     // KEYCODE_INSERT
            'Delete': 112,     // KEYCODE_FORWARD_DEL
            'ContextMenu': 82, // KEYCODE_MENU

            'ArrowUp': 19,     // KEYCODE_DPAD_UP
            'ArrowDown': 20,   // KEYCODE_DPAD_DOWN
            'ArrowLeft': 21,   // KEYCODE_DPAD_LEFT
            'ArrowRight': 22,  // KEYCODE_DPAD_RIGHT

            'ShiftLeft': 59,   // KEYCODE_SHIFT_LEFT
            'ShiftRight': 60,  // KEYCODE_SHIFT_RIGHT
            'ControlLeft': 113,// KEYCODE_CTRL_LEFT
            'ControlRight': 114,// KEYCODE_CTRL_RIGHT
            'AltLeft': 57,     // KEYCODE_ALT_LEFT
            'AltRight': 58,    // KEYCODE_ALT_RIGHT
            'MetaLeft': 117,   // KEYCODE_META_LEFT
            'MetaRight': 118,  // KEYCODE_META_RIGHT

            'Numpad0': 144,    // KEYCODE_NUMPAD_0
            'Numpad1': 145,    // KEYCODE_NUMPAD_1
            'Numpad2': 146,    // KEYCODE_NUMPAD_2
            'Numpad3': 147,    // KEYCODE_NUMPAD_3
            'Numpad4': 148,    // KEYCODE_NUMPAD_4
            'Numpad5': 149,    // KEYCODE_NUMPAD_5
            'Numpad6': 150,    // KEYCODE_NUMPAD_6
            'Numpad7': 151,    // KEYCODE_NUMPAD_7
            'Numpad8': 152,    // KEYCODE_NUMPAD_8
            'Numpad9': 153,    // KEYCODE_NUMPAD_9
            'NumpadEnter': 160,// KEYCODE_NUMPAD_ENTER
            'NumpadAdd': 157,  // KEYCODE_NUMPAD_ADD
            'NumpadSubtract': 156, // KEYCODE_NUMPAD_SUBTRACT
            'NumpadMultiply': 155, // KEYCODE_NUMPAD_MULTIPLY
            'NumpadDivide': 154,   // KEYCODE_NUMPAD_DIVIDE

            'F1': 131,  // KEYCODE_F1
            'F2': 132,  // KEYCODE_F2
            'F3': 133,  // KEYCODE_F3
            'F4': 134,  // KEYCODE_F4
            'F5': 135,  // KEYCODE_F5
            'F6': 136,  // KEYCODE_F6
            'F7': 137,  // KEYCODE_F7
            'F8': 138,  // KEYCODE_F8
            'F9': 139,  // KEYCODE_F9
            'F10': 140, // KEYCODE_F10
            'F11': 141, // KEYCODE_F11
            'F12': 142, // KEYCODE_F12
        };
        // 这里曾有 'Back': 4 / 'Home': 3 / 'Menu': 82 三个非标准 code 的映射：
        // 'Home' 与上面的编辑键重复，会让物理 Home 键变成 Android 主屏键（对象字面量后者覆盖前者）。
        // Android 的返回/主页/多任务由工作台底部按钮发送，键盘侧只保留标准编辑键。

        const androidKeyCode = codeToAndroidKeyCode[event.code];
        return androidKeyCode !== undefined ? androidKeyCode : null;
    }

    snedKeyCode(keyevent, action, keycode) {
        let repeat = 0;
        if (action === 0) {
            repeat = keyevent.repeat ? (this._keyRepeatCounts.get(keycode) || 0) + 1 : 0;
            this._keyRepeatCounts.set(keycode, repeat);
        } else this._keyRepeatCounts.delete(keycode);
        if (this._pressedKeycodes) {
            if (action === 0) this._pressedKeycodes.add(keycode);
            else this._pressedKeycodes.delete(keycode);
        }
        const capsLockState = keyevent.getModifierState('CapsLock');
        const numLockState = keyevent.getModifierState('NumLock');
        const scrollLockState = keyevent.getModifierState('ScrollLock');

        // Android expects the general modifier flag as well as its side flag.
        const modifier = (active, general, leftCode, leftFlag, rightCode, rightFlag) => {
            if (!active) return 0;
            let side = 0;
            if (this._pressedKeycodes.has(leftCode)) side |= leftFlag;
            if (this._pressedKeycodes.has(rightCode)) side |= rightFlag;
            return general | side;
        };
        let metakey = modifier(keyevent.shiftKey, 0x1, 59, 0x40, 60, 0x80) |
            modifier(keyevent.ctrlKey, 0x1000, 113, 0x2000, 114, 0x4000) |
            modifier(keyevent.altKey, 0x2, 57, 0x10, 58, 0x20) |
            modifier(keyevent.metaKey, 0x10000, 117, 0x20000, 118, 0x40000);
        if (capsLockState) {
            metakey |= 0x100000;
        }
        if (numLockState) {
            metakey |= 0x200000;
        }
        if (scrollLockState) metakey |= 0x400000;
        let data = this.createKeyProtocolData(action, keycode, repeat, metakey);
        return this.sendControlData(data);
    }

    createTouchProtocolData(action, x, y, width, height, actionButton, buttons, pressure) {
        const type = 2; // touch event

        const buffer = new ArrayBuffer(1 + 1 + 8 + 4 + 4 + 2 + 2 + 2 + 4 + 4);
        const view = new DataView(buffer);

        let offset = 0;

        view.setUint8(offset, type);
        offset += 1;

        view.setUint8(offset, action);
        offset += 1;

        view.setUint8(offset, 0xff);
        offset += 1;
        view.setUint8(offset, 0xff);
        offset += 1;
        view.setUint8(offset, 0xff);
        offset += 1;
        view.setUint8(offset, 0xff);
        offset += 1;
        view.setUint8(offset, 0xff);
        offset += 1;
        view.setUint8(offset, 0xff);
        offset += 1;
        view.setUint8(offset, 0xff);
        offset += 1;
        view.setUint8(offset, 0xfe);
        offset += 1;

        view.setInt32(offset, x, false);
        offset += 4;
        view.setInt32(offset, y, false);
        offset += 4;
        view.setUint16(offset, width, false);
        offset += 2;
        view.setUint16(offset, height, false);
        offset += 2;

        view.setUint16(offset, pressure, false);
        offset += 2;

        view.setInt32(offset, actionButton, false);
        offset += 4;

        view.setInt32(offset, buttons, false);

        return buffer;
    }

    createTextProtocolData(text) {
        const type = 1; // text event
        const encoded = new TextEncoder().encode(text);
        const buffer = new ArrayBuffer(1 + 4 + encoded.length);
        const view = new DataView(buffer);
        view.setUint8(0, type);
        view.setUint32(1, encoded.length, false);
        new Uint8Array(buffer, 5).set(encoded);
        return buffer;
    }
    createSetClipboardProtocolData(text) {
        // scrcpy set-clipboard: type + u64 sequence + paste flag + u32 length + UTF-8 text
        const type = 9; // set clipboard event
        const encoded = new TextEncoder().encode(text);
        const buffer = new ArrayBuffer(14 + encoded.length);
        const view = new DataView(buffer);
        view.setUint8(0, type);
        // 序号写成两个大端 32 位字，避免依赖 setBigUint64/BigInt。
        view.setUint32(1, 0, false);
        view.setUint32(5, this.nextClipboardSequence(), false);
        view.setUint8(9, 1); // ask the device to paste right after setting the clipboard
        view.setUint32(10, encoded.length, false);
        new Uint8Array(buffer, 14).set(encoded);
        return buffer;
    }
    createKeyProtocolData(action, keycode, repeat, metaState) {
        const type = 0; // key event

        const buffer = new ArrayBuffer(1 + 1 + 4 + 4 + 4);
        const view = new DataView(buffer);

        let offset = 0;

        view.setUint8(offset, type);
        offset += 1;

        view.setUint8(offset, action);
        offset += 1;

        view.setInt32(offset, keycode, false);
        offset += 4;
        view.setInt32(offset, repeat, false);
        offset += 4;
        view.setInt32(offset, metaState, false);

        return buffer;
    }

    createScrollProtocolData(x, y, width, height, hScroll, vScroll, button) {
        const type = 3; // scroll event

        const buffer = new ArrayBuffer(1 + 4 + 4 + 2 + 2 + 2 + 2 + 4);
        const view = new DataView(buffer);

        let offset = 0;
        view.setUint8(offset, type);
        offset += 1;

        view.setInt32(offset, x, false);
        offset += 4;
        view.setInt32(offset, y, false);
        offset += 4;
        view.setUint16(offset, width, false);
        offset += 2;
        view.setUint16(offset, height, false);
        offset += 2;

        const fixedPoint = value => Math.max(-32768, Math.min(32767, Math.trunc((Number(value) || 0) * 32768)));
        view.setInt16(offset, fixedPoint(hScroll), false);
        offset += 2;
        view.setInt16(offset, fixedPoint(vScroll), false);
        offset += 2;

        view.setInt32(offset, button, false);

        return buffer;
    }

    createScreenProtocolData(action) {
        const type = 4; // Back, or wake the screen when it is off.

        const buffer = new ArrayBuffer(1 + 1);
        const view = new DataView(buffer);

        let offset = 0;
        view.setUint8(offset, type);
        offset += 1;

        view.setUint8(offset, action);

        return buffer;
    }

    createPowerProtocolData(action) {
        const type = 10; // scrcpy 3.1 SET_DISPLAY_POWER, boolean on/off.

        const buffer = new ArrayBuffer(1 + 1);
        const view = new DataView(buffer);

        let offset = 0;
        view.setUint8(offset, type);
        offset += 1;

        view.setUint8(offset, action);

        return buffer;
    }

    add_debug_item(text) {
        const p = document.createElement('p');
        p.textContent = text;
        const span = document.createElement('span');
        span.textContent = '0';
        p.appendChild(span);
        document.body.appendChild(p);
        return span;
    }

    screen_on_off(action) {
        let data = null;
        data = this.createScreenProtocolData(action);
        this.sendControlData(data)
    }

    destroy() {
        try {
            this.releasePointer();
            this.closeKeyboard();
            if (this._pressedKeycodes) this._pressedKeycodes.clear();
            if (this._moveFlushTimer) {
                if (typeof this._moveFlushTimer === 'number' && typeof cancelAnimationFrame === 'function') {
                    cancelAnimationFrame(this._moveFlushTimer);
                }
                this._moveFlushTimer = null;
            }
            this._destroyed = true;
            this._pendingMoveData = null;
            this.invalidateGeometry();
            document.removeEventListener('mousedown', this._onMouseDown);
            document.removeEventListener('mouseup', this._onMouseUp);
            document.removeEventListener('mousemove', this._onMouseMove);
            if (this.videoElement) {
                this.videoElement.removeEventListener('focus', this._onVideoFocus);
                this.videoElement.removeEventListener('blur', this._onVideoBlur);
                this.videoElement.removeEventListener('contextmenu', this._onContextMenu);
                this.videoElement.removeEventListener('wheel', this._onWheel);
                this.videoElement.removeEventListener('touchstart', this._onTouchStart);
                this.videoElement.removeEventListener('touchmove', this._onTouchMove);
                this.videoElement.removeEventListener('touchend', this._onTouchEnd);
                this.videoElement.removeEventListener('touchcancel', this._onTouchCancel);
            }
            document.removeEventListener('keydown', this._onKeyDown);
            document.removeEventListener('keyup', this._onKeyUp);
            document.removeEventListener('paste', this._onPaste);
            if (this._releasePressedKeys) {
                if (typeof window !== 'undefined' && window.removeEventListener) {
                    window.removeEventListener('blur', this._releasePressedKeys);
                    window.removeEventListener('pagehide', this._releasePressedKeys);
                }
                this._releasePressedKeys = null;
            }
            if (this._onVisibilityChange) {
                if (typeof document !== 'undefined' && document.removeEventListener) {
                    document.removeEventListener('visibilitychange', this._onVisibilityChange);
                }
                this._onVisibilityChange = null;
            }
            if (this._onVisualViewportChange && typeof window !== 'undefined' && window.visualViewport) {
                window.visualViewport.removeEventListener('resize', this._onVisualViewportChange);
                window.visualViewport.removeEventListener('scroll', this._onVisualViewportChange);
                this._onVisualViewportChange = null;
            }
            if (this._keyboardProxy) {
                this._keyboardProxy.removeEventListener('beforeinput', this._onMobileBeforeInput);
                this._keyboardProxy.removeEventListener('input', this._onMobileInput);
                this._keyboardProxy.removeEventListener('keydown', this._onMobileKeyDown);
                this._keyboardProxy.removeEventListener('keyup', this._onMobileKeyUp);
                this._keyboardProxy.removeEventListener('paste', this._onMobilePaste);
                this._keyboardProxy.removeEventListener('compositionstart', this._onMobileCompositionStart);
                this._keyboardProxy.removeEventListener('compositionend', this._onMobileCompositionEnd);
                this._keyboardProxy.removeEventListener('blur', this._onKeyboardProxyBlur);
                if (this._keyboardProxy.parentNode) {
                    this._keyboardProxy.parentNode.removeChild(this._keyboardProxy);
                }
                this._keyboardProxy = null;
            }
            this.onKeyboardStateChange = null;
        } catch (e) {
            console.warn('ScrcpyInput destroy error:', e);
        }
    }
}

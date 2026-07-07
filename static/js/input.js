class ScrcpyInput {
    constructor(callback, videoElement, width, height, debug = false) {
        this.callback = callback
        this.width = width
        this.height = height
        this.debug = debug
        this.videoElement = videoElement
        this.keyboardActive = false
        this._isComposingText = false
        this._keyboardProxy = this.createKeyboardProxy();
        this._onMobileBeforeInput = null;
        this._onMobileInput = null;
        this._onMobileKeyDown = null;
        this._onMobileKeyUp = null;
        this._onMobilePaste = null;
        this._onMobileCompositionStart = null;
        this._onMobileCompositionEnd = null;
        if (document.body && this._keyboardProxy) {
            document.body.appendChild(this._keyboardProxy);
        }
        const isEditableTarget = (target) => {
            if (!target) return false;
            const tag = (target.tagName || '').toLowerCase();
            return target.isContentEditable || tag === 'input' || tag === 'textarea' || tag === 'select';
        };
        const activateKeyboard = () => {
            this.keyboardActive = true;
            if (this._keyboardProxy && document.activeElement !== this._keyboardProxy) {
                try {
                    this._keyboardProxy.focus({ preventScroll: true });
                    return;
                } catch (e) {
                    try {
                        this._keyboardProxy.focus();
                        return;
                    } catch (_) { }
                }
            }
            try {
                videoElement.focus({ preventScroll: true });
            } catch (e) {
                try { videoElement.focus(); } catch (_) { }
            }
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
        let touchIsPressed = false;
        let activeTouchIdentifier = null;
        let suppressMouseUntil = 0;
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
                activateKeyboard();
                if (event.button === 0) {
                    const point = this.mapClientToDevice(event.clientX, event.clientY, false);
                    if (!point) return;
                    leftButtonIsPressed = true;

                    mouseX = point.x;
                    mouseY = point.y;

                    let data = this.createTouchProtocolData(0, mouseX, mouseY, this.width, this.height, 0, 0, 65535);
                    this.callback(data);
                    event.preventDefault();
                } else if (event.button === 2) {
                    rightButtonIsPressed = true;

                    this.snedKeyCode(event, 0, 4);
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

                if (videoElement.contains(event.target)) {
                    activateKeyboard();
                }

                if (mouseX !== null && mouseY !== null) {
                    let data = this.createTouchProtocolData(1, mouseX, mouseY, this.width, this.height, 0, 0, 0);
                    this.callback(data);
                    event.preventDefault();
                }

            } else if (event.button === 2 && rightButtonIsPressed) {
                rightButtonIsPressed = false;

                this.snedKeyCode(event, 1, 4);
                event.preventDefault();
            }
        };
        document.addEventListener('mouseup', this._onMouseUp);

        this._onMouseMove = (event) => {
            if (Date.now() < suppressMouseUntil) return;
            if (!leftButtonIsPressed) return;

            const point = this.mapClientToDevice(event.clientX, event.clientY, true);
            if (!point) return;

            if (videoElement.contains(event.target)) {
                activateKeyboard();
            }
            mouseX = point.x;
            mouseY = point.y;

            let data = this.createTouchProtocolData(2, mouseX, mouseY, this.width, this.height, 0, 0, 65535);
            this.callback(data);
            event.preventDefault();
        };
        document.addEventListener('mousemove', this._onMouseMove);

        this._onContextMenu = (event) => {
            event.preventDefault();
        };
        videoElement.addEventListener('contextmenu', this._onContextMenu);

        this._onWheel = (event) => {
            activateKeyboard();
            // 阻止默认滚动行为
            event.preventDefault();
            
            const hScroll = event.deltaX;
            const vScroll = event.deltaY;
            const deltaMode = event.deltaMode;
            const deltaZ = event.deltaZ;
            const clientX = event.clientX;
            const clientY = event.clientY;
            const button = event.button;

            const point = this.mapClientToDevice(clientX, clientY, false);
            if (!point) return;


            // switch (deltaMode) {
            //     case WheelEvent.DOM_DELTA_PIXEL:
            //         deltaModeValue.textContent = 'pixel';
            //         break;
            //     case WheelEvent.DOM_DELTA_LINE:
            //         deltaModeValue.textContent = 'row';
            //         break;
            //     case WheelEvent.DOM_DELTA_PAGE:
            //         deltaModeValue.textContent = 'page';
            //         break;
            //     default:
            //         deltaModeValue.textContent = 'unknown';
            // }
            let data = this.createScrollProtocolData(point.x, point.y, this.width, this.height, hScroll, vScroll, button);
            this.callback(data);
        };
        videoElement.addEventListener('wheel', this._onWheel);

        this._onTouchStart = (event) => {
            if (!event.changedTouches || event.changedTouches.length < 1 || touchIsPressed) return;
            const touch = event.changedTouches[0];
            activateKeyboard();
            const point = this.mapClientToDevice(touch.clientX, touch.clientY, false);
            if (!point) return;

            touchIsPressed = true;
            activeTouchIdentifier = touch.identifier;
            suppressMouseUntil = Date.now() + 700;
            mouseX = point.x;
            mouseY = point.y;

            let data = this.createTouchProtocolData(0, mouseX, mouseY, this.width, this.height, 0, 0, 65535);
            this.callback(data);
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
            let data = this.createTouchProtocolData(2, mouseX, mouseY, this.width, this.height, 0, 0, 65535);
            this.callback(data);
            event.preventDefault();
        };
        videoElement.addEventListener('touchmove', this._onTouchMove, { passive: false });

        this._onTouchEnd = (event) => {
            if (!touchIsPressed) return;
            const touch = findTouch(event.changedTouches, activeTouchIdentifier);
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
                let data = this.createTouchProtocolData(1, mouseX, mouseY, this.width, this.height, 0, 0, 0);
                this.callback(data);
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
                let data = this.createTouchProtocolData(1, mouseX, mouseY, this.width, this.height, 0, 0, 0);
                this.callback(data);
            }
            event.preventDefault();
        };
        videoElement.addEventListener('touchcancel', this._onTouchCancel, { passive: false });

        this._onKeyDown = (event) => {
            if (isEditableTarget(event.target)) return;
            if (!this.keyboardActive && document.activeElement !== videoElement) return;
            if (event.isComposing) return;

            if (event.ctrlKey && event.key && event.key.toLowerCase() === 'v') {
                return;
            }

            if (!event.ctrlKey && !event.altKey && !event.metaKey && event.key && event.key.length === 1) {
                if (!event.repeat) {
                    this.callback(this.createTextProtocolData(event.key));
                }
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
            if (!this.keyboardActive && document.activeElement !== videoElement) return;
            if (!event.ctrlKey && !event.altKey && !event.metaKey && event.key && event.key.length === 1) return;

            const androidKeyCode = this.mapToAndroidKeyCode(event);
            if (androidKeyCode !== null) {
                this.snedKeyCode(event, 1, androidKeyCode);
                event.preventDefault();
            }
        };
        document.addEventListener('keyup', this._onKeyUp);

        this._onPaste = (event) => {
            if (isEditableTarget(event.target)) return;
            if (!this.keyboardActive && document.activeElement !== videoElement) return;
            const text = event.clipboardData ? event.clipboardData.getData('text/plain') : '';
            if (text) {
                this.callback(this.createTextProtocolData(text));
                event.preventDefault();
            }
        };
        document.addEventListener('paste', this._onPaste);

        if (this._keyboardProxy) {
            this._onMobileBeforeInput = (event) => {
                if (!this.keyboardActive) return;
                const inputType = event.inputType || '';
                if (this._isComposingText || event.isComposing || inputType === 'insertCompositionText') return;

                if (inputType === 'insertText' || inputType === 'insertReplacementText' || inputType === 'insertFromPaste' || inputType === 'insertFromDrop') {
                    const text = event.data || '';
                    if (text) {
                        this.callback(this.createTextProtocolData(text));
                        event.preventDefault();
                        this.resetKeyboardProxy();
                    }
                    return;
                }

                if (inputType === 'insertLineBreak' || inputType === 'insertParagraph') {
                    this.sendKeyCodePress(66, event);
                    event.preventDefault();
                    this.resetKeyboardProxy();
                    return;
                }

                if (inputType === 'deleteContentBackward' || inputType === 'deleteWordBackward' || inputType === 'deleteHardLineBackward' || inputType === 'deleteSoftLineBackward') {
                    this.sendKeyCodePress(67, event);
                    event.preventDefault();
                    this.resetKeyboardProxy();
                    return;
                }

                if (inputType === 'deleteContentForward' || inputType === 'deleteWordForward' || inputType === 'deleteHardLineForward' || inputType === 'deleteSoftLineForward') {
                    this.sendKeyCodePress(112, event);
                    event.preventDefault();
                    this.resetKeyboardProxy();
                }
            };
            this._keyboardProxy.addEventListener('beforeinput', this._onMobileBeforeInput);

            this._onMobileInput = () => {
                if (!this.keyboardActive || this._isComposingText) return;
                const text = this._keyboardProxy.value || '';
                if (text) {
                    this.callback(this.createTextProtocolData(text));
                    this.resetKeyboardProxy();
                }
            };
            this._keyboardProxy.addEventListener('input', this._onMobileInput);

            this._onMobileKeyDown = (event) => {
                if (!this.keyboardActive || event.isComposing) return;
                if (event.ctrlKey && event.key && event.key.toLowerCase() === 'v') return;
                const beforeInputKeys = new Set(['Backspace', 'Delete', 'Enter', 'NumpadEnter', 'Space']);
                if ((event.key && event.key.length === 1) || beforeInputKeys.has(event.key) || beforeInputKeys.has(event.code)) return;

                const androidKeyCode = this.mapToAndroidKeyCode(event);
                if (androidKeyCode !== null) {
                    this.snedKeyCode(event, 0, androidKeyCode);
                    event.preventDefault();
                }
            };
            this._keyboardProxy.addEventListener('keydown', this._onMobileKeyDown);

            this._onMobileKeyUp = (event) => {
                if (!this.keyboardActive || event.isComposing) return;
                const beforeInputKeys = new Set(['Backspace', 'Delete', 'Enter', 'NumpadEnter', 'Space']);
                if ((event.key && event.key.length === 1) || beforeInputKeys.has(event.key) || beforeInputKeys.has(event.code)) return;

                const androidKeyCode = this.mapToAndroidKeyCode(event);
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
                    this.callback(this.createTextProtocolData(text));
                    event.preventDefault();
                    this.resetKeyboardProxy();
                }
            };
            this._keyboardProxy.addEventListener('paste', this._onMobilePaste);

            this._onMobileCompositionStart = () => {
                this._isComposingText = true;
            };
            this._keyboardProxy.addEventListener('compositionstart', this._onMobileCompositionStart);

            this._onMobileCompositionEnd = (event) => {
                this._isComposingText = false;
                const text = event.data || this._keyboardProxy.value || '';
                if (text) {
                    this.callback(this.createTextProtocolData(text));
                }
                this.resetKeyboardProxy();
            };
            this._keyboardProxy.addEventListener('compositionend', this._onMobileCompositionEnd);
        }
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
        this._keyboardProxy.value = '';
        try {
            this._keyboardProxy.setSelectionRange(0, 0);
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
        this.snedKeyCode(keyEvent, 0, keycode);
        this.snedKeyCode(keyEvent, 1, keycode);
    }

    getRenderedVideoRect() {
        const rect = this.videoElement.getBoundingClientRect();
        const elementWidth = rect.width || (rect.right - rect.left);
        const elementHeight = rect.height || (rect.bottom - rect.top);
        if (!elementWidth || !elementHeight || !this.width || !this.height) {
            return { rect, left: 0, top: 0, width: elementWidth, height: elementHeight };
        }

        const style = window.getComputedStyle ? window.getComputedStyle(this.videoElement) : null;
        const objectFit = style && style.objectFit ? style.objectFit : this.videoElement.style.objectFit || 'contain';
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

        return {
            rect,
            left: (elementWidth - renderedWidth) / 2,
            top: (elementHeight - renderedHeight) / 2,
            width: renderedWidth,
            height: renderedHeight
        };
    }

    mapClientToDevice(clientX, clientY, allowOutside = false) {
        const rendered = this.getRenderedVideoRect();
        if (!rendered.width || !rendered.height || !this.width || !this.height) return null;

        const localX = clientX - rendered.rect.left - rendered.left;
        const localY = clientY - rendered.rect.top - rendered.top;
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
        this.width = width;
        this.height = height;
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

            'Back': 4,    // KEYCODE_BACK
            'Home': 3,    // KEYCODE_HOME
            'Menu': 82,   // KEYCODE_MENU
        };

        const androidKeyCode = codeToAndroidKeyCode[event.code];
        return androidKeyCode !== undefined ? androidKeyCode : null;
    }

    snedKeyCode(keyevent, action, keycode) {
        const capsLockState = keyevent.getModifierState('CapsLock');
        const numLockState = keyevent.getModifierState('NumLock');
        const scrollLockState = keyevent.getModifierState('ScrollLock');

        let metakey = 0;
        if (keyevent.shiftKey) {
            metakey |= 0x40;
        }
        if (keyevent.ctrlKey) {
            metakey |= 0x2000;
        }
        if (keyevent.altKey) {
            metakey |= 0x10;
        }
        if (keyevent.metaKey) {
            metakey |= 0x20000;
        }
        if (capsLockState) {
            metakey |= 0x100000;
        }
        if (numLockState) {
            metakey |= 0x200000;
        }
        // if(scrollLockState)
        // {
        //     metakey |= 0x400000;
        // }
        let data = this.createKeyProtocolData(action, keycode, keyevent.repeat, metakey);
        this.callback(data);
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
        view.setUint8(offset, 0xfd);
        offset += 1;

        view.setInt32(offset, x, false);
        offset += 4;
        view.setInt32(offset, y, false);
        offset += 4;
        view.setUint16(offset, width, false);
        offset += 2;
        view.setUint16(offset, height, false);
        offset += 2;

        view.setInt16(offset, pressure, false);
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

        view.setInt16(offset, hScroll, false);
        offset += 2;
        view.setInt16(offset, vScroll, false);
        offset += 2;

        view.setInt32(offset, button, false);

        return buffer;
    }

    createScreenProtocolData(action) {
        const type = 4; // Screen off/on event

        const buffer = new ArrayBuffer(1 + 1);
        const view = new DataView(buffer);

        let offset = 0;
        view.setUint8(offset, type);
        offset += 1;

        view.setUint8(offset, action);

        return buffer;
    }

    createPowerProtocolData(action) {
        const type = 7; // Screen Power off/on event

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
        this.callback(data)
    }

    destroy() {
        try {
            document.removeEventListener('mousedown', this._onMouseDown);
            document.removeEventListener('mouseup', this._onMouseUp);
            document.removeEventListener('mousemove', this._onMouseMove);
            if (this.videoElement) {
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
            if (this._keyboardProxy) {
                this._keyboardProxy.removeEventListener('beforeinput', this._onMobileBeforeInput);
                this._keyboardProxy.removeEventListener('input', this._onMobileInput);
                this._keyboardProxy.removeEventListener('keydown', this._onMobileKeyDown);
                this._keyboardProxy.removeEventListener('keyup', this._onMobileKeyUp);
                this._keyboardProxy.removeEventListener('paste', this._onMobilePaste);
                this._keyboardProxy.removeEventListener('compositionstart', this._onMobileCompositionStart);
                this._keyboardProxy.removeEventListener('compositionend', this._onMobileCompositionEnd);
                if (this._keyboardProxy.parentNode) {
                    this._keyboardProxy.parentNode.removeChild(this._keyboardProxy);
                }
                this._keyboardProxy = null;
            }
        } catch (e) {
            console.warn('ScrcpyInput destroy error:', e);
        }
    }
}

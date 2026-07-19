import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MobileKeyboardRuntimeTests(unittest.TestCase):
    def run_node(self, body):
        program = r"""
const fs = require('fs');
const vm = require('vm');

class FakeTarget {
  constructor(tagName='div') {
    this.tagName=tagName.toUpperCase();
    this.listeners=new Map();
    this.attributes=new Map();
    this.children=[];
    this.parentNode=null;
    this.style={objectFit:'contain'};
    this.value='';
    this.focusCount=0;
    this.blurCount=0;
    this.isContentEditable=false;
  }
  addEventListener(type, listener) {
    const listeners=this.listeners.get(type) || new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }
  removeEventListener(type, listener) {
    const listeners=this.listeners.get(type);
    if (listeners) listeners.delete(listener);
  }
  listenerCount() {
    return Array.from(this.listeners.values()).reduce((total, listeners)=>total + listeners.size, 0);
  }
  dispatch(type, event={}) {
    event.type=type;
    if (!event.target) event.target=this;
    if (!event.preventDefault) event.preventDefault=()=>{ event.defaultPrevented=true; };
    for (const listener of Array.from(this.listeners.get(type) || [])) listener(event);
    return event;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  contains(target) { return target === this; }
  focus() {
    this.focusCount+=1;
    const previous=document.activeElement;
    if (previous && previous !== this) previous.dispatch('blur', {relatedTarget:this});
    document.activeElement=this;
  }
  blur() {
    this.blurCount+=1;
    if (document.activeElement === this) {
      document.activeElement=document.body;
      this.dispatch('blur', {relatedTarget:document.body});
    }
  }
  appendChild(child) { child.parentNode=this; this.children.push(child); return child; }
  removeChild(child) {
    this.children=this.children.filter(item=>item !== child);
    child.parentNode=null;
    if (document.activeElement === child) document.activeElement=this;
    return child;
  }
  setSelectionRange() {}
  getBoundingClientRect() { return {left:0, top:0, right:100, bottom:200, width:100, height:200}; }
}

const document=new FakeTarget('document');
document.body=new FakeTarget('body');
document.activeElement=document.body;
document.createElement=tagName=>new FakeTarget(tagName);
const window={getComputedStyle:element=>({objectFit:element.style.objectFit || 'contain'})};
globalThis.document=document;
globalThis.window=window;
globalThis.requestAnimationFrame=callback=>{ callback(); return 0; };
globalThis.cancelAnimationFrame=()=>{};
const inputSource=fs.readFileSync('static/js/input.js', 'utf8') + '\nglobalThis.ScrcpyInput=ScrcpyInput;';
vm.runInThisContext(inputSource, {filename:'static/js/input.js'});
""" + body
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_touch_control_does_not_open_keyboard_and_explicit_button_does(self):
        result = self.run_node(
            r"""
const video=new FakeTarget('video');
const sent=[];
const keyboardStates=[];
const input=new ScrcpyInput(data=>sent.push(data), video, 100, 200, false, active=>keyboardStates.push(active));
const proxy=input._keyboardProxy;
const touch=(identifier, x, y)=>({identifier, clientX:x, clientY:y});

video.dispatch('touchstart', {changedTouches:[touch(1, 20, 30)], touches:[touch(1, 20, 30)]});
video.dispatch('touchmove', {changedTouches:[touch(1, 30, 40)], touches:[touch(1, 30, 40)]});
video.dispatch('touchend', {changedTouches:[touch(1, 30, 40)], touches:[]});
const afterTouch={
  keyboardActive:input.keyboardActive,
  proxyFocusCount:proxy.focusCount,
  videoFocusCount:video.focusCount,
  types:sent.map(data=>new Uint8Array(data)[0]),
};

const opened=input.openKeyboard();
proxy.dispatch('beforeinput', {inputType:'insertText', data:'a'});
proxy.dispatch('compositionstart', {});
proxy.dispatch('beforeinput', {inputType:'insertCompositionText', data:'中', isComposing:true});
proxy.dispatch('compositionend', {data:'中文'});
proxy.dispatch('beforeinput', {inputType:'insertLineBreak', data:null});
proxy.dispatch('beforeinput', {inputType:'deleteContentBackward', data:null});
proxy.dispatch('paste', {clipboardData:{getData:()=> 'paste'}});
const afterTyping={
  opened,
  keyboardActive:input.keyboardActive,
  proxyFocusCount:proxy.focusCount,
  types:sent.map(data=>new Uint8Array(data)[0]),
};

const pageButton=new FakeTarget('button');
pageButton.focus();
const countBeforePageKey=sent.length;
document.dispatch('keydown', {target:pageButton, key:'x', code:'KeyX'});
const afterPageFocus={
  keyboardActive:input.keyboardActive,
  countBeforePageKey,
  countAfterPageKey:sent.length,
};
const reopened=input.openKeyboard();

proxy.dispatch('compositionstart', {});
proxy.value='不得发送';
video.dispatch('touchstart', {changedTouches:[touch(2, 40, 50)], touches:[touch(2, 40, 50)]});
const countBeforeLateComposition=sent.length;
proxy.dispatch('compositionend', {data:'不得发送'});
const countAfterLateComposition=sent.length;
video.dispatch('touchend', {changedTouches:[touch(2, 40, 50)], touches:[]});
const afterClose={
  keyboardActive:input.keyboardActive,
  activeElementIsVideo:document.activeElement === video,
  proxyBlurCount:proxy.blurCount,
  countBeforeLateComposition,
  countAfterLateComposition,
};

input.destroy();
console.log(JSON.stringify({
  afterTouch,
  afterTyping,
  afterPageFocus,
  reopened,
  afterClose,
  destroyed:{bodyChildren:document.body.children.length, documentListeners:document.listenerCount(), videoListeners:video.listenerCount()},
  keyboardStates,
}));
"""
        )

        self.assertEqual(result["afterTouch"]["types"], [2, 2, 2])
        self.assertFalse(result["afterTouch"]["keyboardActive"])
        self.assertEqual(result["afterTouch"]["proxyFocusCount"], 0)
        self.assertEqual(result["afterTouch"]["videoFocusCount"], 1)

        self.assertTrue(result["afterTyping"]["opened"])
        self.assertTrue(result["afterTyping"]["keyboardActive"])
        self.assertEqual(result["afterTyping"]["proxyFocusCount"], 1)
        self.assertEqual(result["afterTyping"]["types"], [2, 2, 2, 1, 1, 0, 0, 0, 0, 1])

        self.assertFalse(result["afterPageFocus"]["keyboardActive"])
        self.assertEqual(
            result["afterPageFocus"]["countBeforePageKey"],
            result["afterPageFocus"]["countAfterPageKey"],
        )
        self.assertTrue(result["reopened"])

        self.assertFalse(result["afterClose"]["keyboardActive"])
        self.assertTrue(result["afterClose"]["activeElementIsVideo"])
        self.assertGreaterEqual(result["afterClose"]["proxyBlurCount"], 1)
        self.assertEqual(
            result["afterClose"]["countBeforeLateComposition"],
            result["afterClose"]["countAfterLateComposition"],
        )
        self.assertEqual(result["destroyed"], {"bodyChildren": 0, "documentListeners": 0, "videoListeners": 0})
        self.assertEqual(result["keyboardStates"], [True, False, True, False])

    def test_repeated_reconnects_leave_no_keyboard_proxy_or_listeners(self):
        result = self.run_node(
            r"""
const videos=[];
for (let index=0; index<20; index+=1) {
  const video=new FakeTarget('video');
  videos.push(video);
  const input=new ScrcpyInput(()=>{}, video, 100, 200);
  input.openKeyboard();
  input.destroy();
}
console.log(JSON.stringify({
  bodyChildren:document.body.children.length,
  documentListeners:document.listenerCount(),
  videoListeners:videos.reduce((total, video)=>total + video.listenerCount(), 0),
}));
"""
        )
        self.assertEqual(result, {"bodyChildren": 0, "documentListeners": 0, "videoListeners": 0})

    def test_cover_fit_maps_cropped_landscape_and_portrait_video(self):
        result = self.run_node(
            r"""
const landscapeVideo=new FakeTarget('video');
landscapeVideo.style.objectFit='cover';
const landscape=new ScrcpyInput(()=>{}, landscapeVideo, 200, 100, false);
const landscapeLeft=landscape.mapClientToDevice(0, 100);
const landscapeCenter=landscape.mapClientToDevice(50, 100);

const portraitVideo=new FakeTarget('video');
portraitVideo.style.objectFit='cover';
portraitVideo.getBoundingClientRect=()=>({left:0,top:0,right:200,bottom:100,width:200,height:100});
const portrait=new ScrcpyInput(()=>{}, portraitVideo, 100, 200, false);
const portraitTop=portrait.mapClientToDevice(100, 0);
const portraitCenter=portrait.mapClientToDevice(100, 50);

landscape.destroy();
portrait.destroy();
console.log(JSON.stringify({landscapeLeft,landscapeCenter,portraitTop,portraitCenter}));
"""
        )

        self.assertEqual(result["landscapeLeft"], {"x": 75, "y": 50})
        self.assertEqual(result["landscapeCenter"], {"x": 100, "y": 50})
        self.assertEqual(result["portraitTop"], {"x": 50, "y": 75})
        self.assertEqual(result["portraitCenter"], {"x": 50, "y": 100})


class MobileKeyboardWorkspaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "static" / "js" / "mirror.js").read_text(encoding="utf-8")
        cls.input_script = (ROOT / "static" / "js" / "input.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "css" / "mirror.css").read_text(encoding="utf-8")

    def test_keyboard_control_is_explicit_and_tracks_control_ownership(self):
        self.assertRegex(
            self.template,
            r'id="keyboardBtn"[^>]+data-active="false"[^>]+disabled',
        )
        self.assertIn("bindClick('keyboardBtn', openMobileKeyboard)", self.script)
        self.assertIn("function renderKeyboardControl()", self.script)
        self.assertIn("button.disabled=!ready", self.script)
        self.assertIn("input.openKeyboard()", self.script)
        self.assertIn("typeof state.input.closeKeyboard === 'function'", self.script)
        self.assertIn("this.closeKeyboard(true)", self.input_script)
        self.assertIn("addEventListener('blur', this._onKeyboardProxyBlur)", self.input_script)
        self.assertIn('.nav-btn[data-active="true"]', self.styles)


if __name__ == "__main__":
    unittest.main()

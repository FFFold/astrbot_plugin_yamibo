"""nox WAF 挑战求解器：quickjs 执行挑战脚本 + DOM shim，提取 nox_jst_v1。

反爬细节说明见 docs/superpowers/specs（不入库）。
"""

import base64
import re

from quickjs import Context

_CTOR_NAMES = [
    "Window", "Document", "HTMLElement", "Element", "Node", "Event", "MouseEvent",
    "KeyboardEvent", "DOMException", "CSSStyleDeclaration", "HTMLScriptElement",
    "HTMLIFrameElement", "Navigator", "Screen", "Location", "History", "Storage",
    "HTMLDocument", "HTMLCollection", "NodeList", "NamedNodeMap", "Attr",
    "CharacterData", "Text", "Comment", "DocumentFragment", "DOMTokenList",
    "DOMStringMap", "HTMLBodyElement", "HTMLHeadElement", "HTMLDivElement",
    "HTMLAnchorElement", "HTMLImageElement", "HTMLFormElement", "HTMLInputElement",
    "HTMLLinkElement", "HTMLMetaElement", "HTMLStyleElement", "HTMLCanvasElement",
    "HTMLVideoElement", "HTMLAudioElement", "HTMLSpanElement", "HTMLTableElement",
    "HTMLTableCellElement", "CustomEvent", "UIEvent", "WheelEvent", "TouchEvent",
    "FocusEvent", "InputEvent", "ClipboardEvent", "DragEvent", "PointerEvent",
    "MessageEvent", "EventTarget", "XMLHttpRequest", "FormData", "Blob", "File",
    "FileReader", "Image", "Audio", "AbortController", "AbortSignal", "Headers",
    "Request", "Response", "MutationObserver", "IntersectionObserver",
    "ResizeObserver", "WebSocket", "Worker", "SharedWorker", "Notification",
    "RTCPeerConnection", "MediaQueryList",
]

_CTOR_DEFS = "".join(f"function {n}() {{}} var {n} = {n};\n" for n in _CTOR_NAMES)

_PREAMBLE = r"""
var __noxCaptured = [];
var __cookieStore = '';
function __makeEl(tag) {
  var e = { style: {}, src: '', id: '', type: '', tagName: String(tag).toUpperCase(),
    setAttribute: function (k, v) { this[k] = v; }, appendChild: function () {}, removeChild: function () {},
    insertBefore: function () {}, getContext: function () { return null; },
    getBoundingClientRect: function () { return { width: 0, height: 0 }; },
    addEventListener: function () {}, removeEventListener: function () {},
    offsetWidth: 0, offsetHeight: 0, clientWidth: 0, clientHeight: 0,
    parentNode: null, childNodes: [], children: [] };
  return e;
}
var document = {
  get cookie() { return __cookieStore; },
  set cookie(v) { __cookieStore = String(v); __noxCaptured.push(String(v)); },
  createElement: function (tag) { return __makeEl(tag); },
  getElementById: function () { return null; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  documentElement: { style: {}, setAttribute: function () {}, getAttribute: function () { return null; } },
  addEventListener: function () {}, removeEventListener: function () {},
  body: { appendChild: function () {} },
  head: { appendChild: function () {}, querySelector: function () { return null; } },
  readyState: 'complete', referrer: '', title: '', domain: 'bbs.yamibo.com',
  createTextNode: function () { return {}; },
  createDocumentFragment: function () { return { appendChild: function () {} }; },
  defaultView: null
};
var location = { href: '', reload: function () {}, replace: function () {}, assign: function () {} };
var navigator = { userAgent: '__UA__', platform: 'Win32', language: 'zh-CN', languages: ['zh-CN'],
  appVersion: '__UA__', vendor: 'Google Inc.', plugins: [], mimeTypes: [], onLine: true,
  cookieEnabled: true, hardwareConcurrency: 8, deviceMemory: 8, maxTouchPoints: 0, webdriver: false };
var screen = { width: 1920, height: 1080, colorDepth: 24, availWidth: 1920, availHeight: 1040 };
var history = { pushState: function () {}, replaceState: function () {},
  back: function () {}, forward: function () {}, length: 1, state: null };
var localStorage = { getItem: function () { return null; }, setItem: function () {},
  removeItem: function () {}, clear: function () {}, length: 0, key: function () { return null; } };
var sessionStorage = { getItem: function () { return null; }, setItem: function () {},
  removeItem: function () {}, clear: function () {}, length: 0, key: function () { return null; } };
var performance = { now: function () { return Date.now(); }, timing: { navigationStart: 0 } };
var crypto = { getRandomValues: function (arr) {
  for (var i = 0; i < arr.length; i++) { arr[i] = Math.floor(Math.random() * 256); }
  return arr; } };
var window = this;
window.__noxExpire = 30; window.__noxDomain = ''; window.__noxImd = 1;
window.document = document; window.location = location; window.navigator = navigator;
window.screen = screen; window.history = history; window.localStorage = localStorage;
window.sessionStorage = sessionStorage; window.performance = performance; window.crypto = crypto;
window.setTimeout = function (fn) { try { fn(); } catch (e) {} return 0; };
window.setInterval = function () { return 0; };
window.clearTimeout = function () {}; window.clearInterval = function () {};
var console = { log: function(){}, info: function(){}, warn: function(){}, error: function(){}, debug: function(){} };
var setTimeout = window.setTimeout; var setInterval = window.setInterval;
var clearTimeout = window.clearTimeout; var clearInterval = window.clearInterval;
function btoa(s) { return __b64encode(s); }
function atob(s) { return __b64decode(s); }
function TextEncoder() {}
TextEncoder.prototype.encode = function (s) {
  var bin = unescape(encodeURIComponent(s));
  var arr = [];
  for (var i = 0; i < bin.length; i++) arr.push(bin.charCodeAt(i));
  return arr;
};
function TextDecoder() {}
TextDecoder.prototype.decode = function (u8) {
  var bin = '';
  for (var i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
  return decodeURIComponent(escape(bin));
};
function URLSearchParams(init) {
  this._map = {};
  if (typeof init === 'string') { var parts = String(init).replace(/^\?/, '').split('&');
    for (var i = 0; i < parts.length; i++) { if (!parts[i]) continue; var kv = parts[i].split('=');
      this._map[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || ''); } }
  this.get = function (k) { return k in this._map ? this._map[k] : null; };
  this.has = function (k) { return k in this._map; };
}
""" + _CTOR_DEFS

_TOKEN_RE = re.compile(r"nox_jst_v1=([^;]+)")


class WafSolver:
    """执行挑战脚本并提取 nox_jst_v1 token。"""

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent

    def solve_script(self, script: str) -> str | None:
        """同步执行挑战脚本，返回 token；失败返回 None。"""
        try:
            ctx = Context()
            ctx.add_callable("__b64encode", lambda s: _b64encode(s))
            ctx.add_callable("__b64decode", lambda s: _b64decode(s))
            preamble = _PREAMBLE.replace("__UA__", self.user_agent)
            ctx.eval(preamble)
            ctx.eval(script)
            captured = ctx.eval("JSON.stringify(__noxCaptured)")
        except Exception:
            return None
        m = _TOKEN_RE.search(captured or "")
        return m.group(1) if m else None


def _b64encode(bin_str: str) -> str:
    return base64.b64encode(bin_str.encode("latin-1")).decode("ascii")


def _b64decode(s: str) -> str:
    return base64.b64decode(s.encode("ascii")).decode("latin-1")

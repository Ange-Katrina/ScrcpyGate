/* Extracted from static/pages/login.html.
   Login flow with the sign-in guard: after one failed attempt the server
   requires a signed proof-of-work challenge, and
   reaching the failure threshold locks the source IP for 5 minutes. */
(function () {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
    var pwd = document.getElementById('password');
    var toggle = document.getElementById('pwd-toggle');
    if (pwd && toggle) {
        toggle.addEventListener('click', function () {
            var show = pwd.type === 'password';
            pwd.type = show ? 'text' : 'password';
            toggle.setAttribute('aria-pressed', show ? 'true' : 'false');
            toggle.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
            var svgs = toggle.querySelectorAll('svg');
            if (svgs.length > 1) {
                svgs[0].classList.toggle('hidden', show);
                svgs[1].classList.toggle('hidden', !show);
            }
        });
    }
    var theme = document.getElementById('theme-select');
    try { var savedT = localStorage.getItem('scrcpygate-theme'); if (savedT) { document.documentElement.classList.toggle('dark', savedT === 'dark'); if (theme) theme.value = savedT; } } catch (e) {}
    if (theme) {
        theme.addEventListener('change', function () {
            var v = theme.value;
            var root = document.documentElement;
            if (v === 'dark') {
                root.classList.add('dark');
                try { localStorage.setItem('scrcpygate-theme', 'dark'); } catch (e) {}
            } else if (v === 'light') {
                root.classList.remove('dark');
                try { localStorage.setItem('scrcpygate-theme', 'light'); } catch (e) {}
            } else {
                var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
                root.classList.toggle('dark', !!prefersDark);
                try { localStorage.setItem('scrcpygate-theme', prefersDark ? 'dark' : 'light'); } catch (e) {}
            }
        });
    }

    var captchaEl = document.getElementById('login-captcha');
    var captchaState = document.getElementById('login-captcha-state');
    var verifyButton = document.getElementById('login-captcha-verify');
    var verifyLabel = document.getElementById('login-captcha-label');
    var lockBanner = document.getElementById('login-lock-banner');
    var lockCountdown = document.getElementById('login-lock-countdown');
    var captcha = { armed: false, busy: false };
    var lockUntilMs = 0, generation = 0, proofController = null, expiryTimer = null;
    var proof = { user: null, promise: null, value: null, expiresAtMs: 0 };
    var submitted = false;
    function t(text) { return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(text) : text; }
    function fmtCountdown(ms) {
        var total = Math.max(0, Math.ceil(ms / 1000));
        return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
    }
    function state(kind, label, detail) {
        verifyButton.dataset.state = kind;
        verifyButton.setAttribute('aria-busy', String(kind === 'working'));
        verifyButton.setAttribute('aria-disabled', String(kind === 'working' || kind === 'verified' || lockUntilMs > Date.now()));
        verifyLabel.textContent = t(label);
        captchaState.textContent = t(detail);
    }
    function currentUsername() { return document.getElementById('username').value.trim(); }
    function armCaptcha() {
        captcha.armed = true;
        captchaEl.hidden = false;
        if (!proof.promise && !proofFresh(currentUsername())) {
            state('idle', '点击开始验证', '验证在本机完成，无需拖动或识图。');
        }
    }
    function unarmCaptcha() {
        captcha.armed = false;
        captchaEl.hidden = true;
        invalidateProof();
    }
    function paintLock() {
        var remaining = Math.max(0, lockUntilMs - Date.now());
        if (remaining > 0) {
            lockBanner.hidden = false;
            lockCountdown.textContent = fmtCountdown(remaining);
        } else if (lockUntilMs > 0) {
            lockBanner.hidden = true;
            lockUntilMs = 0;
            var submit = document.getElementById('login-submit');
            if (!captcha.busy) { submit.disabled = false; submit.textContent = t('登录'); }
            var error = document.getElementById('login-error');
            error.textContent = t('封禁已结束；仍需完成人机验证后才能登录。');
            error.style.display = '';
            armCaptcha();
        }
    }
    setInterval(paintLock, 250);
    function applyLock(ms) {
        invalidateProof();
        lockUntilMs = Date.now() + (ms || 0);
        paintLock();
        document.getElementById('login-submit').disabled = true;
        document.getElementById('login-submit').textContent = t('已封禁');
        state('error', '暂时无法验证', '登录已暂时封禁，请稍后重试。');
    }
    function proofFresh(name) {
        return !!proof.value && proof.user === name && Date.now() < proof.expiresAtMs - 1000;
    }
    function invalidateProof() {
        generation += 1;
        clearTimeout(expiryTimer);
        if (proofController) proofController.abort();
        proofController = null;
        proof = { user: null, promise: null, value: null, expiresAtMs: 0 };
        state('idle', '点击开始验证', '验证在本机完成，无需拖动或识图。');
    }
    async function fetchChallenge(user, signal) {
        // Direct bounded fetch allows aborting issuance on username edits.
        var response = await fetch('/api/auth/challenge?user=' + encodeURIComponent(user), {
            credentials: 'same-origin', cache: 'no-store', signal: signal
        });
        if (!response.ok) {
            var error = new Error(response.status === 429 ? '验证请求过于频繁，请稍后重试。' : '验证服务暂不可用，请重试。');
            throw error;
        }
        return response.json();
    }
    async function solveChallenge(challenge, signal) {
        if (challenge.provider === 'builtin') return window.ScrcpyGatePow.solve(challenge, null, { signal: signal });
        // No URL or script supplied in a challenge is executed. Only the one
        // trusted adapter explicitly installed by the operator is reachable.
        var adapter = await import('/static/pow-extension/client.js');
        if (adapter.apiVersion !== 1 || adapter.provider !== challenge.provider || typeof adapter.solve !== 'function') {
            throw new Error('验证组件加载失败，请刷新页面重试。');
        }
        return adapter.solve(challenge, { signal: signal, timeoutMs: 30000 });
    }
    function encodeProof(value) {
        var bytes = new TextEncoder().encode(JSON.stringify(value));
        if (bytes.length > 6144) throw new Error('验证未完成，请重试。');
        return btoa(Array.from(bytes, function (byte) { return String.fromCharCode(byte); }).join(''));
    }
    function startProof() {
        var name = currentUsername();
        if (lockUntilMs > Date.now() || proof.promise || proofFresh(name)) return;
        if (!name) { state('error', '点击开始验证', '请先输入用户名，再点击验证。'); document.getElementById('username').focus(); return; }
        invalidateProof();
        var token = generation, controller = new AbortController(), started = performance.now();
        proofController = controller;
        proof.user = name;
        document.getElementById('login-error').style.display = 'none';
        state('working', '正在验证…', '请稍候，正在完成安全验证。');
        var timer = setTimeout(function () { controller.abort(); }, 35000);
        var abortPromise = new Promise(function (_, reject) {
            controller.signal.addEventListener('abort', function () { reject(new DOMException('Cancelled', 'AbortError')); }, { once: true });
        });
        var work = Promise.resolve().then(function () {
            if (!window.isSecureContext || !window.crypto || !window.crypto.subtle) {
                throw new Error('人机验证需要 HTTPS；本地测试请使用 localhost 或 SSH 隧道。');
            }
            return fetchChallenge(name, controller.signal);
        }).then(async function (payload) {
            if (token !== generation) return null;
            if (payload.locked) { applyLock(payload.locked.retry_after_ms); return null; }
            if (!payload.challenge && payload.captcha_required === false) { unarmCaptcha(); return null; }
            var challenge = payload.challenge;
            if (!challenge || challenge.version !== 1) throw new Error('验证组件加载失败，请刷新页面重试。');
            var result = await solveChallenge(challenge, controller.signal);
            // Give the spinner/check transition enough time to be understood.
            await new Promise(function (resolve) { setTimeout(resolve, Math.max(0, 300 - (performance.now() - started))); });
            if (token !== generation || controller.signal.aborted) return null;
            if (!result || result.solution === undefined) throw new Error('验证未完成，请重试。');
            proof.expiresAtMs = Number(challenge.expires_at) * 1000;
            if (proof.expiresAtMs <= Date.now() + 1000) throw new Error('验证已过期，请重新验证。');
            proof.value = encodeProof({ challenge: challenge, solution: result.solution });
            state('verified', '验证完成', '验证完成，可以登录');
            expiryTimer = setTimeout(function () {
                if (token !== generation) return;
                invalidateProof();
                state('expired', '重新验证', '验证已过期，请重新验证。');
            }, proof.expiresAtMs - Date.now() - 1000);
            return proof.value;
        });
        proof.promise = Promise.race([work, abortPromise]).catch(function (error) {
            if (token !== generation) return;
            proof.value = null;
            var message = error.name === 'AbortError' || error.message === 'proof_of_work_timeout'
                ? '验证耗时过长，请重试。' : /^proof_of_work_|webcrypto_/.test(error.message)
                    ? '验证失败，请重试。' : error.message;
            state('error', '重新验证', message || '验证失败，请重试。');
        }).finally(function () {
            clearTimeout(timer);
            if (token === generation) proof.promise = null;
        });
    }
    verifyButton.addEventListener('click', startProof);
    window.addEventListener('pagehide', invalidateProof);

    function codeOf(err) {
        var payload = err && err.detail && err.detail.payload;
        return payload && (payload.code || payload.errorCode || payload.error_code);
    }

    var form = document.getElementById('login-form');
    if (form) {
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var username = document.getElementById('username');
            var password = document.getElementById('password');
            var error = document.getElementById('login-error');
            var submit = document.getElementById('login-submit');
            var name = username ? username.value.trim() : '';
            var secret = password ? password.value : '';
            var loginAccepted = false;
            if (!name || !secret) {
                if (error) { error.textContent = '请输入用户名和密码'; error.style.display = ''; }
                if (username && !name) { username.focus(); }
                return;
            }
            if (!window.ScrcpyGateApi) {
                if (error) { error.textContent = '认证服务不可用'; error.style.display = ''; }
                return;
            }
            if (lockUntilMs > Date.now()) {
                if (error) { error.textContent = '登录已暂时封禁，剩余 ' + fmtCountdown(lockUntilMs - Date.now()); error.style.display = ''; }
                return;
            }
            if (captcha.busy) return;
            if (captcha.armed && !proofFresh(name)) {
                armCaptcha();
                if (error) { error.textContent = t('请先点击验证按钮，完成验证后再登录。'); error.style.display = ''; }
                verifyButton.focus();
                return;
            }
            if (error) { error.style.display = 'none'; }
            captcha.busy = true;
            submit.disabled = true;
            submit.setAttribute('aria-busy', 'true');
            submit.textContent = '登录中…';

            function attempt(proofPayload) {
                return window.ScrcpyGateApi.post('auth.login', { body: { username: name, password: secret, proof: proofPayload } });
            }

            submitted = true;
            attempt(proofFresh(name) ? proof.value : null)
                .then(function (login) {
                    // A successful login response is not enough when the browser rejects
                    // a Secure cookie on a direct HTTP test URL. Verify the session before
                    // navigating so the user gets an actionable error instead of a redirect loop.
                    loginAccepted = true;
                    return window.ScrcpyGateApi.get('session.current').then(function () { return login; });
                })
                .then(function (login) {
                    var user = login && login.user || {};
                    window.location.href = user.password_reminder_pending && !user.must_change_password
                        ? '/mirror?password_reminder=1' : '/mirror';
                })
                .catch(function (err) {
                    var status = Number(err && err.detail && err.detail.status) || 0;
                    var code = codeOf(err);
                    var payload = err && err.detail && err.detail.payload;
                    if (code === 'RATE_LIMITED' || status === 429) {
                        var retry = err && err.detail && err.detail.retryAfterMs;
                        if (retry) applyLock(retry);
                        if (error) {
                            error.textContent = retry
                                ? '登录已暂时封禁，请在 ' + fmtCountdown(retry) + ' 后重试。'
                                : '登录尝试次数过多，请稍后再试。';
                            error.style.display = '';
                        }
                        invalidateProof();
                        armCaptcha();
                    } else if (code === 'CAPTCHA_REQUIRED' || code === 'CAPTCHA_INVALID') {
                        if (error) { error.textContent = t('请先点击验证按钮，完成验证后再登录。'); error.style.display = ''; }
                        invalidateProof();
                        armCaptcha();
                    } else if (code === 'AUTH_INVALID') {
                        var failures = payload && payload.failures;
                        var lockAfter = payload && payload.lock_after;
                        if (error) {
                            error.textContent = (typeof failures === 'number' && lockAfter)
                                ? '用户名或密码错误（第 ' + failures + ' 次，满 ' + lockAfter + ' 次将封禁 5 分钟）。'
                                : '用户名或密码错误';
                            error.style.display = '';
                        }
                        // 只有服务端明确要求时才显示验证码（登录保护可能被管理员关闭）。
                        invalidateProof();
                        if (payload && payload.captcha_required === false) unarmCaptcha();
                        else armCaptcha();
                    } else if (loginAccepted) {
                        if (error) {
                            error.textContent = status === 401 && window.location.protocol === 'http:'
                                ? '登录成功，但当前 HTTP 地址无法建立安全会话，请使用 HTTPS 地址'
                                : status === 401
                                    ? '登录成功，但会话未建立，请刷新后重试'
                                    : '登录成功，但会话验证失败，请刷新后重试';
                            error.style.display = '';
                        }
                    } else {
                        if (error) {
                            error.textContent = window.ScrcpyGateApi.errorMessage(err);
                            error.style.display = '';
                        }
                    }
                })
                .finally(function () {
                    captcha.busy = false;
                    if (lockUntilMs <= Date.now()) {
                        submit.disabled = false;
                        submit.removeAttribute('aria-busy');
                        submit.textContent = '登录';
                    }
                });
        });
    }

    // Changing accounts cancels work; it never silently starts more CPU work.
    document.getElementById('username').addEventListener('input', function () {
        invalidateProof();
        if (captcha.armed) armCaptcha();
    });

    // 打开页面时查询封禁状态：被封禁的 IP 直接显示倒计时横幅。
    if (window.ScrcpyGateApi) {
        fetch('/api/auth/challenge?status=1', { credentials: 'same-origin', cache: 'no-store' }).then(function (response) { if (!response.ok) throw new Error('status unavailable'); return response.json(); })
            .then(function (payload) {
                if (submitted) return;
                if (payload && payload.locked) {
                    applyLock(payload.locked.retry_after_ms);
                    var error = document.getElementById('login-error');
                    if (error) { error.textContent = '登录已暂时封禁，请在倒计时结束后重试。'; error.style.display = ''; }
                } else if (payload && payload.captcha_required) {
                    armCaptcha();
                } else {
                    unarmCaptcha();
                }
            })
            .catch(function () { /* 非致命：首次提交时服务端仍会给出权威答复 */ });
    }
})();

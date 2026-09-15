/* Extracted from static/pages/login.html.
   Login flow with the sign-in guard: after one failed attempt the server
   requires a signed proof-of-work challenge (slider + WebCrypto solve), and
   reaching the failure threshold locks the source IP for 5 minutes. */
(function () {
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

    // ---- 登录保护：滑块 + PoW + 封禁倒计时 ----
    var captchaEl = document.getElementById('login-captcha');
    var slider = document.getElementById('login-slider');
    var sliderFill = document.getElementById('login-slider-fill');
    var sliderKnob = document.getElementById('login-slider-knob');
    var sliderLabel = document.getElementById('login-slider-label');
    var captchaState = document.getElementById('login-captcha-state');
    var powProgress = document.getElementById('login-pow-progress');
    var powMeta = document.getElementById('login-pow-meta');
    var lockBanner = document.getElementById('login-lock-banner');
    var lockCountdown = document.getElementById('login-lock-countdown');

    var captcha = { armed: false, sliderDone: false, busy: false };
    var sliderResolve = null;
    var lockUntilMs = 0;

    function fmtCountdown(ms) {
        var total = Math.max(0, Math.ceil(ms / 1000));
        return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
    }
    function fmtInt(value) { return Math.round(value).toLocaleString('zh-CN'); }
    function fmtMs(value) { return value < 1000 ? Math.round(value) + ' ms' : (value / 1000).toFixed(1) + ' s'; }

    function setCaptchaState(text) { captchaState.textContent = text; }

    function resetSlider() {
        captcha.sliderDone = false;
        slider.classList.remove('done', 'dragging');
        sliderFill.style.width = '0%';
        sliderKnob.style.left = '3px';
        sliderLabel.textContent = '按住滑块，拖动到最右侧';
        slider.setAttribute('aria-valuenow', '0');
    }

    function armCaptcha() {
        captcha.armed = true;
        captchaEl.hidden = false;
        resetSlider();
        powProgress.classList.remove('done');
        powProgress.firstElementChild.style.width = '0%';
        setCaptchaState('等待拖动滑块');
    }

    function unarmCaptcha() {
        // 服务端关闭了人机验证（或不再要求）时收起验证码面板。
        captcha.armed = false;
        captchaEl.hidden = true;
        resetSlider();
        setCaptchaState('等待拖动滑块');
    }

    function waitForSlider() {
        if (captcha.sliderDone) return Promise.resolve();
        return new Promise(function (resolve) { sliderResolve = resolve; });
    }

    (function bindSlider() {
        var dragging = false;
        function limit() { return Math.max(0, slider.getBoundingClientRect().width - sliderKnob.offsetWidth - 6); }
        function moveTo(clientX) {
            var rect = slider.getBoundingClientRect();
            var max = limit();
            var left = Math.min(Math.max(clientX - rect.left - sliderKnob.offsetWidth / 2, 3), max + 3);
            sliderKnob.style.left = left + 'px';
            var progress = max ? Math.min(1, (left - 3) / max) : 1;
            sliderFill.style.width = (progress * 100).toFixed(1) + '%';
            slider.setAttribute('aria-valuenow', String(Math.round(progress * 100)));
            return progress;
        }
        sliderKnob.addEventListener('pointerdown', function (event) {
            if (captcha.sliderDone) return;
            dragging = true;
            slider.classList.add('dragging');
            try { sliderKnob.setPointerCapture(event.pointerId); } catch (e) {}
            event.preventDefault();
        });
        sliderKnob.addEventListener('pointermove', function (event) { if (dragging) moveTo(event.clientX); });
        sliderKnob.addEventListener('pointerup', function (event) {
            if (!dragging) return;
            dragging = false;
            slider.classList.remove('dragging');
            if (moveTo(event.clientX) >= 0.985) {
                captcha.sliderDone = true;
                slider.classList.add('done');
                sliderLabel.textContent = '已确认，正在计算人机验证…';
                if (sliderResolve) { var resolve = sliderResolve; sliderResolve = null; resolve(); }
            } else {
                sliderKnob.style.left = '3px';
                sliderFill.style.width = '0%';
                sliderLabel.textContent = '未拖到最右侧，请重试';
            }
        });
        sliderKnob.addEventListener('pointercancel', function () {
            dragging = false;
            slider.classList.remove('dragging');
            if (!captcha.sliderDone) { sliderKnob.style.left = '3px'; sliderFill.style.width = '0%'; }
        });
        slider.addEventListener('keydown', function (event) {
            if (captcha.sliderDone) return;
            if (event.key === 'ArrowRight' || event.key === 'End' || event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                captcha.sliderDone = true;
                slider.classList.add('done');
                sliderFill.style.width = '100%';
                sliderLabel.textContent = '已确认，正在计算人机验证…';
                slider.setAttribute('aria-valuenow', '100');
                if (sliderResolve) { var resolve = sliderResolve; sliderResolve = null; resolve(); }
            }
        });
    })();

    function paintLock() {
        var remaining = Math.max(0, lockUntilMs - Date.now());
        if (remaining > 0) {
            lockBanner.hidden = false;
            lockCountdown.textContent = fmtCountdown(remaining);
        } else if (lockUntilMs > 0) {
            lockBanner.hidden = true;
            lockUntilMs = 0;
            var submit = document.getElementById('login-submit');
            if (!captcha.busy) { submit.disabled = false; submit.textContent = '登录'; }
            var error = document.getElementById('login-error');
            error.textContent = '封禁已结束；仍需完成人机验证后才能登录。';
            error.style.display = '';
            armCaptcha();
        }
    }
    setInterval(paintLock, 250);

    function applyLock(retryAfterMs) {
        lockUntilMs = Date.now() + (retryAfterMs || 0);
        paintLock();
        var submit = document.getElementById('login-submit');
        submit.disabled = true;
        submit.textContent = '已封禁';
    }

    // ---- 服务端交互 ----

    function fetchChallenge(user, attempt) {
        if (!window.ScrcpyGateApi) return Promise.reject(new Error('API unavailable'));
        return window.ScrcpyGateApi.get('auth.challenge', { params: { user: user || '' } })
            .catch(function (err) {
                // 挑战签发限流：按 Retry-After 自动重试一次
                var payload = err && err.detail && err.detail.payload;
                if (payload && payload.code === 'CAPTCHA_ISSUE_RATE_LIMITED' && attempt < 2) {
                    var wait = (err.detail && err.detail.retryAfterMs) || 2100;
                    return new Promise(function (resolve) {
                        setTimeout(function () { resolve(fetchChallenge(user, attempt + 1)); }, wait + 100);
                    });
                }
                throw err;
            });
    }

    function runCaptcha(username) {
        armCaptcha();
        setCaptchaState('等待拖动滑块');
        return waitForSlider().then(function () {
            setCaptchaState('正在计算');
            powProgress.classList.remove('done');
            return fetchChallenge(username, 0);
        }).then(function (payload) {
            var challenge = payload && payload.challenge;
            if (!challenge) {
                // 服务端未要求验证码或已锁定：让登录接口给出权威答复
                return null;
            }
            return window.ScrcpyGatePow.solve(challenge, function (info) {
                powProgress.firstElementChild.style.width = Math.min(100, (info.hashes / Math.pow(2, challenge.bits)) * 100).toFixed(1) + '%';
                powMeta.textContent = '难度 ' + challenge.bits + ' bit ｜ 已计算 ' + fmtInt(info.hashes) + ' ｜ 耗时 ' + fmtMs(info.elapsedMs);
                setCaptchaState('正在计算 ' + fmtInt(info.hashes) + ' 次');
            }).then(function (solved) {
                powProgress.firstElementChild.style.width = '100%';
                powProgress.classList.add('done');
                powMeta.textContent = '难度 ' + challenge.bits + ' bit ｜ 已计算 ' + fmtInt(solved.hashes) + ' ｜ 耗时 ' + fmtMs(solved.elapsedMs);
                setCaptchaState('计算完成');
                return {
                    v: challenge.v, algorithm: challenge.algorithm, challenge: challenge.challenge,
                    salt: challenge.salt, bits: challenge.bits, maxNumber: challenge.maxNumber,
                    expires: challenge.expires, signature: challenge.signature, user: challenge.user,
                    number: solved.number
                };
            });
        });
    }

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
            if (error) { error.style.display = 'none'; }
            captcha.busy = true;
            submit.disabled = true;
            submit.setAttribute('aria-busy', 'true');
            submit.textContent = '登录中…';

            function attempt(proof) {
                return window.ScrcpyGateApi.post('auth.login', { body: { username: name, password: secret, proof: proof } })
                    .catch(function (err) {
                        var code = codeOf(err);
                        if (code === 'CAPTCHA_REQUIRED') {
                            return runCaptcha(name).then(function (solved) {
                                if (!solved) throw err;
                                return attempt(solved);
                            });
                        }
                        throw err;
                    });
            }

            attempt(null)
                .then(function () {
                    // A successful login response is not enough when the browser rejects
                    // a Secure cookie on a direct HTTP test URL. Verify the session before
                    // navigating so the user gets an actionable error instead of a redirect loop.
                    loginAccepted = true;
                    return window.ScrcpyGateApi.get('session.current');
                })
                .then(function () { window.location.href = '/mirror'; })
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
                        armCaptcha();
                    } else if (code === 'CAPTCHA_INVALID') {
                        if (error) { error.textContent = '人机验证未通过，请重试。'; error.style.display = ''; }
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

    // 打开页面时查询封禁状态：被封禁的 IP 直接显示倒计时横幅。
    if (window.ScrcpyGateApi) {
        window.ScrcpyGateApi.get('auth.challenge', { params: { user: '' } })
            .then(function (payload) {
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

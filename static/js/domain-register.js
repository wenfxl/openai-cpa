const DOMAIN_REGISTER_METHODS = {
    normalizeDomainRegisterInvite() {
                let ref = String((this.domainRegister && this.domainRegister.invite_ref) || '').trim();
                const pickRef = (text) => {
                    const m = String(text || '').match(/[?&]ref=([^&?#\s]+)/i);
                    return m ? decodeURIComponent(m[1]).trim() : '';
                };
                if (/^https?:\/\//i.test(ref)) {
                    const fromUrl = pickRef(ref);
                    if (fromUrl) ref = fromUrl;
                }
                ref = String(ref || '').replace(/\s+/g, '');
                this.domainRegister.invite_ref = ref;
                return ref;
            },
    applyDomainRegisterFromConfig() {
                const d = (this.config && this.config.domain_register && typeof this.config.domain_register === 'object')
                    ? this.config.domain_register
                    : {};
                if (d.api_token != null) this.domainRegister.api_token = String(d.api_token || '');
                if (d.invite_ref != null) this.domainRegister.invite_ref = String(d.invite_ref || '');
                // 兼容旧配置里的 signup_url
                if (!this.domainRegister.invite_ref && d.signup_url) {
                    this.domainRegister.invite_ref = String(d.signup_url || '');
                }
                if (d.auto_invite_signup != null) this.domainRegister.auto_invite_signup = !!d.auto_invite_signup;
                if (d.domain_prefix) this.domainRegister.domain_prefix = d.domain_prefix;
                if (d.domain_suffix) this.domainRegister.domain_suffix = d.domain_suffix;
                if (d.domain_start != null) this.domainRegister.domain_start = d.domain_start;
                if (d.domain_end != null) this.domainRegister.domain_end = d.domain_end;
                if (d.domain_pad != null) this.domainRegister.domain_pad = d.domain_pad;
                this.normalizeDomainRegisterInvite && this.normalizeDomainRegisterInvite();
            },
    syncDomainRegisterToConfig() {
                if (!this.config || typeof this.config !== 'object') this.config = {};
                this.normalizeDomainRegisterInvite && this.normalizeDomainRegisterInvite();
                const prev = (this.config.domain_register && typeof this.config.domain_register === 'object')
                    ? this.config.domain_register
                    : {};
                this.config.domain_register = {
                    ...prev,
                    api_token: String(this.domainRegister.api_token || ''),
                    invite_ref: String(this.domainRegister.invite_ref || ''),
                    auto_invite_signup: !!this.domainRegister.auto_invite_signup,
                    domain_prefix: String(this.domainRegister.domain_prefix || 'sub2api'),
                    domain_suffix: String(this.domainRegister.domain_suffix || 'dpdns.org'),
                    domain_start: Number(this.domainRegister.domain_start) || 0,
                    domain_end: Number(this.domainRegister.domain_end) || 0,
                    domain_pad: Number(this.domainRegister.domain_pad) || 0,
                };
                // 清理已废弃字段
                delete this.config.domain_register.cf_account_id;
                delete this.config.domain_register.signup_url;
                delete this.config.domain_register.cf_page_url;
                delete this.config.domain_register.cf_sitekey;
                delete this.config.domain_register.headless;
            },
    async saveDomainRegisterConfig() {
                try {
                    this.syncDomainRegisterToConfig();
                    const res = await this.authFetch('/api/config', {
                        method: 'POST',
                        body: JSON.stringify(this.config),
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.showToast('域名扩展配置已保存', 'success');
                        await this.fetchConfig();
                        this.applyDomainRegisterFromConfig();
                    } else {
                        this.showToast('保存失败：' + (data.message || ''), 'error');
                    }
                } catch (e) {
                    this.showToast('保存失败网络异常', 'error');
                }
            },
    async refreshDomainRegisterStatus() {
                if (!this.domainRegisterEnabled) return;
                try {
                    const res = await this.authFetch('/api/domain-register/status');
                    const data = await res.json();
                    if (data && data.data) {
                        const wasRunning = !!this.domainRegister.running;
                        this.domainRegister.running = !!data.data.running;
                        this.domainRegister.statusText = data.data.last_message || '';
                        this.domainRegister.logs = data.data.logs || [];
                        this.domainRegister.progress = data.data.progress || this.domainRegister.progress;
                        if (this.domainRegister.running || this.currentTab === 'domain_register') {
                            this.ensureDomainRegisterStatusPolling();
                        } else if (wasRunning && !this.domainRegister.running) {
                            this.stopDomainRegisterStatusPolling();
                        }
                    }
                } catch (e) {}
            },
    ensureDomainRegisterStatusPolling() {
                if (!this.domainRegisterEnabled) return;
                if (this.domainRegisterStatusTimer) return;
                this.domainRegisterStatusTimer = setInterval(() => {
                    if (!this.domainRegisterEnabled) {
                        this.stopDomainRegisterStatusPolling();
                        return;
                    }
                    if (this.currentTab === 'domain_register' || this.domainRegister.running) {
                        this.refreshDomainRegisterStatus();
                    } else {
                        this.stopDomainRegisterStatusPolling();
                    }
                }, 1000);
            },
    stopDomainRegisterStatusPolling() {
                if (this.domainRegisterStatusTimer) {
                    clearInterval(this.domainRegisterStatusTimer);
                    this.domainRegisterStatusTimer = null;
                }
            },
    async previewDomainRegisterDomains() {
                if (!this.domainRegisterEnabled) return;
                try {
                    const q = new URLSearchParams({
                        domain_prefix: this.domainRegister.domain_prefix || 'sub2api',
                        domain_suffix: this.domainRegister.domain_suffix || 'dpdns.org',
                        domain_start: String(this.domainRegister.domain_start ?? 27),
                        domain_end: String(this.domainRegister.domain_end ?? 127),
                        domain_pad: String(this.domainRegister.domain_pad ?? 4),
                    });
                    const res = await this.authFetch('/api/domain-register/preview-domains?' + q.toString());
                    const data = await res.json();
                    if (data && data.data) {
                        this.domainRegister.preview = data.data.sample || (data.data.domains || []).slice(0, 5);
                    }
                } catch (e) {}
            },
    async inviteDomainRegisterSignup() {
                try {
                    this.ensureDomainRegisterStatusPolling();
                    const ref = this.normalizeDomainRegisterInvite();
                    if (!ref) {
                        this.showToast('请填写邀请码（可粘贴带 ref= 的邀请链接）', 'error');
                        return;
                    }
                    this.showToast('邀请注册进行中…', 'info');
                    const res = await this.authFetch('/api/domain-register/invite-signup', {
                        method: 'POST',
                        body: JSON.stringify({
                            invite_ref: ref,
                        })
                    });
                    const data = await res.json();
                    if (data && data.status === 'success') {
                        this.showToast('邀请成功：主账号额度应已增加（Token 不变）', 'success');
                        this.refreshDomainRegisterStatus();
                    } else {
                        this.showToast('邀请失败：' + ((data && data.message) || ''), 'error');
                        this.refreshDomainRegisterStatus();
                    }
                } catch (e) {
                    this.showToast('邀请请求异常', 'error');
                }
            },
    async startDomainRegisterBatch() {
                try {
                    this.ensureDomainRegisterStatusPolling();
                    const ref = this.normalizeDomainRegisterInvite();
                    if (this.domainRegister.auto_invite_signup && !ref) {
                        this.showToast('已开启自动邀请：请先填写邀请码', 'warning');
                    }
                    const res = await this.authFetch('/api/domain-register/start', {
                        method: 'POST',
                        body: JSON.stringify({
                            api_token: this.domainRegister.api_token,
                            invite_ref: ref || '',
                            auto_invite_signup: !!this.domainRegister.auto_invite_signup,
                            domain_prefix: this.domainRegister.domain_prefix,
                            domain_suffix: this.domainRegister.domain_suffix,
                            domain_start: this.domainRegister.domain_start,
                            domain_end: this.domainRegister.domain_end,
                            domain_pad: this.domainRegister.domain_pad,
                        })
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.showToast(data.message || '已启动', 'success');
                        this.refreshDomainRegisterStatus();
                    } else {
                        this.showToast(data.message || '启动失败', 'error');
                    }
                } catch (e) {
                    this.showToast(String(e), 'error');
                }
            },
    async stopDomainRegisterBatch() {
                try { this.stopDomainRegisterStatusPolling(); } catch (e) {}
                try {
                    this.domainRegister.running = false;
                    this.domainRegister.statusText = '已请求停止';
                    this.showToast('已请求停止', 'success');
                } catch (e) {}
                try {
                    this.authFetch('/api/domain-register/stop', { method: 'POST', body: '{}' }).catch(() => {});
                } catch (e) {}
            },
};

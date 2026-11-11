const MAINTENANCE_METHODS = {
    async fetchCleanupStatus(showToast = false) {
                if (!this.isLoggedIn) return;
                this.cleanupMaintenance.loading = true;
                this.cleanupMaintenance.error = '';
                try {
                    const res = await this.authFetch('/api/system/cleanup_status');
                    const payload = await res.json();
                    if (payload.status === 'success' || payload.status === 'warning') {
                        this.cleanupMaintenance.data = payload.data || null;
                        if (payload.status === 'warning') {
                            this.cleanupMaintenance.error = payload.message || '';
                        } else if (showToast) {
                            this.showToast(payload.message || '清理状态已刷新', 'success');
                        }
                    } else {
                        this.cleanupMaintenance.error = payload.message || '清理状态获取失败';
                    }
                } catch (e) {
                    this.cleanupMaintenance.error = '清理状态请求失败';
                } finally {
                    this.cleanupMaintenance.loading = false;
                }
            },
    async runCleanup(force = false) {
                const confirmed = await this.customConfirm(force ? '确定要强制执行磁盘 / 日志清理吗？\n即使磁盘占用还没到阈值，也会立刻清理日志、缓存和临时文件。' : '确定要执行磁盘 / 日志清理吗？');
                if (!confirmed) return;
                this.cleanupMaintenance.actionLoading = true;
                this.cleanupMaintenance.error = '';
                try {
                    const res = await this.authFetch('/api/system/run_cleanup', {
                        method: 'POST',
                        body: JSON.stringify({ force })
                    });
                    const payload = await res.json();
                    this.cleanupMaintenance.outputTail = payload?.data?.output_tail || [];
                    this.cleanupMaintenance.data = payload?.data?.status || this.cleanupMaintenance.data;
                    if (payload.status === 'success') {
                        this.showToast(payload.message || '清理完成', 'success');
                    } else {
                        this.cleanupMaintenance.error = payload.message || '清理失败';
                        this.showToast(this.cleanupMaintenance.error, 'error');
                    }
                } catch (e) {
                    this.cleanupMaintenance.error = '清理请求失败';
                    this.showToast(this.cleanupMaintenance.error, 'error');
                } finally {
                    this.cleanupMaintenance.actionLoading = false;
                    await this.fetchCleanupStatus(false);
                }
            },
    async fetchGitSyncStatus(showToast = false) {
                if (!this.isLoggedIn) return;
                this.gitSync.loading = true;
                this.gitSync.error = '';
                try {
                    const res = await this.authFetch('/api/system/git_status');
                    const payload = await res.json();
                    if (payload.status === 'success' || payload.status === 'warning') {
                        this.gitSync.data = payload.data || null;
                        this.gitSync.outputTail = [];
                        if (payload.status === 'warning') {
                            this.gitSync.error = payload.message || 'Git 状态读取失败';
                        } else if (showToast) {
                            this.showToast(payload.message || 'Git 状态已刷新', 'success');
                        }
                    } else {
                        this.gitSync.error = payload.message || 'Git 状态读取失败';
                    }
                } catch (e) {
                    this.gitSync.error = 'Git 状态请求失败';
                } finally {
                    this.gitSync.loading = false;
                }
            },
    async runGitSyncAction(action) {
                const isReset = action === 'reset_hard';
                const confirmText = isReset
                    ? `⚠️ 危险操作：\n\n确定要强制覆盖本地代码并同步到远端跟踪分支吗？\n这会直接丢弃当前未提交改动。${this.gitSync.restartAfter ? '\n同步完成后会自动重启项目。' : ''}`
                    : '确定要抓取远端最新 Git 状态吗？';
                const confirmed = await this.customConfirm(confirmText);
                if (!confirmed) return;
    
                this.gitSync.actionLoading = true;
                this.gitSync.lastAction = action;
                try {
                    const res = await this.authFetch('/api/system/git_update', {
                        method: 'POST',
                        body: JSON.stringify({ action, restart_after: isReset ? !!this.gitSync.restartAfter : false })
                    });
                    const payload = await res.json();
                    this.gitSync.outputTail = payload?.data?.output_tail || [];
                        if (payload.status === 'success') {
                            this.gitSync.data = payload?.data?.after || this.gitSync.data;
                            this.showToast(payload.message || 'Git 操作完成', 'success');
                            if (payload?.data?.restart_scheduled) {
                            this.showToast('项目正在重启，页面将在 6 秒后自动刷新...', 'info');
                            setTimeout(() => window.location.reload(), 6000);
                            }
                        } else {
                        this.gitSync.data = payload?.data?.after || this.gitSync.data;
                        this.gitSync.error = payload.message || 'Git 操作失败';
                        this.showToast(this.gitSync.error, payload.status === 'warning' ? 'warning' : 'error');
                    }
                } catch (e) {
                    this.gitSync.error = 'Git 操作请求失败';
                    this.showToast(this.gitSync.error, 'error');
                } finally {
                    this.gitSync.actionLoading = false;
                    await this.fetchGitSyncStatus(false);
                }
            },
    async fetchConfig() {
                this.isLoadingConfig = true;
                this.configLoadError = '';
                try {
                    const res = await this.authFetch('/api/config');
                    this.config = await res.json();
                    if (!this.config.tg_bot) {
                        this.config.tg_bot = { enable: false, token: '', chat_id: '' };
                    }
                    if (!this.config.local_microsoft) {
                        this.config.local_microsoft = {
                            enable_fission: false,
                            pool_fission: false,
                            master_email: '',
                            client_id: '',
                            refresh_token: '',
                            suffix_mode: 'fixed',
                            suffix_len_min: 8,
                            suffix_len_max: 8
                        };
                    }
    
    
                    if (this.config) {
                        if (!this.config.smsbower) {
                            this.config.smsbower = {
                                enabled: false, api_key: '', country: 0, service: 'dr',
                                auto_pick_country: true, verify_on_register: false, reuse_phone: true, reuse_max: 2,
                                max_price: 0.08, min_price: 0.05, min_balance: 10.0, max_tries: 3, poll_timeout_sec: 180
                            };
                        } else {
                            this.config.smsbower.min_price = parseFloat(this.config.smsbower.min_price) || 0.05;
                            this.config.smsbower.enabled = normalizeBooleanLike(this.config.smsbower.enabled, false);
                            this.config.smsbower.auto_pick_country = normalizeBooleanLike(this.config.smsbower.auto_pick_country, true);
                            this.config.smsbower.reuse_phone = normalizeBooleanLike(this.config.smsbower.reuse_phone, true);
                            this.config.smsbower.verify_on_register = normalizeBooleanLike(this.config.smsbower.verify_on_register, false);
                            if(this.config.smsbower.reuse_max === undefined) this.config.smsbower.reuse_max = 2;
                            if(this.config.smsbower.use_proxy === undefined) this.config.smsbower.use_proxy = false;
                        }
    
                        if (!this.config.fivesim) {
                            this.config.fivesim = {
                                enabled: false, api_key: '', country: 'any', service: 'openai',
                                auto_pick_country: true, verify_on_register: false,reuse_phone: true,reuse_max: 2,
                                max_price: 50.0, min_price: 0.0, min_balance: 10.0, max_tries: 3, poll_timeout_sec: 180
                            };
                        } else {
                            if(this.config.fivesim.reuse_max === undefined) this.config.fivesim.reuse_max = 2;
                            if(this.config.fivesim.use_proxy === undefined) this.config.fivesim.use_proxy = false;
                        }
    
                        if (this.config.hero_sms) {
                            this.config.hero_sms.enabled = normalizeBooleanLike(this.config.hero_sms.enabled, false);
                            if(this.config.hero_sms.reuse_max === undefined) this.config.hero_sms.reuse_max = 2;
                            if(this.config.hero_sms.use_proxy === undefined) this.config.hero_sms.use_proxy = false;
                        }
                    }
    
    
                    if (this.config.local_microsoft.suffix_mode === undefined) {
                        this.config.local_microsoft.suffix_mode = 'fixed';
                    }
                    if (this.config.local_microsoft.suffix_len_min === undefined) {
                        this.config.local_microsoft.suffix_len_min = 8;
                    }
                    if (this.config.local_microsoft.suffix_len_max === undefined) {
                        this.config.local_microsoft.suffix_len_max = 8;
                    }
                    if (this.config.local_microsoft.pool_fission === undefined) {
                        this.config.local_microsoft.pool_fission = false;
                    }
                    if (this.config.sub2api_mode.test_model === undefined) {
                        this.config.sub2api_mode.test_model = 'gpt-5.2';
                    }
                    if (Array.isArray(this.config.sub2api_mode.default_proxy)) {
                        this.config.sub2api_mode.default_proxy = this.config.sub2api_mode.default_proxy.join('\n');
                    }
                    if (this.config.sub2api_mode.default_proxy === undefined) {
                        this.config.sub2api_mode.default_proxy = '';
                    }
                    if (!this.config.image2api_mode) {
                        this.config.image2api_mode = {
                            enable: false,
                            api_url: '',
                            api_key: '',
                            retain_reg_only: false,
                            img_only_mode: false
                        };
                    } else {
                        if (this.config.image2api_mode.retain_reg_only === undefined) {
                            this.config.image2api_mode.retain_reg_only = false;
                        }
                        if (this.config.image2api_mode.img_only_mode === undefined) {
                            this.config.image2api_mode.img_only_mode = false;
                        }
                    }
                    if (!this.config.grok2api || typeof this.config.grok2api !== 'object') {
                        this.config.grok2api = {};
                    }
                    const grokDefaults = {
                        enable: false,
                        auto_check: true,
                        save_to_local: true,
                        api_url: '',
                        admin_password: '',
                        min_accounts_threshold: 20,
                        batch_reg_count: 1,
                        min_remaining_weekly_percent: 0,
                        remove_on_limit_reached: true,
                        remove_dead_accounts: true,
                        enable_token_revive: false,
                        check_interval_minutes: 60,
                        threads: 5,
                        retain_reg_only: false,
                        auto_re_oauth: false,
                        account_concurrency: 10,
                        account_load_factor: 10,
                        account_priority: 1,
                        account_rate_multiplier: 1.0,
                        account_group_ids: '',
                        enable_ws_mode: true,
                        test_model: 'grok-4.5',
                        default_proxy: '',
                        auto_import_after_register: false,
                        import_sso_as_grok_web: false,
                        sso_only_mode: false
                    };
                    Object.entries(grokDefaults).forEach(([key, value]) => {
                        if (this.config.grok2api[key] === undefined) this.config.grok2api[key] = value;
                    });
                    if (Array.isArray(this.config.grok2api.default_proxy)) {
                        this.config.grok2api.default_proxy = this.config.grok2api.default_proxy.join('\n');
                    }
    
                    if (!this.config.team_mode) {
                        this.config.team_mode = { enable: false, overspeed: false };
                    } else {
                        if (this.config.team_mode.overspeed === undefined) {
                            this.config.team_mode.overspeed = false;
                        }
                    }
                    if (!this.config.fvia) {
                        this.config.fvia = { token: '' };
                    }
                    if (!this.config.tmailor) {
                        this.config.tmailor = { current_token: '' };
                    }
                    if (!this.config.max_log_lines) {
                        this.config.max_log_lines = 500;
                    }
                    let clusterUploadTimeout = parseInt(this.config.cluster_upload_timeout_sec, 10);
                    if (Number.isNaN(clusterUploadTimeout)) clusterUploadTimeout = 15;
                    this.config.cluster_upload_timeout_sec = Math.max(15, Math.min(3600, clusterUploadTimeout));
                    if (!this.config.temporam) {
                        this.config.temporam = { cookie: '' };
                    }
                    if (!this.config.openai_cpa_cloud || typeof this.config.openai_cpa_cloud !== 'object' || Array.isArray(this.config.openai_cpa_cloud)) {
                        this.config.openai_cpa_cloud = {};
                    }
                    if (!this.config.openai_cpa_cloud.api_url) {
                        this.config.openai_cpa_cloud.api_url = 'https://mail.wfxling.com';
                    }
                    if (this.config.openai_cpa_cloud.api_token === undefined) {
                        this.config.openai_cpa_cloud.api_token = '';
                    }
                    if (!Number.isFinite(Number(this.config.openai_cpa_cloud.limit))) {
                        this.config.openai_cpa_cloud.limit = 20;
                    }
                    if (!this.config.reg_mode) {
                            this.config.reg_mode = 'email';
                        }
                    if (!this.config.reg_provider) {
                            this.config.reg_provider = 'openai';
                        }
                    if (!['protocol', 'cloakbrowser'].includes(this.config.register_driver)) {
                        this.config.register_driver = 'protocol';
                    }
                    if (!this.config.cloakbrowser || typeof this.config.cloakbrowser !== 'object' || Array.isArray(this.config.cloakbrowser)) {
                        this.config.cloakbrowser = {};
                    }
                    const cloakDefaults = {
                        headless: true,
                        humanize: true,
                        geoip: true,
                        locale: '',
                        timezone: '',
                        use_proxy: true,
                        license_key: '',
                        fingerprint_seed: '',
                        user_data_dir: '',
                        extra_args: [],
                        navigation_timeout: 90,
                        action_timeout: 30,
                        profile_timeout: 60,
                        session_timeout: 90,
                        keep_browser_open: false,
                        registration_name: '',
                        registration_birthday: ''
                    };
                    Object.entries(cloakDefaults).forEach(([key, value]) => {
                        if (this.config.cloakbrowser[key] === undefined) {
                            this.config.cloakbrowser[key] = Array.isArray(value) ? [] : value;
                        }
                    });
                    if (!this.config.grok || typeof this.config.grok !== 'object') {
                            this.config.grok = {
                                oauth_timeout: 180
                            };
                        } else {
                            if (this.config.grok.oauth_timeout === undefined) this.config.grok.oauth_timeout = 180;
                        }
                    if (!this.config.tg_bot.template_success) {
                        this.config.tg_bot.template_success = "🎉 <b>注册成功</b>\n⏰ 时间: <code>{time}</code>\n📧 账号: <code>{email}</code>\n🔑 密码: <code>{password}</code>";
                    }
                    if (!this.config.tg_bot.template_stop) {
                        this.config.tg_bot.template_stop = "🛑 <b>系统已收到停止指令</b>\n\n📊 <b>最终运行统计</b>：\n成功率: {success_rate}% · 成功: {success}/{target} · 失败: {failed} 次 · 风控拦截: {retries} 次 · 密码受阻: {pwd_blocked} 次 · 出现手机: {phone_verify} 次 · 总耗时: {elapsed_time}s · 平均单号: {avg_time}s";
                    }
                    if (!this.config.database) {
                        this.config.database = {
                            type: 'sqlite',
                            mysql: { host: '127.0.0.1', port: 3306, user: 'root', password: '', db_name: 'wenfxl_manager' }
                        };
                    }
                    if (!this.config.database.mysql) {
                        this.config.database.mysql = { host: '127.0.0.1', port: 3306, user: 'root', password: '', db_name: 'wenfxl_manager' };
                    }
    				if (!this.config.sub_domain_level) {
                        this.config.sub_domain_level = 1;
                    }
                    if (!this.config.sub2api_mode) {
                        this.config.sub2api_mode = {};
                    }
                    if (this.config.sub2api_mode.account_concurrency === undefined) {
                        this.config.sub2api_mode.account_concurrency = 10;
                    }
                    if (this.config.sub2api_mode.account_load_factor === undefined) {
                        this.config.sub2api_mode.account_load_factor = 10;
                    }
                    if (this.config.sub2api_mode.account_priority === undefined) {
                        this.config.sub2api_mode.account_priority = 1;
                    }
                    if (this.config.sub2api_mode.account_rate_multiplier === undefined) {
                        this.config.sub2api_mode.account_rate_multiplier = 1.0;
                    }
                    if (this.config.sub2api_mode.account_group_ids === undefined) {
                        this.config.sub2api_mode.account_group_ids = '';
                    }
                    if (this.config.sub2api_mode.enable_ws_mode === undefined) {
                        this.config.sub2api_mode.enable_ws_mode = true;
                    }
                    if(this.config.clash_proxy_pool && Array.isArray(this.config.clash_proxy_pool.blacklist)) {
                        this.blacklistStr = this.config.clash_proxy_pool.blacklist.join('\n');
                    } else {
                        this.blacklistStr = '';
                    }
                    if (this.config.clash_proxy_pool.cluster_count !== undefined) {
                        this.clashPool.count = parseInt(this.config.clash_proxy_pool.cluster_count) || 5;
                    }
                    if (this.config.clash_proxy_pool.sub_url !== undefined) {
                        this.clashPool.subUrl = this.config.clash_proxy_pool.sub_url;
                    }
                    if (!this.config.raw_proxy_pool || typeof this.config.raw_proxy_pool !== 'object' || Array.isArray(this.config.raw_proxy_pool)) {
                        this.config.raw_proxy_pool = { enable: false, proxy_list: [] };
                    } else {
                        this.config.raw_proxy_pool.enable = normalizeBooleanLike(this.config.raw_proxy_pool.enable, false);
                        if (!Array.isArray(this.config.raw_proxy_pool.proxy_list)) {
                            this.config.raw_proxy_pool.proxy_list = [];
                        }
                    }
                    if(Array.isArray(this.config.warp_proxy_list)) {
                        this.warpListStr = this.config.warp_proxy_list.join('\n');
                    } else {
                        this.config.warp_proxy_list = [];
                        this.warpListStr = '';
                    }
                    this.rawProxyListStr = this.config.raw_proxy_pool.proxy_list.join('\n');
                    if (this.config.cluster_node_name === undefined) this.config.cluster_node_name = '';
                    if (this.config.cluster_master_url === undefined) this.config.cluster_master_url = '';
                    if (this.config.cluster_secret === undefined) this.config.cluster_secret = 'wenfxl666';
                    if (!Array.isArray(this.config.disabled_mail_domains)) this.config.disabled_mail_domains = [];
                    this.config.disabled_mail_domains = [...new Set(
                        this.config.disabled_mail_domains
                            .map(item => String(item || '').trim().toLowerCase().replace(/^\.+|\.+$/g, ''))
                            .filter(Boolean)
                    )];
                    if (this.config.enable_mail_domain_runtime_control === undefined) this.config.enable_mail_domain_runtime_control = false;
                    this.config.enable_mail_domain_runtime_control = normalizeBooleanLike(this.config.enable_mail_domain_runtime_control, false);
                    if (this.config.enable_mail_domain_grouping === undefined) this.config.enable_mail_domain_grouping = false;
                    this.config.enable_mail_domain_grouping = normalizeBooleanLike(this.config.enable_mail_domain_grouping, false);
                    if (this.config.mail_domain_group_count === undefined) this.config.mail_domain_group_count = 2;
                    this.config.mail_domain_group_count = Math.min(10, Math.max(1, parseInt(this.config.mail_domain_group_count, 10) || 2));
                    if (this.config.mail_domain_group_mode === undefined) this.config.mail_domain_group_mode = 'auto';
                    this.config.mail_domain_group_mode = ['auto', 'manual'].includes(String(this.config.mail_domain_group_mode || '').trim().toLowerCase())
                        ? String(this.config.mail_domain_group_mode || '').trim().toLowerCase()
                        : 'auto';
                    if (this.config.mail_domain_group_strategy === undefined) this.config.mail_domain_group_strategy = 'round_robin';
                    this.config.mail_domain_group_strategy = ['round_robin', 'exhaust_then_next'].includes(String(this.config.mail_domain_group_strategy || '').trim().toLowerCase())
                        ? String(this.config.mail_domain_group_strategy || '').trim().toLowerCase()
                        : 'round_robin';
                    if (!Array.isArray(this.config.mail_domain_groups)) this.config.mail_domain_groups = [];
                    this.config.mail_domain_groups = this.config.mail_domain_groups
                        .slice(0, this.config.mail_domain_group_count)
                        .map(item => normalizeMailDomainCsv(item).join(','));
                    while (this.config.mail_domain_groups.length < this.config.mail_domain_group_count) {
                        this.config.mail_domain_groups.push('');
                    }
                    if (this.config.mail_domain_pinpoint_burst_mode === undefined) this.config.mail_domain_pinpoint_burst_mode = false;
                    this.config.mail_domain_pinpoint_burst_mode = normalizeBooleanLike(this.config.mail_domain_pinpoint_burst_mode, false);
                    if (this.config.mail_domain_prefer_low_failure_mode === undefined) this.config.mail_domain_prefer_low_failure_mode = false;
                    this.config.mail_domain_prefer_low_failure_mode = normalizeBooleanLike(this.config.mail_domain_prefer_low_failure_mode, false);
                    if (this.config.mail_domain_pinpoint_burst_mode && this.config.mail_domain_prefer_low_failure_mode) {
                        this.config.mail_domain_prefer_low_failure_mode = false;
                    }
                    this.applyMailDomainModeConstraints();
                    if (!Array.isArray(this.config.mail_domain_failure_types)) this.config.mail_domain_failure_types = ['discarded_email'];
                    this.config.mail_domain_failure_types = [...new Set(
                        this.config.mail_domain_failure_types
                            .map(item => String(item || '').trim().toLowerCase())
                            .filter(Boolean)
                    )];
                    if (this.config.mail_domain_failure_types.length === 0) this.config.mail_domain_failure_types = ['discarded_email'];
                    if (this.config.mail_domain_fail_threshold === undefined) this.config.mail_domain_fail_threshold = 3;
                    if (this.config.mail_domain_fail_cooldown_sec === undefined) this.config.mail_domain_fail_cooldown_sec = 600;
                } catch (e) {
                    this.configLoadError = e?.message === 'Unauthorized' ? '登录已失效，请重新登录' : '配置加载失败，请稍后重试';
                } finally {
                    this.isLoadingConfig = false;
                }
            },
    async saveConfig() {
                try {
                    if(this.config.clash_proxy_pool) {
                        this.config.clash_proxy_pool.blacklist = this.blacklistStr.split('\n').map(s => s.trim()).filter(s => s);
                        this.config.clash_proxy_pool.cluster_count = parseInt(this.clashPool.count) || 5;
                        this.config.clash_proxy_pool.sub_url = this.clashPool.subUrl;
                    }
                    if (this.config?.sub2api_mode) {
                        this.config.sub2api_mode.default_proxy = String(this.config.sub2api_mode.default_proxy || '')
                            .split(/\r?\n/)
                            .map(s => s.trim())
                            .filter(s => s)
                            .join('\n');
                    }
                    if (this.config.local_microsoft) {
                        const mode = String(this.config.local_microsoft.suffix_mode || 'fixed').toLowerCase();
                        this.config.local_microsoft.suffix_mode = ['fixed', 'range', 'mystic'].includes(mode) ? mode : 'fixed';
    
                        let minLen = parseInt(this.config.local_microsoft.suffix_len_min, 10);
                        let maxLen = parseInt(this.config.local_microsoft.suffix_len_max, 10);
                        if (Number.isNaN(minLen)) minLen = 8;
                        if (Number.isNaN(maxLen)) maxLen = minLen;
                        minLen = Math.max(8, Math.min(32, minLen));
                        maxLen = Math.max(8, Math.min(32, maxLen));
                        if (maxLen < minLen) maxLen = minLen;
                        this.config.local_microsoft.suffix_len_min = minLen;
                        this.config.local_microsoft.suffix_len_max = maxLen;
                    }
                    if (this.config.openai_cpa_cloud) {
                        this.config.openai_cpa_cloud.api_url = String(this.config.openai_cpa_cloud.api_url || 'https://mail.wfxling.com').trim().replace(/\/+$/, '');
                        this.config.openai_cpa_cloud.api_token = String(this.config.openai_cpa_cloud.api_token || '').trim();
                        const cloudLimit = parseInt(this.config.openai_cpa_cloud.limit, 10);
                        this.config.openai_cpa_cloud.limit = Math.max(1, Math.min(100, Number.isFinite(cloudLimit) ? cloudLimit : 20));
                    }
                    this.config.enable_mail_domain_runtime_control = normalizeBooleanLike(this.config.enable_mail_domain_runtime_control, false);
                    this.config.enable_mail_domain_grouping = normalizeBooleanLike(this.config.enable_mail_domain_grouping, false);
                    if (this.config.mail_domain_pinpoint_burst_mode === undefined) this.config.mail_domain_pinpoint_burst_mode = false;
                    this.config.mail_domain_pinpoint_burst_mode = normalizeBooleanLike(this.config.mail_domain_pinpoint_burst_mode, false);
                    if (this.config.mail_domain_prefer_low_failure_mode === undefined) this.config.mail_domain_prefer_low_failure_mode = false;
                    this.config.mail_domain_prefer_low_failure_mode = normalizeBooleanLike(this.config.mail_domain_prefer_low_failure_mode, false);
                    this.applyMailDomainModeConstraints();
                    const mailDomainGroupingError = this.validateMailDomainGrouping();
                    if (mailDomainGroupingError) {
                        this.showToast(mailDomainGroupingError, 'warning');
                        return;
                    }
                    if (!Array.isArray(this.config.mail_domain_failure_types)) {
                        this.config.mail_domain_failure_types = ['discarded_email'];
                    }
                    this.config.mail_domain_failure_types = [...new Set(
                        this.config.mail_domain_failure_types
                            .map(item => String(item || '').trim().toLowerCase())
                            .filter(Boolean)
                    )];
                    if (this.config.mail_domain_failure_types.length === 0) {
                        this.config.mail_domain_failure_types = ['discarded_email'];
                    }
                    if (!this.config.enable_mail_domain_runtime_control) {
                        this.mailDomainRuntimeStats = [];
                        this.mailDomainRuntimeStatsError = '';
                        this.mailDomainRuntimeLastFetchAt = 0;
                    }
                    if (!Array.isArray(this.config.disabled_mail_domains)) {
                        this.config.disabled_mail_domains = [];
                    }
                    this.config.disabled_mail_domains = [...new Set(
                        this.config.disabled_mail_domains
                            .map(item => String(item || '').trim().toLowerCase().replace(/^\.+|\.+$/g, ''))
                            .filter(Boolean)
                    )];
                    this.config.mail_domain_fail_threshold = Math.max(0, parseInt(this.config.mail_domain_fail_threshold, 10) || 0);
                    this.config.mail_domain_fail_cooldown_sec = Math.max(0, parseInt(this.config.mail_domain_fail_cooldown_sec, 10) || 0);
                    let clusterUploadTimeout = parseInt(this.config.cluster_upload_timeout_sec, 10);
                    if (Number.isNaN(clusterUploadTimeout)) clusterUploadTimeout = 15;
                    this.config.cluster_upload_timeout_sec = Math.max(15, Math.min(3600, clusterUploadTimeout));
                    this.config.warp_proxy_list = this.warpListStr.split('\n').map(s => s.trim()).filter(s => s);
                    if (!this.config.raw_proxy_pool || typeof this.config.raw_proxy_pool !== 'object' || Array.isArray(this.config.raw_proxy_pool)) {
                        this.config.raw_proxy_pool = { enable: false, proxy_list: [] };
                    }
                    this.config.raw_proxy_pool.enable = normalizeBooleanLike(this.config.raw_proxy_pool.enable, false);
                    this.config.raw_proxy_pool.proxy_list = this.rawProxyListStr.split('\n').map(s => s.trim()).filter(s => s);
                    if (this.domainRegisterEnabled) {
                        this.syncDomainRegisterToConfig();
                    }
                    const res = await this.authFetch('/api/config', {
                        method: 'POST', body: JSON.stringify(this.config)
                    });
                    const data = await res.json();
                    if(data.status === 'success') {
                        this.showToast(data.message, "success");
                        await this.fetchConfig();
                        if (this.domainRegisterEnabled) this.applyDomainRegisterFromConfig();
                        await this.fetchMailDomainRuntimeStats({ force: true });
                        this.queuePollStats();
                    } else { this.showToast("保存失败：" + data.message, "error"); }
                } catch (e) { this.showToast("保存失败网络异常", "error"); }
            },
};

const { createApp } = Vue;

function normalizeBooleanLike(value, defaultValue = false) {
    if (value === true || value === false) {
        return value;
    }
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['1', 'true', 'yes', 'on'].includes(normalized)) {
            return true;
        }
        if (['0', 'false', 'no', 'off', ''].includes(normalized)) {
            return false;
        }
    }
    if (typeof value === 'number') {
        return value !== 0;
    }
    return defaultValue;
}

createApp({
    data() {
        return {
            appVersion: '检查中...',
            isLoggedIn: !!localStorage.getItem('auth_token'),
            loginPassword: '',
            currentTab: window.location.hash.replace('#', '') || 'console',
            isDarkMode: localStorage.getItem('ui_theme_mode') === 'dark',
			showAccountsPlaintext: false,
            isRunning: false,
            tabs: [
                    { id: 'console', name: '运行主页', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>' },
                    { id: 'cluster', name: '集群总控', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>' },
                    { id: 'email', name: '邮箱配置', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>' },
                    { id: 'mailboxes', name: '微软邮箱库', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>' },
                    { id: 'team_accounts', name: 'Team 账号库', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>' },
                    { id: 'accounts', name: '账号库存', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"></path></svg>' },
                    { id: 'cloud', name: '云端库存', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 15a4 4 0 004 4h11a5 5 0 00-.1-9.995A5.002 5.002 0 1010.5 6H9.75a4 4 0 00-6.75 9z"></path></svg>' },
                    { id: 'sms', name: '手机接码', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"></path></svg>' },
                    { id: 'proxy', name: '网络代理', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"></path></svg>' },
                    { id: 'relay', name: '中转管仓', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 15a4 4 0 004 4h11a5 5 0 00-.1-9.995A5.002 5.002 0 1010.5 6H9.75a4 4 0 00-6.75 9z"></path></svg>' },
                    { id: 'notify', name: '消息通知', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h6a3 3 0 013 3v1a3 3 0 01-3 3H9.436c-1.532 0-2.22.24-2.893.542z"></path></svg>' },
                    { id: 'concurrency', name: '并发与系统', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>' },
				// { id: 'cf_routes', name: 'CF 路由', icon: '🌍' },

            ],
			cfGlobalStatus: null,
			isLoadingSync: false,
            luckmailManualQty: 1,
            luckmailManualAutoTag: false,
            isManualBuying: false,
			cfRoutes: [],
            heroSmsBalance: '0.00',
            heroSmsPrices: [],
            isLoadingBalance: false,
            isLoadingPrices: false,
            selectedCfRoutes: [],
			cfGlobalStatusList: [],
			cfStatusTimer: null,
            isLoadingCfRoutes: false,
			isDeletingAccounts: false,
			isDeletingCfRoutes: false,
			subDomainModal: {
				show: false,
				email: '',
				key: '',
				count: 10,
				sync: false,
				loading: false
			},
			tempSubDomains: [],
            logs: [],
            logBuffer: [],
            logFlushTimer: null,
            config: null,
            mailDomainRuntimeStats: [],
            mailDomainRuntimeStatsError: '',
            mailDomainRuntimePanelCollapsed: normalizeBooleanLike(localStorage.getItem('mail_domain_runtime_panel_collapsed'), false),
            mailDomainRuntimeLastFetchAt: 0,
            blacklistStr: "",
            warpListStr: "",
            rawProxyListStr: "",
            accounts: [],
            selectedAccounts: [],
            hideRegisterOnlyAccounts: false,
			currentPage: 1,
            pageSize: 10,
            totalAccounts: 0,
            evtSource: null,
            stats: {
                success: 0, failed: 0, retries: 0, total: 0, target: 0,
                pwd_blocked: 0, phone_verify: 0,
                success_rate: '0.0%', elapsed: '0.0s', avg_time: '0.0s', progress_pct: '0%',
                mode: '未启动'
            },
            inventoryStats: {
                local: { total: 0, active: 0, disabled: 0 },
                cloud: { total: 0, cpa: 0, sub2api: 0, enabled: 0 }
            },
            statsTimer: null,

            showPwd: {
                login: false, web: false, cf: false, imap: false,
                free_token: false, free_pass: false,
                cm: false, mc: false, clash: false, cpa: false, sub2api: false,
                cf_key: false, cf_modal_key: false,
                mail_domains: true, cf_email: true, gpt_base: true, imap_user: true,
                free_url: true, cm_url: true, cm_email: true, mc_base: true,
                ai_base: true, cluster_url: true, proxy: true, clash_api: true,
                clash_test: true, tg_token: false, tg_chatid: false, cpa_url: true, sub_url: true,
                cluster_secret: false, hero_key: false, duck_token: false, duck_cookie: false,
                smsbower_key: false,fivesim_key: false,
                luckmail: false,
                temporam: false,
                tmailor_token: false,
                fvia_token: false,
                subUrl: false,
                showMailboxesPlaintext: false,
                db_pass: false,
                master_rt: false,
                image2api_url: true,
                image2api_key: false
            },

            toasts: [],
            toastId: 0,
            confirmModal: { show: false, message: '', resolve: null },
            updateInfo: { hasUpdate: false, version: '', url: '', changelog: '' },
            sub2apiGroups: [],
            gmailOAuth: {
                authUrl: '',
                pastedCode: '',
                isLoading: false,
                isGenerating: false
            },
            isLoadingSub2APIGroups: false,
            cloudAccounts: [],
            selectedCloud: [],
            cloudFilters: ['sub2api', 'cpa', "image2api"],
            showCloudPlaintext: false,
            cloudPage: 1,
            cloudPageSize: 10,
            cloudTotal: 0,
            localCheckTimes: {},
            localCloudDetails: {},
            isCloudActionLoading: false,
            showCloudDetailModal: false,
            currentCloudDetail: null,
            nowTimestamp: Math.floor(Date.now() / 1000),
            clusterNodes: {},
            mailboxes: [],
            selectedMailboxes: [],
            mailboxPage: 1,
            mailboxPageSize: 10,
            totalMailboxes: 0,
            showImportMailboxModal: false,
            importMailboxText: '',
            isImportingMailbox: false,
            outlookAuth: {
                showModal: false,
                mailbox: null,
                currentClientId: '',
                authUrl: '',
                pastedUrl: '',
                isGenerating: false,
                isLoading: false
            },
            BUILTIN_CLIENT_ID: "7feada80-d946-4d06-b134-73afa3524fb7",
            clashPool: {
                loading: false,
                subUrl: '',
                target: 'all',
                count: 5,
                instances: [],
                groups: []
            },
            gmail_oauth_mode: {
                master_email: '',
                fission_enable: false,
                fission_mode: 'suffix',
                suffix_mode: 'mystic',
                suffix_len_min: 8,
                suffix_len_max: 12
            },
            cloudStatusFilter: 'all',
            searchAccounts: '',
            searchCloud: '',
            searchMailboxes: '',

            smsBowerBalance: '0.00',
            isLoadingSmsBowerBalance: false,
            isLoadingSmsBowerPrices: false,
            smsBowerPrices: [],

            fivesimBalance: null,
            isLoadingFivesimBalance: false,
            fivesimPrices: [],
            isLoadingFivesimPrices: false,
            isRestarting: false,
            isRefreshingAccounts: false,
            teamAccounts: [],
            showImportTeamModal: false,
            importTeamText: '',
            isImportingTeam: false,
            showTeamsPlaintext: false,
            teamPage: 1,
            teamPageSize: 50,
            totalTeamAccounts: 0,
            authResetModal: {
                show: false,
                clearLicense: true,
                clearHwid: true,
                clearLease: true
            },
            cfTools: {
                workerName: 'openai-cpa',
                results: [],
                isHosting: false,
                isEnablingEmail: false,
                isDeploying: false,
                isSettingCatchAll: false
            },
            isUpdatingSystem: false,
        };
    },
    watch: {
        searchAccounts() {
            this.currentPage = 1;
            this.fetchAccounts();
        },
        searchCloud() {
            this.cloudPage = 1;
            this.fetchCloudAccounts();
        },
        searchMailboxes() {
            this.mailboxPage = 1;
            this.fetchMailboxes();
        },
        'config.email_api_mode'(nextMode) {
            const supportedModes = ['cloudflare_temp_email', 'freemail', 'cloudmail', 'openai_cpa'];
            if (!supportedModes.includes(String(nextMode || '').trim())) {
                this.config.enable_mail_domain_runtime_control = false;
                this.mailDomainRuntimeStats = [];
                this.mailDomainRuntimeStatsError = '';
                this.mailDomainRuntimeLastFetchAt = 0;
            }
        }
    },
    mounted() {
        this.applyTheme();
        this.fetchSystemVersion();
        if (this.isLoggedIn) {
            this.initApp();
        }
        window.addEventListener('hashchange', () => {
            const tab = window.location.hash.replace('#', '');
            if (tab && this.tabs.some(t => t.id === tab)) {
                this.switchTab(tab);
            }
        });
        this.timer = setInterval(() => {
            this.nowTimestamp = Math.floor(Date.now() / 1000);
        }, 1000);
    },
    beforeUnmount() {
        if(this.statsTimer) clearInterval(this.statsTimer);
    },
	computed: {
        totalPages() {
            return Math.ceil(this.totalAccounts / this.pageSize) || 1;
        },
        filteredAccounts() {
            let res = this.accounts;
            if (this.searchAccounts) {
                const term = this.searchAccounts.toLowerCase();
                res = res.filter(a => a.email && a.email.toLowerCase().includes(term));
            }
            return res;
        },
        filteredCloud() {
            let res = this.cloudAccounts;
            if (this.searchCloud) {
                const term = this.searchCloud.toLowerCase();
                res = res.filter(a => a.credential && a.credential.toLowerCase().includes(term));
            }
            return res;
        },
        filteredMailboxes() {
            let res = this.mailboxes;
            if (this.searchMailboxes) {
                const term = this.searchMailboxes.toLowerCase();
                res = res.filter(a => a.email && a.email.toLowerCase().includes(term));
            }
            return res;
        },
        cloudTotalPages() {
            return Math.ceil(this.cloudTotal / this.cloudPageSize) || 1;
        },
        mailboxTotalPages() {
            return Math.ceil(this.totalMailboxes / this.mailboxPageSize) || 1;
        },
        availableMailDomainCount() {
            return this.mailDomainRuntimeStats.filter(item => item && item.is_available).length;
        },
        cooldownMailDomainCount() {
            return this.mailDomainRuntimeStats.filter(item => item && !item.is_available).length;
        }
    },
    methods: {
        applyTheme() {
            const nextMode = this.isDarkMode ? 'dark' : 'light';
            document.body.classList.toggle('theme-dark', this.isDarkMode);
            localStorage.setItem('ui_theme_mode', nextMode);
        },
        toggleTheme() {
            this.isDarkMode = !this.isDarkMode;
            this.applyTheme();
            this.showToast(this.isDarkMode ? '已切换为护眼模式' : '已切换为日间模式', 'info');
        },
        showToast(message, type = 'info') {
            const id = this.toastId++;
            this.toasts.push({ id, message, type });
            setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 3500);
        },

        async customConfirm(message) {
            return new Promise((resolve) => {
                this.confirmModal = { show: true, message, resolve };
            });
        },
        handleConfirm(result) {
            if (this.confirmModal.resolve) this.confirmModal.resolve(result);
            this.confirmModal.show = false;
        },
        async authFetch(url, options = {}) {
            const token = localStorage.getItem('auth_token');
            if (!options.headers) options.headers = {};
            options.headers['Authorization'] = 'Bearer ' + token;
            if (options.body && typeof options.body === 'string') {
                options.headers['Content-Type'] = 'application/json';
            }
            const res = await fetch(url, options);
            if (res.status === 401) {
                if (this.isLoggedIn && !this.isRestarting) {
                    this.logout();
                    this.showToast("登录状态过期，请重新登录！", "warning");
                }
                throw new Error("Unauthorized");
            }
            return res;
        },

        async handleLogin() {
            if(!this.loginPassword) { this.showToast("请输入密码！", "warning"); return; }
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: this.loginPassword })
                });
                const data = await res.json();
                if (data.status === 'success') {
					this.logs = [];
                    localStorage.setItem('auth_token', data.token);
                    this.isLoggedIn = true;
                    this.initApp();
                    this.showToast("登录成功，欢迎回来！", "success");
                } else { this.showToast(data.message, "error"); }
            } catch (e) { this.showToast("登录请求失败，请检查后端服务。", "error"); }
        },
        logout() {
            localStorage.removeItem('auth_token');
            this.isLoggedIn = false;
            this.loginPassword = '';
			this.logs = [];
            Object.keys(this.showPwd).forEach(k => this.showPwd[k] = false);
			if(this.evtSource) {
                this.evtSource.close();
                this.evtSource = null;
            }
            if(this.statsTimer) clearInterval(this.statsTimer);
        },
        async initApp() {
            await this.fetchConfig();
            if (this.config?.enable_mail_domain_runtime_control) {
                await this.fetchMailDomainRuntimeStats();
            } else {
                this.mailDomainRuntimeStats = [];
                this.mailDomainRuntimeStatsError = '';
                this.mailDomainRuntimeLastFetchAt = 0;
            }
            this.initSSE();
            this.fetchAccounts();
            this.fetchCloudAccounts();
            this.fetchTeamAccounts();
            this.fetchMailboxes();
            this.startStatsPolling();
            this.checkUpdate();
            this.fetchInventoryStats();
            if (this.config && this.config.reg_mode === 'extension') {
                this.listenToExtension();
            }
            if (this.currentTab === 'proxy') {
                this.fetchClashPool();
            }
        },
        startStatsPolling() {
            if(this.statsTimer) clearTimeout(this.statsTimer);
            this.pollStats();
        },
        async pollStats() {
            if(!this.isLoggedIn) return;
            try {
                const res = await this.authFetch('/api/stats');
                const data = await res.json();

                this.stats = data;

                if (this.config?.reg_mode === 'extension') {
                    data.target = this.config?.normal_mode?.target_count || 0;
                    const wasRunning = this.isRunning;
                    this.isRunning = data.is_running;
                    if (this.isRunning) {
                        this.stats.mode = '插件托管运行中...';
                        if (parseFloat(data.elapsed) <= 0) {
                            this.stats.elapsed = "0.0s";
                        }
                        if (!wasRunning && this.isExtConnected && !this._extDispatchTimer) {
                            console.log("[总控] 同步到全局运行状态，当前节点自动加入生产线...");
                            this.dispatchExtensionTask();
                        }
                    }
                    this.stats = data;
                } else {
                    this.isRunning = data.is_running;
                }

                if (
                    this.currentTab === 'email' &&
                    this.isRunning &&
                    this.config?.enable_mail_domain_runtime_control &&
                    !this.mailDomainRuntimePanelCollapsed &&
                    Date.now() - this.mailDomainRuntimeLastFetchAt >= 1000
                ) {
                    this.fetchMailDomainRuntimeStats({ silent: true });
                }

                if (this.currentTab === 'cluster') {
                    const cRes = await this.authFetch('/api/cluster/view');
                    const cData = await cRes.json();
                    if (cData.status === 'success') {
                        this.clusterNodes = cData.nodes;
                    }
                }
            } catch(e) {

            } finally {
                this.statsTimer = setTimeout(() => {
                    this.pollStats();
                }, 1000);
            }
        },
        async fetchConfig() {
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
                    }

                    if (!this.config.fivesim) {
                        this.config.fivesim = {
                            enabled: false, api_key: '', country: 'any', service: 'openai',
                            auto_pick_country: true, verify_on_register: false,reuse_phone: true,reuse_max: 2,
                            max_price: 50.0, min_price: 0.0, min_balance: 10.0, max_tries: 3, poll_timeout_sec: 180
                        };
                    } else {
                        if(this.config.fivesim.reuse_max === undefined) this.config.fivesim.reuse_max = 2;
                    }

                    if (this.config.hero_sms) {
                        this.config.hero_sms.enabled = normalizeBooleanLike(this.config.hero_sms.enabled, false);
                        if(this.config.hero_sms.reuse_max === undefined) this.config.hero_sms.reuse_max = 2;
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
                        retain_reg_only: false
                    };
                } else if (this.config.image2api_mode.retain_reg_only === undefined) {
                    this.config.image2api_mode.retain_reg_only = false;
                }
                if (!this.config.team_mode) {
                    this.config.team_mode = { enable: false };
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
                if (!this.config.temporam) {
                    this.config.temporam = { cookie: '' };
                }
                if (!this.config.reg_mode) {
                        this.config.reg_mode = 'protocol';
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
                if (!Array.isArray(this.config.mail_domain_failure_types)) this.config.mail_domain_failure_types = ['discarded_email'];
                this.config.mail_domain_failure_types = [...new Set(
                    this.config.mail_domain_failure_types
                        .map(item => String(item || '').trim().toLowerCase())
                        .filter(Boolean)
                )];
                if (this.config.mail_domain_failure_types.length === 0) this.config.mail_domain_failure_types = ['discarded_email'];
                if (this.config.mail_domain_fail_threshold === undefined) this.config.mail_domain_fail_threshold = 3;
                if (this.config.mail_domain_fail_cooldown_sec === undefined) this.config.mail_domain_fail_cooldown_sec = 600;
            } catch (e) {}
        },
        async fetchMailDomainRuntimeStats(options = {}) {
            const { silent = false } = options;
            if (!this.config?.enable_mail_domain_runtime_control) {
                this.mailDomainRuntimeStats = [];
                this.mailDomainRuntimeStatsError = '';
                this.mailDomainRuntimeLastFetchAt = 0;
                return;
            }
            try {
                const res = await this.authFetch('/api/config/mail_domain_runtime_stats');
                const data = await res.json();
                if (data.status === 'success' && Array.isArray(data.items)) {
                    this.mailDomainRuntimeStats = data.items;
                    this.mailDomainRuntimeStatsError = '';
                    this.mailDomainRuntimeLastFetchAt = Date.now();
                } else {
                    this.mailDomainRuntimeStatsError = data.message || '域名运行时状态获取失败';
                    if (!silent) {
                        this.showToast(this.mailDomainRuntimeStatsError, 'error');
                    }
                }
            } catch (e) {
                this.mailDomainRuntimeStatsError = '域名运行时状态获取失败，请检查后端接口或网络连接';
                if (!silent) {
                    this.showToast(this.mailDomainRuntimeStatsError, 'error');
                }
            }
        },
        toggleMailDomainRuntimePanel() {
            this.mailDomainRuntimePanelCollapsed = !this.mailDomainRuntimePanelCollapsed;
            localStorage.setItem('mail_domain_runtime_panel_collapsed', this.mailDomainRuntimePanelCollapsed ? 'true' : 'false');
        },
        isMailDomainRuntimePristine(item) {
            if (!item || typeof item !== 'object') return false;
            return !item.last_used_at && !item.success_count && !item.fail_count && !(item.cooldown_remaining_sec > 0);
        },
        toggleMailDomainDisabled(domain) {
            const normalized = String(domain || '').trim().toLowerCase().replace(/^\.+|\.+$/g, '');
            if (!normalized) return;
            if (!Array.isArray(this.config.disabled_mail_domains)) {
                this.config.disabled_mail_domains = [];
            }
            const next = new Set(
                this.config.disabled_mail_domains
                    .map(item => String(item || '').trim().toLowerCase().replace(/^\.+|\.+$/g, ''))
                    .filter(Boolean)
            );
            if (next.has(normalized)) {
                next.delete(normalized);
            } else {
                next.add(normalized);
            }
            this.config.disabled_mail_domains = Array.from(next);
            this.saveConfig();
        },
        async clearMailDomainRuntimeCooldowns() {
            try {
                const res = await this.authFetch('/api/config/mail_domain_runtime_stats/clear', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    this.mailDomainRuntimeStatsError = '';
                    this.showToast(data.message || '已清除全部域名冷却', 'success');
                    await this.fetchMailDomainRuntimeStats({ silent: true });
                    this.pollStats();
                } else {
                    this.showToast(data.message || '清除全部域名冷却失败', 'error');
                }
            } catch (e) {
                this.showToast('清除全部域名冷却失败，请检查网络连接', 'error');
            }
        },
        async clearMailDomainRuntimeRowCounters(domain) {
            try {
                const res = await this.authFetch('/api/config/mail_domain_runtime_stats/clear_counters', {
                    method: 'POST',
                    body: JSON.stringify({ domain })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(data.message || '已清空域名计数', 'success');
                    await this.fetchMailDomainRuntimeStats({ silent: true });
                    this.pollStats();
                } else {
                    this.showToast(data.message || '清空域名计数失败', 'error');
                }
            } catch (e) {
                this.showToast('清空域名计数失败，请检查网络连接', 'error');
            }
        },
        async clearMailDomainRuntimeRowCooldown(domain) {
            try {
                const res = await this.authFetch('/api/config/mail_domain_runtime_stats/clear_cooldown', {
                    method: 'POST',
                    body: JSON.stringify({ domain })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(data.message || '已清除域名冷却', 'success');
                    await this.fetchMailDomainRuntimeStats({ silent: true });
                    this.pollStats();
                } else {
                    this.showToast(data.message || '清除域名冷却失败', 'error');
                }
            } catch (e) {
                this.showToast('清除域名冷却失败，请检查网络连接', 'error');
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
                this.config.enable_mail_domain_runtime_control = normalizeBooleanLike(this.config.enable_mail_domain_runtime_control, false);
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
                this.config.warp_proxy_list = this.warpListStr.split('\n').map(s => s.trim()).filter(s => s);
                if (!this.config.raw_proxy_pool || typeof this.config.raw_proxy_pool !== 'object' || Array.isArray(this.config.raw_proxy_pool)) {
                    this.config.raw_proxy_pool = { enable: false, proxy_list: [] };
                }
                this.config.raw_proxy_pool.enable = normalizeBooleanLike(this.config.raw_proxy_pool.enable, false);
                this.config.raw_proxy_pool.proxy_list = this.rawProxyListStr.split('\n').map(s => s.trim()).filter(s => s);
                const res = await this.authFetch('/api/config', {
                    method: 'POST', body: JSON.stringify(this.config)
                });
                const data = await res.json();
                if(data.status === 'success') {
                    this.showToast(data.message, "success");
                    await this.fetchConfig();
                    await this.fetchMailDomainRuntimeStats();
                    this.pollStats();
                } else { this.showToast("保存失败：" + data.message, "error"); }
            } catch (e) { this.showToast("保存失败网络异常", "error"); }
        },
        filterLocalAccounts(status) {
            this.accountStatusFilter = status;
            this.currentPage = 1;
            this.fetchAccounts();

            const statusMap = {
                'all': '全部',
                'unpushed': '未推送',
                'active': '活跃',
                'disabled': '已禁用'
            };
            this.showToast(`已筛选: ${statusMap[status]}的本地账号`, 'info');
        },
		async fetchAccounts(isManual = false) {
            if (isManual) {
                this.currentPage = 1;
            }
            try {
                let url = `/api/accounts?page=${this.currentPage}&page_size=${this.pageSize}`;
                if (this.hideRegisterOnlyAccounts) {
                    url += '&hide_reg=1';
                }
                if (this.searchAccounts) {
                    url += `&search=${encodeURIComponent(this.searchAccounts)}`;
                }
                if (this.accountStatusFilter && this.accountStatusFilter !== 'all') {
                    url += `&status_filter=${this.accountStatusFilter}`;
                }
                const res = await this.authFetch(url);
                const data = await res.json();
                if(data.status === 'success') {
                    this.accounts = data.data ? data.data : data;
                    if (data.total !== undefined) {
                        this.totalAccounts = data.total;
                    } else {
                        this.totalAccounts = this.accounts.length;
                    }

                    this.selectedAccounts = [];
                    if (isManual) this.showToast("账号列表已刷新！", "success");
                }
            } catch (e) {
                console.error("获取账号列表失败:", e);
            }
        },
		changePage(newPage) {
            if (!newPage || isNaN(newPage)) newPage = 1;
            newPage = Math.max(1, Math.min(newPage, this.totalPages));
            if (this.currentPage === newPage) {
                this.$forceUpdate(); // 强制刷新非法输入的UI
                return;
            }
            this.currentPage = newPage;
            this.selectedAccounts = [];
            this.fetchAccounts(false);
        },
		changePageSize() {
            this.currentPage = 1;

            this.selectedAccounts = [];

            this.fetchAccounts(false);
        },
        switchTab(tabId) {
            if (!this.isLoggedIn) return;
            this.currentTab = tabId;
            window.location.hash = tabId;
			if (tabId === 'console') {
				this.pollStats();
			}
            if (tabId === 'accounts') {
                this.fetchAccounts();
            }
			if (tabId === 'email') {
				this.fetchConfig();
                    this.fetchMailDomainRuntimeStats();
			}
			if (tabId === 'cloud') {
			    this.fetchCloudAccounts();
			}
            if (tabId === 'cluster') {
                this.initClusterWebSocket();
            } else {
                if (this.clusterWs) this.clusterWs.close();
            }
            if (tabId === 'mailboxes') {
                this.fetchMailboxes();
            }
            if (tabId === 'proxy') {
                this.fetchClashPool();
            }
            if (tabId === 'team_accounts') {
                this.fetchTeamAccounts();
            }
        },
        async exportSelectedAccounts() {
            if (this.selectedAccounts.length === 0) {
                this.showToast("请先勾选需要导出的账号", "warning");
                return;
            }

            const emails = this.selectedAccounts.map(acc => acc.email);

            try {
                const res = await this.authFetch('/api/accounts/export_selected', {
                    method: 'POST',
                    body: JSON.stringify({ emails: emails })
                });
                const result = await res.json();

                if (result.status === 'success') {
                    const data = result.data;
                    const timestamp = Math.floor(Date.now() / 1000);
                    if (data.length > 1) {
                        const zip = new JSZip();

                        data.forEach((tokenObj, index) => {
                            const accEmail = tokenObj.email || "unknown";
                            const parts = accEmail.split('@');
                            const prefix = parts[0] || "user";
                            const domain = parts[1] || "domain";

                            const filename = `token_${prefix}_${domain}_${timestamp + index}.json`;
                            zip.file(filename, JSON.stringify(tokenObj, null, 4));
                        });

                        const content = await zip.generateAsync({ type: "blob" });
                        const url = window.URL.createObjectURL(content);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `CPA_Batch_Export_${data.length}_${timestamp}.zip`;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        window.URL.revokeObjectURL(url);

                        this.showToast(`🎉 成功打包导出 ${data.length} 个账号的压缩包！`, "success");
                    } else {
                        data.forEach((tokenObj, index) => {
                            setTimeout(() => {
                                const accEmail = tokenObj.email || "unknown";
                                const parts = accEmail.split('@');
                                const prefix = parts[0] || "user";
                                const domain = parts[1] || "domain";

                                const ts = Math.floor(Date.now() / 1000) + index;
                                const filename = `token_${prefix}_${domain}_${ts}.json`;
                                const jsonString = JSON.stringify(tokenObj, null, 4);
                                const blob = new Blob([jsonString], { type: 'application/json;charset=utf-8' });
                                const url = window.URL.createObjectURL(blob);

                                const a = document.createElement('a');
                                a.href = url;
                                a.download = filename;
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                                window.URL.revokeObjectURL(url);
                            }, index * 300);
                        });
                        this.showToast(`🎉 成功触发 ${data.length} 个独立 Token 文件的下载！`, "success");
                    }

                    this.selectedAccounts = [];
                } else {
                    this.showToast(result.message, "warning");
                }
            } catch (e) {
                console.error(e);
                this.showToast("导出请求失败，请检查网络或 JSZip 是否加载", "error");
            }
        },
		maskEmail(email) {
            if (!email) return '';
            const parts = email.split('@');
            if (parts.length !== 2) return '******';

            const name = parts[0];
            const maskedDomain = '***.***';

            if (name.length <= 3) {
                return name + '***@' + maskedDomain;
            }
            return name.substring(0, 3) + '***@' + maskedDomain;
        },
		exportAccountsToTxt() {
			if (this.selectedAccounts.length === 0) return;

			const textContent = this.selectedAccounts
				.map(acc => `${acc.email}----${acc.password}`)
				.join('\n');

			const blob = new Blob([textContent], { type: 'text/plain;charset=utf-8' });
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;

			const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
			link.download = `accounts_login_${dateStr}.txt`;

			document.body.appendChild(link);
			link.click();
			document.body.removeChild(link);
			URL.revokeObjectURL(url);

			this.showToast(`成功导出 ${this.selectedAccounts.length} 个账号到 TXT`, 'success');
		},
		async deleteSelectedAccounts() {
            if (this.selectedAccounts.length === 0) return;

            const confirmed = await this.customConfirm(`⚠️ 危险操作：\n\n确定要彻底删除选中的 ${this.selectedAccounts.length} 个账号吗？\n删除后数据将无法恢复！`);
            if (!confirmed) return;
			this.isDeletingAccounts = true;
            try {
                const emailsToDelete = this.selectedAccounts.map(acc => acc.email);

                const res = await this.authFetch('/api/accounts/delete', {
                    method: 'POST',
                    body: JSON.stringify({ emails: emailsToDelete })
                });

                const data = await res.json();

                if (data.status === 'success') {
                    this.showToast(`成功物理删除 ${emailsToDelete.length} 个账号`, 'success');
                    this.selectedAccounts = [];
                    this.fetchAccounts();
                    this.fetchInventoryStats();
                } else {
                    this.showToast('删除失败: ' + data.message, 'error');
                }
            } catch (error) {
                this.showToast('删除请求异常，请检查后端', 'error');
            } finally {
				this.isDeletingAccounts = false;
			}
        },
        toggleAll(event) {
            if (event.target.checked) this.selectedAccounts = [...this.filteredAccounts];
            else this.selectedAccounts = [];
        },
        toggleHideRegisterOnlyAccounts() {
            this.hideRegisterOnlyAccounts = !this.hideRegisterOnlyAccounts;
            this.currentPage = 1;
            this.fetchAccounts(true);
        },
		async toggleSystem() {
            if (this.isToggling) return;
            this.isToggling = true;
            try {
                if (this.isRunning) {
                    this.isRunning = false;
                    if (this._extDispatchTimer) {
                        clearTimeout(this._extDispatchTimer);
                        this._extDispatchTimer = null;
                    }
                    if (this.config?.reg_mode === 'extension') {
                        window.postMessage({ type: "CMD_STOP_WORKER" }, "*");
                        await this.authFetch('/api/ext/stop', { method: 'POST' });
                        this.showToast("已向集群发送停止指令", "info");
                    } else {
                        await this.stopTask();
                    }
                } else {
                    if (this.config?.reg_mode === 'extension') {
                        this.showToast("📡 正在探测节点在线状态...", "info");
                        try {
                            const localId = localStorage.getItem('local_worker_id') || 'Node-Pilot-01';
                            const checkRes = await this.authFetch(`/api/ext/check_node?worker_id=${localId}`);
                            const checkData = await checkRes.json();
                            if (!checkData.online) {
                                const now = new Date();
                                const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });
                                this.showToast(`🚫 启动失败：节点 [${localId}] 未连接或已掉线！`, "error");
                                this.logs.push({
                                    parsed: true,
                                    time: timeStr,
                                    level: '系统',
                                    text: '🛑 请确认是否安装了本项目plugin目录里的浏览器插件，并强制刷新该页面！',
                                    raw: `[${timeStr}] [系统] 🛑 请确认是否安装了本项目plugin目录里的浏览器插件，并强制刷新该页面！`
                                });
                                return;
                            }
                        } catch (e) {

                            this.showToast("🚫 无法连接到总控服务器检查状态", "error");
                            return;
                        }
                        this.isRunning = true;
                        this.currentTab = 'console';
                        this.showToast("✅ 节点在线！已启动【浏览器插件托管】模式", "success");
                        await this.authFetch('/api/ext/reset_stats', { method: 'POST' });
                        await this.dispatchExtensionTask();

                    } else {
                        this.isRunning = true;
                        this.currentTab = 'console';
                        this.showToast("已启动【协议】模式", "success");
                        let mode = 'normal';
                        if (this.config?.cpa_mode?.enable) mode = 'cpa';
                        if (this.config?.sub2api_mode?.enable) mode = 'sub2api';
                        await this.startTask(mode);
                    }
                }
            } finally {
                this.isToggling = false;
            }
        },
        async startTask(mode) {
            try {
                const res = await this.authFetch(`/api/start?mode=${mode}`, { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    this.isRunning = true;
                    this.currentTab = 'console';
                    this.pollStats();
                    await this.fetchMailDomainRuntimeStats();
                    this.showToast(`启动成功`, "success");
                } else { this.showToast(data.message, "error"); }
            } catch (e) { this.showToast("启动请求发送失败", "error"); }
        },
        async stopTask() {
            try {
                const res = await this.authFetch('/api/stop', { method: 'POST' });
                const data = await res.json();
                this.showToast("任务已停止", "info");
                this.isRunning = false;
                await this.fetchMailDomainRuntimeStats();
                const now = new Date();
                const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false }); // 获取如 14:30:05 格式
                this.logs.push({
                    parsed: true,
                    time: timeStr,
                    level: '系统',
                    text: '🛑 接收到紧急停止指令，引擎已停止运行！',
                    raw: `[${timeStr}] [系统] 🛑 接收到紧急停止指令，引擎已停止运行！`
                });

                this.$nextTick(() => {
                    const container = document.getElementById('terminal-container');
                    if (container) {
                        container.scrollTop = container.scrollHeight;
                    }
                });
                this.pollStats();
            } catch (e) {
                this.showToast("停止请求发送失败", "error");
            }
        },
        async bulkPushCPA() {
            if (!this.config.cpa_mode.enable) {
              this.showToast("🚫 请先开启 CPA 巡检并填写 API", "warning"); return;
            }
            if (this.selectedAccounts.length === 0) return;
            const targetAccounts = this.selectedAccounts.filter(acc => !acc.push_platform || !acc.push_platform.toUpperCase().includes('CPA'));

            if (targetAccounts.length === 0) {
                this.showToast("⚠️ 选中的账号都已推送过 CPA，无需重复推送！", "warning");
                return;
            }
            const skippedCount = this.selectedAccounts.length - targetAccounts.length;
            const extraMsg = skippedCount > 0 ? `\n(已自动帮您过滤跳过 ${skippedCount} 个重复账号)` : '';

            const confirmed = await this.customConfirm(`确定将 ${targetAccounts.length} 个新账号推送到 CPA？${extraMsg}`);
            if (!confirmed) return;

            this.currentTab = 'console';
            const emailList = targetAccounts.map(acc => acc.email);

            try {
              const res = await this.authFetch('/api/account/action', {
                  method: 'POST',
                  body: JSON.stringify({ emails: emailList, action: 'push' })
              });
                const result = await res.json();
                this.showToast(result.message, result.status);
            } catch (e) {
                this.showToast("批量推送请求异常", "error");
            } finally {
                this.selectedAccounts = [];
                if (typeof this.fetchAccounts === 'function') this.fetchAccounts();
                if (typeof this.fetchInventoryStats === 'function') this.fetchInventoryStats();
            }
        },
        async bulkPushSub2API() {
            if (!this.config.sub2api_mode.enable) {
                this.showToast("🚫 请先开启 Sub2API 模式并填写参数", "warning"); return;
            }
            if (this.selectedAccounts.length === 0) return;
            const targetAccounts = this.selectedAccounts.filter(acc => !acc.push_platform || !acc.push_platform.toUpperCase().includes('SUB2API'));

            if (targetAccounts.length === 0) {
                this.showToast("⚠️ 选中的账号都已推送过 Sub2API，无需重复推送！", "warning");
                return;
            }

            const skippedCount = this.selectedAccounts.length - targetAccounts.length;
            const extraMsg = skippedCount > 0 ? `\n(已自动帮您过滤跳过 ${skippedCount} 个重复账号)` : '';

            const confirmed = await this.customConfirm(`确定将 ${targetAccounts.length} 个新账号推送到 Sub2API？${extraMsg}`);
            if (!confirmed) return;

            this.currentTab = 'console';
            const emailList = targetAccounts.map(acc => acc.email);

            try {
                const res = await this.authFetch('/api/account/action', {
                    method: 'POST',
                    body: JSON.stringify({ emails: emailList, action: 'push_sub2api' })
                });
                const result = await res.json();
                this.showToast(result.message, result.status);
            } catch (e) {
                this.showToast("批量推送请求异常", "error");
            } finally {
                this.selectedAccounts = [];
                if (typeof this.fetchAccounts === 'function') this.fetchAccounts();
                if (typeof this.fetchInventoryStats === 'function') this.fetchInventoryStats();
            }
        },
        async bulkPushImage2API() {
            if (!this.config.image2api_mode || !this.config.image2api_mode.enable) {
                this.showToast("🚫 请先开启 Image2API 模式并填写参数", "warning"); return;
            }
            if (this.selectedAccounts.length === 0) return;
            const targetAccounts = this.selectedAccounts.filter(acc => !acc.push_platform || !acc.push_platform.toUpperCase().includes('IMAGE2API'));

            if (targetAccounts.length === 0) {
                this.showToast("⚠️ 选中的账号都已推送过 Image2API，无需重复推送！", "warning");
                return;
            }

            const skippedCount = this.selectedAccounts.length - targetAccounts.length;
            const extraMsg = skippedCount > 0 ? `\n(已自动帮您过滤跳过 ${skippedCount} 个重复账号)` : '';

            const confirmed = await this.customConfirm(`确定将 ${targetAccounts.length} 个新账号推送到 Image2API？${extraMsg}`);
            if (!confirmed) return;

            this.currentTab = 'console';
            const emailList = targetAccounts.map(acc => acc.email);

            try {
                const res = await this.authFetch('/api/account/action', {
                    method: 'POST',
                    body: JSON.stringify({ emails: emailList, action: 'push_image2api' })
                });
                const result = await res.json();
                this.showToast(result.message, result.status);
            } catch (e) {
                this.showToast("批量推送请求异常", "error");
            } finally {
                this.selectedAccounts = [];
                if (typeof this.fetchAccounts === 'function') this.fetchAccounts();
                if (typeof this.fetchInventoryStats === 'function') this.fetchInventoryStats();
            }
        },
        async triggerAccountAction(account, action) {
            if (action === 'push') {
                if (!this.config.cpa_mode.enable) {
                    this.showToast("🚫 无法推送：请先配置 CPA 参数！", "warning"); return;
                }
                if (account.push_platform && account.push_platform.toUpperCase().includes('CPA')) {
                    this.showToast("⚠️ 该账号已在 CPA 平台，无需重复推送！", "warning"); return;
                }
            }
            if (action === 'push_sub2api') {
                if (!this.config.sub2api_mode.enable) {
                    this.showToast("🚫 无法推送：请先配置 Sub2API 参数！", "warning"); return;
                }
                if (account.push_platform && account.push_platform.toUpperCase().includes('SUB2API')) {
                    this.showToast("⚠️ 该账号已在 Sub2API 平台，无需重复推送！", "warning"); return;
                }
            }
            if (action === 'push_image2api') {
                if (!this.config.image2api_mode || !this.config.image2api_mode.enable) {
                    this.showToast("🚫 无法推送：请先配置 Image2API 参数！", "warning"); return;
                }
                if (account.push_platform && account.push_platform.toUpperCase().includes('IMAGE2API')) {
                    this.showToast("⚠️ 该账号已在 Image2API 平台，无需重复推送！", "warning"); return;
                }
            }
            this.currentTab = 'console';
            try {
                const res = await this.authFetch('/api/account/action', {
                    method: 'POST', body: JSON.stringify({ email: account.email, action: action })
                });
                const result = await res.json();
                this.showToast(result.message, result.status);
                if (action === 'push' || action === 'push_sub2api' || action === 'push_image2api') {
                    if (typeof this.fetchAccounts === 'function') this.fetchAccounts();
                    if (typeof this.fetchInventoryStats === 'function') this.fetchInventoryStats();
                }
            } catch (e) {
                this.showToast("请求异常", "error");
            }
        },
        async fetchInventoryStats() {
            try {
                const res = await this.authFetch('/api/accounts/stats');
                const json = await res.json();
                if (json.status === 'success') {
                    this.inventoryStats.local = json.data.local;
                } else {
                    console.error("获取统计数据失败:", json.message);
                }
            } catch (e) {
                console.error('获取统计面板异常', e);
            }
        },
        async clearLogs() {
            this.logs = [];
            try { await this.authFetch('/api/logs/clear', { method: 'POST' }); } catch (e) {}
        },
		initSSE() {
            if (this.evtSource) {
                this.evtSource.close();
                this.evtSource = null;
            }
            if (this.logFlushTimer) {
                clearInterval(this.logFlushTimer);
                this.logFlushTimer = null;
            }
            if (this.sseReconnectTimer) {
                clearTimeout(this.sseReconnectTimer);
                this.sseReconnectTimer = null;
            }

            const token = localStorage.getItem('auth_token');
            if (!token) return;
            const timestamp = new Date().getTime();
            const url = `/api/logs/stream?token=${token}&_t=${timestamp}`;

            this.evtSource = new EventSource(url);
            this.logFlushTimer = setInterval(() => {
                if (this.logBuffer.length > 0) {
                    const container = document.getElementById('terminal-container');
                    let isScrolledToBottom = true;
                    if (container) {
                        isScrolledToBottom = container.scrollHeight - container.clientHeight <= container.scrollTop + 100;
                    }
                    this.logs.push(...this.logBuffer);
                    this.logBuffer = [];
                    const maxLines = (this.config && this.config.max_log_lines) ? this.config.max_log_lines : 500;
                    if (this.logs.length > maxLines) {
                        this.logs.splice(0, this.logs.length - maxLines);
                    }
                    this.$nextTick(() => {
                        if (container && (isScrolledToBottom || this.logs.length < 20)) {
                            container.scrollTo({
                                top: container.scrollHeight,
                                behavior: 'auto'
                            });
                        }
                    });
                }
            }, 300);

            this.evtSource.onmessage = (event) => {
                let rawText = event.data;
                rawText = rawText.trim();
                if (!rawText) return;

                let logObj = { id: Date.now() + Math.random(), parsed: false, raw: rawText };
                const regex = /^\[(.*?)\]\s*\[(.*?)\]\s+(.*)$/;
                const match = rawText.match(regex);

                if (match) {
                    logObj = {
                        parsed: true,
                        time: match[1],
                        level: match[2].toUpperCase(),
                        text: match[3],
                        raw: rawText
                    };
                }
                this.logBuffer.push(logObj);
            };
            this.evtSource.onerror = (event) => {
                console.error("🔴 SSE 连接断开或异常。");
                if (this.evtSource) {
                    this.evtSource.close();
                    this.evtSource = null;
                }

                if (this.isLoggedIn) {
                    console.log("⏳ 准备在 3 秒后强制重新建立日志通道...");
                    this.sseReconnectTimer = setTimeout(() => {
                        this.initSSE();
                    }, 3000);
                }
            };
        },
		handleSubDomainToggle() {
			if (this.config.enable_sub_domains) {
				this.subDomainModal.email = this.config.cf_api_email || '';
				this.subDomainModal.key = this.config.cf_api_key || '';
				this.subDomainModal.show = true;
			}
		},
		// async executeGenerateDomainsOnly() {
			// if (!this.config.mail_domains) return this.showToast('请先填写上方的主发信域名池！', 'warning');

			// const level = this.config.sub_domain_level || 1;

			// try {
				// const res = await this.authFetch('/api/config/generate_subdomains', {
					// method: 'POST',
					// body: JSON.stringify({
						// main_domains: this.config.mail_domains,
						// count: this.config.sub_domain_count || 10,
						// level: level,
						// api_email: this.config.cf_api_email || '',
						// api_key: this.config.cf_api_key || '',
						// sync: false
					// })
				// });
				// const data = await res.json();
				// if (data.status === 'success') {
					// this.config.sub_domains_list = data.domains;
					// this.showToast('生成成功！如需推送到 CF，请点击右侧推送按钮。', 'success');
				// } else {
					// this.showToast(data.message, 'error');
				// }
			// } catch (e) {
				// this.showToast('生成接口请求失败', 'error');
			// }
		// },

		async executeSyncToCF() {
			const rawList = this.config.mail_domains || '';
			const subDomains = rawList.split(',').map(d => d.trim()).filter(d => d);

			if (subDomains.length === 0) return this.showToast('当前没有可解析的主域，请先填写！', 'warning');
			if (!this.config.cf_api_email || !this.config.cf_api_key) return this.showToast('请填写 CF 账号邮箱和 API Key！', 'warning');
			const confirmed = await this.customConfirm(`把 ${subDomains.length} 个主域名解析到 Cloudflare，确定继续吗？`);
			if (!confirmed) return;
			this.isLoadingSync = true;
			this.showToast('🚀 多线程同步中，请耐心等待...', 'info');
            this.currentTab = 'console';
			try {
				const res = await this.authFetch('/api/config/add_wildcard_dns', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({
						sub_domains: subDomains.join(','),
						api_email: this.config.cf_api_email,
						api_key: this.config.cf_api_key
					})
				});

				const data = await res.json();
				if (data.status === 'success') {
					this.showToast('✅ 解析成功...', 'success');
				} else {
					this.showToast(data.message || '解析失败', 'error');
				}
			} catch (e) {
				this.showToast('解析接口请求异常', 'error');
			} finally {
				this.isLoadingSync = false;
			}
		},
		// async checkCfGlobalStatus() {
			// if (!this.config.mail_domains) return;
			// const domains = this.config.mail_domains;
			// try {
				// const res = await this.authFetch(`/api/config/cf_global_status?main_domain=${encodeURIComponent(domains)}`);
				// const data = await res.json();
				// if (data.status === 'success') {
					// this.cfGlobalStatusList = data.data;
					// const allEnabled = data.data.length > 0 && data.data.every(item => item.is_enabled);
					// if (allEnabled && this.cfStatusTimer) {
						// this.stopCfStatusPolling();
						// this.showToast('✨ 线上状态已全部激活！', 'success');
					// }
				// }
			// } catch (e) {
				// this.showToast("无法获取 CF 路由全局状态", e);
			// }
		// },
		// async startCfStatusPolling() {
			// this.stopCfStatusPolling();
			// this.isLoadingCfRoutes = true;

			// this.showToast("🚀 开启 CF 状态智能监控...");

			// this.cfStatusTimer = setInterval(() => {
				// this.checkCfGlobalStatus();
			// }, 8000);
			// await this.fetchCfRoutes();
		// },
		// stopCfStatusPolling() {
			// if (this.cfStatusTimer) {
				// clearInterval(this.cfStatusTimer);
				// this.cfStatusTimer = null;
				// this.isLoadingCfRoutes = false;
				// this.showToast("🛑 智能监控已停止。");
			// }
		// },
		// async fetchCfRoutes() {
			// if (!this.config.mail_domains) return this.showToast('请先填写主发信域名池 (用于反推Zone ID)！', 'warning');
			// if (!this.config.cf_api_email || !this.config.cf_api_key) return this.showToast('请填写 CF 账号邮箱和 API Key！', 'warning');

			// this.isLoadingCfRoutes = true;
			// this.showToast('🔍 正在连线 Cloudflare 查询线上路由记录...', 'info');

			// try {
				// const res = await this.authFetch('/api/config/query_cf_domains', {
					// method: 'POST',
					// body: JSON.stringify({
						// main_domains: this.config.mail_domains,
						// api_email: this.config.cf_api_email,
						// api_key: this.config.cf_api_key
					// })
				// });
				// const data = await res.json();
				// if (data.status === 'success') {
					// if (data.domains) {
						// this.cfRoutes = data.domains.split(',').filter(d=>d).map(d => ({
							// domain: d,
							// loading: false
						// }));
					// } else {
						// this.cfRoutes = [];
					// }
					// this.selectedCfRoutes = [];
					// this.showToast(data.message, 'success');
				// } else {
					// this.showToast(data.message, 'error');
				// }
				// await this.checkCfGlobalStatus();
			// } catch (e) {
				// this.showToast('查询接口请求失败', 'error');
			// } finally {
				// if (!this.cfStatusTimer) {
					// this.isLoadingCfRoutes = false;
				// }
			// }
		// },

		// async deleteSelectedCfRoutes() {
			// if (this.selectedCfRoutes.length === 0) return;
			// const domainsToDelete = this.selectedCfRoutes.map(item => item.domain);

			// this.isDeletingCfRoutes = true;
			// try {
				// await this.executeDeleteCfDomains(domainsToDelete);
			// } finally {
				// this.isDeletingCfRoutes = false;
			// }
		// },

		// async deleteSingleCfRoute(routeObj) {
			// routeObj.loading = true;
			// try {
				// await this.executeDeleteCfDomains([routeObj.domain]);
			// } finally {
				// routeObj.loading = false;
			// }
		// },

		// async executeDeleteCfDomains(domainsArray) {
			// if (!this.config.cf_api_email || !this.config.cf_api_key) return this.showToast('请填写 CF 账号邮箱和 API Key！', 'warning');

			// const count = domainsArray.length;
			// const confirmed = await this.customConfirm(`⚠️ 危险操作：\n\n即将调用 Cloudflare API 强制删除这 ${count} 个域名的路由解析记录。确定要继续吗？`);
			// if (!confirmed) return;
			// if (count > 1) this.isDeletingCfRoutes = true;
			// this.showToast(`🗑️ 正在连线 Cloudflare 销毁 ${count} 条记录...`, 'info');

			// try {
				// const res = await this.authFetch('/api/config/delete_cf_domains', {
					// method: 'POST',
					// body: JSON.stringify({
						// sub_domains: domainsArray.join(','),
						// api_email: this.config.cf_api_email,
						// api_key: this.config.cf_api_key
					// })
				// });
				// const data = await res.json();
				// if (data.status === 'success') {
					// this.showToast(data.message, 'success');
					// this.fetchCfRoutes();
				// } else {
					// this.showToast(data.message, 'error');
				// }
			// } catch (e) {
				// this.showToast('删除接口请求失败', 'error');
			// } finally {
				// this.isDeletingCfRoutes = false;
			// }
		// },

		toggleAllCfRoutes(event) {
			if (event.target.checked) this.selectedCfRoutes = [...this.cfRoutes];
			else this.selectedCfRoutes = [];
		},
        async fetchHeroSmsBalance() {
            if (!this.config.hero_sms.api_key) return this.showToast('请先填写 API Key！', 'warning');
            this.isLoadingBalance = true;
            try {
                const res = await this.authFetch('/api/sms/balance');
                const data = await res.json();
                if (data.status === 'success') {
                    this.heroSmsBalance = data.balance;
                    this.showToast('余额刷新成功', 'success');
                } else {
                    this.showToast(data.message || '查询失败', 'error');
                }
            } catch (e) {
                this.showToast('查询异常: ' + e.message, 'error');
            } finally {
                this.isLoadingBalance = false;
            }
        },
        async fetchHeroSmsPrices() {
            if (!this.config.hero_sms.api_key) return this.showToast('请先填写 API Key！', 'warning');
            this.isLoadingPrices = true;
            try {
                const res = await this.authFetch('/api/sms/prices', {
                    method: 'POST',
                    body: JSON.stringify({ service: this.config.hero_sms.service })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.heroSmsPrices = data.prices;
                    this.showToast(`获取到 ${data.prices.length} 个国家的库存数据`, 'success');
                } else {
                    this.showToast(data.message || '获取失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常: ' + e.message, 'error');
            } finally {
                this.isLoadingPrices = false;
            }
        },
        async fetchSmsBowerBalance() {
            if (!this.config.smsbower.api_key) return this.showToast('请先填写 SmsBower API Key！', 'warning');
            this.isLoadingSmsBowerBalance = true;
            try {
                const res = await this.authFetch('/api/smsbower/balance');
                const data = await res.json();
                if (data.status === 'success') {
                    this.smsBowerBalance = data.balance;
                    this.showToast('余额刷新成功', 'success');
                } else {
                    this.showToast(data.message || '查询失败', 'error');
                }
            } catch (e) {
                this.showToast('查询异常: ' + e.message, 'error');
            } finally {
                this.isLoadingSmsBowerBalance = false;
            }
        },
        async fetchSmsBowerPrices() {
            if (!this.config.smsbower.api_key) return this.showToast('请先填写 API Key！', 'warning');
            this.isLoadingSmsBowerPrices = true;
            try {
                const res = await this.authFetch('/api/smsbower/prices', {
                    method: 'POST',
                    body: JSON.stringify({ service: this.config.smsbower.service })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.smsBowerPrices = data.prices;
                    this.showToast(`获取到 ${data.prices.length} 个国家的库存数据`, 'success');
                } else {
                    this.showToast(data.message || '获取失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常: ' + e.message, 'error');
            } finally {
                this.isLoadingSmsBowerPrices = false;
            }
        },
        async fetchFivesimBalance() {
            if (!this.config.fivesim.api_key) return this.showToast('请先填写 5SIM API Key！', 'warning');
            this.isLoadingFivesimBalance = true;
            try {
                const res = await this.authFetch('/api/fivesim/balance');
                const data = await res.json();
                if (data.status === 'success') {
                    this.fivesimBalance = data.balance;
                    this.showToast('5SIM 余额刷新成功', 'success');
                } else {
                    this.showToast(data.message || '查询失败', 'error');
                }
            } catch (e) {
                this.showToast('查询异常: ' + e.message, 'error');
            } finally {
                this.isLoadingFivesimBalance = false;
            }
        },

        async fetchFivesimPrices() {
            if (!this.config.fivesim.api_key) return this.showToast('请先填写 5SIM API Key！', 'warning');
            this.isLoadingFivesimPrices = true;
            try {
                const res = await this.authFetch('/api/fivesim/prices', {
                    method: 'POST',
                    body: JSON.stringify({ service: this.config.fivesim.service })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.fivesimPrices = data.prices || [];
                    this.showToast(`获取到 ${data.prices.length} 个国家的库存数据`, 'success');
                } else {
                    this.showToast(data.message || '拉取失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常: ' + e.message, 'error');
            } finally {
                this.isLoadingFivesimPrices = false;
            }
        },
        async executeManualLuckMailBuy() {
            if (this.luckmailManualQty < 1) return;
            this.isManualBuying = true;
            try {
                const res = await this.authFetch('/api/luckmail/bulk_buy', {
                    method: 'POST',
                    body: JSON.stringify({
                        quantity: this.luckmailManualQty,
                        auto_tag: this.luckmailManualAutoTag,
                        config: this.config.luckmail
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast('购买失败: ' + data.message, 'error');
                }
            } catch (e) {
                this.showToast('网络请求异常', 'error');
            } finally {
                this.isManualBuying = false;
            }
        },
        async fetchSub2ApiGroups() {
            if (!this.config || !this.config.sub2api_mode) return;
            if (!this.config.sub2api_mode.api_url || !this.config.sub2api_mode.api_key) {
                this.showToast('Please save the Sub2API URL and API key first.', 'warning');
                return;
            }

            this.isLoadingSub2APIGroups = true;
            try {
                const res = await this.authFetch('/api/sub2api/groups');
                const data = await res.json();
                if (data.status === 'success') {
                    const raw = data.data;
                    let groups = [];
                    if (Array.isArray(raw)) groups = raw;
                    else if (raw && Array.isArray(raw.list)) groups = raw.list;
                    else if (raw && Array.isArray(raw.data)) groups = raw.data;

                    this.sub2apiGroups = groups;
                    if (groups.length === 0) {
                        this.showToast('No Sub2API groups found. Create one in Sub2API first.', 'warning');
                    } else {
                        this.showToast(`Fetched ${groups.length} Sub2API groups.`, 'success');
                    }
                } else {
                    this.showToast(data.message || 'Failed to fetch Sub2API groups.', 'error');
                }
            } catch (e) {
                this.showToast('Group fetch error: ' + e.message, 'error');
            } finally {
                this.isLoadingSub2APIGroups = false;
            }
        },
        isGroupSelected(id) {
            if (!this.config || !this.config.sub2api_mode) return false;
            const ids = String(this.config.sub2api_mode.account_group_ids || '')
                .split(',')
                .map(s => s.trim())
                .filter(s => s);
            return ids.includes(String(id));
        },
        toggleGroup(id) {
            if (!this.config || !this.config.sub2api_mode) return;
            const ids = String(this.config.sub2api_mode.account_group_ids || '')
                .split(',')
                .map(s => s.trim())
                .filter(s => s);
            const value = String(id);
            const index = ids.indexOf(value);
            if (index >= 0) ids.splice(index, 1);
            else ids.push(value);
            this.config.sub2api_mode.account_group_ids = ids.join(',');
        },
        async startManualCheck() {
            if(this.isRunning) {
                this.showToast('请先停止当前运行的任务', 'warning');
                return;
            }
            this.currentTab = 'console';
            try {
                const res = await this.authFetch('/api/start_check', {
                    method: 'POST'
                });
                const data = await res.json();

                if(data.code === 200) {
                    this.showToast(data.message, 'success');
                    this.pollStats();
                } else {
                    this.showToast(data.message || '启动测活失败', 'error');
                }
            } catch (err) {
                this.showToast('网络请求异常', 'error');
            }
        },
        async checkUpdate(isManual = false) {
            try {
                const res = await this.authFetch(`/api/system/check_update?current_version=${this.appVersion}`);
                const data = await res.json();

                if (data.status === 'success') {
                    if (data.has_update) {
                        this.updateInfo = {
                            hasUpdate: true,
                            version: data.remote_version,
                            url: data.html_url || data.download_url || 'https://github.com/wenfxl/openai-cpa/releases/latest',
                            changelog: data.changelog
                        };
                        if (isManual) {
                            this.promptUpdate();
                        }
                    } else if (isManual) {
                        this.showToast("当前已是最新版本！", "success");
                    }
                } else {
                    if (isManual) this.showToast(data.message || "检查更新失败", "error");
                }
            } catch (e) {
                if (isManual) this.showToast("检查更新请求失败，请检查网络", "error");
            }
        },
        async promptUpdate() {
            if (!this.updateInfo.hasUpdate) return;
            const msg = `🚀 发现新版本: ${this.updateInfo.version}\n\n📝 更新内容:\n${this.updateInfo.changelog}\n\n是否立即执行一键更新？\n(系统将自动识别 Docker/本地环境，更新期间请勿关闭页面)`;
            const confirmed = await this.customConfirm(msg);
            if (confirmed) {
                this.executeAutoUpdate();
            }
        },
        async executeAutoUpdate() {
            this.isUpdatingSystem = true;
            this.showToast("🚀 正在下发更新指令，请耐心等待...", "info");
            try {
                const res = await this.authFetch('/api/system/auto_update', { method: 'POST' });
                const data = await res.json();

                if (data.status === 'success') {
                    this.showToast(`✅ ${data.message}`, "success");
                    if(this.statsTimer) clearInterval(this.statsTimer);
                    if(this.evtSource) this.evtSource.close();

                    this.showToast("⏳ 网页将在 20 秒后自动刷新...", "info");
                    setTimeout(() => {
                        window.location.reload();
                    }, 20000);

                } else if (data.status === 'warning') {
                    this.showToast(`⚠️ ${data.message}`, "warning");
                    this.isUpdatingSystem = false;
                } else {
                    this.showToast(`❌ 更新失败: ${data.message}`, "error");
                    this.isUpdatingSystem = false;
                }
            } catch (e) {
                this.showToast("更新指令已发送，由于后端重启，连接已断开，请稍后手动刷新。", "warning");
                setTimeout(() => { window.location.reload(); }, 20000);
            }
        },
        async getGmailAuthUrl() {
            this.gmailOAuth.isGenerating = true;
            try {
                const res = await this.authFetch('/api/gmail/auth_url');
                const data = await res.json();
                if (data.status === 'success') {
                    this.gmailOAuth.authUrl = data.url;
                    this.showToast("授权链接已生成，请在浏览器中打开", "success");
                } else {
                    this.showToast(data.message, "error");
                }
            } catch (e) {
                this.showToast("获取失败，请检查 credentials.json 是否已放置", "error");
            } finally {
                this.gmailOAuth.isGenerating = false;
            }
        },
        async submitGmailAuthCode() {
            this.gmailOAuth.isLoading = true;
            try {
                const res = await this.authFetch('/api/gmail/exchange_code', {
                    method: 'POST',
                    body: JSON.stringify({ code: this.gmailOAuth.pastedCode })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast("🎉 永久授权成功！系统已自动关联该 Gmail", "success");
                    this.gmailOAuth.authUrl = '';
                    this.gmailOAuth.pastedCode = '';
                } else {
                    this.showToast(data.message, "error");
                }
            } catch (e) {
                this.showToast("网络请求异常", "error");
            } finally {
                this.gmailOAuth.isLoading = false;
            }
        },
        async restartSystem() {
            const confirmed = await this.customConfirm("⚠️ 危险操作：\n\n确定要重启整个后端系统吗？\n如果当前有任务正在运行，将会被强制中断！");
            if (!confirmed) return;

            try {
                this.showToast("🚀 正在向服务器发送重启指令...", "info");
                this.isRestarting = true;
                const res = await this.authFetch('/api/system/restart', { method: 'POST' });
                const data = await res.json();

                if (data.status === 'success') {
                    this.showToast("✅ 系统正在重启，网页将于 6 秒后自动刷新...", "success");
                    if(this.statsTimer) clearInterval(this.statsTimer);
                    if(this.evtSource) this.evtSource.close();
                    if(this.cfStatusTimer) clearInterval(this.cfStatusTimer);

                    setTimeout(() => {
                        window.location.reload();
                    }, 6000);
                } else {
                    this.isRestarting = false;
                    this.showToast(data.message || "重启指令发送失败", "error");
                }
            } catch (e) {
                this.isRestarting = false;
                this.showToast("请求异常，请检查后端状态", "error");
            }
        },
        formatTime(dateStr) {
            if (!dateStr) return '-';
            let utcStr = dateStr;
            if (typeof dateStr === 'string' && !dateStr.includes('Z')) {
                utcStr = dateStr.replace(' ', 'T') + 'Z';
            }
            const d = new Date(utcStr);
            if (isNaN(d.getTime())) return dateStr;
            const pad = (n) => n.toString().padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        },
        async exportSub2Api() {
            if (this.selectedAccounts.length === 0) {
                this.showToast('请先勾选账号', 'warning');
                return;
            }
            try {
                const emailsToExport = this.selectedAccounts.map(item =>
                    typeof item === 'object' ? item.email : item
                );

                const response = await this.authFetch('/api/accounts/export_sub2api', {
                    method: 'POST',
                    body: JSON.stringify({ emails: emailsToExport })
                });
                const res = await response.json();

                if (res.status === 'success') {
                    const accounts = res.data.accounts;
                    const timestamp = Math.floor(Date.now() / 1000);

                    if (accounts.length > 1) {
                        const zip = new JSZip();

                        accounts.forEach((acc, index) => {
                            const prefix = (acc.name || "user").split('@')[0];

                            const singleAccountData = {
                                exported_at: res.data.exported_at,
                                proxies: res.data.proxies,
                                accounts: [acc]
                            };

                            const filename = `sub2api_${prefix}_${timestamp + index}.json`;
                            zip.file(filename, JSON.stringify(singleAccountData, null, 2));
                        });

                        const content = await zip.generateAsync({ type: "blob" });
                        const url = window.URL.createObjectURL(content);
                        const link = document.createElement('a');
                        link.href = url;
                        link.download = `Sub2Api_批量导出_${accounts.length}个_${timestamp}.zip`;
                        document.body.appendChild(link);
                        link.click();
                        link.remove();
                        window.URL.revokeObjectURL(url);

                        this.showToast(`🎉 成功打包并下载 ${accounts.length} 个独立配置文件！`, 'success');
                    } else {
                        const content = JSON.stringify(res.data, null, 2);
                        const blob = new Blob([content], { type: 'application/json' });
                        const url = window.URL.createObjectURL(blob);
                        const link = document.createElement('a');
                        link.href = url;
                        link.download = `sub2api_export_${timestamp}.json`;
                        document.body.appendChild(link);
                        link.click();
                        link.remove();
                        window.URL.revokeObjectURL(url);

                        this.showToast(`成功导出 ${accounts.length} 个账号到单个文件`, 'success');
                    }

                    this.selectedAccounts = [];
                } else {
                    this.showToast(res.message || '导出失败', 'error');
                }
            } catch (error) {
                console.error('导出异常:', error);
                this.showToast('导出异常，请检查 JSZip 是否加载', 'error');
            }
        },

        async fetchInventoryStats() {
            try {
                const res = await this.authFetch('/api/accounts/stats');
                const json = await res.json();
                if (json.status === 'success') {
                    this.inventoryStats.local = json.data.local;
                } else {
                    console.error("获取统计数据失败:", json.message);
                }
            } catch (e) {
                console.error('获取统计面板异常', e);
            }
        },

        async fetchCloudAccounts() {
            if (this.cloudFilters.length === 0) {
                this.cloudAccounts = [];
                this.cloudTotal = 0;
                this.inventoryStats.cloud = {
                    total: 0, enabled: 0, cpa: 0, cpa_active: 0, cpa_disabled: 0, sub2api: 0, sub2api_active: 0, sub2api_disabled: 0, image2api: 0, image2api_active: 0, image2api_disabled: 0
                };
                return;
            }
            const types = this.cloudFilters.join(',');
            let url = `/api/cloud/accounts?types=${types}&status_filter=${this.cloudStatusFilter}&page=${this.cloudPage}&page_size=${this.cloudPageSize}`;

            if (this.searchCloud) {
                url += `&search=${encodeURIComponent(this.searchCloud)}`;
            }

            try {
                const res = await this.authFetch(url);
                const data = await res.json();

                if(data.status === 'success') {
                    this.cloudAccounts = (data.data || []).map(acc => ({
                        ...acc,
                        last_check: this.localCheckTimes[acc.id] || acc.last_check || '-',
                        details: acc.account_type === 'image2api' ? (acc.details || {}) : (this.localCloudDetails[acc.id] || acc.details || {}),
                        _loading: null
                    }));
                    this.cloudTotal = data.total || 0;
                    this.selectedCloud = [];

                    if (data.cloud_stats) {
                        this.inventoryStats.cloud = data.cloud_stats;
                    }

                    if (typeof this.fetchInventoryStats === 'function') {
                        this.fetchInventoryStats();
                    }
                } else {
                    this.showToast(data.message, "error");
                    this.inventoryStats.cloud = {
                        total: 0, enabled: 0, cpa: 0, cpa_active: 0, cpa_disabled: 0, sub2api: 0, sub2api_active: 0, sub2api_disabled: 0, image2api: 0, image2api_active: 0, image2api_disabled: 0
                    };
                    this.cloudAccounts = [];
                    this.cloudTotal = 0;
                }
            } catch (e) {
                console.error(e);
                if (this.isLoggedIn && e.message !== "Unauthorized") {
                    this.showToast("获取云端数据失败", "error");
                    this.inventoryStats.cloud = {
                        total: 0, enabled: 0, cpa: 0, cpa_active: 0, cpa_disabled: 0, sub2api: 0, sub2api_active: 0, sub2api_disabled: 0, image2api: 0, image2api_active: 0, image2api_disabled: 0
                    };
                    this.cloudAccounts = [];
                    this.cloudTotal = 0;
                }
            }
        },

        async singleCloudAction(acc, action) {
            if (action === 'delete' && !confirm('⚠️ 危险操作：确认在远端彻底删除该账号吗？')) return;

            const actionName = action === 'check' ? '测活' : (action === 'enable' ? '启用' : (action === 'disable' ? '禁用' : (action === 'refresh' ? '刷新凭证' : '删除')));
            this.showToast(`正在对账号进行 ${actionName}，请稍候...`, 'info');
            acc._loading = action;

            try {
                const res = await this.authFetch('/api/cloud/action', {
                    method: 'POST',
                    body: JSON.stringify({ accounts: [{id: String(acc.id), type: acc.account_type}], action: action })
                });
                const result = await res.json();
                if (result.updated_details && result.updated_details[acc.id]) {
                    acc.details = Object.assign({}, acc.details, result.updated_details[acc.id]);
                    this.localCloudDetails[acc.id] = acc.details;
                }
                if (action === 'enable' && result.status !== 'error') acc.status = 'active';
                if (action === 'disable' && result.status !== 'error') acc.status = 'disabled';

                if (action === 'check') {
                    this.currentTab = 'console';
                    const now = new Date().toLocaleString('zh-CN', { hour12: false });
                    this.localCheckTimes[acc.id] = now;
                    acc.last_check = now;

                    if (result.status === 'warning' || result.status === 'error') {
                        acc.status = 'disabled';
                    } else {
                        acc.status = 'active';
                    }
                }
                this.showToast(result.message, result.status);

                setTimeout(() => {
                    if (['delete', 'check', 'enable', 'disable', 'refresh'].includes(action)) {
                        this.fetchCloudAccounts();
                    }
                }, 1500);

            } catch (e) {
                this.showToast("操作异常，请检查网络", "error");
            } finally {
                acc._loading = null;
            }
        },
        filterByCard(platformType, status) {
            if (platformType === 'all') {
                this.cloudFilters = ['sub2api', 'cpa'];
            } else if (platformType === 'cpa') {
                this.cloudFilters = ['cpa'];
            } else if (platformType === 'sub2api') {
                this.cloudFilters = ['sub2api'];
            }else if (platformType === 'image2api') {
                this.cloudFilters = ['image2api']
            }
            this.cloudStatusFilter = status || 'all';
            this.cloudPage = 1;
            this.fetchCloudAccounts();
            const typeName = platformType === 'all' ? '全部平台' : (platformType === 'cpa' ? 'CPA' : (platformType === 'sub2api' ? 'Sub2API' : 'Image2API'));
            const statusName = status === 'active' ? '存活' : (status === 'disabled' ? '失效' : '全部');
            this.showToast(`已筛选: ${typeName} - ${statusName}账号`, 'info');
        },
        async bulkCloudAction(action) {
            if (this.selectedCloud.length === 0) {
                return this.showToast('请先勾选需要操作的账号', 'warning');
            }
            if (action === 'delete' && !confirm(`⚠️ 危险操作：确认删除选中的 ${this.selectedCloud.length} 个账号吗？`)) return;

            const actionName = action === 'check' ? '测活' : (action === 'enable' ? '启用' : (action === 'disable' ? '禁用' : (action === 'refresh' ? '刷新凭证' : '删除')));
            this.showToast(`正在批量 ${actionName} ${this.selectedCloud.length} 个账号，耗时较长请耐心等待...`, 'info');
            this.isCloudActionLoading = true;

            try {
                const res = await this.authFetch('/api/cloud/action', {
                    method: 'POST',
                    body: JSON.stringify({ accounts: this.selectedCloud, action: action })
                });
                const result = await res.json();
                if (result.updated_details) {
                    this.selectedCloud.forEach(selected => {
                        const targetAcc = this.cloudAccounts.find(a => String(a.id) === String(selected.id) && a.account_type === selected.type);
                        if (targetAcc && result.updated_details[selected.id]) {
                            targetAcc.details = Object.assign({}, targetAcc.details, result.updated_details[selected.id]);
                            this.localCloudDetails[selected.id] = targetAcc.details;
                        }
                    });
                }
                if (action === 'check') {
                    const now = new Date().toLocaleString('zh-CN', { hour12: false });
                    this.selectedCloud.forEach(c => { this.localCheckTimes[c.id] = now; });
                }

                this.showToast(result.message, result.status);
                this.fetchCloudAccounts();
                this.selectedCloud = [];
            } catch (e) {
                this.showToast("批量操作异常", "error");
            } finally {
                this.isCloudActionLoading = false;
            }
        },
        toggleAllCloud(e) {
            if (e.target.checked) {
                this.selectedCloud = this.filteredCloud.map(a => ({ id: String(a.id), type: a.account_type }));
            } else {
                this.selectedCloud = [];
            }
        },
        viewCloudDetails(acc) {
            if (!acc.details || Object.keys(acc.details).length === 0) {
                this.showToast("CPA 账号暂无用量缓存，请先点击【测活】拉取！", "warning");
                return;
            }
            this.currentCloudDetail = acc;
            this.showCloudDetailModal = true;
        },
        changeCloudPage(newPage) {
            if (!newPage || isNaN(newPage)) newPage = 1;
            newPage = Math.max(1, Math.min(newPage, this.cloudTotalPages));
            if (this.cloudPage === newPage) {
                this.$forceUpdate();
                return;
            }
            this.cloudPage = newPage;
            this.fetchCloudAccounts();
        },
        changeCloudPageSize() {
            this.cloudPage = 1;
            this.selectedCloud = [];
            this.fetchCloudAccounts();
        },
        async remoteControlNode(nodeName, action) {
            try {
                const res = await this.authFetch('/api/cluster/control', {
                    method: 'POST',
                    body: JSON.stringify({ node_name: nodeName, action: action })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(`✅ 指令 [${action}] 已成功发送至节点: ${nodeName}`, 'success');
                } else {
                    this.showToast(data.message, 'warning');
                }
            } catch (e) {
                this.showToast('控制请求异常', 'error');
            }
        },
        formatDuration(seconds) {
            if (!seconds || seconds < 0) return "0s";
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            const s = Math.floor(seconds % 60);

            let res = "";
            if (h > 0) res += h + "h ";
            if (m > 0 || h > 0) res += m + "m ";
            res += s + "s";
            return res;
        },
        getOnlineDuration(joinTime) {
            if (!joinTime) return '0s';
            const diff = this.nowTimestamp - Math.floor(joinTime);
            return this.formatDuration(diff);
        },
        maskValue(val, type = 'auto') {
            if (!val) return '未配置';
            if (type === 'email' || (type === 'auto' && val.includes('@'))) {
                const parts = val.split('@');
                return parts[0].substring(0, 2) + '***@' + '***';
            }
            if (type === 'url' || (type === 'auto' && val.startsWith('http'))) {
                try {
                    const url = new URL(val);
                    return `${url.protocol}//*****${url.port ? ':'+url.port : ''}${url.pathname.length > 1 ? '/...' : ''}`;
                } catch(e) { return val.substring(0, 8) + '...'; }
            }
            return val.length > 8 ? val.substring(0, 4) + '***' + val.slice(-4) : val.substring(0, 2) + '***';
        },
        initClusterWebSocket() {
            if (this.clusterWs) {
                this.clusterWs.close();
            }

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const token = localStorage.getItem('auth_token');
            const wsUrl = `${protocol}//${window.location.host}/api/cluster/view_ws?token=${token}`;

            this.clusterWs = new WebSocket(wsUrl);

            this.clusterWs.onmessage = (event) => {
                const res = JSON.parse(event.data);
                if (res.status === 'success') {
                    this.clusterNodes = res.nodes;
                }
            };

            this.clusterWs.onclose = () => {
                setTimeout(() => {
                    if (this.currentTab === 'cluster' && this.isLoggedIn) {
                        this.initClusterWebSocket();
                    }
                }, 3000);
            };
        },
        async dispatchExtensionTask() {
            if (!this.isRunning) return;

            try {
                const res = await this.authFetch('/api/ext/generate_task');
                const data = await res.json();
                if (!this.isRunning) {
                    this.showToast("任务已生成，但系统已停止，已丢弃该任务。", "warning");
                    return;
                }
                const now = new Date();
                const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });

                if (data.status !== 'success') {
                    this.logs.push({ parsed: true, time: timeStr, level: '总控', text: `任务生成失败: ${data.message}`, raw: `[${timeStr}] [总控] 任务生成失败: ${data.message}` });
                    return;
                }

                const task = data.task_data;
                const taskId = "TASK_" + new Date().getTime();

                this.logs.push({ parsed: true, time: timeStr, level: '总控', text: `📦 任务包裹已打包，目标邮箱:${task.email}，正在进行...`, raw: `[${timeStr}] [总控] 📦 任务包裹已打包，目标邮箱:${task.email}，正在进行...` });

                this.$nextTick(() => {
                    const container = document.getElementById('terminal-container');
                    if (container) container.scrollTop = container.scrollHeight;
                });

                window.postMessage({
                    type: "CMD_EXECUTE_TASK",
                    payload: {
                        taskId: taskId,
                        apiUrl: window.location.origin,
                        token: localStorage.getItem('auth_token'),
                        email: task.email,
                        email_jwt: task.email_jwt,
                        password: task.password,
                        firstName: task.firstName,
                        lastName: task.lastName,
                        birthday: task.birthday,
                        registerUrl: task.registerUrl,
                        code_verifier: task.code_verifier,
                        expected_state: task.expected_state
                    }
                }, "*");

            } catch (error) {
                const now = new Date();
                const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });
                this.logs.push({ parsed: true, time: timeStr, level: '总控', text: `下发任务异常: ${error.message}`, raw: `[${timeStr}] [总控] 下发任务异常: ${error.message}` });
            }
        },

        syncTokenToExtension() {
            let localWorkerId = localStorage.getItem('local_worker_id');
            if (!localWorkerId) return; // 还没有 ID 则等待 Ready 信号生成

            const payload = {
                apiUrl: window.location.origin,
                token: localStorage.getItem('auth_token'), // 💥 确保抓取登录后的最新 Token
                workerId: localWorkerId
            };

            window.postMessage({ type: "CMD_INIT_NODE", payload: payload }, "*");
            console.log(`📡 [总控] 身份同步指令已下发: ${localWorkerId}`);
        },

        listenToExtension() {
            if (this.config?.reg_mode !== 'extension') return;
            if (this._hasExtensionListener) {
                this.syncTokenToExtension();
                return;
            }

            this._hasExtensionListener = true;
            this.isExtConnected = false;

            window.addEventListener("message", async (event) => {
                if (!event.data) return;

                if (event.data.type === "WORKER_READY") {
                    const now = new Date();
                    const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });

                    if (this._extDetectionTimer) {
                        clearInterval(this._extDetectionTimer);
                        this._extDetectionTimer = null;
                        console.log("🎯 [总控] 锁定插件频段，探测雷达已关闭。");
                    }

                    this.isExtConnected = true;

                    let localWorkerId = localStorage.getItem('local_worker_id');
                    if (!localWorkerId) {
                        localWorkerId = 'Node-' + Math.random().toString(36).substring(2, 6).toUpperCase();
                        localStorage.setItem('local_worker_id', localWorkerId);
                    }
                    console.log(`[集群管控] 本机节点识别码: ${localWorkerId}`);

                    this.logs.push({
                        parsed: true, time: timeStr, level: '总控',
                        text: '✅ 插件连接成功，正在同步身份凭证...',
                        raw: `[${timeStr}] [总控] ✅ 插件连接成功，正在同步身份凭证...`
                    });

                    this.$nextTick(() => {
                        const container = document.getElementById('terminal-container');
                        if (container) container.scrollTop = container.scrollHeight;
                    });

                    this.syncTokenToExtension();
                    return;
                }

                if (event.data.type === "WORKER_LOG_REPLY") {
                    const now = new Date();
                    const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });
                    this.logs.push({
                        parsed: true, time: timeStr, level: '节点',
                        text: event.data.log, raw: `[${timeStr}] [节点] ${event.data.log}`
                    });
                    this.$nextTick(() => {
                        const container = document.getElementById('terminal-container');
                        if (container) container.scrollTop = container.scrollHeight;
                    });
                }

                if (event.data.type === "WORKER_RESULT_REPLY") {
                    const result = event.data.result;
                    try {
                        await this.authFetch('/api/ext/submit_result', {
                            method: 'POST',
                            body: JSON.stringify(result)
                        });
                    } catch (e) {
                        console.error("统计上报失败", e);
                    }

                    if (result.status === 'success') {
                        this.showToast(`🎉 收到节点捷报！注册成功！`, "success");
                    } else {
                        this.showToast(`❌ 节点汇报失败: ${result.error_msg}`, "error");
                    }

                    if (this.isRunning) {
                        const targetCount = this.config?.normal_mode?.target_count || 0;
                        if (targetCount > 0 && this.stats && this.stats.success >= targetCount) {
                            this.showToast(`🎯 已达到目标产出数量 (${targetCount})，自动停止调度！`, "success");
                            this.isRunning = false;
                            window.postMessage({ type: "CMD_STOP_WORKER" }, "*");

                            const timeStr = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                            this.logs.push({
                                parsed: true, time: timeStr, level: '总控',
                                text: `🛑 目标产量已达成，总控引擎已自动挂起。`,
                                raw: `[${timeStr}] [总控] 🛑 目标产量已达成，总控引擎已自动挂起。`
                            });
                            return;
                        }
                        this.showToast(`准备下发下一个插件任务...`, "info");
                        this._extDispatchTimer = setTimeout(() => {
                            this._extDispatchTimer = null;
                            this.dispatchExtensionTask();
                        }, 4000);
                    }
                }
            });

            if (this._extDetectionTimer) clearInterval(this._extDetectionTimer);
            this._extDetectionTimer = setInterval(() => {
                if (this.config?.reg_mode === 'extension' && !this.isExtConnected) {
                    console.log("📡 [总控] 正在扫描空域...");
                    window.postMessage({ type: "CHECK_EXTENSION_READY" }, "*");
                } else if (this.config?.reg_mode !== 'extension') {
                    clearInterval(this._extDetectionTimer);
                    this._extDetectionTimer = null;
                }
            }, 2000);
        },
        async changeRegMode(mode) {
            if (!this.config) return;
            this.config.reg_mode = mode;

            await this.saveConfig();
            this.showToast(`模式已切换为: ${mode === 'protocol' ? '纯协议模式' : '插件托管模式'}`, 'info');

            if (mode === 'extension') {
                this.listenToExtension();
            } else {
                if (this._extDetectionTimer) {
                    clearInterval(this._extDetectionTimer);
                    this._extDetectionTimer = null;
                }
                this.isExtConnected = false;
                window.postMessage({ type: "CMD_STOP_WORKER" }, "*");
                console.log("🛑 [总控] 已进入协议模式，切断插件链路。");
            }
        },
        async fetchMailboxes(isManual = false) {
            if (isManual) this.mailboxPage = 1;
            let url = `/api/mailboxes?page=${this.mailboxPage}&page_size=${this.mailboxPageSize}`;
            if (this.searchMailboxes) {
                url += `&search=${encodeURIComponent(this.searchMailboxes)}`;
            }

            try {
                const res = await this.authFetch(url);
                const data = await res.json();
                if(data.status === 'success') {
                    this.mailboxes = data.data;
                    this.totalMailboxes = data.total || this.mailboxes.length;
                    this.selectedMailboxes = [];
                    if (isManual) this.showToast("邮箱库已刷新！", "success");
                }
            } catch (e) {
                console.error("获取邮箱库失败:", e);
            }
        },
        changeMailboxPage(newPage) {
            if (!newPage || isNaN(newPage)) newPage = 1;
            newPage = Math.max(1, Math.min(newPage, this.mailboxTotalPages));
            if (this.mailboxPage === newPage) {
                this.$forceUpdate();
                return;
            }
            this.mailboxPage = newPage;
            this.fetchMailboxes();
        },
        changeMailboxPageSize() {
            this.mailboxPage = 1;
            this.fetchMailboxes();
        },
        toggleAllMailboxes(event) {
            if (event.target.checked) this.selectedMailboxes = [...this.filteredMailboxes];
            else this.selectedMailboxes = [];
        },
        async submitImportMailboxes() {
            if (!this.importMailboxText.trim()) return this.showToast("请输入内容", "warning");
            this.isImportingMailbox = true;
            try {
                const res = await this.authFetch('/api/mailboxes/import', {
                    method: 'POST',
                    body: JSON.stringify({ raw_text: this.importMailboxText })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(`成功导入 ${data.count} 个邮箱！`, "success");
                    this.showImportMailboxModal = false;
                    this.importMailboxText = '';
                    this.fetchMailboxes(true);
                } else {
                    this.showToast("导入失败: " + data.message, "error");
                }
            } catch (e) {
                this.showToast("导入请求失败", "error");
            } finally {
                this.isImportingMailbox = false;
            }
        },
        async deleteSelectedMailboxes() {
            if (this.selectedMailboxes.length === 0) return;
            const confirmed = await this.customConfirm(`确定要删除选中的 ${this.selectedMailboxes.length} 个邮箱吗？`);
            if (!confirmed) return;

            const idsToDelete = this.selectedMailboxes.map(m => m.id || m.email);
            try {
                const res = await this.authFetch('/api/mailboxes/delete', {
                    method: 'POST',
                    body: JSON.stringify({ ids: idsToDelete })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast("删除成功", "success");
                    this.fetchMailboxes();
                } else {
                    this.showToast("删除失败: " + data.message, "error");
                }
            } catch (e) {
                this.showToast("请求异常", "error");
            }
        },
        openOutlookAuthModal(mailbox) {
            const cid = mailbox.client_id || this.config?.local_microsoft?.client_id || this.BUILTIN_CLIENT_ID;
            if (!cid) {
                this.showToast("🚫 无法获取有效的 Client ID！", "warning");
                return;
            }
            this.outlookAuth.mailbox = mailbox;
            this.outlookAuth.currentClientId = cid;
            this.outlookAuth.authUrl = '';
            this.outlookAuth.pastedUrl = '';
            this.outlookAuth.showModal = true;
        },

        async generateOutlookAuthUrl() {
            this.outlookAuth.isGenerating = true;
            try {
                const res = await this.authFetch('/api/mailboxes/oauth_url', {
                    method: 'POST',
                    body: JSON.stringify({ client_id: this.outlookAuth.currentClientId })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.outlookAuth.authUrl = data.url;
                } else {
                    this.showToast("生成失败: " + data.message, "error");
                }
            } catch (e) {
                this.showToast("网络请求异常", "error");
            } finally {
                this.outlookAuth.isGenerating = false;
            }
        },
        async submitOutlookAuthCode() {
            this.outlookAuth.isLoading = true;
            try {
                const res = await this.authFetch('/api/mailboxes/oauth_exchange', {
                    method: 'POST',
                    body: JSON.stringify({
                        email: this.outlookAuth.mailbox.email,
                        client_id: this.outlookAuth.currentClientId,
                        code_or_url: this.outlookAuth.pastedUrl
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(data.message, "success");
                    this.outlookAuth.showModal = false;
                    if (this.outlookAuth.mailbox.isFission && data.refresh_token) {
                        this.config.local_microsoft.refresh_token = data.refresh_token;
                        await this.saveConfig();
                        this.showToast("✅ Token 已自动填入并保存！", "success");
                    } else {
                        this.fetchMailboxes();
                    }
                } else {
                    this.showToast("换取失败: " + data.message, "error");
                }
            } catch (e) {
                this.showToast("网络请求异常", "error");
            } finally {
                this.outlookAuth.isLoading = false;
            }
        },
        openFissionAuthModal() {
            if (!this.config.local_microsoft.master_email) {
                this.showToast("🚫 请先填写裂变主邮箱账号！", "warning");
                return;
            }

            this.outlookAuth.mailbox = {
                email: this.config.local_microsoft.master_email,
                isFission: true
            };
            this.outlookAuth.currentClientId = this.config.local_microsoft.client_id || this.BUILTIN_CLIENT_ID;
            this.outlookAuth.authUrl = '';
            this.outlookAuth.pastedUrl = '';
            this.outlookAuth.showModal = true;
        },
        exportSelectedMailboxesToTxt() {
            if (this.selectedMailboxes.length === 0) {
                this.showToast("请先勾选需要导出的邮箱", "warning");
                return;
            }
            const textContent = this.selectedMailboxes
                .map(m => {
                    const pwd = m.password || '';
                    const cid = m.client_id || '';
                    const rt = m.refresh_token || '';
                    return `${m.email}----${pwd}----${cid}----${rt}`;
                })
                .join('\n');

            const blob = new Blob([textContent], { type: 'text/plain;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;

            const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
            link.download = `microsoft_mailboxes_${dateStr}.txt`;

            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);

            this.showToast(`🎉 成功导出 ${this.selectedMailboxes.length} 个邮箱到 TXT`, 'success');
            this.selectedMailboxes = [];
        },
        async recoverSelectedMailboxes() {
            if (this.selectedMailboxes.length === 0) {
                this.showToast("请先勾选需要恢复的邮箱", "warning");
                return;
            }

            const confirmed = await this.customConfirm(`确定要将选中的 ${this.selectedMailboxes.length} 个邮箱状态重置为【正常/闲置】吗？\n(可用于解除死号误标)`);
            if (!confirmed) return;

            const emailsToRecover = this.selectedMailboxes.map(m => m.email);

            try {
                const res = await this.authFetch('/api/mailboxes/update_status', {
                    method: 'POST',
                    body: JSON.stringify({ emails: emailsToRecover, status: 0 })
                });
                const data = await res.json();

                if (data.status === 'success') {
                    this.showToast(data.message, "success");
                    this.selectedMailboxes = [];
                    this.fetchMailboxes();
                } else {
                    this.showToast("恢复失败: " + data.message, "error");
                }
            } catch (e) {
                this.showToast("请求异常", "error");
            }
        },
        async fetchClashPool() {
            this.clashPool.loading = true;
            try {
                const res = await this.authFetch('/api/clash/status');
                const d = await res.json();
                if (d.status === 'success') {
                    this.clashPool.instances = d.data.instances;
                    this.clashPool.groups = d.data.groups;
                    if (this.clashPool.instances.length > 0) {
                        this.clashPool.count = this.clashPool.instances.length;
                    }
                }
            } catch (e) {}
            this.clashPool.loading = false;
        },
        async handleClashDeploy() {
            this.showToast('正在调整实例规模...', 'info');
            try {
                const res = await this.authFetch('/api/clash/deploy', {
                    method: 'POST',
                    body: JSON.stringify({ count: this.clashPool.count })
                });
                const d = await res.json();
                this.showToast(d.message, d.status);
                this.fetchClashPool();
            } catch (e) { this.showToast('网络错误', 'error'); }
        },
        async handleClashUpdate() {
            if (!this.clashPool.subUrl) return this.showToast('请输入订阅链接', 'error');
            this.clashPool.loading = true;
            try {
                const res = await this.authFetch('/api/clash/update', {
                    method: 'POST',
                    body: JSON.stringify({ sub_url: this.clashPool.subUrl, target: this.clashPool.target })
                });
                const d = await res.json();
                this.showToast(d.message, d.status);
                this.fetchClashPool();
            } catch (e) { this.showToast('网络错误', 'error'); }
            this.clashPool.loading = false;
        },
        fillProxyGroup(name) {
            if (this.config && this.config.clash_proxy_pool) {
                this.config.clash_proxy_pool.group_name = name;
                this.showToast(`已自动填入策略组：${name}`, 'success');
            }
        },
        syncClusterToPool() {
            if (!this.clashPool.instances || this.clashPool.instances.length === 0) {
                this.showToast('当前没有运行中的实例', 'warning');
                return;
            }

            const generatedList = this.clashPool.instances
                .filter(ins => ins.status === 'running')
                .map(ins => {
                    const idx = ins.name.split('_')[1];
                    return `http://127.0.0.1:${41000 + parseInt(idx)}`;
                });

            if (this.config && this.config.clash_proxy_pool) {
                this.warpListStr = generatedList.join('\n');

                this.config.clash_proxy_pool.pool_mode = true;
                this.config.clash_proxy_pool.enable = true;

                this.showToast(`✅ 已自动同步 ${generatedList.length} 个端口到独享池`, 'success');

                this.$nextTick(() => {
                    const el = document.getElementById('proxy-intelligence-pool');
                    if (el) el.scrollIntoView({ behavior: 'smooth' });
                });
            }
        },
        async exportAllAccounts() {
            try {
                const res = await this.authFetch('/api/accounts/export_all', { method: 'POST' });
                const data = await res.json();

                if (data.status === 'success') {
                    const allData = data.data;
                    if (allData.length === 0) {
                        this.showToast('账号库是空的，无需导出', 'warning');
                        return;
                    }

                    const zip = new JSZip();
                    const timestamp = Math.floor(Date.now() / 1000);

                    const txtContent = allData.map(acc => `${acc.email}----${acc.password}`).join('\n');
                    zip.file(`accounts_list_${timestamp}.txt`, txtContent);

                    const cpaFolder = zip.folder("cpa");
                    const sub2apiFolder = zip.folder("sub2api");

                    const proxyPool = this.buildSub2ApiProxyPool(this.config?.sub2api_mode?.default_proxy || "");

                    const validAccounts = allData.filter(acc => acc.token_data && acc.token_data.access_token);

                    validAccounts.forEach((acc, index) => {
                        const accEmail = acc.email || "unknown";
                        const parts = accEmail.split('@');
                        const prefix = parts[0] || "user";
                        const domain = parts[1] || "domain";

                        const cpaData = {
                            ...acc.token_data,
                            email: accEmail,
                            password: acc.password
                        };
                        cpaFolder.file(`token_${prefix}_${domain}_${timestamp + index}.json`, JSON.stringify(cpaData, null, 4));

                        const proxyObj = proxyPool.length ? proxyPool[index % proxyPool.length] : null;
                        const accountNode = {
                            name: accEmail.slice(0, 64),
                            platform: "openai",
                            type: "oauth",
                            credentials: { refresh_token: acc.token_data.refresh_token || "" },
                            concurrency: this.config?.sub2api_mode?.account_concurrency || 10,
                            priority: this.config?.sub2api_mode?.account_priority || 1,
                            rate_multiplier: this.config?.sub2api_mode?.account_rate_multiplier || 1.0,
                            extra: { load_factor: this.config?.sub2api_mode?.account_load_factor || 10 }
                        };

                        if (proxyObj) {
                            accountNode.proxy_key = proxyObj.proxy_key;
                        }

                        const sub2apiData = {
                            exported_at: new Date().toISOString(),
                            proxies: proxyObj ? [proxyObj] : [],
                            accounts: [accountNode]
                        };
                        sub2apiFolder.file(`sub2api_${prefix}_${domain}_${timestamp + index}.json`, JSON.stringify(sub2apiData, null, 4));
                    });

                    const content = await zip.generateAsync({ type: "blob" });
                    const url = window.URL.createObjectURL(content);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `OpenAI_Accounts_Bundle_${timestamp}.zip`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);

                    this.showToast(`成功导出 ${allData.length} 个账号，并已自动注入 Sub2API 代理节点！`, 'success');
                } else {
                    this.showToast(data.message || '导出失败', 'error');
                }
            } catch (e) {
                console.error(e);
                this.showToast('导出异常，请检查网络或刷新页面', 'error');
            }
        },

        async clearAllAccounts() {
            const confirmed = await this.customConfirm('⚠️ 危险操作！确定要删除【账号库】中的所有已注册账号吗？此操作不可恢复。');
            if (!confirmed) return;

            try {
                const res = await this.authFetch('/api/accounts/clear_all', { method: 'POST' });
                const data = await res.json();

                if (data.status === 'success') {
                    this.showToast('账号库已全部清空', 'success');
                    this.fetchAccounts();
                    this.fetchInventoryStats();
                } else {
                    this.showToast(data.message, 'error');
                }
            } catch (e) {
                this.showToast('清空异常', 'error');
            }
        },
        async exportAllMailboxes() {
            try {
                const res = await this.authFetch('/api/mailboxes/export_all', { method: 'POST' });
                const data = await res.json();

                if (data.status === 'success') {
                    const allData = data.data;
                    if (allData.length === 0) {
                        this.showToast('邮箱库是空的，无需导出', 'warning');
                        return;
                    }
                    const text = allData.map(m =>
                        `${m.email}----${m.password}----${m.client_id || ''}----${m.refresh_token || ''}`
                    ).join('\n');

                    const blob = new Blob([text], { type: 'text/plain' });
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `Mailboxes_Backup_${new Date().getTime()}.txt`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);

                    this.showToast(`成功导出 ${allData.length} 个邮箱`, 'success');
                } else {
                    this.showToast(data.message || '导出失败', 'error');
                }
            } catch (e) {
                this.showToast('导出异常', 'error');
            }
        },
        async clearAllMailboxes() {
            const confirmed = await this.customConfirm('⚠️ 危险操作！确定要删除【微软邮箱库】中的所有数据吗？');
            if (!confirmed) return;

            try {
                const res = await this.authFetch('/api/mailboxes/clear_all', { method: 'POST' });
                const data = await res.json();

                if (data.status === 'success') {
                    this.showToast('邮箱库已全部清空', 'success');
                    this.fetchMailboxes();
                } else {
                    this.showToast(data.message, 'error');
                }
            } catch (e) {
                this.showToast('清空异常', 'error');
            }
        },
        parseSub2ApiProxy(proxyUrl) {
            if (!proxyUrl) return null;
            try {
                let parseUrl = proxyUrl;
                const originalProtocol = proxyUrl.split('://')[0];
                if (originalProtocol && !['http', 'https', 'socks4', 'socks5'].includes(originalProtocol)) {
                     parseUrl = proxyUrl.replace(originalProtocol + '://', 'http://');
                }

                const url = new URL(parseUrl);
                const protocol = originalProtocol || url.protocol.replace(':', '');
                const host = url.hostname;
                const port = url.port;
                const username = decodeURIComponent(url.username || '');
                const password = decodeURIComponent(url.password || '');

                if (!protocol || !host || !port) return null;

                const proxyKey = `${protocol}|${host}|${port}|${username}|${password}`;
                const proxyDict = {
                    proxy_key: proxyKey,
                    name: "openai-cpa",
                    protocol: protocol,
                    host: host,
                    port: parseInt(port),
                    status: "active"
                };
                if (username && password) {
                    proxyDict.username = username;
                    proxyDict.password = password;
                }
                return proxyDict;
            } catch (e) {
                return null;
            }
        },
        buildSub2ApiProxyPool(rawValue) {
            const rawItems = Array.isArray(rawValue)
                ? rawValue
                : String(rawValue || '').replace(/\r/g, '\n').split('\n');

            const proxyPool = [];
            const seen = new Set();
            rawItems.forEach(item => {
                const value = String(item || '').trim();
                if (!value || seen.has(value)) return;
                seen.add(value);

                const proxyObj = this.parseSub2ApiProxy(value);
                if (proxyObj) {
                    proxyPool.push(proxyObj);
                }
            });
            return proxyPool;
        },
        async bulkRefreshLocalTokens() {
            if (this.selectedAccounts.length === 0) return;

            const confirmed = await this.customConfirm(`确定要批量刷新这 ${this.selectedAccounts.length} 个账号的凭证吗？\n\n系统会自动剔除死号，并将成功的新凭证同步覆盖至远端平台。`);
            if (!confirmed) return;

            this.isRefreshingAccounts = true;
            this.showToast(`🚀 正在后端并发刷新 ${this.selectedAccounts.length} 个账号，请稍候...`, 'info');
            this.currentTab = 'console';
            try {
                const emails = this.selectedAccounts.map(acc => acc.email);
                const res = await this.authFetch('/api/accounts/bulk_refresh', {
                    method: 'POST',
                    body: JSON.stringify({ emails: emails })
                });
                const result = await res.json();

                this.showToast(result.message, result.status);
            } catch (e) {
                this.showToast("请求异常", "error");
            } finally {
                this.selectedAccounts = [];
                this.fetchAccounts();
                this.fetchInventoryStats();
                this.isRefreshingAccounts = false;
            }
        },
        async fetchSystemVersion() {
            try {
                const res = await fetch('/api/system/version');
                const data = await res.json();
                if (data.status === 'success') {
                    this.appVersion = data.version;
                }
            } catch (e) {
                return null;
            }
        },
        getPlatformBadges(platformStr) {
            if (!platformStr) return [];
            const platforms = platformStr.split(',')
                                         .map(p => p.trim().toUpperCase())
                                         .filter(p => p);

            if (platforms.length >= 3) {
                return [{ name: '🚀 三平台同步', type: 'TRIPLE' }];
            }
            return platforms.map(p => {
                let displayName = p;
                if (p === 'IMAGE2API') displayName = '🖼️ IMAGE2API';
                if (p === 'SUB2API') displayName = '🛸 SUB2API';
                if (p === 'CPA') displayName = '🎯 CPA';
                return { name: displayName, type: p };
            });
        },
        getBadgeClass(type) {
            switch (type) {
                case 'CPA':
                    return 'bg-blue-50 text-blue-600 border-blue-200';
                case 'SUB2API':
                    return 'bg-purple-50 text-purple-600 border-purple-200';
                case 'IMAGE2API':
                    return 'bg-pink-50 text-pink-600 border-pink-200';
                case 'TRIPLE':
                    return 'bg-gradient-to-r from-blue-500 via-purple-500 to-pink-500 text-white border-transparent shadow-md';
                default:
                    return 'bg-slate-50 text-slate-600 border-slate-200';
            }
        },
        async fetchTeamAccounts(isManual = false) {
            if (isManual) this.teamPage = 1;
            let url = `/api/team_accounts?page=${this.teamPage}&page_size=${this.teamPageSize}`;

            try {
                const res = await this.authFetch(url);
                const data = await res.json();
                if(data.status === 'success') {
                    this.teamAccounts = data.data;
                    this.totalTeamAccounts = data.total || this.teamAccounts.length;
                    if (isManual) this.showToast("Team 库已刷新！", "success");
                }
            } catch (e) {
                console.error("获取 Team 库失败:", e);
            }
        },
        changeTeamPage(newPage) {
            if (!newPage || isNaN(newPage)) newPage = 1;
            const maxPage = Math.ceil(this.totalTeamAccounts / this.teamPageSize) || 1;
            newPage = Math.max(1, Math.min(newPage, maxPage));
            if (this.teamPage === newPage) {
                this.$forceUpdate();
                return;
            }
            this.teamPage = newPage;
            this.fetchTeamAccounts();
        },
        async submitImportTeams() {
            if (!this.importTeamText.trim()) return this.showToast("请输入内容", "warning");
            this.isImportingTeam = true;
            try {
                const res = await this.authFetch('/api/team_accounts/import', {
                    method: 'POST',
                    body: JSON.stringify({ raw_text: this.importTeamText })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(`成功导入 ${data.count} 个 Team Token！`, "success");
                    this.showImportTeamModal = false;
                    this.importTeamText = '';
                    this.fetchTeamAccounts(true);
                } else {
                    this.showToast("导入失败: " + data.message, "error");
                }
            } catch (e) {
                this.showToast("导入请求失败", "error");
            } finally {
                this.isImportingTeam = false;
            }
        },
        async deleteSingleTeam(id) {
            const confirmed = await this.customConfirm('确定要删除该 Team 账号吗？');
            if (!confirmed) return;
            try {
                const res = await this.authFetch('/api/team_accounts/delete', {
                    method: 'POST',
                    body: JSON.stringify({ ids: [id] })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast("删除成功", "success");
                    this.fetchTeamAccounts();
                } else {
                    this.showToast("删除失败: " + data.message, "error");
                }
            } catch (e) {
                this.showToast("请求异常", "error");
            }
        },
        async clearAllTeamAccounts() {
            const confirmed = await this.customConfirm('⚠️ 危险操作！确定要清空【Team 团队账号库】中的所有数据吗？');
            if (!confirmed) return;
            try {
                const res = await this.authFetch('/api/team_accounts/clear_all', { method: 'POST' });
                const data = await res.json();

                if (data.status === 'success') {
                    this.showToast('Team 库已全部清空', 'success');
                    this.fetchTeamAccounts();
                } else {
                    this.showToast(data.message, 'error');
                }
            } catch (e) {
                this.showToast('清空异常', 'error');
            }
        },
        async uploadLicenseFile() {
            const fileInput = document.getElementById('licenseFileInput');
            if (!fileInput || !fileInput.files.length) {
                this.showToast('请先选择一个授权文件！', 'warning');
                return;
            }
            const file = fileInput.files[0];
            const reader = new FileReader();
            reader.onload = async (e) => {
                const fileContent = e.target.result;

                try {
                    const response = await this.authFetch('/api/auth/upload_license', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            content: fileContent
                        })
                    });

                    const res = await response.json();
                    if (res.status === 'success') {
                        this.showToast(res.message, 'success');
                        fileInput.value = '';
                    } else {
                        this.showToast(res.message, 'error');
                    }
                } catch (error) {
                    console.error(error);
                    this.showToast('上传授权文件发生网络错误', 'error');
                }
            };
            reader.readAsText(file);
        },

        async submitAuthReset() {
            if (!this.authResetModal.clearLicense && !this.authResetModal.clearHwid && !this.authResetModal.clearLease) {
                this.showToast('请至少勾选一项需要清除的数据！', 'warning');
                return;
            }
            const confirmed = await this.customConfirm('⚠️ 危险操作：清除授权数据后可能导致程序异常或需要重新绑定授权！\n\n确定继续吗？');
            if (!confirmed) return;
            try {
                const response = await this.authFetch('/api/auth/reset', {
                    method: 'POST',
                    body: JSON.stringify({
                        clear_license: this.authResetModal.clearLicense,
                        clear_hwid: this.authResetModal.clearHwid,
                        clear_lease: this.authResetModal.clearLease
                    })
                });

                const res = await response.json();
                if (res.status === 'success') {
                    this.showToast(res.message, 'success');
                    this.authResetModal.show = false;
                } else {
                    this.showToast(res.message, 'error');
                }
            } catch (error) {
                this.showToast('执行重置操作发生网络错误', 'error');
            }
        },
        async testTgNotification() {
            if (!this.config.tg_bot.token || !this.config.tg_bot.chat_id) {
                this.showToast('请先填写完整的 Bot Token 和 Chat ID', 'warning');
                return;
            }
            this.isTestingTg = true;
            this.showToast('正在发送测试消息，请稍候...', 'info');
            try {
                const res = await this.authFetch('/api/notify/test_tg', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        token: this.config.tg_bot.token,
                        chat_id: this.config.tg_bot.chat_id
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.message, 'error');
                }
            } catch (e) {
                this.showToast('测试请求异常，请检查后端网络或全局代理设置', 'error');
            } finally {
                this.isTestingTg = false;
            }
        },
        async clearGmailToken() {
            const confirmed = await this.customConfirm('确定要清除已保存的 Gmail 授权 Token 吗？\n清除后系统将无法读取邮件，直到你重新完成 OAuth 授权。');
            if (!confirmed) return;
            try {
                const res = await this.authFetch('/api/gmail/clear_token', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(data.message, 'success');
                } else {
                    this.showToast(data.message, 'error');
                }
            } catch (error) {
                this.showToast('清理授权失败', 'error');
            }
        },
        async uploadGmailCredentials() {
            const fileInput = document.getElementById('gmailCredentialsInput');
            if (!fileInput || !fileInput.files.length) {
                this.showToast('请先选择从 Google 下载的 JSON 凭据文件！', 'warning');
                return;
            }
            const file = fileInput.files[0];
            const reader = new FileReader();

            reader.onload = async (e) => {
                const jsonContent = e.target.result;

                try {
                    JSON.parse(jsonContent);
                } catch (err) {
                    this.showToast('非法格式：请确保上传的是正确的 JSON 文件！', 'error');
                    return;
                }
                this.showToast('正在同步凭据至云端...', 'info');

                try {
                    const response = await this.authFetch('/api/gmail/upload_credentials', {
                        method: 'POST',
                        body: JSON.stringify({ content: jsonContent })
                    });
                    const data = await response.json();
                    if (data.status === 'success') {
                        this.showToast(data.message, 'success');
                        fileInput.value = '';
                    } else {
                        this.showToast(data.message, 'error');
                    }
                } catch (error) {
                    this.showToast('上传失败，请检查后端 API 连通性', 'error');
                }
            };
            reader.readAsText(file);
        },
        copyText(text) {
            navigator.clipboard.writeText(text).then(() => {
                this.showToast('✅ 复制成功: ' + text, 'success');
            }).catch(() => {
                this.showToast('❌ 复制失败', 'error');
            });
        },

        async handleCFBatchHosting() {
            if (!this.config.mail_domains) return this.showToast('请填写主发信域名池！', 'warning');
            if (!this.config.cf_api_email || !this.config.cf_api_key) return this.showToast('请填写 CF 账号邮箱和 API Key！', 'warning');

            this.cfTools.isHosting = true;
            this.showToast('正在连线 CF 获取 NS，请稍候...', 'info');
            this.currentTab = 'console';
            try {
                const res = await this.authFetch('/api/cloudflare/add_zones', {
                    method: 'POST',
                    body: JSON.stringify({ domains: this.config.mail_domains, api_email: this.config.cf_api_email, api_key: this.config.cf_api_key })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.cfTools.results = data.data;
                    this.showToast('✅ 批量获取 NS 完成，请在界面下方复制修改！', 'success');
                    this.currentTab = 'email';
                } else this.showToast(data.message, 'error');
            } catch (e) { this.showToast('请求异常', 'error'); } finally { this.cfTools.isHosting = false; }
        },
        async handleCFEnableEmail() {
            if (!this.config.mail_domains) return this.showToast('发信域名池为空', 'warning');
            this.cfTools.isEnablingEmail = true;
            this.showToast('正在检测 NS 并激活 CF 企业邮局...', 'info');
            this.currentTab = 'console';
            try {
                const res = await this.authFetch('/api/cloudflare/enable_email', {
                    method: 'POST',
                    body: JSON.stringify({ domains: this.config.mail_domains, api_email: this.config.cf_api_email, api_key: this.config.cf_api_key })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.cfTools.results = data.data;
                    this.showToast('🎉 邮件服务校验激活完毕！', 'success');
                    this.currentTab = 'email';
                } else this.showToast(data.message, 'error');
            } catch (e) { this.showToast('请求异常', 'error'); } finally { this.cfTools.isEnablingEmail = false; }
        },
        async handleCFDeployWorker() {
            if (!this.config.cf_api_email || !this.config.cf_api_key) return this.showToast('请填写 CF 凭据！', 'warning');
            if (!this.cfTools.workerName) return this.showToast('请输入 Worker 项目名！', 'warning');

            let currentSecret = this.config.openai_cpa?.webhook_secret || '';
            const currentWebhookUrl = window.location.origin;

            const confirmed = await this.customConfirm(`将在后台为您部署 Worker: [${this.cfTools.workerName}]\n自动注入当前面板地址和通信密钥，确定执行吗？`);
            if (!confirmed) return;

            this.cfTools.isDeploying = true;
            this.showToast('正在推送至 Cloudflare 节点...', 'info');
            this.currentTab = 'console';

            try {
                const res = await this.authFetch('/api/cloudflare/deploy_worker', {
                    method: 'POST',
                    body: JSON.stringify({
                        api_email: this.config.cf_api_email,
                        api_key: this.config.cf_api_key,
                        worker_name: this.cfTools.workerName,
                        webhook_url: currentWebhookUrl,
                        webhook_secret: currentSecret
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(`🎉 Worker 部署并绑定就绪！`, 'success');
                    this.currentTab = 'email';
                } else {
                    this.showToast(`部署失败: ${data.message}`, 'error');
                }
            } catch (e) { this.showToast('请求异常', 'error'); } finally { this.cfTools.isDeploying = false; }
        },
        async handleCFCatchAll() {
            if (!this.config.mail_domains) return this.showToast('发信域名池为空', 'warning');
            if (!this.cfTools.workerName) return this.showToast('请输入目标 Worker 项目名', 'warning');

            this.cfTools.isSettingCatchAll = true;
            this.showToast('正在下发 Catch-All 路由规则...', 'info');
            this.currentTab = 'console';
            try {
                const res = await this.authFetch('/api/cloudflare/setup_catch_all', {
                    method: 'POST',
                    body: JSON.stringify({
                        domains: this.config.mail_domains, api_email: this.config.cf_api_email,
                        api_key: this.config.cf_api_key, worker_name: this.cfTools.workerName
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.cfTools.results = data.data;
                    this.showToast('🎉 Catch-All 规则配置完毕！', 'success');
                    this.currentTab = 'email';
                } else this.showToast(data.message, 'error');
            } catch (e) { this.showToast('请求异常', 'error'); } finally { this.cfTools.isSettingCatchAll = false; }
        },
    }
}).mount('#app');
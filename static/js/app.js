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

function normalizeMailDomainCsv(value) {
    const seen = new Set();
    return String(value || '')
        .split(',')
        .map(part => String(part || '').trim().toLowerCase().replace(/^\.+|\.+$/g, ''))
        .filter(part => {
            if (!part || seen.has(part)) return false;
            seen.add(part);
            return true;
        });
}

createApp({
    data() {
        return {
            appVersion: '检查中...',
            isLoggedIn: !!localStorage.getItem('auth_token'),
            loginPassword: '',
            currentTab: window.location.hash.replace('#', '') || 'console',
            currentLanguage: getInitialLanguage(),
            languageObserver: null,
            isDarkMode: localStorage.getItem('ui_theme_mode') === 'dark',
			showAccountsPlaintext: false,
            isRunning: false,
            isLoadingConfig: false,
            configLoadError: '',
            mobileNavOpen: false,
            plugins: [],
            domainRegisterEnabled: false,
            domainRegisterStatusTimer: null,
            domainRegister: {
                api_token: '',
                invite_ref: '',
                auto_invite_signup: true,
                domain_prefix: 'sub2api',
                domain_suffix: 'dpdns.org',
                domain_start: 27,
                domain_end: 127,
                domain_pad: 4,
                running: false,
                statusText: '',
                logs: [],
                preview: [],
                progress: {done:0,total:0,ok:0,fail:0}
            },

            tabs: [
                    { id: 'console', name: '运行主页', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>' },
                    { id: 'cluster', name: '集群总控', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>' },
                    { id: 'email', name: '邮箱配置', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>' },
                    { id: 'mailboxes', name: '微软邮箱库', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>' },
                    { id: 'team_accounts', name: 'Team 账号库', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>' },
                    { id: 'accounts', name: '账号库存', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"></path></svg>' },
                    { id: 'image_accounts', name: '半成品库存', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>' },
                    { id: 'cloud', name: '云端库存', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 15a4 4 0 004 4h11a5 5 0 00-.1-9.995A5.002 5.002 0 1010.5 6H9.75a4 4 0 00-6.75 9z"></path></svg>' },
                    { id: 'sms', name: '手机接码', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"></path></svg>' },
                    { id: 'proxy', name: '网络代理', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"></path></svg>' },
                    { id: 'relay', name: '中转管仓', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 15a4 4 0 004 4h11a5 5 0 00-.1-9.995A5.002 5.002 0 1010.5 6H9.75a4 4 0 00-6.75 9z"></path></svg>' },
                    { id: 'notify', name: '消息通知', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h6a3 3 0 013 3v1a3 3 0 01-3 3H9.436c-1.532 0-2.22.24-2.893.542z"></path></svg>' },
                                        { id: 'domain_register', name: '域名注册', plugin: 'domain_register', icon: '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"></path></svg>', plugin: 'domain_register' },
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
            mailDomainRuntimePollIntervalMs: 2500,
            mailDomainRuntimeFetchPromise: null,
            mailDomainRuntimeFetchQueued: false,
            blacklistStr: "",
            warpListStr: "",
            rawProxyListStr: "",
            accountStatusFilter: 'all',
            accounts: [],
            selectedAccounts: [],
            hideRegisterOnlyAccounts: false,
			currentPage: 1,
            pageSize: 10,
            totalAccounts: 0,
            evtSource: null,
            stats: {
                success: 0, failed: 0, retries: 0, total: 0, target: 0,
                pwd_blocked: 0, phone_verify: 0, discard_count: 0,
                success_rate: '0.0%', elapsed: '0.0s', avg_time: '0.0s', progress_pct: '0%',
                mode: '未启动',
                memory: { rss_mb: null, predicted_mid_mb: null, predicted_high_mb: null, safety_level: 'unknown', safety_label: '无数据' }
            },
            memoryPrediction: null,
            isLoadingMemoryPrediction: false,
            memoryPredictionError: '',
            cleanupMaintenance: {
                loading: false,
                actionLoading: false,
                data: null,
                error: '',
                outputTail: []
            },
            inventoryStats: {
                local: {
                    total: 0,
                    active: 0,
                    disabled: 0,
                    unpushed: 0,
                    pushed: 0,
                    credential: 0,
                    image2api: 0,
                    with_token: 0,
                    reg_only: 0,
                    imgsub2api: 0
                },
                cloud: { total: 0, cpa: 0, sub2api: 0, image2api: 0, grok2api: 0, enabled: 0 }
            },
            statsTimer: null,

            showPwd: {
                login: false, web: false, cf: false, imap: false,
                free_token: false, free_pass: false,
                cm: false, mc: false, clash: false, cpa: false, sub2api: false,
                cf_key: false, cf_modal_key: false,
                mail_domains: true, cf_email: true, gpt_base: true, imap_user: true,
                free_url: true, cm_url: true, cm_email: true, mc_base: true,
                ai_base: true, cluster_url: true, proxy: true, clash_api: true,checkProxy:true,
                clash_test: true, tg_token: false, tg_chatid: false, cpa_url: true, sub_url: true,
                cluster_secret: false, hero_key: false, duck_token: false, duck_cookie: false,
                smsbower_key: false,fivesim_key: false,smsbower_cookie: false,
                luckmail: false,
                temporam: false,
                tmailor_token: false,
                fvia_token: false,
                subUrl: false,
                showMailboxesPlaintext: false,
                db_pass: false,
                master_rt: false,
                image2api_url: true,
                image2api_key: false,
                grok2api_url: true,
                grok2api_pass: false
            },

            toasts: [],
            toastId: 0,
            confirmModal: { show: false, message: '', resolve: null },
            updateInfo: { hasUpdate: false, version: '', url: '', changelog: '' },
            gitSync: {
                loading: false,
                actionLoading: false,
                data: null,
                error: '',
                lastAction: '',
                outputTail: [],
                restartAfter: true
            },
            sub2apiGroups: [],
            gmailOAuth: {
                authUrl: '',
                pastedCode: '',
                isLoading: false,
                isGenerating: false
            },
            isLoadingSub2APIGroups: false,
            cloudAccounts: [],
            rawCloudAccounts: [],
            selectedCloud: [],
            cloudFilters: ['sub2api', 'cpa', "image2api", "grok2api"],
            showCloudPlaintext: false,
            cloudPage: 1,
            cloudPageSize: 10,
            cloudTotal: 0,
            cloudFetchState: {
                loading: false,
                currentType: '',
                completed: 0,
                total: 0,
                message: ''
            },
            localCheckTimes: {},
            localCloudDetails: {},
            isCloudActionLoading: false,
            showCloudDetailModal: false,
            currentCloudDetail: null,
            nowTimestamp: Math.floor(Date.now() / 1000),
            clusterNodes: {},
            clusterSyncTasks: [],
            clusterSyncTasksLoading: false,
            showClusterSyncErrors: true,
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
                groups: [],
                subscriptions: [],
                mode: '',
                message: '',
                isDeploying: false,
                runtimeActionLoading: false,
                subscriptionActionLoading: false,
                delayLoading: false,
                activeGroupName: '',
                view: 'groups',
                nodeSelections: {},
                delayResults: {},
                newSubscriptionName: '',
                newSubscriptionUrl: '',
                makeSelectedSubscription: true
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
            isTestingTg: false,
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
                deleteDomains: '',
                results: [],
                isHosting: false,
                isEnablingEmail: false,
                isDeploying: false,
                isSettingCatchAll: false,
                isDeletingHosting: false
            },
            isUpdatingSystem: false,
            imageAccounts: [],
            selectedImageAccounts: [],
            searchImageAccounts: '',
            imagePage: 1,
            imagePageSize: 10,
            totalImageAccounts: 0,
            isUpgradingOAuthAll: false,
            isUpgradingSelected: false,
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
    async mounted() {
        this.applyLanguage(false);
        this.applyTheme();
        await this.fetchSystemVersion();
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
        this.startLanguageObserver();
        this.$nextTick(() => this.applyLanguageToDom());
    },
    beforeUnmount() {
        this.stopDomainRegisterStatusPolling && this.stopDomainRegisterStatusPolling();
        if(this.statsTimer) clearInterval(this.statsTimer);
        if (this.languageObserver) this.languageObserver.disconnect();
        if (typeof document !== 'undefined') {
            document.body.classList.remove('overflow-hidden');
        }
    },
	computed: {
        visibleTabs() {
            return (this.tabs || []).filter(t => !t.plugin || (t.plugin === 'domain_register' && this.domainRegisterEnabled));
        },

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
            return this.cloudAccounts;
        },
        activeCloudFilterLabel() {
            if (!this.cloudFetchState.currentType) return '待命';
            const labels = {
                sub2api: 'Sub2API',
                cpa: 'CPA',
                image2api: 'Image2API',
                grok2api: 'Grok2API'
            };
            return labels[this.cloudFetchState.currentType] || this.cloudFetchState.currentType;
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
        },
        activeClashGroup() {
            if (!this.clashPool?.activeGroupName) return null;
            return (this.clashPool.groups || []).find(group => group.name === this.clashPool.activeGroupName) || null;
        },
        selectedClashSubscription() {
            return (this.clashPool?.subscriptions || []).find(item => item.selected) || null;
        },
        normalizedMailDomains() {
            if (!this.config) return [];
            return normalizeMailDomainCsv(this.config.mail_domains);
        },
        autoMailDomainGroupsPreview() {
            if (!this.config || !this.config.enable_mail_domain_grouping || this.config.mail_domain_group_mode !== 'auto') {
                return [];
            }
            const domains = this.normalizedMailDomains;
            const groupCount = Math.min(10, Math.max(1, parseInt(this.config.mail_domain_group_count, 10) || 0));
            if (domains.length === 0 || groupCount < 1 || groupCount > domains.length) {
                return [];
            }
            const groups = Array.from({ length: groupCount }, () => []);
            domains.forEach((domain, index) => {
                groups[index % groupCount].push(domain);
            });
            return groups;
        },
        mailDomainGroupLabelMap() {
            if (!this.config || !this.config.enable_mail_domain_grouping) {
                return {};
            }
            const groups = this.config.mail_domain_group_mode === 'manual'
                ? this.config.mail_domain_groups
                    .map(group => normalizeMailDomainCsv(group))
                    .filter(group => group.length > 0)
                : this.autoMailDomainGroupsPreview;
            return groups.reduce((map, group, index) => {
                group.forEach(domain => {
                    map[domain] = `[${index + 1}]`;
                });
                return map;
            }, {});
        },
        sortedMailDomainRuntimeStats() {
            if (!this.config || !this.config.enable_mail_domain_grouping) {
                return this.mailDomainRuntimeStats;
            }
            const groups = this.config.mail_domain_group_mode === 'manual'
                ? this.config.mail_domain_groups
                    .map(group => normalizeMailDomainCsv(group))
                    .filter(group => group.length > 0)
                : this.autoMailDomainGroupsPreview;
            const orderMap = groups.reduce((map, group, groupIndex) => {
                group.forEach((domain, domainIndex) => {
                    map[domain] = { groupIndex, domainIndex };
                });
                return map;
            }, {});
            return [...this.mailDomainRuntimeStats].sort((a, b) => {
                const left = orderMap[a?.domain] || { groupIndex: Number.MAX_SAFE_INTEGER, domainIndex: Number.MAX_SAFE_INTEGER };
                const right = orderMap[b?.domain] || { groupIndex: Number.MAX_SAFE_INTEGER, domainIndex: Number.MAX_SAFE_INTEGER };
                if (left.groupIndex !== right.groupIndex) return left.groupIndex - right.groupIndex;
                if (left.domainIndex !== right.domainIndex) return left.domainIndex - right.domainIndex;
                return String(a?.domain || '').localeCompare(String(b?.domain || ''));
            });
        },
        grokMode() {
            return !!this.config && this.config.reg_provider === 'grok';
        }
    },
    methods: {
        ...REALTIME_METHODS,
        ...CLOUD_METHODS,
        ...CLUSTER_METHODS,
        ...MAILBOX_METHODS,
        ...ACCOUNT_METHODS,
        ...DOMAIN_REGISTER_METHODS,
        ...MAINTENANCE_METHODS,
        ...PROXY_METHODS,
        ...CLOUDFLARE_METHODS,
        t(text) {
            return translateText(text, this.currentLanguage);
        },
        targetLanguageLabel() {
            return this.currentLanguage === TRADITIONAL_LANGUAGE ? '简体中文' : '繁體中文';
        },
        resolveClashSubscriptionUrl(rawUrl) {
            const text = String(rawUrl || '').trim();
            if (!text) return '';
            if (/^https?:\/\//i.test(text)) return text;
            if (text.startsWith('//')) return `${window.location.protocol}${text}`;
            const base = window.location.origin.replace(/\/+$/, '');
            if (text.startsWith('/')) return `${base}${text}`;
            return `${base}/${text.replace(/^\.?\//, '')}`;
        },
        isMobileViewport() {
            return typeof window !== 'undefined' && window.innerWidth < 768;
        },
        toggleMobileNav(forceState = null) {
            const nextState = typeof forceState === 'boolean' ? forceState : !this.mobileNavOpen;
            this.mobileNavOpen = nextState;
            if (typeof document !== 'undefined') {
                document.body.classList.toggle('overflow-hidden', nextState && this.isMobileViewport());
            }
        },
        formatClashSubscriptionLabel(subscription) {
            if (!subscription) return '未命名订阅';
            const name = String(subscription.name || '').trim();
            if (name && name !== '当前订阅') return name;
            const source = String(subscription.url || subscription.raw_url || '').trim();
            try {
                const parsed = new URL(source);
                const target = parsed.searchParams.get('url');
                if (target) {
                    const targetUrl = new URL(target);
                    const lastSeg = targetUrl.pathname.split('/').filter(Boolean).pop();
                    return `${targetUrl.hostname}${lastSeg ? ' / ' + lastSeg : ''}`;
                }
                const lastSeg = parsed.pathname.split('/').filter(Boolean).pop();
                return `${parsed.hostname}${lastSeg ? ' / ' + lastSeg : ''}`;
            } catch (_) {
                return name || '未命名订阅';
            }
        },
        toggleLanguage() {
            const nextLanguage = this.currentLanguage === TRADITIONAL_LANGUAGE ? DEFAULT_LANGUAGE : TRADITIONAL_LANGUAGE;
            this.setLanguage(nextLanguage);
            this.showToast(nextLanguage === TRADITIONAL_LANGUAGE ? '已切换为繁体中文' : '已切换为简体中文', 'info');
        },
        setLanguage(language) {
            this.currentLanguage = SUPPORTED_LANGUAGES.includes(language) ? language : DEFAULT_LANGUAGE;
            this.applyLanguage();
        },
        applyLanguage(translateDom = true) {
            APP_LOCALE = this.currentLanguage;
            localStorage.setItem(LANGUAGE_STORAGE_KEY, this.currentLanguage);
            document.title = this.t('Wenfxl 注册管理系统');
            if (translateDom) {
                this.$nextTick(() => this.applyLanguageToDom());
            }
        },
        applyLanguageToDom(root = document.body) {
            if (!root) return;
            const ignoredTags = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE']);
            const translateNodeText = (node) => {
                if (!node.nodeValue || !node.nodeValue.trim()) return;
                if (!I18N_ORIGINAL_TEXT_NODES.has(node)) {
                    I18N_ORIGINAL_TEXT_NODES.set(node, node.nodeValue);
                }
                const sourceText = I18N_ORIGINAL_TEXT_NODES.get(node);
                node.nodeValue = translateText(sourceText, this.currentLanguage);
            };
            const translateAttributes = (element) => {
                ['placeholder', 'title', 'aria-label', 'alt'].forEach((attr) => {
                    const value = element.getAttribute(attr);
                    if (!value) return;
                    let originalAttrs = I18N_ORIGINAL_ATTRS.get(element);
                    if (!originalAttrs) {
                        originalAttrs = {};
                        I18N_ORIGINAL_ATTRS.set(element, originalAttrs);
                    }
                    if (!Object.prototype.hasOwnProperty.call(originalAttrs, attr)) {
                        originalAttrs[attr] = value;
                    }
                    const translatedValue = translateText(originalAttrs[attr], this.currentLanguage);
                    if (value !== translatedValue) {
                        element.setAttribute(attr, translatedValue);
                    }
                });
            };

            if (root.nodeType === Node.TEXT_NODE) {
                translateNodeText(root);
                return;
            }
            if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
            if (root.nodeType === Node.ELEMENT_NODE) {
                if (ignoredTags.has(root.tagName)) return;
                translateAttributes(root);
            }
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
                acceptNode(node) {
                    const parent = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
                    if (parent && ignoredTags.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                }
            });
            const nodes = [];
            while (walker.nextNode()) nodes.push(walker.currentNode);
            nodes.forEach((node) => {
                if (node.nodeType === Node.TEXT_NODE) translateNodeText(node);
                if (node.nodeType === Node.ELEMENT_NODE) translateAttributes(node);
            });
        },
        startLanguageObserver() {
            if (this.languageObserver) this.languageObserver.disconnect();
            this.languageObserver = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    if (mutation.type === 'childList') {
                        if (this.currentLanguage === TRADITIONAL_LANGUAGE) {
                            mutation.addedNodes.forEach((node) => this.applyLanguageToDom(node));
                        }
                    }
                    else if (mutation.type === 'attributes') {
                        if (this.currentLanguage === TRADITIONAL_LANGUAGE) {
                            this.applyLanguageToDom(mutation.target);
                        }
                    }
                    else if (mutation.type === 'characterData') {
                        const node = mutation.target;
                        const currentText = node.nodeValue;
                        if (!currentText || !currentText.trim()) return;

                        const originalText = I18N_ORIGINAL_TEXT_NODES.get(node);
                        const expectedTranslation = translateText(originalText, this.currentLanguage);
                        if (currentText === expectedTranslation) return;
                        I18N_ORIGINAL_TEXT_NODES.set(node, currentText);
                        if (this.currentLanguage === TRADITIONAL_LANGUAGE) {
                            this.applyLanguageToDom(node);
                        }
                    }
                });
            });
            this.languageObserver.observe(document.body, {
                childList: true,
                subtree: true,
                attributes: true,
                characterData: true,
                attributeFilter: ['placeholder', 'title', 'aria-label', 'alt']
            });
        },
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
        isDefaultClusterSecret(secret) {
            return ['','wenfxl666'].includes(String(secret || '').trim());
        },
        showToast(message, type = 'info') {
            const id = this.toastId++;
            this.toasts.push({ id, message: this.t(message), type });
            setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 3500);
        },

        async customConfirm(message) {
            return new Promise((resolve) => {
                this.confirmModal = { show: true, message: this.t(message), resolve };
            });
        },
        handleConfirm(result) {
            if (this.confirmModal.resolve) this.confirmModal.resolve(result);
            this.confirmModal.show = false;
        },
        
        async loadPlugins() {
            try {
                const res = await this.authFetch('/api/plugins');
                const data = await res.json();
                const items = (data && data.items) ? data.items : [];
                this.plugins = items;
                this.domainRegisterEnabled = items.some(p => (p.id === 'domain_register' || p.name === 'domain_register') && p.enabled !== false);
                if (this.domainRegisterEnabled) {
                    this.applyDomainRegisterFromConfig();
                }
            } catch (e) {
                this.domainRegisterEnabled = false;
            }
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

        formatMemoryMb(value) {
            if (value === null || value === undefined || value === '') return 'N/A';
            const numeric = Number(value);
            if (Number.isNaN(numeric)) return 'N/A';
            return `${numeric.toFixed(1)} MB`;
        },

        formatMainlandDateTime(date, options = {}) {
            return formatMainlandDateTime(date, options);
        },

        memorySafetyClass(level) {
            const classes = {
                ok: 'bg-emerald-50 text-emerald-700 border-emerald-200',
                watch: 'bg-sky-50 text-sky-700 border-sky-200',
                warning: 'bg-amber-50 text-amber-700 border-amber-200',
                critical: 'bg-rose-50 text-rose-700 border-rose-200',
                unknown: 'bg-slate-50 text-slate-600 border-slate-200'
            };
            return classes[level] || classes.unknown;
        },

        memoryRecommendationClass(level) {
            const classes = {
                ok: 'bg-emerald-50 text-emerald-700 border-emerald-200',
                watch: 'bg-sky-50 text-sky-700 border-sky-200',
                warning: 'bg-amber-50 text-amber-700 border-amber-200',
                critical: 'bg-rose-50 text-rose-700 border-rose-200',
                unknown: 'bg-slate-50 text-slate-600 border-slate-200'
            };
            return classes[level] || classes.unknown;
        },

        gitSyncStateClass(data) {
            if (!data) return 'bg-slate-50 text-slate-600 border-slate-200';
            if (!data.is_clean) return 'bg-amber-50 text-amber-700 border-amber-200';
            if ((data.behind || 0) > 0) return 'bg-sky-50 text-sky-700 border-sky-200';
            return 'bg-emerald-50 text-emerald-700 border-emerald-200';
        },

        async fetchMemoryPrediction() {
            if (!this.isLoggedIn) return;
            this.isLoadingMemoryPrediction = true;
            this.memoryPredictionError = '';
            try {
                const res = await this.authFetch('/api/system/memory_prediction');
                const data = await res.json();
                if (data.status === 'success') {
                    this.memoryPrediction = data;
                } else {
                    this.memoryPredictionError = data.message || '内存预测数据获取失败';
                }
            } catch (e) {
                this.memoryPredictionError = '内存预测 API 请求失败';
            } finally {
                this.isLoadingMemoryPrediction = false;
            }
        },

        async applyMemoryRecommendation() {
            const suggested = this.memoryPrediction?.recommendation?.suggested_config;
            if (!suggested || !this.config) {
                this.showToast('当前没有可套用的建议配置', 'warning');
                return;
            }

            this.config.enable_multi_thread_reg = !!suggested.enable_multi_thread_reg;
            this.config.reg_threads = suggested.reg_threads ?? this.config.reg_threads;
            if (!this.config.cpa_mode || typeof this.config.cpa_mode !== 'object') {
                this.config.cpa_mode = {};
            }
            if (!this.config.sub2api_mode || typeof this.config.sub2api_mode !== 'object') {
                this.config.sub2api_mode = {};
            }
            this.config.cpa_mode.threads = suggested.cpa_threads ?? this.config.cpa_mode.threads;
            this.config.sub2api_mode.threads = suggested.sub2api_threads ?? this.config.sub2api_mode.threads;
            this.config.max_log_lines = suggested.max_log_lines ?? this.config.max_log_lines;

            this.showToast('已回填建议值，正在保存配置...', 'info');
            await this.saveConfig();
        },

        async applyMemoryRecommendationAndRestart() {
            const suggested = this.memoryPrediction?.recommendation?.suggested_config;
            if (!suggested || !this.config) {
                this.showToast('当前没有可套用的建议配置', 'warning');
                return;
            }
            const confirmed = await this.customConfirm('确定要套用建议配置并立即重启项目吗？\n这适合服务器已经明显吃紧、需要尽快释放压力的场景。');
            if (!confirmed) return;
            await this.applyMemoryRecommendation();
            await this.restartSystem();
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
            this.mobileNavOpen = false;
            if (typeof document !== 'undefined') {
                document.body.classList.remove('overflow-hidden');
            }
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
            await this.loadPlugins();

            await this.fetchConfig();
            if (this.domainRegisterEnabled) {
                this.applyDomainRegisterFromConfig();
                if (this.currentTab === 'domain_register') this.ensureDomainRegisterStatusPolling();
            }
            if (this.config?.enable_mail_domain_runtime_control) {
                await this.fetchMailDomainRuntimeStats({ force: true });
            } else {
                this.mailDomainRuntimeStats = [];
                this.mailDomainRuntimeStatsError = '';
                this.mailDomainRuntimeLastFetchAt = 0;
            }
            this.initSSE();
            this.fetchAccounts();
            if (this.currentTab === 'cloud') {
			    this.fetchCloudAccounts();
			}
            if (this.currentTab === 'mailboxes') {
			    this.fetchMailboxes();
			}
            if (this.currentTab === 'team_accounts') {
                this.fetchTeamAccounts();
            }
            this.startStatsPolling();
            this.checkUpdate();
            this.fetchInventoryStats();

            // if (this.config && this.config.reg_mode === 'extension') {
            //     this.listenToExtension();
            // }
            if (this.currentTab === 'image_accounts') {
                this.fetchImageAccounts();
            }
            if (this.currentTab === 'cluster') {
                this.fetchClusterSyncTasks();
            }
            if (this.currentTab === 'proxy') {
                this.fetchClashPool();
            }
            if (this.currentTab === 'concurrency') {
                this.fetchMemoryPrediction();
                this.fetchGitSyncStatus(false);
                this.fetchCleanupStatus(false);
            }
        },
        applyMailDomainModeExclusion(changedMode = '') {
            this.applyMailDomainModeConstraints(changedMode);
        },
        applyMailDomainModeConstraints(changedMode = '') {
            if (!this.config) return;
            this.config.enable_mail_domain_grouping = normalizeBooleanLike(this.config.enable_mail_domain_grouping, false);
            this.config.mail_domain_pinpoint_burst_mode = normalizeBooleanLike(this.config.mail_domain_pinpoint_burst_mode, false);
            this.config.mail_domain_prefer_low_failure_mode = normalizeBooleanLike(this.config.mail_domain_prefer_low_failure_mode, false);
            this.config.mail_domain_group_count = Math.min(10, Math.max(1, parseInt(this.config.mail_domain_group_count, 10) || 2));
            this.config.mail_domain_group_mode = ['auto', 'manual'].includes(String(this.config.mail_domain_group_mode || '').trim().toLowerCase())
                ? String(this.config.mail_domain_group_mode || '').trim().toLowerCase()
                : 'auto';
            this.config.mail_domain_group_strategy = ['round_robin', 'exhaust_then_next'].includes(String(this.config.mail_domain_group_strategy || '').trim().toLowerCase())
                ? String(this.config.mail_domain_group_strategy || '').trim().toLowerCase()
                : 'round_robin';
            if (!Array.isArray(this.config.mail_domain_groups)) {
                this.config.mail_domain_groups = [];
            }
            this.config.mail_domain_groups = this.config.mail_domain_groups
                .slice(0, this.config.mail_domain_group_count)
                .map(item => normalizeMailDomainCsv(item).join(','));
            while (this.config.mail_domain_groups.length < this.config.mail_domain_group_count) {
                this.config.mail_domain_groups.push('');
            }
            if (changedMode === 'grouping' && this.config.enable_mail_domain_grouping) {
                this.config.mail_domain_pinpoint_burst_mode = false;
            }
            if (changedMode === 'pinpoint' && this.config.mail_domain_pinpoint_burst_mode) {
                this.config.enable_mail_domain_grouping = false;
            }
            if (this.config.enable_mail_domain_grouping && this.config.mail_domain_pinpoint_burst_mode) {
                if (changedMode === 'pinpoint') {
                    this.config.enable_mail_domain_grouping = false;
                } else {
                    this.config.mail_domain_pinpoint_burst_mode = false;
                }
            }
            if (this.config.mail_domain_pinpoint_burst_mode && this.config.mail_domain_prefer_low_failure_mode) {
                if (changedMode === 'pinpoint') {
                    this.config.mail_domain_prefer_low_failure_mode = false;
                } else if (changedMode === 'low_failure') {
                    this.config.mail_domain_pinpoint_burst_mode = false;
                } else {
                    this.config.mail_domain_prefer_low_failure_mode = false;
                }
            }
        },
        validateMailDomainGrouping() {
            if (!this.config) return '';
            this.applyMailDomainModeConstraints();
            if (!this.config.enable_mail_domain_grouping) {
                return '';
            }
            const masterDomains = normalizeMailDomainCsv(this.config.mail_domains);
            if (masterDomains.length === 0) {
                return '启用域名分组前请先填写 mail_domains';
            }
            const groupCount = Math.min(10, Math.max(1, parseInt(this.config.mail_domain_group_count, 10) || 0));
            if (groupCount < 1 || groupCount > 10) {
                return '分组数量必须在 1 到 10 之间';
            }
            if (groupCount > masterDomains.length) {
                return '分组数量不能大于有效主域名数量';
            }
            if (this.config.mail_domain_group_mode !== 'manual') {
                return '';
            }
            const masterSet = new Set(masterDomains);
            const assigned = new Set();
            for (let index = 0; index < groupCount; index += 1) {
                const domains = normalizeMailDomainCsv(this.config.mail_domain_groups[index] || '');
                if (domains.length === 0) {
                    return `第 ${index + 1} 组至少需要填写一个域名`;
                }
                for (const domain of domains) {
                    if (!masterSet.has(domain)) {
                        return `第 ${index + 1} 组存在未配置在 mail_domains 中的域名: ${domain}`;
                    }
                    if (assigned.has(domain)) {
                        return `域名 ${domain} 不能重复出现在多个分组中`;
                    }
                    assigned.add(domain);
                }
            }
            const missing = masterDomains.filter(domain => !assigned.has(domain));
            if (missing.length > 0) {
                return `手动分组未覆盖所有主域名，缺少: ${missing.join(', ')}`;
            }
            return '';
        },
        normalizeMailDomainGroupInput(index) {
            if (!this.config || !Array.isArray(this.config.mail_domain_groups)) return;
            this.config.mail_domain_groups[index] = normalizeMailDomainCsv(this.config.mail_domain_groups[index]).join(',');
        },
        async fetchMailDomainRuntimeStats(options = {}) {
            const { silent = false, force = false } = options;
            if (!this.config?.enable_mail_domain_runtime_control) {
                this.mailDomainRuntimeStats = [];
                this.mailDomainRuntimeStatsError = '';
                this.mailDomainRuntimeLastFetchAt = 0;
                this.mailDomainRuntimeFetchPromise = null;
                this.mailDomainRuntimeFetchQueued = false;
                return;
            }
            if (!force && this.mailDomainRuntimeFetchPromise) {
                this.mailDomainRuntimeFetchQueued = true;
                return this.mailDomainRuntimeFetchPromise;
            }
            const request = (async () => {
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
                } finally {
                    this.mailDomainRuntimeFetchPromise = null;
                    if (this.mailDomainRuntimeFetchQueued) {
                        this.mailDomainRuntimeFetchQueued = false;
                        this.fetchMailDomainRuntimeStats({ silent: true, force: true });
                    }
                }
            })();
            this.mailDomainRuntimeFetchPromise = request;
            return request;
        },
        toggleMailDomainRuntimePanel() {
            this.mailDomainRuntimePanelCollapsed = !this.mailDomainRuntimePanelCollapsed;
            localStorage.setItem('mail_domain_runtime_panel_collapsed', this.mailDomainRuntimePanelCollapsed ? 'true' : 'false');
            if (!this.mailDomainRuntimePanelCollapsed && this.config?.enable_mail_domain_runtime_control) {
                this.fetchMailDomainRuntimeStats({ silent: true, force: true });
            }
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
                    await this.fetchMailDomainRuntimeStats({ silent: true, force: true });
                    this.queuePollStats();
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
                    this.showToast(data.message || '已清除域名异常', 'success');
                    await this.fetchMailDomainRuntimeStats({ silent: true, force: true });
                    this.queuePollStats();
                } else {
                    this.showToast(data.message || '清除域名异常失败', 'error');
                }
            } catch (e) {
                this.showToast('清除域名异常失败，请检查网络连接', 'error');
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
                    await this.fetchMailDomainRuntimeStats({ silent: true, force: true });
                    this.queuePollStats();
                } else {
                    this.showToast(data.message || '清除域名冷却失败', 'error');
                }
            } catch (e) {
                this.showToast('清除域名冷却失败，请检查网络连接', 'error');
            }
        },
        filterLocalAccounts(status) {
            this.accountStatusFilter = status;
            this.currentPage = 1;
            this.fetchAccounts();

            const statusMap = {
                'all': '全部',
                'unpushed': '未推送',
                'pushed': '已推送',
                'credential': '有凭证',
                'image2api': 'Img凭证',
                'active': '活跃',
                'disabled': '已禁用',
                'with_token': '完整凭证',
                'reg_only': '半成品号',
                'imgsub2api': 'ImgSub2API'
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
            if (this.isMobileViewport()) {
                this.toggleMobileNav(false);
            }
            window.location.hash = tabId;
            if (tabId === 'domain_register') {
                this.refreshDomainRegisterStatus();
                this.ensureDomainRegisterStatusPolling();
                this.previewDomainRegisterDomains();
            } else if (!(this.domainRegister && this.domainRegister.running)) {
                this.stopDomainRegisterStatusPolling();
            }
			if (tabId === 'console') {
				this.queuePollStats();
			}
            if (tabId === 'accounts') {
                this.fetchAccounts();
            }
			if (tabId === 'email') {
				this.fetchConfig();
                    this.fetchMailDomainRuntimeStats({ force: true });
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
            if (tabId === 'concurrency') {
                this.fetchMemoryPrediction();
                this.fetchGitSyncStatus(false);
                this.fetchCleanupStatus(false);
            }
            if (tabId === 'team_accounts') {
                this.fetchTeamAccounts();
            }
            if (tabId === 'image_accounts') {
                this.fetchImageAccounts();
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

			const selectedObjs = this.accounts.filter(acc => this.selectedAccounts.includes(acc.email));
            const textContent = selectedObjs.map(acc => `${acc.email}----${acc.password}`).join('\n');

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
                const emailsToDelete = this.selectedAccounts;

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
        toggleAllCloud(e) {
            if (e.target.checked) {
                this.selectedCloud = this.filteredCloud.map(a => String(a.id) + '|' + a.account_type);
            } else {
                this.selectedCloud = [];
            }
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
                            const localId = localStorage.getItem('local_worker_id');
                            if (!localId) {
                                const now = new Date();
            const timeStr = formatMainlandTime(now);
                                this.showToast("🚫 启动失败：未检测到插件节点身份，请先刷新插件连接", "error");
                                this.logs.push({
                                    parsed: true,
                                    time: timeStr,
                                    level: '系统',
                                    text: '🛑 未检测到有效插件节点身份，请确认插件已连接并刷新页面后重试。',
                                    raw: `[${timeStr}] [系统] 🛑 未检测到有效插件节点身份，请确认插件已连接并刷新页面后重试。`
                                });
                                return;
                            }
                            const checkRes = await this.authFetch(`/api/ext/check_node?worker_id=${localId}`);
                            const checkData = await checkRes.json();
                            if (!checkData.online) {
                                const now = new Date();
            const timeStr = formatMainlandTime(now);
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
                        if (this.config?.grok2api?.enable) mode = 'grok2api';
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
                    this.queuePollStats();
                    await this.fetchMailDomainRuntimeStats({ force: true });
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
                await this.fetchMailDomainRuntimeStats({ force: true });
                const now = new Date();
            const timeStr = formatMainlandTime(now); // 获取如 14:30:05 格式
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
                this.queuePollStats();
            } catch (e) {
                this.showToast("停止请求发送失败", "error");
            }
        },
        async bulkPushCPA() {
            if (!this.config.cpa_mode.enable) {
              this.showToast("🚫 请先开启 CPA 巡检并填写 API", "warning"); return;
            }
            if (this.selectedAccounts.length === 0) return;
            const selectedObjs = this.accounts.filter(acc => this.selectedAccounts.includes(acc.email));
            const targetAccounts = selectedObjs.filter(acc => !acc.push_platform || !acc.push_platform.toUpperCase().includes('CPA'));

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
            const selectedObjs = this.accounts.filter(acc => this.selectedAccounts.includes(acc.email));
            const targetAccounts = selectedObjs.filter(acc => !acc.push_platform || !acc.push_platform.toUpperCase().includes('SUB2API'));

            // if (targetAccounts.length === 0) {
            //     this.showToast("⚠️ 选中的账号都已推送过 Sub2API，无需重复推送！", "warning");
            //     return;
            // }

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
            const selectedObjs = this.accounts.filter(acc => this.selectedAccounts.includes(acc.email));
            const targetAccounts = selectedObjs.filter(acc => !acc.push_platform || !acc.push_platform.toUpperCase().includes('IMAGE2API'));

            // if (targetAccounts.length === 0) {
            //     this.showToast("⚠️ 选中的账号都已推送过 Image2API，无需重复推送！", "warning");
            //     return;
            // }

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
                // if (account.push_platform && account.push_platform.toUpperCase().includes('CPA')) {
                //     this.showToast("⚠️ 该账号已在 CPA 平台，无需重复推送！", "warning"); return;
                // }
            }
            if (action === 'push_sub2api') {
                if (!this.config.sub2api_mode.enable) {
                    this.showToast("🚫 无法推送：请先配置 Sub2API 参数！", "warning"); return;
                }
                // if (account.push_platform && account.push_platform.toUpperCase().includes('SUB2API')) {
                //     this.showToast("⚠️ 该账号已在 Sub2API 平台，无需重复推送！", "warning"); return;
                // }
            }
            if (action === 'push_image2api') {
                if (!this.config.image2api_mode || !this.config.image2api_mode.enable) {
                    this.showToast("🚫 无法推送：请先配置 Image2API 参数！", "warning"); return;
                }
                // if (account.push_platform && account.push_platform.toUpperCase().includes('IMAGE2API')) {
                //     this.showToast("⚠️ 该账号已在 Image2API 平台，无需重复推送！", "warning"); return;
                // }
            }
            if (action === 'grok2api-import') {
                if (!this.config.grok2api || !this.config.grok2api.admin_password) {
                    this.showToast("🚫 无法导入：请在设置页配置 Grok2API 地址和密码", "warning"); return;
                }
            }
            this.currentTab = 'console';
            try {
                const res = await this.authFetch('/api/account/action', {
                    method: 'POST', body: JSON.stringify({ email: account.email, action: action })
                });
                const result = await res.json();
                this.showToast(result.message, result.status);
                if (action === 'push' || action === 'push_sub2api' || action === 'push_image2api' || action === 'grok2api-import') {
                    if (typeof this.fetchAccounts === 'function') this.fetchAccounts();
                    if (typeof this.fetchInventoryStats === 'function') this.fetchInventoryStats();
                }
            } catch (e) {
                this.showToast("请求异常", "error");
            }
        },
        async clearLogs() {
            this.logs = [];
            try { await this.authFetch('/api/logs/clear', { method: 'POST' }); } catch (e) {}
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
                    else if (raw && Array.isArray(raw.items)) groups = raw.items;

                    this.sub2apiGroups = groups;

                    // 清理配置里已在线上删除的分组 ID，避免推送失败
                    const validIds = new Set(
                        groups
                            .map(g => String((g && (g.id ?? g.group_id ?? g.groupId ?? g.ID)) ?? '').trim())
                            .filter(Boolean)
                    );
                    const selected = String(this.config.sub2api_mode.account_group_ids || '')
                        .split(',')
                        .map(s => s.trim())
                        .filter(Boolean);
                    const kept = selected.filter(id => validIds.has(id));
                    const dropped = selected.filter(id => !validIds.has(id));
                    if (dropped.length) {
                        this.config.sub2api_mode.account_group_ids = kept.join(',');
                        try {
                            await this.saveConfig();
                            this.showToast(
                                `已清除失效分组 ID: ${dropped.join(', ')}，并已自动保存配置`,
                                'warning'
                            );
                        } catch (e) {
                            this.showToast(
                                `已清除失效分组 ID: ${dropped.join(', ')}。自动保存失败，请手动点击保存配置`,
                                'warning'
                            );
                        }
                    }

                    if (groups.length === 0) {
                        this.showToast('未找到 Sub2API 分组，请先在 Sub2API 创建。', 'warning');
                    } else if (!dropped.length) {
                        this.showToast(`已获取 ${groups.length} 个 Sub2API 分组`, 'success');
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
                    this.queuePollStats();
                } else {
                    this.showToast(data.message || '启动测活失败', 'error');
                }
            } catch (err) {
                this.showToast('网络请求异常', 'error');
            }
        },
        async checkUpdate(isManual = false) {
            if (this.appVersion === '检查中...' || !this.appVersion) return;
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
            return formatMainlandDateTime(d);
        },
async exportSub2Api() {
            if (this.selectedAccounts.length === 0) {
                this.showToast('请先勾选账号', 'warning');
                return;
            }
            try {
                const emailsToExport = this.selectedAccounts;

                const response = await this.authFetch('/api/accounts/export_sub2api', {
                    method: 'POST',
                    body: JSON.stringify({ emails: emailsToExport })
                });
                const res = await response.json();

                if (res.status === 'success') {
                    const accounts = res.data.accounts;
                    const timestamp = Math.floor(Date.now() / 1000);

                    // 无论数量多少，直接将返回的数据(包含所有选中的accounts)作为一个JSON文件下载
                    const content = JSON.stringify(res.data, null, 2);
                    const blob = new Blob([content], { type: 'application/json' });
                    const url = window.URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    // 文件名带上数量和时间戳
                    link.download = `sub2api_批量导出_${accounts.length}个_${timestamp}.json`;
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                    window.URL.revokeObjectURL(url);

                    this.showToast(`🎉 成功导出 ${accounts.length} 个账号到单个 JSON 文件`, 'success');

                    this.selectedAccounts = [];
                } else {
                    this.showToast(res.message || '导出失败', 'error');
                }
            } catch (error) {
                console.error('导出异常:', error);
                // 已经不再使用 JSZip，修改一下错误提示
                this.showToast('导出异常，请检查网络或刷新页面', 'error');
            }
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
            const timeStr = formatMainlandTime(now);

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
            const timeStr = formatMainlandTime(now);
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
            const timeStr = formatMainlandTime(now);

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
            const timeStr = formatMainlandTime(now);
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

                        const timeStr = formatMainlandTime(new Date());
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
        async changeRegProvider(provider) {
            if (!this.config) return;
            const next = (provider === 'grok') ? 'grok' : 'openai';
            this.config.reg_provider = next;
            if (!this.config.grok || typeof this.config.grok !== 'object') {
                this.config.grok = {
                            oauth_timeout: 180
                        };
            } else {
                if (this.config.grok.oauth_timeout === undefined) this.config.grok.oauth_timeout = 180;
            }
            await this.saveConfig();
            this.showToast(`注册平台已切换为: ${next === 'grok' ? 'Grok/xAI' : 'OpenAI(默认)'}`, 'info');
        },
        async changeRegMode(mode) {
            if (!this.config) return;
            this.config.reg_mode = mode;

            await this.saveConfig();
            this.showToast(`模式已切换为: ${mode === 'email' ? '邮箱注册' : '手机号注册'}`, 'info');

            // if (mode === 'extension') {
            //     this.listenToExtension();
            // } else {
            //     if (this._extDetectionTimer) {
            //         clearInterval(this._extDetectionTimer);
            //         this._extDetectionTimer = null;
            //     }
            //     this.isExtConnected = false;
            //     window.postMessage({ type: "CMD_STOP_WORKER" }, "*");
            //     console.log("🛑 [总控] 已进入协议模式，切断插件链路。");
            // }
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
        async handleOverspeedToggle(event) {
            const isTurningOn = event.target.checked;
            if (isTurningOn) {
                const hasCookie = this.teamAccounts && this.teamAccounts.some(team => team.cookies && team.cookies.trim() !== '');

                if (!hasCookie) {
                    this.showToast('⛔ 开启失败：当前账号库中未检测到完整的 Cookie 数据！', 'error');
                    this.showToast('请先按 access_token----cookies 格式导入数据。', 'warning');
                    event.target.checked = false;
                    this.config.team_mode.overspeed = false;
                    return;
                }
                this.config.team_mode.enable = true;
            }
            this.config.team_mode.overspeed = isTurningOn;
            await this.saveConfig();
            this.showToast(`🏎️ 超速妙模式已${isTurningOn ? '开启' : '关闭'}`, 'success');
            this.showToast(`超速妙模式必须使用域名邮箱，使用微软、谷歌等邮箱请不要开启`, 'success');
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

    }
}).mount('#app');

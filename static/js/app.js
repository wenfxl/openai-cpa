const { createApp } = Vue;

createApp({
    data() {
        return {
            isLoggedIn: !!localStorage.getItem('auth_token'),
            loginPassword: '',
            currentTab: 'console',
			showAccountsPlaintext: false,
            isRunning: false,
            tabs: [
                { id: 'console', name: '运行主页', icon: '💻' },
                { id: 'accounts', name: '账号库存', icon: '📦' },
                { id: 'email', name: '邮箱配置', icon: '📧' },
                { id: 'sms', name: '手机接码', icon: '📱' },
				// { id: 'cf_routes', name: 'CF 路由', icon: '🌍' },
                { id: 'proxy', name: '网络代理', icon: '🌐' },
                { id: 'relay', name: '中转管仓', icon: '☁️' },
                { id: 'concurrency', name: '并发与系统', icon: '⚙️' }
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
            config: null,
            blacklistStr: "",
            warpListStr: "",
            accounts: [],
            selectedAccounts: [],
			currentPage: 1,
            pageSize: 10,
            totalAccounts: 0,
            evtSource: null,
            stats: {
                success: 0, failed: 0, retries: 0, total: 0, target: 0,
                success_rate: '0.0%', elapsed: '0.0s', avg_time: '0.0s', progress_pct: '0%',
                mode: '未启动'
            },
            statsTimer: null,

            showPwd: {
                login: false, web: false, cf: false, imap: false, 
                free_token: false, free_pass: false,
                cm: false, mc: false, clash: false, cpa: false, sub2api: false,
                cf_key: false, cf_modal_key: false 
            },

            toasts: [],
            toastId: 0,
            confirmModal: { show: false, message: '', resolve: null }
        };
    },
    mounted() {
        if (this.isLoggedIn) {
            this.initApp();
        }
    },
    beforeUnmount() {
        if(this.statsTimer) clearInterval(this.statsTimer);
    },
	computed: {
        totalPages() {
            return Math.ceil(this.totalAccounts / this.pageSize) || 1;
        }
    },
    methods: {
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
                this.logout();
                this.showToast("登录状态过期，请重新登录！", "warning");
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
        initApp() {
            this.fetchConfig();
            this.fetchAccounts();
            this.initSSE();
            this.startStatsPolling();
        },
        startStatsPolling() {
            if(this.statsTimer) clearInterval(this.statsTimer);
            this.pollStats();
            this.statsTimer = setInterval(this.pollStats, 1000);
        },
        async pollStats() {
            if(!this.isLoggedIn) return;
            try {
                const res = await this.authFetch('/api/stats');
                const data = await res.json();
                this.stats = data;
                this.isRunning = data.is_running;
            } catch(e){}
        },
        async fetchConfig() {
            try {
                const res = await this.authFetch('/api/config');
                this.config = await res.json();
				
				if (!this.config.sub_domain_level) {
                    this.config.sub_domain_level = 1;
                }
                if(this.config.clash_proxy_pool && Array.isArray(this.config.clash_proxy_pool.blacklist)) {
                    this.blacklistStr = this.config.clash_proxy_pool.blacklist.join('\n');
                }
                if(Array.isArray(this.config.warp_proxy_list)) {
                    this.warpListStr = this.config.warp_proxy_list.join('\n');
                }
            } catch (e) {}
        },
        async saveConfig() {
            try {
                if(this.config.clash_proxy_pool) {
                    this.config.clash_proxy_pool.blacklist = this.blacklistStr.split('\n').map(s => s.trim()).filter(s => s);
                }
                this.config.warp_proxy_list = this.warpListStr.split('\n').map(s => s.trim()).filter(s => s);
                const res = await this.authFetch('/api/config', {
                    method: 'POST', body: JSON.stringify(this.config)
                });
                const data = await res.json();
                if(data.status === 'success') {
                    this.showToast(data.message, "success");
                    this.pollStats();
                } else { this.showToast("保存失败：" + data.message, "error"); }
            } catch (e) { this.showToast("保存失败网络异常", "error"); }
        },
		async fetchAccounts(isManual = false) {
            if (isManual) {
                this.currentPage = 1;
            }
            try {
                const res = await this.authFetch(`/api/accounts?page=${this.currentPage}&page_size=${this.pageSize}`);
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
            if (newPage < 1 || newPage > this.totalPages) return;
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
            this.currentTab = tabId;
			if (tabId === 'console') {
				this.pollStats(); 
			}
            if (tabId === 'accounts') {
                this.fetchAccounts();
            }
			if (tabId === 'email') {
				this.fetchConfig();
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
                    result.data.forEach((tokenObj, index) => {
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
                    
                    this.showToast(`🎉 成功触发 ${result.data.length} 个独立 Token 文件的下载！`, "success");
                    this.selectedAccounts = [];
                } else {
                    this.showToast(result.message, "warning");
                }
            } catch (e) {
                this.showToast("导出请求失败，请检查网络", "error");
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
            if (event.target.checked) this.selectedAccounts = [...this.accounts];
            else this.selectedAccounts = [];
        },

		async toggleSystem() {
			if (this.isRunning) {
				await this.stopTask();
			} else {
				let mode = 'normal';
				if (this.config?.cpa_mode?.enable) mode = 'cpa';
				if (this.config?.sub2api_mode?.enable) mode = 'sub2api';
				await this.startTask(mode);
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
            const confirmed = await this.customConfirm(`确定推送到 CPA？`);
            if (!confirmed) return;
            this.currentTab = 'console';
            for (let i = 0; i < this.selectedAccounts.length; i++) {
                const acc = this.selectedAccounts[i];
                try {
                    await this.authFetch('/api/account/action', {
                        method: 'POST', body: JSON.stringify({ email: acc.email, action: 'push' })
                    });
                } catch (e) {}
                await new Promise(r => setTimeout(r, 500));
            }
            this.showToast(`批量推送完毕！`, "success");
            this.selectedAccounts = []; 
        },
		async bulkPushSub2API() {
            if (!this.config.sub2api_mode.enable) {
                this.showToast("🚫 请先开启 Sub2API 模式并填写参数", "warning"); return;
            }
            if (this.selectedAccounts.length === 0) return;
            const confirmed = await this.customConfirm(`确定推送到 Sub2API？`);
            if (!confirmed) return;
            this.currentTab = 'console';
            for (let i = 0; i < this.selectedAccounts.length; i++) {
                const acc = this.selectedAccounts[i];
                try {
                    await this.authFetch('/api/account/action', {
                        method: 'POST', body: JSON.stringify({ email: acc.email, action: 'push_sub2api' })
                    });
                } catch (e) {}
                await new Promise(r => setTimeout(r, 500));
            }
            this.showToast(`批量推送完毕！`, "success");
            this.selectedAccounts = []; 
        },
        async triggerAccountAction(account, action) {
            if (action === 'push' && !this.config.cpa_mode.enable) {
                this.showToast("🚫 无法推送：请先配置 CPA 参数！", "warning"); return;
            }
            try {
                const res = await this.authFetch('/api/account/action', {
                    method: 'POST', body: JSON.stringify({ email: account.email, action: action })
                });
                const result = await res.json();
                this.showToast(result.message, result.status);
            } catch (e) {}
        },
        async clearLogs() {
            this.logs = []; 
            try { await this.authFetch('/api/logs/clear', { method: 'POST' }); } catch (e) {}
        },
		initSSE() {
            if (this.evtSource) {
                this.evtSource.close();
            }

            const token = localStorage.getItem('auth_token');
            const url = `/api/logs/stream?token=${token}`;
            
            this.evtSource = new EventSource(url);
            this.evtSource.onmessage = (event) => {
                let rawText = event.data;
                rawText = rawText.trim();
                if (!rawText) return;
                
                let logObj = { parsed: false, raw: rawText };
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
                
                this.logs.push(logObj);
                if (this.logs.length > 2000) {
                    this.logs.splice(0, this.logs.length - 2000);
                }

                this.$nextTick(() => {
                    const container = document.getElementById('terminal-container');
                    if (container) {
                        const isScrolledToBottom = container.scrollHeight - container.clientHeight <= container.scrollTop + 50;
                        if (isScrolledToBottom || this.logs.length < 50) {
                            container.scrollTop = container.scrollHeight;
                        }
                    }
                });
            };

            this.evtSource.onerror = (event) => {
                console.error("SSE 连接异常，浏览器将自动尝试重连...", event);
                if (!this.isLoggedIn) {
                    this.evtSource.close();
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
                const res = await this.authFetch('/api/sms/balance'); // 需后端配合增加此接口
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
        }
    }
}).mount('#app');
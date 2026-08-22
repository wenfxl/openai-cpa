const ACCOUNT_METHODS = {
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
    async fetchImageAccounts(isManual = false) {
                if (isManual) this.imagePage = 1;
                let url = `/api/image_accounts?page=${this.imagePage}&page_size=${this.imagePageSize}`;
                if (this.searchImageAccounts) {
                    url += `&search=${encodeURIComponent(this.searchImageAccounts)}`;
                }
                try {
                    const res = await this.authFetch(url);
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.imageAccounts = data.data;
                        this.totalImageAccounts = data.total;
                        if (isManual) this.showToast("半成品库已刷新", "success");
                    }
                } catch (e) {
                    this.showToast("半成品库加载失败", "error");
                }
            },
    changeImagePage(newPage) {
                this.imagePage = newPage;
                this.fetchImageAccounts();
            },
    changeImagePageSize() {
                this.imagePage = 1;
                this.fetchImageAccounts();
            },
    toggleAllImageAccounts(e) {
                if (e.target.checked) {
                    this.selectedImageAccounts = this.imageAccounts.map(a => a.email);
                } else {
                    this.selectedImageAccounts = [];
                }
            },
    async deleteSelectedImageAccounts() {
                if (this.selectedImageAccounts.length === 0) return;
                if (!confirm(`确定要删除选中的 ${this.selectedImageAccounts.length} 个半成品账号吗？`)) return;
    
                try {
                    const res = await this.authFetch('/api/accounts/delete', {
                        method: 'POST',
                        body: JSON.stringify({ emails: this.selectedImageAccounts })
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.showToast("删除成功", "success");
                        this.selectedImageAccounts = [];
                        this.fetchImageAccounts();
                    } else {
                        this.showToast("删除失败", "error");
                    }
                } catch (e) {
                    this.showToast("删除请求失败", "error");
                }
            },
    exportImageAccounts() {
                if (this.selectedImageAccounts.length === 0 && this.imageAccounts.length === 0) {
                    this.showToast("没有可导出的数据", "warning");
                    return;
                }
                const targetList = this.selectedImageAccounts.length > 0
                    ? this.imageAccounts.filter(a => this.selectedImageAccounts.includes(a.email))
                    : this.imageAccounts;
    
                const text = targetList.map(a => `${a.email}----${a.password}`).join('\n');
                const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `IMG_Accounts_${new Date().getTime()}.txt`;
                a.click();
                URL.revokeObjectURL(url);
                this.selectedImageAccounts = [];
            },
    async triggerOAuthUpgrade(emailsParam) {
                this.currentTab = 'console';
                try {
                    const res = await this.authFetch('/api/image_accounts/upgrade_oauth', {
                        method: 'POST',
                        body: JSON.stringify({ emails: emailsParam })
                    });
                    const data = await res.json();
                    if(data.status === 'success') {
                        this.showToast(`任务已提交: 开始处理 ${data.count} 个账号`, "success");
                        this.fetchImageAccounts();
                    } else {
                        this.showToast(data.message || "请求失败", "error");
                    }
                } catch(e) {
                    this.showToast("网络请求异常", "error");
                }
            },
    async upgradeSingleImageAccount(acc) {
                acc._upgrading = true;
                await this.triggerOAuthUpgrade([acc.email]);
                setTimeout(() => { acc._upgrading = false; this.fetchImageAccounts(); }, 2000);
            },
    async upgradeSelectedImageAccounts() {
                if(this.selectedImageAccounts.length === 0) return;
                this.isUpgradingSelected = true;
                await this.triggerOAuthUpgrade(this.selectedImageAccounts);
                this.isUpgradingSelected = false;
                this.selectedImageAccounts = [];
            },
    async upgradeAllImageAccounts() {
                if(!confirm("确定要对所有 IMG 账号进行 OAuth 凭证提取吗？该操作将占用所有并发线程。")) return;
                this.isUpgradingOAuthAll = true;
                await this.triggerOAuthUpgrade("ALL");
                this.isUpgradingOAuthAll = false;
            },
    async bulkFetchSub2ApiUsage() {
                const sub2apiIds = this.selectedCloud
                    .filter(val => val.endsWith('|sub2api'))
                    .map(val => val.split('|')[0]);
    
                if (sub2apiIds.length === 0) {
                    this.showToast('当前选中的账号中没有 Sub2API 类型的账号，无法获取利用率。', 'warning');
                    return;
                }
    
                this.isCloudActionLoading = true;
                try {
                    const response = await this.authFetch('/api/cloud/sub2api/usage/bulk', {
                        method: 'POST',
                        body: JSON.stringify({ account_ids: sub2apiIds })
                    });
    
                    const res = await response.json();
    
                    if (res.status === 'success') {
                        const usageData = res.data;
                        this.cloudAccounts.forEach(acc => {
                            if (acc.account_type === 'sub2api' && usageData[acc.id]) {
                                if (!acc.details) {
                                    acc.details = {};
                                }
                                acc.details.window_stats = usageData[acc.id];
                            }
                        });
                        this.showToast(`成功刷新了 ${sub2apiIds.length} 个账号的额度利用率！`, 'success');
                    } else {
                        this.showToast(res.message || '获取利用率失败', 'error');
                    }
                } catch (e) {
                    this.showToast('网络错误: ' + e.message, 'error');
                } finally {
                    this.isCloudActionLoading = false;
                }
            },
    async exportSelectedAccounts() {
                if (this.selectedAccounts.length === 0) {
                    this.showToast("请先勾选需要导出的账号", "warning");
                    return;
                }
    
                const emails = this.selectedAccounts;
    
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
};

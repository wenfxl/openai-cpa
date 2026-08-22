const MAILBOX_METHODS = {
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
                if (event.target.checked) this.selectedMailboxes = this.filteredMailboxes.map(m => m.email);
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
                const selectedObjs = this.mailboxes.filter(m => this.selectedMailboxes.includes(m.email));
                const idsToDelete = selectedObjs.map(m => m.id || m.email);
                try {
                    const res = await this.authFetch('/api/mailboxes/delete', {
                        method: 'POST',
                        body: JSON.stringify({ids: idsToDelete})
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.showToast("删除成功", "success");
                        this.selectedMailboxes = []
                        this.fetchMailboxes();
                    } else {
                        this.showToast("删除失败: " + data.message, "error");
                    }
                } catch (e) {
                    this.showToast("请求异常", "error");
                }
            },
    openOutlookAuthModal(mailbox)
            {
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
                const selectedObjs = this.mailboxes.filter(m => this.selectedMailboxes.includes(m.email));
                const textContent = selectedObjs
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
    
                const emailsToRecover = this.selectedMailboxes;
    
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
};

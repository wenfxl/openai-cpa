const CLOUDFLARE_METHODS = {
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
    async handleCFDeleteHosting() {
                const targetDomains = String(this.cfTools.deleteDomains || '').trim();
                if (!targetDomains) return this.showToast('请先填写需要删除的 CF 托管域名！', 'warning');
                if (!this.config.cf_api_email || !this.config.cf_api_key) return this.showToast('请填写 CF 账号邮箱和 API Key！', 'warning');
    
                const confirmed = await this.customConfirm(`⚠️ 危险操作：\n\n即将删除你在下方填写的 CF 托管域名及其 DNS / 邮件路由配置，确定继续吗？`);
                if (!confirmed) return;
    
                this.cfTools.isDeletingHosting = true;
                this.showToast('正在批量删除 CF 托管域名...', 'info');
                this.currentTab = 'console';
                try {
                    const res = await this.authFetch('/api/cloudflare/delete_zones', {
                        method: 'POST',
                        body: JSON.stringify({ domains: targetDomains, api_email: this.config.cf_api_email, api_key: this.config.cf_api_key })
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.cfTools.results = data.data || [];
                        this.showToast('✅ 托管域名删除完成', 'success');
                        this.currentTab = 'email';
                    } else {
                        this.showToast(data.message || '删除失败', 'error');
                    }
                } catch (e) {
                    this.showToast('请求异常', 'error');
                } finally {
                    this.cfTools.isDeletingHosting = false;
                }
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
};

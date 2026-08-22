const PROXY_METHODS = {
    async fetchClashPool() {
                this.clashPool.loading = true;
                try {
                    const res = await this.authFetch('/api/clash/status');
                    const d = await res.json();
                    if (d.status === 'success') {
                        this.clashPool.instances = d.data.instances;
                        this.clashPool.groups = d.data.groups;
                        this.clashPool.subscriptions = (d.data.subscriptions?.items || []).map((item) => ({
                            ...item,
                            raw_url: item.url,
                            url: this.resolveClashSubscriptionUrl(item.url)
                        }));
                        this.clashPool.mode = d.data.mode || '';
                        this.clashPool.message = d.data.message || '';
                        if (this.clashPool.instances.length > 0 && !this.clashPool.isDeploying) {
                            this.clashPool.count = this.clashPool.instances.length;
                        }
                        const currentSub = this.clashPool.subscriptions.find(item => item.selected) || this.clashPool.subscriptions[0] || null;
                        this.clashPool.subUrl = currentSub?.url || '';
                        const activeExists = this.clashPool.groups.some(group => group.name === this.clashPool.activeGroupName);
                        if (this.clashPool.groups.length > 0) {
                            if (!activeExists) {
                                this.primeActiveClashGroup(this.clashPool.groups[0]);
                            }
                        } else {
                            this.clashPool.activeGroupName = '';
                        }
                        if (!activeExists) {
                            this.clashPool.view = 'groups';
                            this.clashPool.delayResults = {};
                        }
                    }
                } catch (e) {}
                this.clashPool.loading = false;
            },
    primeActiveClashGroup(group) {
                if (!group || !group.name) return;
                this.clashPool.activeGroupName = group.name;
                this.fillProxyGroup(group.name);
                if (!this.clashPool.nodeSelections[group.name]) {
                    this.clashPool.nodeSelections[group.name] = group.current || (Array.isArray(group.nodes) ? group.nodes[0] : '') || '';
                }
            },
    setActiveClashGroup(group) {
                this.primeActiveClashGroup(group);
                this.clashPool.view = 'nodes';
            },
    backToClashGroups() {
                this.clashPool.view = 'groups';
            },
    getClashDelayRows(group) {
                if (!group || !Array.isArray(group.nodes)) return [];
                const resultMap = this.clashPool.delayResults[group.name]?.results || {};
                const healthyNodes = Array.isArray(group.healthy_nodes) ? group.healthy_nodes.filter(Boolean) : [];
                const sourceNodes = healthyNodes.length ? healthyNodes : group.nodes;
                const rows = sourceNodes.map((nodeName) => ({
                    nodeName,
                    result: resultMap[nodeName] || null,
                    isCurrent: group.current === nodeName,
                    isSelected: this.clashPool.nodeSelections[group.name] === nodeName
                }));
                rows.sort((a, b) => {
                    const aOk = a.result?.status === 'ok';
                    const bOk = b.result?.status === 'ok';
                    if (aOk && bOk) return (a.result.delay || Number.MAX_SAFE_INTEGER) - (b.result.delay || Number.MAX_SAFE_INTEGER);
                    if (aOk) return -1;
                    if (bOk) return 1;
                    if (a.isCurrent) return -1;
                    if (b.isCurrent) return 1;
                    return a.nodeName.localeCompare(b.nodeName, 'zh-CN');
                });
                return rows;
            },
    getClashHealthyCount(group) {
                return Array.isArray(group?.healthy_nodes) ? group.healthy_nodes.filter(Boolean).length : 0;
            },
    async handleClashDeploy() {
                this.showToast('正在调整实例规模...', 'info');
                this.clashPool.isDeploying = true;
                try {
                    const res = await this.authFetch('/api/clash/deploy', {
                        method: 'POST',
                        body: JSON.stringify({ count: this.clashPool.count })
                    });
                    const d = await res.json();
                    this.showToast(d.message, d.status);
                    if (d.status === 'success') {
                        setTimeout(() => {
                            this.fetchClashPool();
                            this.clashPool.isDeploying = false;
                        }, 5000);
                    } else {
                        this.clashPool.isDeploying = false;
                    }
                } catch (e) {
                    this.showToast('网络错误', 'error');
                    this.clashPool.isDeploying = false;
                }
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
                    if (d.status === 'success') {
                        setTimeout(() => {
                            this.fetchClashPool();
                        }, 5000);
                    }
                } catch (e) { this.showToast('网络错误', 'error'); }
                this.clashPool.loading = false;
            },
    fillProxyGroup(name) {
                if (this.config && this.config.clash_proxy_pool) {
                    this.config.clash_proxy_pool.group_name = name;
                    this.showToast(`已自动填入策略组：${name}`, 'success');
                }
            },
    async handleClashRuntime(action) {
                this.clashPool.runtimeActionLoading = true;
                try {
                    const res = await this.authFetch('/api/clash/runtime', {
                        method: 'POST',
                        body: JSON.stringify({ action })
                    });
                    const data = await res.json();
                    this.showToast(data.message || 'Clash 运行控制已执行', data.status);
                    if (data.status === 'success') {
                        setTimeout(() => this.fetchClashPool(), 1200);
                    }
                } catch (e) {
                    this.showToast('Clash 运行控制请求失败', 'error');
                } finally {
                    this.clashPool.runtimeActionLoading = false;
                }
            },
    async addClashSubscription() {
                if (!this.clashPool.newSubscriptionUrl) {
                    this.showToast('请输入订阅链接', 'warning');
                    return;
                }
                this.clashPool.subscriptionActionLoading = true;
                try {
                    const normalizedUrl = this.resolveClashSubscriptionUrl(this.clashPool.newSubscriptionUrl);
                    const res = await this.authFetch('/api/clash/subscriptions/add', {
                        method: 'POST',
                        body: JSON.stringify({
                            name: this.clashPool.newSubscriptionName,
                            url: normalizedUrl,
                            make_selected: this.clashPool.makeSelectedSubscription
                        })
                    });
                    const data = await res.json();
                    this.showToast(data.message || '订阅已保存', data.status);
                    if (data.status === 'success') {
                        this.clashPool.newSubscriptionName = '';
                        this.clashPool.newSubscriptionUrl = '';
                        await this.fetchClashPool();
                    }
                } catch (e) {
                    this.showToast('保存订阅失败', 'error');
                } finally {
                    this.clashPool.subscriptionActionLoading = false;
                }
            },
    async selectClashSubscription(subscriptionId) {
                this.clashPool.subscriptionActionLoading = true;
                try {
                    this.clashPool.activeGroupName = '';
                    this.clashPool.view = 'groups';
                    this.clashPool.delayResults = {};
                    const subscription = this.clashPool.subscriptions.find(item => item.id === subscriptionId);
                    const res = await this.authFetch('/api/clash/subscriptions/select', {
                        method: 'POST',
                        body: JSON.stringify({
                            subscription_id: subscriptionId,
                            target: this.clashPool.target,
                            resolved_url: subscription?.url || ''
                        })
                    });
                    const data = await res.json();
                    const label = this.formatClashSubscriptionLabel(subscription);
                    this.showToast(
                        data.status === 'success'
                            ? `已切换订阅：${label}`
                            : (data.message || `切换订阅失败：${label}`),
                        data.status
                    );
                    if (data.status === 'success') {
                        await this.fetchClashPool();
                    }
                } catch (e) {
                    this.showToast('切换订阅失败', 'error');
                } finally {
                    this.clashPool.subscriptionActionLoading = false;
                }
            },
    async deleteClashSubscription(subscription) {
                const confirmed = await this.customConfirm(`确定删除订阅 [${subscription?.name || subscription?.id || '未命名'}] 吗？`);
                if (!confirmed) return;
                this.clashPool.subscriptionActionLoading = true;
                try {
                    const res = await this.authFetch('/api/clash/subscriptions/delete', {
                        method: 'POST',
                        body: JSON.stringify({ subscription_id: subscription.id })
                    });
                    const data = await res.json();
                    this.showToast(data.message || '订阅已删除', data.status);
                    if (data.status === 'success') {
                        await this.fetchClashPool();
                    }
                } catch (e) {
                    this.showToast('删除订阅失败', 'error');
                } finally {
                    this.clashPool.subscriptionActionLoading = false;
                }
            },
    async testClashGroup(groupName) {
                if (!groupName) return;
                this.clashPool.delayLoading = true;
                try {
                    const res = await this.authFetch('/api/clash/delay', {
                        method: 'POST',
                        body: JSON.stringify({ group_name: groupName, target: this.clashPool.target })
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.clashPool.delayResults[groupName] = data.data;
                        const group = this.clashPool.groups.find(item => item.name === groupName);
                        if (group && Array.isArray(data.data?.healthy_nodes)) {
                            group.healthy_nodes = data.data.healthy_nodes;
                        }
                        this.showToast(data.message || '延迟测试完成', 'success');
                    } else {
                        this.showToast(data.message || '延迟测试失败', 'error');
                    }
                } catch (e) {
                    this.showToast('延迟测试请求失败', 'error');
                } finally {
                    this.clashPool.delayLoading = false;
                }
            },
    async clearClashHealthyNodes(groupName) {
                if (!groupName) return;
                const confirmed = await this.customConfirm(`确定清空策略组 [${groupName}] 的有效节点池吗？清空后会重新显示全部节点。`);
                if (!confirmed) return;
                this.clashPool.delayLoading = true;
                try {
                    const res = await this.authFetch('/api/clash/tested_nodes/clear', {
                        method: 'POST',
                        body: JSON.stringify({ group_name: groupName })
                    });
                    const data = await res.json();
                    this.showToast(data.message || '有效节点池已清空', data.status);
                    if (data.status === 'success') {
                        const group = this.clashPool.groups.find(item => item.name === groupName);
                        if (group) group.healthy_nodes = [];
                        if (this.clashPool.delayResults[groupName]) {
                            this.clashPool.delayResults[groupName].healthy_nodes = [];
                        }
                    }
                } catch (e) {
                    this.showToast('清空有效节点池失败', 'error');
                } finally {
                    this.clashPool.delayLoading = false;
                }
            },
    async switchClashGroupNode(groupName) {
                const proxyName = this.clashPool.nodeSelections[groupName];
                if (!groupName || !proxyName) {
                    this.showToast('请先选择策略组和目标节点', 'warning');
                    return;
                }
                try {
                    const res = await this.authFetch('/api/clash/switch', {
                        method: 'POST',
                        body: JSON.stringify({ group_name: groupName, proxy_name: proxyName, target: this.clashPool.target })
                    });
                    const data = await res.json();
                    this.showToast(data.message || '节点已切换', data.status);
                    if (data.status === 'success') {
                        await this.fetchClashPool();
                    }
                } catch (e) {
                    this.showToast('切换节点失败', 'error');
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
                    const emails = this.selectedAccounts;
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
};

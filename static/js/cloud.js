const CLOUD_METHODS = {
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
                    this.rawCloudAccounts = [];
                    this.cloudAccounts = [];
                    this.cloudTotal = 0;
                    this.inventoryStats.cloud = {
                        total: 0, enabled: 0, cpa: 0, cpa_active: 0, cpa_disabled: 0, sub2api: 0, sub2api_active: 0, sub2api_disabled: 0, image2api: 0, image2api_active: 0, image2api_disabled: 0, grok2api: 0, grok2api_active: 0, grok2api_disabled: 0
                    };
                    this.cloudFetchState = { loading: false, currentType: '', completed: 0, total: 0, message: '未选择平台' };
                    return;
                }
                const typeQueue = [...this.cloudFilters];
                this.cloudFetchState = {
                    loading: true,
                    currentType: typeQueue[0] || '',
                    completed: 0,
                    total: typeQueue.length,
                    message: '准备分批获取云端库存...'
                };
                try {
                    const combined = [];
                    for (let index = 0; index < typeQueue.length; index += 1) {
                        const type = typeQueue[index];
                        this.cloudFetchState.currentType = type;
                        this.cloudFetchState.message = `正在获取 ${type.toUpperCase()} 数据...`;
                        const url = `/api/cloud/accounts?types=${type}&status_filter=all&page=1&page_size=2000`;
                        const res = await this.authFetch(url);
                        const data = await res.json();
                        if (data.status !== 'success') {
                            throw new Error(data.message || `${type} 拉取失败`);
                        }
                        combined.push(...(data.data || []));
                        this.cloudFetchState.completed = index + 1;
                    }
                    this.rawCloudAccounts = combined.map(acc => ({
                        ...acc,
                        last_check: this.localCheckTimes[`${acc.account_type}|${acc.id}`] || this.localCheckTimes[acc.id] || acc.last_check || '-',
                        details: acc.account_type === 'image2api' ? (acc.details || {}) : (this.localCloudDetails[`${acc.account_type}|${acc.id}`] || this.localCloudDetails[acc.id] || acc.details || {}),
                        _loading: null
                    }));
                    this.inventoryStats.cloud = this.computeCloudStats(this.rawCloudAccounts);
                    this.applyCloudAccountView();
                    if (typeof this.fetchInventoryStats === 'function') {
                        this.fetchInventoryStats();
                    }
                    this.cloudFetchState.message = `已完成 ${typeQueue.length} 个平台的分批获取`;
                } catch (e) {
                    console.error(e);
                    if (this.isLoggedIn && e.message !== "Unauthorized") {
                        this.showToast("获取云端数据失败", "error");
                        this.inventoryStats.cloud = {
                            total: 0, enabled: 0, cpa: 0, cpa_active: 0, cpa_disabled: 0, sub2api: 0, sub2api_active: 0, sub2api_disabled: 0, image2api: 0, image2api_active: 0, image2api_disabled: 0, grok2api: 0, grok2api_active: 0, grok2api_disabled: 0
                        };
                        this.rawCloudAccounts = [];
                        this.cloudAccounts = [];
                        this.cloudTotal = 0;
                        this.cloudFetchState.message = '获取失败';
                    }
                } finally {
                    this.cloudFetchState.loading = false;
                }
            },
    computeCloudStats(items) {
                const rows = Array.isArray(items) ? items : [];
                const isActive = (item) => item.status === 'active';
                const isType = (item, type) => item.account_type === type;
                return {
                    total: rows.length,
                    enabled: rows.filter(isActive).length,
                    cpa: rows.filter(item => isType(item, 'cpa')).length,
                    cpa_active: rows.filter(item => isType(item, 'cpa') && isActive(item)).length,
                    cpa_disabled: rows.filter(item => isType(item, 'cpa') && !isActive(item)).length,
                    sub2api: rows.filter(item => isType(item, 'sub2api')).length,
                    sub2api_active: rows.filter(item => isType(item, 'sub2api') && isActive(item)).length,
                    sub2api_disabled: rows.filter(item => isType(item, 'sub2api') && !isActive(item)).length,
                    image2api: rows.filter(item => isType(item, 'image2api')).length,
                    image2api_active: rows.filter(item => isType(item, 'image2api') && isActive(item)).length,
                    image2api_disabled: rows.filter(item => isType(item, 'image2api') && !isActive(item)).length,
                    grok2api: rows.filter(item => isType(item, 'grok2api')).length,
                    grok2api_active: rows.filter(item => isType(item, 'grok2api') && isActive(item)).length,
                    grok2api_disabled: rows.filter(item => isType(item, 'grok2api') && !isActive(item)).length
                };
            },
    applyCloudAccountView() {
                let rows = [...this.rawCloudAccounts];
                if (this.cloudStatusFilter !== 'all') {
                    rows = rows.filter(item => item.status === this.cloudStatusFilter);
                }
                if (this.searchCloud) {
                    const term = this.searchCloud.toLowerCase();
                    rows = rows.filter(item => {
                        const credential = String(item.credential || '').toLowerCase();
                        const id = String(item.id || '').toLowerCase();
                        return credential.includes(term) || id.includes(term);
                    });
                }
                this.cloudTotal = rows.length;
                const totalPages = Math.max(1, Math.ceil(this.cloudTotal / this.cloudPageSize) || 1);
                if (this.cloudPage > totalPages) {
                    this.cloudPage = totalPages;
                }
                const startIdx = (this.cloudPage - 1) * this.cloudPageSize;
                this.cloudAccounts = rows.slice(startIdx, startIdx + this.cloudPageSize);
                this.selectedCloud = [];
            },
    async singleCloudAction(acc, action) {
                if (action === 'delete' && !confirm('⚠️ 危险操作：确认在远端彻底删除该账号吗？')) return;
    
                const actionName = action === 'check' ? '测活' : (action === 'enable' ? '启用' : (action === 'disable' ? '禁用' : (action === 'refresh' ? '刷新凭证' : '删除')));
                this.showToast(`正在对账号进行 ${actionName}，请稍候...`, 'info');
                acc._loading = action;
    
                try {
                    const res = await this.authFetch('/api/cloud/action', {
                        method: 'POST',
                        body: JSON.stringify({ accounts: [{id: String(acc.id), type: acc.account_type, platform: (acc.details && (acc.details.platform || acc.details.type)) || ''}], action: action })
                    });
                    const result = await res.json();
                    const detailKey = `${acc.account_type}|${acc.id}`;
                    const updatedDetail = result.updated_details && (result.updated_details[detailKey] || result.updated_details[acc.id]);
                    if (updatedDetail) {
                        acc.details = Object.assign({}, acc.details, updatedDetail);
                        this.localCloudDetails[detailKey] = acc.details;
                        this.localCloudDetails[acc.id] = acc.details;
                    }
                    if (action === 'enable' && result.status !== 'error') acc.status = 'active';
                    if (action === 'disable' && result.status !== 'error') acc.status = 'disabled';
    
                    if (action === 'check') {
                        this.currentTab = 'console';
                const now = formatMainlandDateTime(new Date());
                        this.localCheckTimes[`${acc.account_type}|${acc.id}`] = now;
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
                    this.cloudFilters = ['sub2api', 'cpa', 'image2api', 'grok2api'];
                } else if (platformType === 'cpa') {
                    this.cloudFilters = ['cpa'];
                } else if (platformType === 'sub2api') {
                    this.cloudFilters = ['sub2api'];
                }else if (platformType === 'image2api') {
                    this.cloudFilters = ['image2api']
                }else if (platformType === 'grok2api') {
                    this.cloudFilters = ['grok2api']
                }
                this.cloudStatusFilter = status || 'all';
                this.cloudPage = 1;
                this.fetchCloudAccounts();
                const typeName = platformType === 'all' ? '全部平台' : (platformType === 'cpa' ? 'CPA' : (platformType === 'sub2api' ? 'Sub2API' : (platformType === 'grok2api' ? 'Grok2API' : 'Image2API')));
                const statusName = status === 'active' ? '存活' : (status === 'disabled' ? '失效' : '全部');
                this.showToast(`已筛选: ${typeName} - ${statusName}账号`, 'info');
            },
    async bulkCloudAction(action) {
                if (this.selectedCloud.length === 0) {
                    return this.showToast('请先勾选需要操作的账号', 'warning');
                }
                if (action === 'delete' && !confirm(`⚠️ 危险操作：确认删除选中的 ${this.selectedCloud.length} 个账号吗？`)) return;
                const actionAccounts = this.selectedCloud.map(k => {
                    const [id, type] = String(k).split('|');
                    const row = this.cloudAccounts.find(a => String(a.id) === String(id) && a.account_type === type);
                    const platform = (row && row.details && (row.details.platform || row.details.type)) || '';
                    return { id: String(id), type: type, platform };
                });
                const actionName = action === 'check' ? '测活' : (action === 'enable' ? '启用' : (action === 'disable' ? '禁用' : (action === 'refresh' ? '刷新凭证' : '删除')));
                this.showToast(`正在批量 ${actionName} ${this.selectedCloud.length} 个账号，耗时较长请耐心等待...`, 'info');
                this.isCloudActionLoading = true;
    
                try {
                    const res = await this.authFetch('/api/cloud/action', {
                        method: 'POST',
                        body: JSON.stringify({ accounts: actionAccounts, action: action })
                    });
                    const result = await res.json();
                    if (result.updated_details) {
                        actionAccounts.forEach(selected => {
                            const targetAcc = this.cloudAccounts.find(a => String(a.id) === String(selected.id) && a.account_type === selected.type);
                            const detailKey = `${selected.type}|${selected.id}`;
                            const updatedDetail = result.updated_details[detailKey] || result.updated_details[selected.id];
                            if (targetAcc && updatedDetail) {
                                targetAcc.details = Object.assign({}, targetAcc.details, updatedDetail);
                                this.localCloudDetails[detailKey] = targetAcc.details;
                                this.localCloudDetails[selected.id] = targetAcc.details;
                            }
                        });
                    }
                    if (action === 'check') {
                        const now = formatMainlandDateTime(new Date());
                        actionAccounts.forEach(c => {
                            this.localCheckTimes[`${c.type}|${c.id}`] = now;
                            this.localCheckTimes[c.id] = now;
                        });
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
    toggleAll(event) {
                if (event.target.checked) this.selectedAccounts = [...this.filteredAccounts];
                else this.selectedAccounts = [];
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
                this.applyCloudAccountView();
            },
    changeCloudPageSize() {
                this.cloudPage = 1;
                this.selectedCloud = [];
                this.applyCloudAccountView();
            },
    onCloudSearchInput() {
                this.cloudPage = 1;
                this.applyCloudAccountView();
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
                        if (action === 'export_accounts') {
                            setTimeout(() => this.fetchClusterSyncTasks({ silent: true }), 1000);
                        }
                    } else {
                        this.showToast(data.message, 'warning');
                    }
                } catch (e) {
                    this.showToast('控制请求异常', 'error');
                }
            },
};

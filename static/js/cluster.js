const CLUSTER_METHODS = {
    async fetchClusterSyncTasks(options = {}) {
                const { silent = false } = options;
                if (!silent) {
                    this.clusterSyncTasksLoading = true;
                }
                try {
                    const res = await this.authFetch('/api/cluster/sync_tasks?limit=20');
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.clusterSyncTasks = Array.isArray(data.tasks) ? data.tasks : [];
                    } else if (!silent) {
                        this.showToast(data.message || '同步任务列表获取失败', 'warning');
                    }
                } catch (e) {
                    if (!silent) {
                        this.showToast('同步任务列表获取失败', 'error');
                    }
                } finally {
                    if (!silent) {
                        this.clusterSyncTasksLoading = false;
                    }
                }
            },
    async clearTerminalClusterSyncTasks() {
                const confirmed = await this.customConfirm('确定清理所有已完成、已取消和已中断的同步任务吗？此操作不可恢复。');
                if (!confirmed) return;
                try {
                    const res = await this.authFetch('/api/cluster/sync_tasks/clear_terminal', {
                        method: 'POST'
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.showToast(data.message || '终态同步任务已清理', 'success');
                        await this.fetchClusterSyncTasks();
                    } else {
                        this.showToast(data.message || '终态同步任务清理失败', 'warning');
                    }
                } catch (e) {
                    this.showToast('终态同步任务清理失败', 'error');
                }
            },
    async retryClusterSyncTask(taskId) {
                if (!taskId) return;
                try {
                    const res = await this.authFetch(`/api/cluster/sync_tasks/${encodeURIComponent(taskId)}/retry`, {
                        method: 'POST'
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.showToast(data.message || `同步任务 ${taskId} 已重新排队`, 'success');
                        await this.fetchClusterSyncTasks({ silent: true });
                    } else {
                        this.showToast(data.message || '同步任务重试失败', 'warning');
                    }
                } catch (e) {
                    this.showToast('同步任务重试失败', 'error');
                }
            },
    async cancelClusterSyncTask(taskId) {
                if (!taskId) return;
                try {
                    const res = await this.authFetch(`/api/cluster/sync_tasks/${encodeURIComponent(taskId)}/cancel`, {
                        method: 'POST'
                    });
                    const data = await res.json();
                    if (data.status === 'success') {
                        this.showToast(data.message || `同步任务 ${taskId} 已取消`, 'success');
                        await this.fetchClusterSyncTasks({ silent: true });
                    } else {
                        this.showToast(data.message || '同步任务取消失败', 'warning');
                    }
                } catch (e) {
                    this.showToast('同步任务取消失败', 'error');
                }
            },
    formatClusterSyncStatus(status) {
                const statusMap = {
                    pending: '排队中',
                    running: '导入中',
                    success: '已完成',
                    partial_success: '部分成功',
                    failed: '失败',
                    retry_wait: '待重试',
                    cancel_requested: '取消中',
                    cancelled: '已取消'
                };
                return statusMap[String(status || '').trim()] || (status || '未知');
            },
    clusterSyncStatusClass(status) {
                const normalized = String(status || '').trim();
                if (normalized === 'success') return 'bg-emerald-50 text-emerald-600 border-emerald-200';
                if (normalized === 'partial_success') return 'bg-amber-50 text-amber-600 border-amber-200';
                if (normalized === 'failed') return 'bg-rose-50 text-rose-600 border-rose-200';
                if (normalized === 'retry_wait') return 'bg-orange-50 text-orange-600 border-orange-200';
                if (normalized === 'cancel_requested') return 'bg-slate-100 text-slate-600 border-slate-300';
                if (normalized === 'cancelled') return 'bg-slate-100 text-slate-500 border-slate-200';
                if (normalized === 'running') return 'bg-sky-50 text-sky-600 border-sky-200';
                return 'bg-slate-100 text-slate-600 border-slate-200';
            },
    formatClusterSyncTime(value) {
                if (!value) return '-';
                const normalized = String(value).replace(' ', 'T');
                const date = new Date(normalized);
                if (Number.isNaN(date.getTime())) return value;
                return this.formatMainlandDateTime(date);
            },
    formatClusterSyncError(errorMessage) {
                const text = String(errorMessage || '').trim();
                return text || '-';
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
};

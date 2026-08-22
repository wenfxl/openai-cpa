const REALTIME_METHODS = {
    startStatsPolling() {
                if(this.statsTimer) clearTimeout(this.statsTimer);
                this.pollStats();
            },
    shouldPollMailDomainRuntimeStats() {
                return !!(
                    this.currentTab === 'email' &&
                    this.isRunning &&
                    this.config?.enable_mail_domain_runtime_control &&
                    !this.mailDomainRuntimePanelCollapsed
                );
            },
    queuePollStats() {
                if (this._pollStatsInFlight) {
                    this._pollStatsQueued = true;
                    return;
                }
                this.pollStats();
            },
    async pollStats() {
                if(!this.isLoggedIn) return;
                if (this._pollStatsInFlight) {
                    this._pollStatsQueued = true;
                    return;
                }
                this._pollStatsInFlight = true;
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
                        this.shouldPollMailDomainRuntimeStats() &&
                        Date.now() - this.mailDomainRuntimeLastFetchAt >= this.mailDomainRuntimePollIntervalMs
                    ) {
                        await this.fetchMailDomainRuntimeStats({ silent: true });
                    }
    
                    if (this.currentTab === 'cluster') {
                        const cRes = await this.authFetch('/api/cluster/view');
                        const cData = await cRes.json();
                        if (cData.status === 'success') {
                            this.clusterNodes = cData.nodes;
                        }
                        await this.fetchClusterSyncTasks({ silent: true });
                    }
                } catch(e) {
    
                } finally {
                    this._pollStatsInFlight = false;
                    if (this._pollStatsQueued) {
                        this._pollStatsQueued = false;
                        this.queuePollStats();
                        return;
                    }
                    this.statsTimer = setTimeout(() => {
                        this.queuePollStats();
                    }, 1000);
                }
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
                        this.fetchClusterSyncTasks({ silent: true });
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
};

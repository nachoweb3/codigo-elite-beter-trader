// CE BetterTrader Pro - Inteligencia de Trading en Solana
const API_BASE = window.location.origin;

// Global state
const state = {
    wallet: null,
    data: null,
    flowData: null,
    portfolio: null,
    loading: false,
    timeOfDay: null,
    authToken: localStorage.getItem('bettertrader_token') || null,
    authConfig: null,
    access: null,
    payPollTimer: null
};

// ============================================================
// AUTH / ACCESS (login por firma + pago en SOL)
// ============================================================

// Base58 (compacto, solo encode — suficiente para firmas de Phantom)
const B58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function base58Encode(bytes) {
    const digits = [0];
    for (let i = 0; i < bytes.length; i++) {
        let carry = bytes[i];
        for (let j = 0; j < digits.length; j++) {
            carry += digits[j] << 8;
            digits[j] = carry % 58;
            carry = (carry / 58) | 0;
        }
        while (carry > 0) {
            digits.push(carry % 58);
            carry = (carry / 58) | 0;
        }
    }
    // Leading zeros
    let zeros = 0;
    while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
    let out = '';
    for (let i = 0; i < zeros; i++) out += '1';
    for (let i = digits.length - 1; i >= 0; i--) out += B58_ALPHABET[digits[i]];
    return out;
}

// fetch con token de sesión adjunto (si existe)
async function apiFetch(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (state.authToken) headers['Authorization'] = `Bearer ${state.authToken}`;
    const resp = await fetch(url, { ...options, headers });
    if (resp.status === 401) {
        // Sesión expirada o acceso restringido
        logoutSession();
    }
    return resp;
}

function logoutSession() {
    state.authToken = null;
    state.access = null;
    localStorage.removeItem('bettertrader_token');
    showGate();
}

function showGate() {
    const gate = document.getElementById('accessGate');
    if (gate) gate.style.display = 'flex';
    const dash = document.getElementById('dashboardView');
    if (dash) dash.classList.add('hidden');
    const load = document.getElementById('loadingState');
    if (load) load.style.display = 'none';
    const err = document.getElementById('errorState');
    if (err) err.style.display = 'none';
}

function hideGate() {
    const gate = document.getElementById('accessGate');
    if (gate) gate.style.display = 'none';
}

async function initAuth() {
    // Consultar la configuración pública de acceso
    let cfg = null;
    try {
        const cResp = await fetch(`${API_BASE}/api/auth/config`);
        cfg = await cResp.json();
    } catch (e) { /* sin red */ }
    state.authConfig = cfg;

    // Modo dev (ACCESS_CONTROL=False): entrada directa sin login
    if (cfg && cfg.access_control === false) {
        hideGate();
        return;
    }

    // Si hay token guardado, validarlo y cargar el estado de acceso
    if (!state.authToken) {
        if (cfg && cfg.demo_mode && cfg.demo_wallet) {
            showDemoLogin(cfg);
        } else {
            showGate();
        }
        return;
    }
    try {
        const resp = await apiFetch(`${API_BASE}/api/auth/me?token=${encodeURIComponent(state.authToken)}`);
        if (!resp.ok) {
            showGate();
            return;
        }
        const data = await resp.json();
        state.wallet = data.wallet;
        state.access = data.access;
        hideGate();
        updateAuthUI();
        if (!data.access.has_access) {
            openPaymentModal(data.access);
        }
    } catch (e) {
        showGate();
    }
}

// Modo demo (solo desarrollo): botón que entra con una wallet demo sin firma
function showDemoLogin(cfg) {
    const gate = document.getElementById('accessGate');
    if (!gate) return;
    gate.style.display = 'flex';
    const buttons = gate.querySelector('.access-buttons');
    const hint = document.getElementById('gateHint');
    if (hint) hint.textContent = 'Modo demo activo (solo desarrollo).';
    if (!buttons) return;
    const demoBtn = document.createElement('button');
    demoBtn.className = 'btn-primary access-login-btn demo-btn';
    demoBtn.innerHTML = '<span>⚡ Entrar en modo demo</span>';
    demoBtn.onclick = async () => {
        try {
            // El servidor crea una sesión demo firmada internamente
            const resp = await fetch(`${API_BASE}/api/auth/demo`);
            const data = await resp.json();
            if (!resp.ok) { if (hint) hint.textContent = data.detail || 'Error demo'; return; }
            state.authToken = data.token;
            state.wallet = data.wallet;
            state.access = data.access;
            localStorage.setItem('bettertrader_token', data.token);
            hideGate();
            updateAuthUI();
            if (data.access.has_access) showNotification('Modo demo — acceso de prueba', 'warning');
            else openPaymentModal(data.access);
        } catch (e) { if (hint) hint.textContent = 'Error de red en modo demo.'; }
    };
    buttons.appendChild(demoBtn);
}

function updateAuthUI() {
    const connected = document.getElementById('connectedWallet');
    if (connected && state.wallet) connected.textContent = shortenAddress(state.wallet);
    const mobileConnected = document.getElementById('mobileConnectedWallet');
    if (mobileConnected && state.wallet) mobileConnected.textContent = shortenAddress(state.wallet);

    const adminBtn = document.getElementById('adminBtn');
    if (adminBtn) adminBtn.style.display = state.access && state.access.is_admin ? '' : 'none';
}

async function gateLogin() {
    // 1. Conectar wallet Phantom (o Solflare)
    let publicKey = null;
    const provider = window.solana || window.phantom?.solana;
    if (provider) {
        try {
            const resp = await provider.connect();
            publicKey = resp.publicKey.toString();
        } catch (e) {
            const hint = document.getElementById('gateHint');
            if (hint) hint.textContent = 'Conexión cancelada o error: ' + (e.message || e);
            return;
        }
    } else if (window.solflare) {
        try {
            const resp = await window.solflare.connect();
            publicKey = resp.publicKey.toString();
        } catch (e) {
            const hint = document.getElementById('gateHint');
            if (hint) hint.textContent = 'Conexión cancelada o error: ' + (e.message || e);
            return;
        }
    } else {
        const hint = document.getElementById('gateHint');
        if (hint) hint.textContent = 'Instala Phantom (phantom.app) o Solflare para iniciar sesión de forma segura.';
        return;
    }

    const status = document.getElementById('accessStatus');
    const hint = document.getElementById('gateHint');
    if (status) status.innerHTML = '<span class="live-dot"></span> Pidiendo firma... Revisa tu wallet.';
    if (hint) hint.textContent = `Wallet: ${shortenAddress(publicKey)} — firma el mensaje para continuar.`;

    // 2. Pedir challenge al servidor
    let challenge;
    try {
        const cResp = await fetch(`${API_BASE}/api/auth/challenge`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wallet_address: publicKey })
        });
        const cData = await cResp.json();
        challenge = cData.challenge;
    } catch (e) {
        if (hint) hint.textContent = 'Error de red pidiendo el challenge.';
        return;
    }

    // 3. Firmar el mensaje
    let signature;
    try {
        const encoded = new TextEncoder().encode(challenge);
        const sigResp = await provider.signMessage(encoded, 'utf8');
        signature = base58Encode(sigResp.signature);
    } catch (e) {
        if (hint) hint.textContent = 'Firma rechazada: ' + (e.message || e);
        return;
    }

    // 4. Verificar en el servidor y crear sesión
    try {
        const vResp = await fetch(`${API_BASE}/api/auth/verify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wallet_address: publicKey, challenge, signature })
        });
        const vData = await vResp.json();
        if (!vResp.ok) {
            if (hint) hint.textContent = vData.detail || 'Error verificando la firma.';
            return;
        }
        state.authToken = vData.token;
        state.wallet = vData.wallet;
        state.access = vData.access;
        localStorage.setItem('bettertrader_token', vData.token);
        hideGate();
        updateAuthUI();
        if (vData.access.has_access) {
            showNotification('¡Bienvenido! Acceso confirmado', 'success');
        } else {
            openPaymentModal(vData.access);
        }
    } catch (e) {
        if (hint) hint.textContent = 'Error de red verificando la firma.';
    }
}

// --- Pago en SOL ---
function openPaymentModal(access) {
    const modal = document.getElementById('paymentModal');
    if (!modal) return;
    modal.style.display = 'flex';

    const summary = document.getElementById('paySummary');
    if (summary) {
        summary.innerHTML = `
            <div class="pay-price">${access.price_sol} SOL</div>
            <div class="pay-desc">por <strong>${access.duration_days} días</strong> de acceso a CE BetterTrader PRO<br>
            Wallet: <code>${shortenAddress(state.wallet)}</code></div>
        `;
    }
    const addr = document.getElementById('payAddress');
    if (addr) addr.textContent = access.merchant_wallet || '--';

    const qr = document.getElementById('payQr');
    if (qr && access.merchant_wallet) {
        qr.innerHTML = '';
        try {
            new QRCode(qr, {
                text: `solana:${access.merchant_wallet}?amount=${access.price_sol}`,
                width: 160,
                height: 160
            });
        } catch (e) {
            qr.innerHTML = '<p class="pay-status">Escanea con la app de tu wallet o copia la dirección.</p>';
        }
    }
}

function closePaymentModal() {
    const modal = document.getElementById('paymentModal');
    if (modal) modal.style.display = 'none';
}

async function copyPayAddress() {
    const addr = document.getElementById('payAddress');
    if (!addr) return;
    try {
        await navigator.clipboard.writeText(addr.textContent);
        showNotification('Dirección copiada', 'success');
    } catch (e) {
        showNotification('Copia manualmente: ' + addr.textContent, 'warning');
    }
}

async function checkMyPayment() {
    const btn = document.getElementById('payCheckBtn');
    const status = document.getElementById('payStatus');
    if (btn) { btn.disabled = true; btn.querySelector('span').textContent = 'Verificando en blockchain...'; }
    if (status) status.textContent = 'Consultando las transacciones entrantes de la wallet de pagos...';

    try {
        const resp = await apiFetch(`${API_BASE}/api/auth/pay/check?token=${encodeURIComponent(state.authToken)}`, { method: 'POST' });
        const data = await resp.json();
        if (data.paid) {
            state.access = data.access;
            closePaymentModal();
            showNotification('✅ Pago confirmado — acceso activado', 'success');
            updateAuthUI();
        } else {
            if (status) status.textContent = 'Aún no vemos el pago. Si ya enviaste el SOL, espera unos segundos y pulsa verificar otra vez.';
        }
    } catch (e) {
        if (status) status.textContent = 'Error consultando el pago. Inténtalo de nuevo.';
    } finally {
        if (btn) { btn.disabled = false; btn.querySelector('span').textContent = '✓ Ya pagué — Verificar'; }
    }
}

// --- Panel Admin ---
function openAdminModal() {
    const modal = document.getElementById('adminModal');
    if (!modal) return;
    modal.style.display = 'flex';
    loadAdminWallets();
}

function closeAdminModal() {
    const modal = document.getElementById('adminModal');
    if (modal) modal.style.display = 'none';
}

async function loadAdminWallets() {
    const list = document.getElementById('adminWalletList');
    if (!list) return;
    list.innerHTML = '<p class="pay-status">Cargando wallets con acceso...</p>';
    try {
        const resp = await apiFetch(`${API_BASE}/api/auth/admin/wallets?token=${encodeURIComponent(state.authToken)}`);
        const data = await resp.json();
        if (!resp.ok) {
            list.innerHTML = `<p class="pay-status">${data.detail || 'Error'}</p>`;
            return;
        }
        list.innerHTML = data.wallets.length
            ? data.wallets.map(w => `
                <div class="admin-wallet-row">
                    <div>
                        <code>${shortenAddress(w.wallet)}</code>
                        <span class="admin-wallet-type ${w.type}">${w.type === 'whitelist' ? 'whitelist' : 'pago'}</span>
                        ${w.status.remaining_days != null ? `<span class="admin-wallet-days">${w.status.remaining_days} días</span>` : ''}
                    </div>
                    <button class="icon-btn" onclick="adminRemoveWallet('${w.wallet}')" title="Quitar acceso">✕</button>
                </div>`).join('')
            : '<p class="pay-status">Aún no hay wallets con acceso. Añade la primera con el campo de arriba.</p>';
    } catch (e) {
        list.innerHTML = '<p class="pay-status">Error cargando wallets.</p>';
    }
}

async function adminAddWallet() {
    const input = document.getElementById('adminWalletInput');
    if (!input || !input.value.trim()) return;
    const resp = await apiFetch(`${API_BASE}/api/auth/admin/whitelist?token=${encodeURIComponent(state.authToken)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wallet_address: input.value.trim(), action: 'add' })
    });
    const data = await resp.json();
    if (resp.ok) {
        input.value = '';
        showNotification('Wallet añadida a la whitelist', 'success');
        loadAdminWallets();
    } else {
        showNotification(data.detail || 'Error', 'error');
    }
}

async function adminRemoveWallet(wallet) {
    const resp = await apiFetch(`${API_BASE}/api/auth/admin/whitelist?token=${encodeURIComponent(state.authToken)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wallet_address: wallet, action: 'remove' })
    });
    if (resp.ok) {
        showNotification('Acceso revocado', 'success');
        loadAdminWallets();
    }
}

// Botón de logout en el header (añadido al cargar)
function setupLogoutButton() {
    const actions = document.querySelector('.header-actions');
    if (!actions || document.getElementById('logoutBtn')) return;
    const btn = document.createElement('button');
    btn.id = 'logoutBtn';
    btn.className = 'icon-btn';
    btn.title = 'Cerrar sesión';
    btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor"><path d="M11 3H5a2 2 0 00-2 2v10a2 2 0 002 2h6v-2H5V5h6V3zm4.3 4.3L17 9H9v2h8l-1.7 1.7L16.7 14 21 10l-4.3-4-1.4 1.3z"/></svg>';
    btn.onclick = () => {
        if (state.authToken) fetch(`${API_BASE}/api/auth/logout?token=${encodeURIComponent(state.authToken)}`, { method: 'POST' }).catch(() => {});
        logoutSession();
        showNotification('Sesión cerrada', 'warning');
    };
    actions.appendChild(btn);
}

// Chart instances
const charts = {
    pnl: null,
    trades: null,
    timeline: null,
    byToken: null
};

// ============================================================
// INITIALIZATION
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

function initApp() {
    setupLogoutButton();
    initAuth();

    const DEFAULT_WALLET = "5JcKL7iUH71Ls7D5FtWRM3KExG8NpW69NZQkRPZ2jh4Y";
    // Recordar la última wallet analizada
    const savedWallet = localStorage.getItem('bettertrader_wallet');
    const walletInput = document.getElementById('walletInput');
    const analyzeBtn = document.getElementById('analyzeBtn');

    if (walletInput) {
        walletInput.value = savedWallet || DEFAULT_WALLET;
        if (savedWallet) {
            const connected = document.getElementById('connectedWallet');
            if (connected) connected.textContent = shortenAddress(savedWallet);
            const mobileConnected = document.getElementById('mobileConnectedWallet');
            if (mobileConnected) mobileConnected.textContent = shortenAddress(savedWallet);
        }
    }

    // Event listeners
    if (analyzeBtn) {
        analyzeBtn.addEventListener('click', analyzeWallet);
    }

    if (walletInput) {
        walletInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') analyzeWallet();
        });
    }

    // Navigation
    setupMobileNav();
    setupNavigation();

    console.log('CE BetterTrader Pro v2.0 - Inteligencia de Trading en Solana cargado');
}

function setupMobileNav() {
    const drawer = document.getElementById('mobileDrawer');
    const overlay = document.getElementById('mobileDrawerOverlay');
    const menuBtn = document.getElementById('mobileMenuBtn');
    const closeBtn = document.getElementById('mobileDrawerClose');
    const navContainer = document.getElementById('mobileDrawerNav');
    if (!drawer || !overlay || !navContainer) return;

    // Clonar los items de navegación del sidebar dentro del drawer
    // (se quita .nav-item para que el handler de navegación principal no interfiera)
    const sourceItems = document.querySelectorAll('.sidebar-nav .nav-item');
    sourceItems.forEach(item => {
        const clone = item.cloneNode(true);
        clone.classList.remove('nav-item');
        clone.classList.add('mobile-nav-item');
        navContainer.appendChild(clone);
    });

    const openDrawer = () => {
        drawer.classList.add('open');
        overlay.classList.add('show');
        document.body.style.overflow = 'hidden';
    };
    const closeDrawer = () => {
        drawer.classList.remove('open');
        overlay.classList.remove('show');
        document.body.style.overflow = '';
    };

    if (menuBtn) menuBtn.addEventListener('click', openDrawer);
    if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
    overlay.addEventListener('click', closeDrawer);

    // Al navegar desde el drawer: cerrar, sincronizar estado y hacer scroll
    navContainer.querySelectorAll('.mobile-nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const viewName = item.getAttribute('data-view');
            closeDrawer();

            document.querySelectorAll('.sidebar-nav .nav-item').forEach(n => {
                n.classList.toggle('active', n.getAttribute('data-view') === viewName);
            });
            document.querySelectorAll('.mobile-nav-item').forEach(n => {
                n.classList.toggle('active', n.getAttribute('data-view') === viewName);
            });

            const dashboardView = document.getElementById('dashboardView');
            if (dashboardView) dashboardView.classList.remove('hidden');

            const tradingSection = document.getElementById('tradingSection');
            if (tradingSection) {
                tradingSection.style.display = (viewName === 'trading') ? 'grid' : 'none';
            }

            const scrollTargets = {
                dashboard: '.hero-stats',
                portfolio: '.portfolio-section',
                analytics: '.charts-section',
                signals: '.money-flow-section',
                trading: '#tradingSection'
            };
            const target = scrollTargets[viewName];
            if (target) {
                const el = document.querySelector(target);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }

            if (viewName === 'trading') {
                setTimeout(() => {
                    initTrading();
                    loadStrategies();
                }, 150);
            }
        });
    });
}

function setupNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const viewName = item.getAttribute('data-view');
            if (!viewName) return;

            // Actualizar item activo
            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');

            // El dashboard siempre está visible; trading es la única vista con sección propia
            const dashboardView = document.getElementById('dashboardView');
            if (dashboardView) dashboardView.classList.remove('hidden');

            const tradingSection = document.getElementById('tradingSection');
            if (tradingSection) {
                tradingSection.style.display = (viewName === 'trading') ? 'grid' : 'none';
            }

            const scrollTargets = {
                dashboard: '.hero-stats',
                portfolio: '.portfolio-section',
                analytics: '.charts-section',
                signals: '.money-flow-section',
                trading: '#tradingSection'
            };

            const target = scrollTargets[viewName];
            if (target) {
                const el = document.querySelector(target);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }

            if (viewName === 'trading') {
                setTimeout(() => {
                    initTrading();
                    loadStrategies();
                }, 150);
            }
        });
    });
}

// ============================================================
// WALLET ANALYSIS
// ============================================================

async function analyzeWallet() {
    const walletInput = document.getElementById('walletInput');
    if (!walletInput) return;

    const wallet = walletInput.value.trim();

    if (!wallet) {
        showError('Por favor ingresa una dirección de wallet');
        return;
    }

    if (!isValidSolanaAddress(wallet)) {
        showError('Dirección de wallet inválida');
        return;
    }

    state.wallet = wallet;
    localStorage.setItem('bettertrader_wallet', wallet);
    const connected = document.getElementById('connectedWallet');
    if (connected) connected.textContent = shortenAddress(wallet);
    const mobileConnected = document.getElementById('mobileConnectedWallet');
    if (mobileConnected) mobileConnected.textContent = shortenAddress(wallet);
    showLoading();

    try {
        // Fetch all data in parallel
        const [analyzeResponse, flowResponse, portfolioResponse] = await Promise.all([
            apiFetch(`${API_BASE}/api/wallet/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wallet_address: wallet, limit: 100 })
            }),
            apiFetch(`${API_BASE}/api/wallet/flow-directions?wallet_address=${wallet}`),
            apiFetch(`${API_BASE}/api/wallet/portfolio?wallet_address=${wallet}`)
        ]);

        if (!analyzeResponse.ok) {
            throw new Error('Error al analizar la wallet');
        }

        const data = await analyzeResponse.json();
        state.data = data;

        // Hide loading, show dashboard
        hideLoading();
        showDashboard();

        // Update all UI components
        updateAllComponents(data);

        // Load flow data if available
        if (flowResponse.ok) {
            const flowData = await flowResponse.json();
            state.flowData = flowData;
            updateFlowSection(flowData);
        }

        // Load portfolio data
        if (portfolioResponse.ok) {
            const portfolio = await portfolioResponse.json();
            state.portfolio = portfolio;
            updatePortfolioSection(portfolio);
        }

        // Load stream activity
        loadStreamActivity();
        updateLastUpdated();

        // Análisis y evolución de cartera EN TIEMPO REAL:
        // re-consulta los precios/PnL en vivo cada 45s sin recargar la página.
        startLiveRefresh(wallet);
        updateRateLimitStatus();

    } catch (error) {
        console.error('Analysis error:', error);
        showError(error.message || 'Error al conectar con el servidor');
    }
}

// Refresh automático: actualiza precios en vivo, PnL y flujos cada 45 segundos.
let liveRefreshTimer = null;
let liveRefreshInProgress = false;

function startLiveRefresh(wallet) {
    stopLiveRefresh();
    liveRefreshTimer = setInterval(() => refreshLiveData(wallet), 45000);
    console.log(`CE BetterTrader: actualización en vivo cada 45s para ${wallet.slice(0, 6)}…`);
}

function stopLiveRefresh() {
    if (liveRefreshTimer) {
        clearInterval(liveRefreshTimer);
        liveRefreshTimer = null;
    }
}

async function refreshLiveData(wallet) {
    if (liveRefreshInProgress || !wallet) return;
    liveRefreshInProgress = true;
    try {
        // Estado del rate limiter: avisa si la API de Solana está saturada
        updateRateLimitStatus();

        const [analyzeResponse, flowResponse, portfolioResponse] = await Promise.all([
            apiFetch(`${API_BASE}/api/wallet/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wallet_address: wallet, limit: 100 })
            }),
            apiFetch(`${API_BASE}/api/wallet/flow-directions?wallet_address=${wallet}`),
            apiFetch(`${API_BASE}/api/wallet/portfolio?wallet_address=${wallet}`)
        ]);

        if (!analyzeResponse.ok) return;
        const data = await analyzeResponse.json();
        state.data = data;

        // Actualiza métricas, PnL en vivo, gráficos, tabla y perfil
        updateAllComponents(data);
        if (flowResponse.ok) {
            const flowData = await flowResponse.json();
            state.flowData = flowData;
            updateFlowSection(flowData);
        }
        if (portfolioResponse.ok) {
            const portfolio = await portfolioResponse.json();
            state.portfolio = portfolio;
            updatePortfolioSection(portfolio);
        }
        updateLastUpdated();
    } catch (error) {
        // Silencioso: no interrumpir la experiencia si el refresh falla
        console.error('Live refresh error:', error);
    } finally {
        liveRefreshInProgress = false;
    }
}

function updateLastUpdated() {
    const el = document.getElementById('lastUpdated');
    if (el) {
        const now = new Date();
        el.textContent = `Actualizado ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
    }
}

// Alerta cuando la API externa (Helius) está cerca de su límite.
// Con ~100 personas analizando a la vez, el bucket puede quedarse sin
// tokens: avisamos para que el usuario sepa que el análisis va más lento
// porque la API está saturada, no porque la app esté rota.
async function updateRateLimitStatus() {
    const el = document.getElementById('rateLimitWarning');
    if (!el) return;
    try {
        const resp = await apiFetch(`${API_BASE}/api/system/ratelimit`, { cache: 'no-store' });
        if (!resp.ok) return;
        const s = await resp.json();
        // Saturada si la presión es alta y hay gente esperando, o el bucket está casi vacío
        const saturated = (s.pressure >= 85 && s.waiters > 0) || s.tokens_available <= 1.5;
        el.style.display = saturated ? '' : 'none';
        if (saturated) {
            el.title = `La API de Solana está al ${Math.round(s.pressure)}% de su capacidad (${s.waiters} solicitudes en espera). Los análisis pueden ir más lentos.`;
        }
    } catch (e) {
        // Silencioso: es un indicador auxiliar
    }
}

async function loadStreamActivity() {
    if (!state.wallet) return;

    try {
        const response = await apiFetch(`${API_BASE}/api/wallet/simulate-stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wallet_address: state.wallet, limit: 50 })
        });

        if (response.ok) {
            const streamData = await response.json();
            updateActivityFeed(streamData.events || []);
        }
    } catch (error) {
        console.error('Error loading stream:', error);
    }
}

// ============================================================
// UI UPDATES
// ============================================================

function updateAllComponents(data) {
    updateHeroStats(data.metrics);
    updateMetricsCards(data.metrics);
    updateCharts(data.tokens, data.metrics);
    updateTokensTable(data.tokens);
    updateInsights(data.recommendations);
    updateTransactionsList(data.transactions);
    updateWalletProfile(data.profile);
    updateTimeOfDay(data.time_of_day);
    updateTradingScore(data.score);
    updateCommunityBenchmark();
}

// Trading Score global (0-100) con gauge circular y desglose
function updateTradingScore(score) {
    const card = document.getElementById('scoreCard');
    if (!card) return;

    if (!score || score.total == null) {
        card.style.display = 'none';
        return;
    }
    card.style.display = '';

    const total = Math.round(score.total);
    const num = document.getElementById('scoreNumber');
    if (num) num.textContent = total;

    const arc = document.getElementById('scoreArc');
    if (arc) {
        const C = 2 * Math.PI * 40; // 251.2
        const offset = C * (1 - Math.min(100, Math.max(0, total)) / 100);
        arc.style.transition = 'stroke-dashoffset 1.2s cubic-bezier(0.4, 0, 0.2, 1)';
        arc.style.strokeDashoffset = offset;
        // Color del arco según la nota
        const color = total >= 75 ? '#22c55e' : total >= 60 ? '#38bdf8' : total >= 45 ? '#f59e0b' : '#ef4444';
        arc.style.stroke = color;
    }

    const grade = document.getElementById('scoreGrade');
    if (grade) {
        const g = score.grade || '';
        grade.textContent = `Nota ${g}`;
        grade.className = 'score-grade';
        grade.classList.add(`grade-${String(g).toLowerCase()}`);
    }

    const label = document.getElementById('scoreLabel');
    if (label) label.textContent = score.label || '--';

    const bars = [
        ['sbRent', score.rentabilidad],
        ['sbCons', score.consistencia],
        ['sbRiesgo', score.riesgo],
        ['sbEfic', score.eficiencia]
    ];
    bars.forEach(([id, val]) => {
        const el = document.getElementById(id);
        if (el) el.style.width = `${Math.min(100, Math.max(0, val || 0))}%`;
    });

    const summary = document.getElementById('scoreSummary');
    if (summary) summary.textContent = score.summary || '';
}

function updateCommunityBenchmark() {
    const card = document.getElementById('communityCard');
    if (!card || !state.wallet) return;

    apiFetch(`${API_BASE}/api/community/benchmark?wallet_address=${encodeURIComponent(state.wallet)}`)
        .then(r => r.json())
        .then(data => {
            if (!data.available) return;
            card.style.display = '';
            const stats = document.getElementById('communityStats');
            const foot = document.getElementById('communityFoot');
            if (!stats || !foot) return;

            const p = data.percentiles || {};
            const rows = [];
            const defs = [
                { key: 'win_rate', label: 'Tasa de Éxito' },
                { key: 'profit_factor', label: 'Factor de Beneficio' },
                { key: 'total_pnl', label: 'P&L Total' },
                { key: 'total_trades', label: 'Actividad' }
            ];
            defs.forEach(d => {
                const val = p[d.key];
                if (val == null) return;
                const color = val >= 70 ? 'good' : val >= 40 ? 'mid' : 'low';
                rows.push(`
                    <div class="comm-row">
                        <span class="comm-label">${d.label}</span>
                        <div class="comm-bar"><div class="comm-bar-fill ${color}" style="width:${Math.min(val, 100)}%"></div></div>
                        <span class="comm-pct">${val}%</span>
                    </div>`);
            });

            const pcts = Object.values(p).filter(v => v != null);
            if (!pcts.length) {
                // Primera wallet de la comunidad: aún no hay con quién comparar
                stats.innerHTML = '<div class="comm-label" style="grid-template-columns:none;display:block;line-height:1.5;">Sé la primera wallet analizada 🎉<br><span style="color:var(--text-muted);">A medida que la comunidad analice más wallets, verás aquí tus percentiles.</span></div>';
                foot.textContent = `Comunidad: ${data.community_size} wallet`;
                return;
            }

            stats.innerHTML = rows.join('');

            const top = Object.keys(p).filter(k => (p[k] || 0) >= 80)
                .map(k => ({win_rate: 'éxito', profit_factor: 'factor', total_pnl: 'P&L', total_trades: 'actividad'})[k])
                .filter(Boolean);
            foot.textContent = `Mejor que el ${Math.max(...pcts)}% en ${top.length ? 'top: ' + top.join(', ') : 'la comunidad'} · ${data.community_size} wallets`;
        })
        .catch(() => { /* silencioso: no bloquea el dashboard */ });
}

function updateWalletProfile(profile) {
    const card = document.getElementById('profileCard');
    if (!card || !profile) return;

    card.style.display = '';

    const emoji = document.getElementById('profileEmoji');
    const title = document.getElementById('profileTitle');
    const desc = document.getElementById('profileDesc');
    const tags = document.getElementById('profileTags');

    if (emoji) emoji.textContent = profile.emoji || '🧠';
    if (title) title.textContent = profile.label || 'Perfil';
    if (desc) desc.textContent = profile.description || '';

    if (tags) {
        tags.innerHTML = '';
        const chips = [];
        (profile.strengths || []).forEach(s => {
            chips.push(`<span class="profile-chip chip-good">✓ ${s}</span>`);
        });
        (profile.weaknesses || []).forEach(w => {
            chips.push(`<span class="profile-chip chip-warn">! ${w}</span>`);
        });
        tags.innerHTML = chips.join('');
    }
}

// Convierte una hora UTC (la que usa el blockchain) a la hora local del navegador
function utcHourToLocal(utcHour) {
    if (utcHour == null) return null;
    const d = new Date(Date.UTC(2026, 0, 1, utcHour, 0));
    return d.getHours();
}

function updateTimeOfDay(tod) {
    state.timeOfDay = tod || null;
    renderOptimalWindow();

    const card = document.getElementById('timeOfDayCard');
    if (!card || !tod) return;

    card.style.display = '';

    const main = document.getElementById('todMain');
    const detail = document.getElementById('todDetail');
    const summary = document.getElementById('todSummary');

    const fmtHour = h => `${String(h).padStart(2, '0')}:00`;

    const bestLocal = utcHourToLocal(tod.best_hour);
    const worstLocal = utcHourToLocal(tod.worst_hour);
    const offsetMin = new Date().getTimezoneOffset();
    const isLocalDifferent = bestLocal !== tod.best_hour;

    if (main) {
        main.innerHTML = `
            <span class="tod-hour">${fmtHour(bestLocal)}</span>
            <span class="tod-pnl ${tod.best_hour_pnl >= 0 ? 'text-green' : 'text-red'}">
                ${tod.best_hour_pnl >= 0 ? '+' : ''}${tod.best_hour_pnl.toFixed(4)} SOL
            </span>`;
        if (isLocalDifferent) {
            main.innerHTML += `<span class="tod-utc">= ${fmtHour(tod.best_hour)} UTC</span>`;
        }
    }

    if (detail) {
        detail.textContent = `${tod.best_hour_trades} trades cerrados · peor franja ${fmtHour(worstLocal)} (${tod.worst_hour_pnl >= 0 ? '+' : ''}${tod.worst_hour_pnl.toFixed(4)} SOL)`;
    }

    if (summary) {
        summary.textContent = tod.summary || '';
        if (isLocalDifferent) {
            summary.textContent += ` (horas convertidas a tu hora local, UTC${offsetMin <= 0 ? '+' : '-'}${Math.abs(offsetMin / 60)})`;
        }
    }
}

// Muestra la ventana horaria óptima en la sección de Auto-Trading
function renderOptimalWindow() {
    const el = document.getElementById('optimalWindow');
    if (!el) return;

    const tod = state.timeOfDay;
    if (!tod || tod.best_hour == null) {
        el.style.display = 'none';
        return;
    }

    const fmtHour = h => `${String(h).padStart(2, '0')}:00`;
    const bestLocal = utcHourToLocal(tod.best_hour);
    const worstLocal = utcHourToLocal(tod.worst_hour);
    const endHour = (bestLocal + 1) % 24;

    el.style.display = 'inline-flex';
    el.innerHTML = `
        <span class="ow-icon">🕐</span>
        <div class="ow-body">
            <strong>Ventana óptima de trading</strong>
            <span>${fmtHour(bestLocal)} – ${fmtHour(endHour)} · evita las ${fmtHour(worstLocal)}</span>
        </div>
    `;
}

function updateHeroStats(metrics) {
    // Total P&L
    const totalPnlEl = document.getElementById('totalPnl');
    if (totalPnlEl) {
        const pnlValue = metrics.total_pnl || 0;
        totalPnlEl.textContent = `${pnlValue >= 0 ? '+' : ''}${pnlValue.toFixed(4)} SOL`;
        totalPnlEl.className = 'hero-value ' + (pnlValue >= 0 ? 'text-green' : 'text-red');
    }

    const pnlChangeText = document.getElementById('pnlChangeText');
    if (pnlChangeText) {
        const pnlValue = metrics.total_pnl || 0;
        if (pnlValue > 0) {
            pnlChangeText.textContent = 'Ganancia Total (realizado + en vivo)';
        } else if (pnlValue < 0) {
            pnlChangeText.textContent = 'Pérdida Total (realizado + en vivo)';
        } else {
            pnlChangeText.textContent = 'Punto de Equilibrio';
        }
    }

    // Win Rate
    const winRateEl = document.getElementById('winRate');
    if (winRateEl) {
        winRateEl.textContent = `${(metrics.win_rate || 0).toFixed(1)}%`;
    }

    const winRateBar = document.getElementById('winRateBar');
    if (winRateBar) {
        winRateBar.style.width = `${metrics.win_rate || 0}%`;
    }

    const winningTrades = document.getElementById('winningTrades');
    const losingTrades = document.getElementById('losingTrades');
    if (winningTrades) winningTrades.textContent = metrics.winning_trades || 0;
    if (losingTrades) losingTrades.textContent = metrics.losing_trades || 0;

    // Total Trades
    const totalTrades = document.getElementById('totalTrades');
    if (totalTrades) {
        totalTrades.textContent = metrics.total_trades || 0;
    }
}

function updateMetricsCards(metrics) {
    // Profit Factor
    const profitFactor = document.getElementById('profitFactor');
    if (profitFactor) {
        const pf = metrics.profit_factor || 0;
        profitFactor.textContent = pf.toFixed(2);
        profitFactor.className = 'metric-value ' + (pf >= 2 ? 'text-green' : pf < 1 ? 'text-red' : '');
    }

    const profitFactorGauge = document.getElementById('profitFactorGauge');
    if (profitFactorGauge) {
        const pf = Math.min(metrics.profit_factor || 0, 3) / 3 * 100;
        profitFactorGauge.style.width = `${pf}%`;
    }

    // Avg Hold Time
    const avgHoldTime = document.getElementById('avgHoldTime');
    if (avgHoldTime) {
        avgHoldTime.textContent = formatDuration(metrics.avg_hold_time_seconds || 0);
    }

    // Best Trade
    const largestWin = document.getElementById('largestWin');
    if (largestWin) {
        const win = metrics.largest_win || 0;
        largestWin.textContent = `+${win.toFixed(4)} SOL`;
    }

    // Total Fees
    const totalFees = document.getElementById('totalFees');
    if (totalFees) {
        totalFees.textContent = `${(metrics.total_fees || 0).toFixed(4)} SOL`;
    }

    // Sharpe Ratio
    const sharpeRatio = document.getElementById('sharpeRatio');
    if (sharpeRatio && metrics.sharpe_ratio != null && !isNaN(metrics.sharpe_ratio)) {
        sharpeRatio.textContent = metrics.sharpe_ratio.toFixed(2);
    } else if (sharpeRatio) {
        sharpeRatio.textContent = '—';
    }

    // Avg Trade Size
    const avgTradeSize = document.getElementById('avgTradeSize');
    if (avgTradeSize) {
        avgTradeSize.textContent = `${(metrics.avg_trade_size || 0).toFixed(4)} SOL`;
    }
}

function updateCharts(tokens, metrics) {
    const pnlChartEl = document.getElementById('pnlMiniChart');
    const pnlTimelineEl = document.getElementById('pnlTimelineChart');
    const pnlByTokenEl = document.getElementById('pnlByTokenChart');

    // Destroy existing charts
    Object.values(charts).forEach(chart => {
        if (chart) chart.destroy();
    });

    const topTokens = (tokens || []).slice(0, 8);

    // P&L Mini Chart
    if (pnlChartEl) {
        const ctx = pnlChartEl.getContext('2d');
        charts.pnl = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: topTokens.map(t => t.token_symbol || '?'),
                datasets: [{
                    data: topTokens.map(t => t.total_pnl || 0),
                    backgroundColor: topTokens.map(t =>
                        (t.total_pnl || 0) >= 0 ? 'rgba(63, 185, 80, 0.8)' : 'rgba(248, 81, 73, 0.8)'
                    ),
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { display: false },
                    x: { display: false }
                }
            }
        });
    }

    // P&L Timeline Chart (evolución de cartera en tiempo real)
    if (pnlTimelineEl && topTokens.length > 0) {
        const ctx = pnlTimelineEl.getContext('2d');
        let cumulative = 0;
        const cumulativePnl = topTokens.map(t => {
            cumulative += (t.total_pnl || 0);
            return cumulative;
        });

        // Guardar el PnL actual en el historial local para trazar la evolución
        const totalNow = metrics.total_pnl || cumulativePnl[cumulativePnl.length - 1] || 0;
        const history = loadPnlHistory();
        history.push({ t: Date.now(), pnl: totalNow });
        // Mantener solo las últimas 120 muestras (~90 min con refresh de 45s)
        while (history.length > 120) history.shift();
        savePnlHistory(history);

        const labels = history.map(h => {
            const d = new Date(h.t);
            return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
        });
        const values = history.map(h => h.pnl);
        const isUp = values[values.length - 1] >= (values[0] || 0);

        // Fusión con la evolución persistida en el servidor: si el historial
        // del servidor tiene más puntos que el local (otro dispositivo, o
        // análisis anteriores), lo usamos como fuente principal.
        const drawTimeline = (labelsIn, valuesIn) => {
            const up = valuesIn[valuesIn.length - 1] >= (valuesIn[0] || 0);
            const mainColor = up ? '#22c55e' : '#ef4444';

            // Curva de equity + máximo drawdown: pico a pico sobre la equity
            let peak = -Infinity;
            let maxDd = 0;
            const drawdowns = valuesIn.map(v => {
                peak = Math.max(peak, v);
                const dd = peak - v;
                maxDd = Math.max(maxDd, dd);
                return -dd; // negativo: se dibuja hacia abajo desde la equity
            });
            const maxDdPct = maxDd !== 0 && peak !== -Infinity && peak !== 0
                ? (maxDd / Math.abs(peak)) * 100 : 0;

            charts.timeline = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labelsIn,
                    datasets: [{
                        label: 'P&L de Cartera (realizado + en vivo)',
                        data: valuesIn,
                        borderColor: mainColor,
                        backgroundColor: up ? 'rgba(34, 197, 94, 0.12)' : 'rgba(239, 68, 68, 0.12)',
                        fill: true,
                        tension: 0.35,
                        pointRadius: 0,
                        borderWidth: 2
                    }, {
                        label: 'Drawdown',
                        data: drawdowns,
                        borderColor: 'rgba(239, 68, 68, 0.0)',
                        backgroundColor: 'rgba(239, 68, 68, 0.08)',
                        fill: '+1',
                        tension: 0.35,
                        pointRadius: 0,
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                afterBody: (items) => {
                                    const i = items[0].dataIndex;
                                    const dd = drawdowns[i] || 0;
                                    return `Drawdown: ${dd < 0 ? (dd).toFixed(4) : '0.0000'} SOL`;
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            grid: { color: 'rgba(255,255,255,0.05)' },
                            ticks: { color: '#8b949e' }
                        },
                        x: {
                            grid: { display: false },
                            ticks: { color: '#8b949e' }
                        }
                    }
                }
            });

            // Badge de drawdown máximo en el header del gráfico
            const ddBadge = document.getElementById('maxDrawdownBadge');
            if (ddBadge) {
                if (maxDd > 0) {
                    ddBadge.textContent = `Max DD ${maxDd.toFixed(2)} SOL (${maxDdPct.toFixed(1)}%)`;
                    ddBadge.style.display = '';
                } else {
                    ddBadge.style.display = 'none';
                }
            }
        };

        // Dibujar primero con el historial local (rápido, sin red)
        drawTimeline(labels, values);

        // Luego, si el servidor tiene más historia, redibujar con ella
        if (state.wallet) {
            apiFetch(`${API_BASE}/api/community/evolution?wallet_address=${encodeURIComponent(state.wallet)}`)
                .then(r => r.json())
                .then(ev => {
                    const pts = (ev.points || []).filter(p => p && typeof p.t === 'number');
                    if (pts.length > history.length) {
                        const sLabels = pts.map(h => {
                            const d = new Date(h.t * 1000);
                            return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
                        });
                        drawTimeline(sLabels, pts.map(h => h.pnl || 0));
                    }
                })
                .catch(() => { /* silencioso */ });
        }
    }

    // P&L by Token Chart
    if (pnlByTokenEl && topTokens.length > 0) {
        const ctx = pnlByTokenEl.getContext('2d');
        charts.byToken = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: topTokens.map(t => t.token_symbol || '?'),
                datasets: [{
                    label: 'P&L (SOL)',
                    data: topTokens.map(t => t.total_pnl || 0),
                    backgroundColor: topTokens.map(t =>
                        (t.total_pnl || 0) >= 0 ? '#22c55e' : '#ef4444'
                    ),
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: {
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { color: '#8b949e' }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: '#8b949e' }
                    }
                }
            }
        });
    }
}

function updateTokensTable(tokens) {
    const tbody = document.getElementById('tokensTableBody');
    if (!tbody) return;

    tbody.innerHTML = '';

    if (!tokens || tokens.length === 0) {
        tbody.innerHTML = '<div class="empty-state"><p>No hay tokens operados</p></div>';
        return;
    }

    tokens.slice(0, 15).forEach(token => {
        const row = document.createElement('div');
        row.className = 'token-table-row';

        // PnL total = realizado + no realizado en tiempo real (posiciones abiertas)
        const unrealized = token.unrealized_pnl_sol || 0;
        const totalPnl = (token.realized_pnl || 0) + unrealized;
        const isHolding = !!token.is_still_holding && token.current_holdings > 0;
        const pnlClass = totalPnl >= 0 ? 'positive' : 'negative';
        const roiClass = (token.roi_percent || 0) >= 0 ? 'positive' : 'negative';

        // Token logo URL (con fallback visual de avatar)
        const logoUrl = token.token_logo || token.logo;

        // Subtexto del PnL no realizado (en vivo) cuando hay posición abierta
        const liveBadge = isHolding && token.unrealized_pnl_sol !== undefined && token.unrealized_pnl_sol !== null
            ? `<span class="col-pnl-live ${unrealized >= 0 ? 'positive' : 'negative'}">${unrealized >= 0 ? '+' : ''}${unrealized.toFixed(4)} en vivo</span>`
            : '';

        row.innerHTML = `
            <div class="col-token">
                <div class="token-cell">
                    ${logoUrl
                        ? `<img src="${logoUrl}" alt="${token.token_symbol || 'TOKEN'}" class="token-cell-logo" onload="tokenLogoLoaded(this)" onerror="tokenLogoFallback(this)">`
                        : `<span class="token-avatar" style="background:${avatarGradient(token.token_symbol)}">${avatarText(token.token_symbol)}</span>`
                    }
                    <div class="token-cell-info">
                        <span class="token-symbol">${token.token_symbol || 'Desconocido'}</span>
                        <span class="token-name">${token.token_name || ''}</span>
                    </div>
                </div>
            </div>
            <div class="col-pnl ${pnlClass}">
                <span class="col-pnl-value">${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(4)} SOL</span>
                ${liveBadge}
            </div>
            <span class="col-roi ${roiClass}">${(token.roi_percent || 0) >= 0 ? '+' : ''}${(token.roi_percent || 0).toFixed(1)}%</span>
            <span class="col-trades">${token.trades_count || 0}</span>
            <span class="col-holdings">${token.current_holdings ? token.current_holdings.toFixed(4) : '0.00'}</span>
        `;
        tbody.appendChild(row);
    });
}

function updateInsights(recommendations) {
    const list = document.getElementById('insightsList');
    if (!list) return;

    list.innerHTML = '';

    if (!recommendations || recommendations.length === 0) {
        list.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:20px;">Analiza tu wallet para ver recomendaciones</p>';
        return;
    }

    recommendations.slice(0, 6).forEach(rec => {
        const item = document.createElement('div');
        item.className = `recommendation-item priority-${rec.priority || 'medium'}`;

        // Icono por tipo de recomendación
        const typeIcons = {
            risk_management: '🛑', risk_reward: '⚖️', hold_time_bias: '⏱️',
            asymmetry: '🔄', position_sizing: '📐', revenge_trading: '🧠',
            cost_reduction: '💰', opportunity: '🎯', diversification: '🧩',
            positive: '🏆', profit_taking: '📈', best_hours: '🕐', no_exit_strategy: '🚪'
        };
        const icon = typeIcons[rec.type] || (rec.priority === 'high' ? '⚠' : rec.priority === 'low' ? '💡' : 'ℹ');

        // Etiqueta de prioridad en español
        const priorityLabels = { high: 'ALTA', medium: 'MEDIA', low: 'INFO' };
        const priorityLabel = priorityLabels[rec.priority] || 'INFO';

        const signalType = rec.type || '';
        const recId = `rec-${signalType}-${item.childElementCount || Date.now()}`;

        item.innerHTML = `
            <div class="recommendation-header">
                <span class="recommendation-icon">${icon}</span>
                <div class="recommendation-body">
                    <div class="recommendation-title">${rec.title}</div>
                    <div class="recommendation-desc">${rec.description}</div>
                </div>
            </div>
            <div class="recommendation-actions">
                <span class="recommendation-priority priority-${rec.priority || 'medium'}">${priorityLabel}</span>
                <button class="rec-vote-btn" data-signal="${signalType}" data-useful="true" title="¿Fue útil?">👍</button>
                <button class="rec-vote-btn" data-signal="${signalType}" data-useful="false" title="No me sirvió">👎</button>
            </div>
        `;
        list.appendChild(item);
    });

    // Delegación de votos: cada clic envía feedback al servidor
    list.onclick = async (e) => {
        const btn = e.target.closest('.rec-vote-btn');
        if (!btn) return;
        btn.disabled = true;
        const signal = btn.dataset.signal;
        const useful = btn.dataset.useful === 'true';
        try {
            const resp = await apiFetch(`${API_BASE}/api/feedback/vote?signal_type=${encodeURIComponent(signal)}&useful=${useful}&wallet_address=${encodeURIComponent(state.wallet || '')}`, { method: 'POST' });
            if (resp.ok) {
                const data = await resp.json();
                const actions = btn.closest('.recommendation-actions');
                actions.querySelectorAll('.rec-vote-btn').forEach(b => b.disabled = true);
                const pct = Math.round((data.score || 0) * 100);
                const badge = actions.querySelector('.recommendation-priority');
                badge.textContent = `${badge.textContent} · ${pct}% útil`;
                showNotification(`Gracias por tu feedback (${pct}% útil)`, 'success');
            }
        } catch (err) {
            console.error('Vote error:', err);
            btn.disabled = false;
        }
    };
}

function updateTransactionsList(transactions) {
    const list = document.getElementById('transactionsList');
    if (!list) return;

    list.innerHTML = '';

    if (!transactions || transactions.length === 0) {
        list.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:20px;">No hay transacciones recientes</p>';
        return;
    }

    transactions.slice(0, 15).forEach(tx => {
        const item = document.createElement('div');
        item.className = 'transaction-item';

        const typeClass = tx.type ? tx.type.toLowerCase() : '';
        const badgeClass = typeClass === 'buy' ? 'buy' : typeClass === 'sell' ? 'sell' : '';
        const typeLabel = typeClass === 'buy' ? 'COMPRA' : typeClass === 'sell' ? 'VENTA' : (tx.type || 'TRADE').toUpperCase();

        const date = tx.timestamp ? new Date(tx.timestamp) : new Date();
        const timeAgo = formatTimeAgo(date);

        // Token logo
        const logoUrl = tx.token_logo || tx.logo;

        item.innerHTML = `
            <div class="tx-info">
                <span class="tx-badge ${badgeClass}">${typeLabel}</span>
                <span class="tx-time">${timeAgo}</span>
            </div>
            <div class="tx-amount">
                <div class="tx-token-info">
                    ${logoUrl
                        ? `<img src="${logoUrl}" alt="${tx.token_symbol || 'TOKEN'}" class="tx-token-logo" onload="tokenLogoLoaded(this)" onerror="tokenLogoFallback(this)">`
                        : `<span class="token-avatar" style="background:${avatarGradient(tx.token_symbol)}">${avatarText(tx.token_symbol)}</span>`
                    }
                    <div class="tx-amounts">
                        <div class="tx-amount-primary">${(tx.token_amount || 0).toFixed(4)} ${tx.token_symbol || 'TOKEN'}</div>
                        <div class="tx-amount-secondary">${(tx.sol_amount || 0).toFixed(4)} SOL</div>
                    </div>
                </div>
            </div>
        `;
        list.appendChild(item);
    });
}

// ============================================================
// PORTFOLIO SECTION
// ============================================================

function updatePortfolioSection(portfolio) {
    if (!portfolio) return;

    // Update SOL balance display
    const solBalanceEl = document.getElementById('portfolioSolBalance');
    if (solBalanceEl) {
        solBalanceEl.textContent = `${(portfolio.sol_balance || 0).toFixed(4)} SOL`;
    }

    // Update tokens count
    const tokensCountEl = document.getElementById('portfolioTokensCount');
    if (tokensCountEl) {
        tokensCountEl.textContent = portfolio.total_tokens || 0;
    }

    // Update total value if available
    const totalValueEl = document.getElementById('portfolioTotalValue');
    if (totalValueEl && portfolio.total_value_usd !== null && portfolio.total_value_usd !== undefined) {
        totalValueEl.textContent = `$${portfolio.total_value_usd.toFixed(2)} USD`;
    }

    // Update portfolio tokens grid
    const portfolioGrid = document.getElementById('portfolioTokensGrid');
    if (!portfolioGrid) return;

    portfolioGrid.innerHTML = '';

    if (!portfolio.tokens || portfolio.tokens.length === 0) {
        portfolioGrid.innerHTML = `
            <div class="portfolio-empty">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                </svg>
                <p>No tienes tokens en esta wallet</p>
            </div>
        `;
        return;
    }

    portfolio.tokens.forEach(token => {
        const card = document.createElement('div');
        card.className = 'portfolio-token-card';

        // Token logo or fallback visual
        const logoUrl = token.logo;
        const tokenName = token.name || token.symbol || 'Token Desconocido';
        const tokenSymbol = token.symbol || 'TOKEN';
        const tokenAmount = formatTokenAmount(token.amount, token.decimals || 9);
        const tokenValue = token.value_usd ? `$${token.value_usd.toFixed(2)}` : '--';

        card.innerHTML = `
            <div class="token-card-header">
                <div class="token-logo-container">
                    ${logoUrl
                        ? `<img src="${logoUrl}" alt="${tokenSymbol}" class="token-logo" onload="tokenLogoLoaded(this)" onerror="tokenLogoFallback(this)">`
                        : `<span class="token-avatar token-avatar-lg" style="background:${avatarGradient(tokenSymbol)}">${avatarText(tokenSymbol)}</span>`
                    }
                </div>
                <div class="token-info">
                    <div class="token-symbol">${tokenSymbol}</div>
                    <div class="token-name">${tokenName}</div>
                </div>
            </div>
            <div class="token-card-body">
                <div class="token-amount">${tokenAmount}</div>
                <div class="token-value">${tokenValue}</div>
            </div>
            <div class="token-card-footer">
                <span class="token-mint" title="${token.mint}">${shortenAddress(token.mint)}</span>
            </div>
        `;

        portfolioGrid.appendChild(card);
    });
}

function formatTokenAmount(amount, decimals) {
    if (!amount || amount === 0) return '0';
    // Format based on amount size
    if (amount < 0.000001) return amount.toExponential(2);
    if (amount < 0.001) return amount.toFixed(6);
    if (amount < 1) return amount.toFixed(4);
    if (amount < 1000) return amount.toFixed(2);
    return amount.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function shortenAddress(address) {
    if (!address) return '';
    return `${address.substring(0, 4)}...${address.substring(address.length - 4)}`;
}

// ============================================================
// FLOW SECTION
// ============================================================

function updateFlowSection(data) {
    const flowOutgoing = document.getElementById('flowOutgoing');
    if (!flowOutgoing) return;

    const totalOut = (data.sol_flows?.outgoing || []).reduce((sum, f) => sum + (f.amount || 0), 0);
    const totalIn = (data.sol_flows?.incoming || []).reduce((sum, f) => sum + (f.amount || 0), 0);
    const netFlow = data.sol_flows?.net || 0;

    // Update cards
    flowOutgoing.textContent = `${totalOut.toFixed(4)} SOL`;

    const flowIncoming = document.getElementById('flowIncoming');
    if (flowIncoming) {
        flowIncoming.textContent = `${totalIn.toFixed(4)} SOL`;
    }

    // Update bars
    const maxFlow = Math.max(totalOut, totalIn, 1);
    const outBar = document.getElementById('flowOutBar');
    const inBar = document.getElementById('flowInBar');

    if (outBar) outBar.style.width = `${(totalOut / maxFlow) * 100}%`;
    if (inBar) inBar.style.width = `${(totalIn / maxFlow) * 100}%`;

    // Update net label
    const netLabel = document.getElementById('flowNetLabel');
    if (netLabel) {
        if (netFlow > 0) {
            netLabel.textContent = `+${netFlow.toFixed(2)} SOL`;
            netLabel.style.color = 'var(--accent-green)';
        } else if (netFlow < 0) {
            netLabel.textContent = `${netFlow.toFixed(2)} SOL`;
            netLabel.style.color = 'var(--accent-red)';
        } else {
            netLabel.textContent = 'Breakeven';
            netLabel.style.color = 'var(--text-secondary)';
        }
    }

    // Animate arrows
    const outArrow = document.getElementById('flowArrowOut');
    const inArrow = document.getElementById('flowArrowIn');

    if (outArrow && inArrow) {
        outArrow.classList.toggle('active', totalOut > totalIn * 1.1);
        inArrow.classList.toggle('active', totalIn > totalOut * 1.1);
    }

    // Update directions list
    updateFlowDirectionsList(data.token_flows || []);

    // Update recommendations
    if (data.recommendations && data.recommendations.length > 0) {
        updateFlowRecommendations(data.recommendations);
    }
}

function updateFlowDirectionsList(tokenFlows) {
    const list = document.getElementById('flowDirectionsList');
    if (!list) return;

    list.innerHTML = '';

    if (!tokenFlows || tokenFlows.length === 0) {
        list.innerHTML = '<div class="flow-empty-state"><p>No hay flujos detectados</p></div>';
        return;
    }

    tokenFlows.slice(0, 8).forEach(token => {
        const item = document.createElement('div');
        item.className = 'flow-direction-item';

        const pnlClass = (token.pnl || 0) >= 0 ? 'positive' : 'negative';
        const recBadge = (token.pnl || 0) > 0 ? 'success' : (token.pnl || 0) < -1 ? 'warning' : 'info';
        const recText = (token.pnl || 0) > 0 ? 'Rentable' : (token.pnl || 0) < -1 ? 'Revisar' : 'Neutral';

        const invested = token.invested || 0;
        const recovered = token.recovered || 0;
        const pnl = token.pnl || 0;
        const roi = token.roi_percent || 0;

        const tokenLogo = token.logo;
        const tokenSymbol = token.symbol || '??';

        item.innerHTML = `
            <div class="flow-token-icon">
                ${tokenLogo
                    ? `<img src="${tokenLogo}" alt="${tokenSymbol}" class="flow-token-logo" onload="tokenLogoLoaded(this)" onerror="tokenLogoFallback(this)">`
                    : `<span class="token-avatar" style="background:${avatarGradient(tokenSymbol)}">${avatarText(tokenSymbol)}</span>`
                }
            </div>
            <div class="flow-token-info">
                <div class="flow-token-symbol">${token.symbol || 'UNKNOWN'}</div>
                <div class="flow-token-status">${(token.buys || 0) + (token.sells || 0)} trades</div>
            </div>
            <div class="flow-token-pnl">
                <div class="flow-pnl-value ${pnlClass}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(3)} SOL</div>
                <div class="flow-pnl-percent ${pnlClass}">${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%</div>
            </div>
            <div class="flow-token-recommendation">
                <span class="flow-rec-badge ${recBadge}">${recText}</span>
            </div>
        `;
        list.appendChild(item);
    });
}

function updateFlowRecommendations(recommendations) {
    const insightsList = document.getElementById('insightsList');
    if (!insightsList) return;

    // Mapear tipo de flujo a prioridad estándar
    const priorityMap = { success: 'low', warning: 'high', info: 'medium' };
    const priorityLabels = { high: 'ALTA', medium: 'MEDIA', low: 'INFO' };

    recommendations.forEach(rec => {
        const priority = priorityMap[rec.type] || 'medium';
        const item = document.createElement('div');
        item.className = `recommendation-item priority-${priority}`;

        const icon = rec.type === 'success' ? '🏆' : rec.type === 'warning' ? '⚠' : 'ℹ';

        item.innerHTML = `
            <div class="recommendation-header">
                <span class="recommendation-icon">${icon}</span>
                <div class="recommendation-body">
                    <div class="recommendation-title">${rec.title}</div>
                    <div class="recommendation-desc">${rec.description}</div>
                </div>
            </div>
            <span class="recommendation-priority priority-${priority}">${priorityLabels[priority]}</span>
        `;
        insightsList.appendChild(item);
    });
}

function updateActivityFeed(events) {
    const list = document.getElementById('flowActivityList');
    const countEl = document.getElementById('flowActivityCount');

    if (!list) return;

    list.innerHTML = '';
    if (countEl) countEl.textContent = `${events.length} eventos`;

    if (!events || events.length === 0) {
        list.innerHTML = '<div class="flow-empty-state"><p>Esperando actividad...</p></div>';
        return;
    }

    events.slice(0, 10).forEach(event => {
        const item = document.createElement('div');
        item.className = `flow-activity-item ${event.type || ''}`;

        const isBuy = event.type === 'buy';
        const iconSvg = isBuy ?
            '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 2v10l-4-4h8l-4 4z"/></svg>' :
            '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 14V4l4 4H4l4 4z"/></svg>';

        const typeLabel = isBuy ? 'COMPRA' : 'VENTA';
        const solAmount = event.data?.sol_amount || 0;
        const tokenSymbol = event.data?.token_symbol || 'TOKEN';
        const reason = event.analysis?.reason || event.analysis?.what_happening || '';

        const date = event.timestamp ? new Date(event.timestamp) : new Date();
        const timeAgo = formatTimeAgo(date);

        item.innerHTML = `
            <div class="flow-activity-icon">${iconSvg}</div>
            <div class="flow-activity-content">
                <div class="flow-activity-type">${typeLabel} • ${tokenSymbol}</div>
                <div class="flow-activity-reason">${reason}</div>
            </div>
            <div class="flow-activity-amount">
                <div class="flow-activity-sol">${solAmount >= 0 ? '+' : ''}${solAmount.toFixed(3)} SOL</div>
                <div class="flow-activity-time">${timeAgo}</div>
            </div>
        `;
        list.appendChild(item);
    });
}

// ============================================================
// UTILITY FUNCTIONS
// ============================================================

// Historial de evolución de cartera (persistido localmente por wallet)
function loadPnlHistory() {
    try {
        const key = `bettertrader_pnl_history_${state.wallet || 'default'}`;
        return JSON.parse(localStorage.getItem(key) || '[]');
    } catch (e) {
        return [];
    }
}

function savePnlHistory(history) {
    try {
        const key = `bettertrader_pnl_history_${state.wallet || 'default'}`;
        localStorage.setItem(key, JSON.stringify(history));
    } catch (e) {
        // localStorage puede fallar en modo privado
    }
}

// Fallback para imágenes de logo que no cargan: muestra un avatar
// con la inicial del símbolo y un color derivado del nombre.
const AVATAR_COLORS = [
    ['#00d9ff', '#7c3aed'], ['#f97316', '#ef4444'], ['#22c55e', '#0ea5e9'],
    ['#eab308', '#ec4899'], ['#06b6d4', '#8b5cf6'], ['#f43f5e', '#f97316'],
    ['#10b981', '#3b82f6'], ['#a855f7', '#ec4899']
];

function avatarGradient(seed) {
    let hash = 0;
    const str = String(seed || 'T');
    for (let i = 0; i < str.length; i++) {
        hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
    }
    const [c1, c2] = AVATAR_COLORS[hash % AVATAR_COLORS.length];
    return `linear-gradient(135deg, ${c1}, ${c2})`;
}

function avatarText(alt) {
    const s = String(alt || 'T').trim();
    // Símbolos de 1-2 caracteres: mostrar ambos (ej. '00' -> '00')
    if (s.length <= 2) return s.toUpperCase() || 'T';
    return s.substring(0, 1).toUpperCase();
}

window.tokenLogoFallback = function(img) {
    // Evitar bucles si el fallback mismo falla
    img.onerror = null;
    img.onload = null;
    const alt = img.getAttribute('alt');
    const avatar = document.createElement('span');
    avatar.className = 'token-avatar';
    avatar.textContent = avatarText(alt);
    avatar.style.background = avatarGradient(alt);
    img.replaceWith(avatar);
};

// Si la imagen no es razonablemente cuadrada (banner, foto alargada),
// se sustituye por el avatar para que la UI no se vea rara.
window.tokenLogoLoaded = function(img) {
    if (!img.complete || !img.naturalWidth || !img.naturalHeight) return;
    const ratio = img.naturalWidth / img.naturalHeight;
    if (ratio > 1.35 || ratio < 0.75) {
        tokenLogoFallback(img);
    }
};

function isValidSolanaAddress(address) {
    const base58Regex = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
    return base58Regex.test(address);
}

function formatDuration(seconds) {
    if (!seconds || seconds === 0) return '--';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
}

function formatTimeAgo(date) {
    if (!date || !(date instanceof Date)) return '--';

    const seconds = Math.floor((new Date() - date) / 1000);

    if (seconds < 60) return 'ahora mismo';
    if (seconds < 3600) return `hace ${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `hace ${Math.floor(seconds / 3600)}h`;
    if (seconds < 604800) return `hace ${Math.floor(seconds / 86400)}d`;
    return date.toLocaleDateString();
}

// ============================================================
// STATE MANAGEMENT
// ============================================================

function showLoading() {
    state.loading = true;
    const loadingState = document.getElementById('loadingState');
    const errorState = document.getElementById('errorState');
    const dashboardView = document.getElementById('dashboardView');
    const analyzeBtn = document.getElementById('analyzeBtn');

    if (loadingState) loadingState.classList.remove('hidden');
    if (errorState) errorState.classList.add('hidden');
    if (dashboardView) dashboardView.classList.add('hidden');
    if (analyzeBtn) analyzeBtn.disabled = true;
}

function hideLoading() {
    state.loading = false;
    const loadingState = document.getElementById('loadingState');
    const analyzeBtn = document.getElementById('analyzeBtn');

    if (loadingState) loadingState.classList.add('hidden');
    if (analyzeBtn) analyzeBtn.disabled = false;
}

function showDashboard() {
    const dashboardView = document.getElementById('dashboardView');
    if (dashboardView) dashboardView.classList.remove('hidden');
}

function showError(message) {
    state.loading = false;
    const loadingState = document.getElementById('loadingState');
    const errorState = document.getElementById('errorState');
    const errorMessage = document.getElementById('errorMessage');
    const dashboardView = document.getElementById('dashboardView');
    const analyzeBtn = document.getElementById('analyzeBtn');

    if (loadingState) loadingState.classList.add('hidden');
    if (errorState) errorState.classList.remove('hidden');
    if (errorMessage) errorMessage.textContent = message;
    if (dashboardView) dashboardView.classList.add('hidden');
    if (analyzeBtn) analyzeBtn.disabled = false;
}

function refreshData() {
    if (state.wallet) {
        analyzeWallet();
    }
}

// Global functions for HTML onclick handlers
window.refreshData = refreshData;
window.loadFlowData = () => {
    if (state.wallet) {
        apiFetch(`${API_BASE}/api/wallet/flow-directions?wallet_address=${state.wallet}`)
            .then(r => r.json())
            .then(data => updateFlowSection(data));
    }
};
window.viewAllTransactions = () => {
    // Scroll to transactions section
    const transactionsPanel = document.querySelector('.transactions-panel');
    if (transactionsPanel) {
        transactionsPanel.scrollIntoView({ behavior: 'smooth' });
    }
};

// ============================================================
// TRADING FUNCTIONS
// ============================================================

const tradingState = {
    tradeType: 'buy', // 'buy' or 'sell'
    currentQuote: null,
    selectedTokens: {
        input: 'So11111111111111111111111111111111111111112', // SOL
        output: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v' // USDC
    },
    popularTokens: [],
    privateKey: null,
    walletConnected: false
};

// Initialize trading section
async function initTrading() {
    try {
        const response = await apiFetch(`${API_BASE}/api/trading/tokens/popular`);
        const data = await response.json();
        if (data.success) {
            tradingState.popularTokens = data.tokens;
            populateTokenSelectors();
        }
    } catch (error) {
        console.error('Error loading tokens:', error);
    }
}

function populateTokenSelectors() {
    const inputSelect = document.getElementById('inputToken');
    const outputSelect = document.getElementById('outputToken');

    if (!inputSelect || !outputSelect) return;

    const options = tradingState.popularTokens.map(token =>
        `<option value="${token.mint}">${token.symbol} - ${token.name}</option>`
    ).join('');

    inputSelect.innerHTML = options;
    outputSelect.innerHTML = options;

    // Set defaults
    inputSelect.value = tradingState.selectedTokens.input;
    outputSelect.value = tradingState.selectedTokens.output;
}

window.setTradeType = function(type) {
    tradingState.tradeType = type;

    const buyBtn = document.querySelector('.trade-type-btn[data-type="buy"]');
    const sellBtn = document.querySelector('.trade-type-btn[data-type="sell"]');
    const inputLabel = document.getElementById('inputTokenLabel');
    const outputLabel = document.getElementById('outputTokenLabel');

    if (buyBtn) buyBtn.classList.toggle('active', type === 'buy');
    if (sellBtn) sellBtn.classList.toggle('active', type === 'sell');
    if (inputLabel) inputLabel.textContent = type === 'buy' ? 'Vender (SOL)' : 'Vender (Token)';
    if (outputLabel) outputLabel.textContent = type === 'buy' ? 'Comprar (Token)' : 'Recibir (SOL)';
};

window.getQuote = async function() {
    const inputToken = document.getElementById('inputToken')?.value;
    const outputToken = document.getElementById('outputToken')?.value;
    const amount = parseFloat(document.getElementById('tradeAmount')?.value);
    const quoteResult = document.getElementById('quoteResult');
    const getQuoteBtn = document.querySelector('.btn-quote');

    if (!inputToken || !outputToken || !amount || amount <= 0) {
        showNotification('Por favor ingresa una cantidad válida', 'error');
        return;
    }

    // Show loading
    if (getQuoteBtn) {
        getQuoteBtn.disabled = true;
        getQuoteBtn.innerHTML = '<span class="btn-spinner"></span> Obteniendo...';
    }

    try {
        const response = await apiFetch(`${API_BASE}/api/trading/quote`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                input_token: inputToken,
                output_token: outputToken,
                amount: amount
            })
        });

        const data = await response.json();

        if (data.success) {
            tradingState.currentQuote = data;

            if (quoteResult) {
                quoteResult.style.display = 'block';
                quoteResult.innerHTML = `
                    <div class="quote-header">
                        <span class="quote-label">Quote Recibido</span>
                        <span class="quote-success">✓</span>
                    </div>
                    <div class="quote-details">
                        <div class="quote-row">
                            <span>Entregas:</span>
                            <strong>${data.input_amount.toFixed(6)}</strong>
                        </div>
                        <div class="quote-row">
                            <span>Recibes (est.):</span>
                            <strong class="text-success">${data.expected_output.toFixed(6)}</strong>
                        </div>
                        <div class="quote-row">
                            <span>Precio ejecución:</span>
                            <strong>${data.execution_price.toFixed(8)}</strong>
                        </div>
                        <div class="quote-row">
                            <span>Impacto precio:</span>
                            <strong class="${data.price_impact_pct > 2 ? 'text-warning' : ''}">
                                ${data.price_impact_pct.toFixed(2)}%
                            </strong>
                        </div>
                        <div class="quote-route">
                            <span class="route-label">Ruta:</span>
                            <div class="route-steps">
                                ${formatRouteSteps(data.route)}
                            </div>
                        </div>
                    </div>
                `;
            }

            // Enable execute button
            const executeBtn = document.getElementById('executeTradeBtn');
            if (executeBtn) {
                executeBtn.disabled = false;
                executeBtn.classList.add('pulse-animation');
            }
        } else {
            showNotification(data.error || 'Error al obtener quote', 'error');
        }
    } catch (error) {
        console.error('Quote error:', error);
        showNotification('Error al conectar con el servidor', 'error');
    } finally {
        if (getQuoteBtn) {
            getQuoteBtn.disabled = false;
            getQuoteBtn.textContent = 'Obtener Quote';
        }
    }
};

function formatRouteSteps(route) {
    if (!route || route.length === 0) return '<span class="route-step">Directo</span>';
    return route.map(r => {
        const label = r.swapInfo?.label || r.label || 'Swap';
        return `<span class="route-step">${label}</span><span class="route-arrow">→</span>`;
    }).join('');
}

window.showTradeModal = function() {
    if (!tradingState.currentQuote) {
        showNotification('Primero obtén un quote', 'warning');
        return;
    }

    const modal = document.getElementById('tradeConfirmModal');
    const modalDetails = document.getElementById('modalTradeDetails');

    if (modalDetails) {
        const inputToken = tradingState.popularTokens.find(t =>
            t.mint === document.getElementById('inputToken')?.value
        );
        const outputToken = tradingState.popularTokens.find(t =>
            t.mint === document.getElementById('outputToken')?.value
        );

        modalDetails.innerHTML = `
            <div class="modal-detail-row">
                <span>Operación:</span>
                <strong class="trade-${tradingState.tradeType}">
                    ${tradingState.tradeType === 'buy' ? 'COMPRA' : 'VENTA'}
                </strong>
            </div>
            <div class="modal-detail-row">
                <span>Desde:</span>
                <strong>${inputToken?.symbol || 'Token'} (${tradingState.currentQuote.input_amount.toFixed(6)})</strong>
            </div>
            <div class="modal-detail-row">
                <span>Hacia:</span>
                <strong>${outputToken?.symbol || 'Token'} (${tradingState.currentQuote.expected_output.toFixed(6)})</strong>
            </div>
            <div class="modal-detail-row">
                <span>Slippage:</span>
                <strong>${document.getElementById('slippageSelect')?.value || 1}%</strong>
            </div>
            <div class="modal-detail-row highlight">
                <span>Impacto precio:</span>
                <strong>${tradingState.currentQuote.price_impact_pct.toFixed(2)}%</strong>
            </div>
        `;
    }

    if (modal) {
        modal.style.display = 'flex';
        modal.classList.add('show');
    }
};

window.closeTradeModal = function() {
    const modal = document.getElementById('tradeConfirmModal');
    if (modal) {
        modal.classList.remove('show');
        setTimeout(() => {
            modal.style.display = 'none';
        }, 200);
    }
};

window.executeTrade = async function() {
    // La ejecución de swaps no recibe claves privadas desde el navegador.
    // El endpoint server-side permanece bloqueado en producción hasta integrar
    // la firma local de Phantom/Solflare.
    // Check if wallet is connected
    if (!tradingState.walletConnected) {
        showNotification('Conecta tu wallet primero', 'warning');
        closeTradeModal();
        return;
    }

    // Nunca enviamos private keys al servidor. La ejecución segura requiere
    // construir y firmar la transacción localmente con el proveedor wallet.
    showNotification('La ejecución online segura se habilitará con firma directa de Phantom/Solflare. Tu private key nunca se envía.', 'warning');
    closeTradeModal();
    return;
};

function addTradeToHistory(trade) {
    const historyList = document.getElementById('tradeHistoryList');
    if (!historyList) return;

    const tradeItem = document.createElement('div');
    tradeItem.className = `trade-item ${trade.status === 'success' ? 'success' : 'failed'}`;
    tradeItem.innerHTML = `
        <div class="trade-item-header">
            <span class="trade-type-badge ${trade.type}">${trade.type.toUpperCase()}</span>
            <span class="trade-time">${new Date().toLocaleTimeString()}</span>
        </div>
        <div class="trade-item-details">
            <div class="trade-pair">
                ${trade.input_token?.slice(0, 4)}... → ${trade.output_token?.slice(0, 4)}...
            </div>
            <div class="trade-amount">
                ${trade.input_amount?.toFixed(6)} → ${trade.expected_output?.toFixed(6)}
            </div>
        </div>
        ${trade.signature ? `
            <div class="trade-signature">
                <a href="https://solscan.io/tx/${trade.signature}" target="_blank">
                    Ver en Solscan →
                </a>
            </div>
        ` : ''}
        ${trade.error ? `<div class="trade-error">${trade.error}</div>` : ''}
    `;

    historyList.insertBefore(tradeItem, historyList.firstChild);

    // Keep only last 10 trades in UI
    while (historyList.children.length > 10) {
        historyList.removeChild(historyList.lastChild);
    }
}

window.quickCreateStrategy = async function(strategyType) {
    if (!state.wallet) {
        showNotification('Analiza una wallet primero', 'warning');
        return;
    }

    const templates = {
        'dca': {
            name: 'DCA SOL/USDT',
            config: {
                interval_hours: 24,
                buy_amount: 10,
                max_buy_price: 150
            }
        },
        'signal': {
            name: 'Signal AI Tracker',
            config: {
                follow_smart_money: true,
                min_confidence: 0.7,
                max_position_size: 500
            }
        },
        'grid': {
            name: 'Grid Trading BONK',
            config: {
                grid_levels: 5,
                spread_percent: 2,
                investment_per_level: 20
            }
        }
    };

    const template = templates[strategyType];
    if (!template) return;

    try {
        const response = await apiFetch(`${API_BASE}/api/trading/strategies`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: template.name,
                strategy_type: strategyType,
                wallet_address: state.wallet,
                token_pair: 'SOL/USDT',
                config: template.config
            })
        });

        const data = await response.json();

        if (data.id) {
            showNotification(`Estrategia "${template.name}" creada: ID ${data.id.slice(0, 8)}...`, 'success');
            loadStrategies();
        } else {
            showNotification(data.error || 'Error al crear estrategia', 'error');
        }
    } catch (error) {
        console.error('Strategy creation error:', error);
        showNotification('Error al crear estrategia', 'error');
    }
};

async function loadStrategies() {
    if (!state.wallet) return;

    try {
        const response = await apiFetch(`${API_BASE}/api/trading/strategies?wallet_address=${state.wallet}`);
        const data = await response.json();

        if (data.success && data.strategies.length > 0) {
            updateStrategiesUI(data.strategies);
        }
    } catch (error) {
        console.error('Error loading strategies:', error);
    }
}

function updateStrategiesUI(strategies) {
    const strategiesList = document.getElementById('strategiesList');
    if (!strategiesList) return;

    strategiesList.innerHTML = strategies.map(s => `
        <div class="strategy-item ${s.is_active ? 'active' : 'inactive'}">
            <div class="strategy-info">
                <span class="strategy-name">${s.name}</span>
                <span class="strategy-type">${s.type.toUpperCase()}</span>
            </div>
            <div class="strategy-pair">${s.token_pair}</div>
            <div class="strategy-status ${s.is_active ? 'running' : 'stopped'}">
                ${s.is_active ? '● Activo' : '○ Detenido'}
            </div>
            ${s.is_active ? `
                <button class="btn-stop-strategy" onclick="stopStrategy('${s.id}')">
                    Detener
                </button>
            ` : ''}
        </div>
    `).join('');
}

window.stopStrategy = async function(strategyId) {
    try {
        const response = await apiFetch(`${API_BASE}/api/trading/strategies/${strategyId}/stop`, {
            method: 'POST'
        });

        const data = await response.json();

        if (data.success) {
            showNotification('Estrategia detenida', 'success');
            loadStrategies();
        } else {
            showNotification(data.error || 'Error al detener estrategia', 'error');
        }
    } catch (error) {
        console.error('Stop strategy error:', error);
        showNotification('Error al detener estrategia', 'error');
    }
};

// Connect wallet for trading (simplified - in production use Phantom/other wallet adapters)
window.connectTradingWallet = async function() {
    // Check if Phantom wallet is available
    if (window.solana && window.solana.isPhantom) {
        try {
            const response = await window.solana.connect();
            tradingState.walletConnected = true;

            // Get public key and save connection state
            const publicKey = response.publicKey.toString();
            tradingState.publicKey = publicKey;

            showNotification('Wallet Phantom conectada', 'success');

            const connectBtn = document.getElementById('connectWalletBtn');
            if (connectBtn) {
                connectBtn.innerHTML = `✓ ${publicKey.slice(0, 4)}...${publicKey.slice(-4)}`;
                connectBtn.classList.add('connected');
            }
        } catch (error) {
            console.error('Wallet connection error:', error);
            showNotification('Error al conectar wallet', 'error');
        }
    } else {
        showNotification('Instala Phantom Wallet para firmar de forma segura', 'warning');
    }
};

// Connect Phantom wallet from modal
window.connectPhantom = async function() {
    if (window.solana && window.solana.isPhantom) {
        try {
            const response = await window.solana.connect();
            tradingState.walletConnected = true;
            const publicKey = response.publicKey.toString();
            tradingState.publicKey = publicKey;

            // Close modal
            const modal = document.getElementById('walletConnectModal');
            if (modal) modal.style.display = 'none';

            showNotification(`Wallet conectada: ${publicKey.slice(0, 4)}...${publicKey.slice(-4)}`, 'success');

            const connectBtn = document.getElementById('connectWalletBtn');
            if (connectBtn) {
                connectBtn.innerHTML = `✓ ${publicKey.slice(0, 4)}...`;
                connectBtn.classList.add('connected');
            }
        } catch (error) {
            console.error('Phantom connection error:', error);
            showNotification('Error al conectar Phantom', 'error');
        }
    } else {
        showNotification('Phantom Wallet no detectado. Descárgalo de phantom.app', 'warning');
        window.open('https://phantom.app', '_blank');
    }
};

// Connect Solflare wallet from modal
window.connectSolflare = async function() {
    if (window.solflare) {
        try {
            const response = await window.solflare.connect();
            tradingState.walletConnected = true;
            const publicKey = response.publicKey.toString();
            tradingState.publicKey = publicKey;

            // Close modal
            const modal = document.getElementById('walletConnectModal');
            if (modal) modal.style.display = 'none';

            showNotification(`Wallet Solflare conectada: ${publicKey.slice(0, 4)}...${publicKey.slice(-4)}`, 'success');

            const connectBtn = document.getElementById('connectWalletBtn');
            if (connectBtn) {
                connectBtn.innerHTML = `✓ ${publicKey.slice(0, 4)}...`;
                connectBtn.classList.add('connected');
            }
        } catch (error) {
            console.error('Solflare connection error:', error);
            showNotification('Error al conectar Solflare', 'error');
        }
    } else {
        showNotification('Solflare Wallet no detectado. Descárgalo de solflare.com', 'warning');
        window.open('https://solflare.com', '_blank');
    }
};

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        background: ${type === 'success' ? '#10b981' : type === 'error' ? '#ef4444' : type === 'warning' ? '#f59e0b' : '#3b82f6'};
        color: white;
        border-radius: 8px;
        z-index: 10000;
        animation: slideIn 0.3s ease;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    `;

    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// Add CSS animations
const styleSheet = document.createElement('style');
styleSheet.textContent = `
    @keyframes slideIn {
        from { transform: translateX(100%); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOut {
        from { transform: translateX(0); opacity: 1; }
        to { transform: translateX(100%); opacity: 0; }
    }
    @keyframes pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(0, 217, 255, 0.4); }
        50% { box-shadow: 0 0 0 10px rgba(0, 217, 255, 0); }
    }
    .pulse-animation {
        animation: pulse 2s infinite;
    }
    .btn-spinner {
        display: inline-block;
        width: 14px;
        height: 14px;
        border: 2px solid rgba(255,255,255,0.3);
        border-top-color: white;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    .text-success { color: #10b981; }
    .text-warning { color: #f59e0b; }
    .hidden { display: none !important; }
`;
document.head.appendChild(styleSheet);

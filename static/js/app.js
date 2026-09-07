/**
 * Rehabilitation AI System - Main Application JavaScript
 * Handles global functionality, WebSocket connections, and utilities
 */

// ============================================
// Security: escape user-controlled text before it goes into innerHTML.
// Patient names, registration details, support tickets, etc. are all
// free text typed by users - always pass them through this before
// interpolating into an innerHTML template string.
// ============================================
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str ?? '';
    return div.innerHTML;
}

// Global state
const AppState = {
    isCameraActive: false,
    isSessionActive: false,
    currentPatientId: null,
    wsConnection: null,
    sessionData: {
        startTime: null,
        repCount: 0,
        accuracy: 0,
        rom: 0,
        stability: 0
    }
};

// ============================================
// DOM Ready
// ============================================
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

function initializeApp() {
    // Remove loading overlay
    setTimeout(() => {
        const overlay = document.getElementById('loadingOverlay');
        if (overlay) {
            overlay.classList.add('hidden');
            setTimeout(() => overlay.remove(), 500);
        }
    }, 500);
    
    // Setup event listeners
    setupEventListeners();
    
    // Check system health
    checkSystemHealth();

    // Premium interaction layer
    initTopBarScrollShadow();
    initButtonRipples();
    initScrollReveal();

    // New: functional top-bar search + notification dropdown
    initGlobalSearch();
    initNotificationBell();
}

// ============================================
// Global Search (top bar)
// Lives on every page; Enter jumps to the Patients page pre-filtered.
// On the Patients page itself, typing here mirrors into the page's
// own search box instead of navigating away.
// ============================================
function initGlobalSearch() {
    const input = document.getElementById('globalSearch');
    if (!input) return;

    const onPatientsPage = window.location.pathname.startsWith('/patients');

    input.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        const query = input.value.trim();
        if (onPatientsPage) {
            const local = document.getElementById('searchInput');
            if (local) {
                local.value = query;
                local.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
            }
            return;
        }
        if (!query) return;
        window.location.href = `/patients?q=${encodeURIComponent(query)}`;
    });
}

// ============================================
// Notification Bell
// Reuses the existing recent-activity endpoint so no new backend
// route is needed; shows the same events as a lightweight dropdown.
// ============================================
function initNotificationBell() {
    const btn = document.getElementById('notifBtn');
    const panel = document.getElementById('notifPanel');
    const list = document.getElementById('notifList');
    const dot = document.getElementById('notifDot');
    if (!btn || !panel || !list) return;

    let loaded = false;

    btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const opening = !panel.classList.contains('open');
        panel.classList.toggle('open', opening);
        if (opening && !loaded) {
            loaded = true;
            try {
                const res = await fetch('/api/dashboard/recent-activity?limit=8');
                const rows = await res.json();
                if (!Array.isArray(rows) || rows.length === 0) {
                    list.innerHTML = '<div class="notif-empty">No recent activity</div>';
                } else {
                    list.innerHTML = rows.map(r => `
                        <div class="notif-item">
                            <i class="fas fa-circle-notch"></i>
                            <div>
                                <div class="notif-title">${r.action || ''} ${r.entity_type || ''}${r.entity_id ? ' #' + r.entity_id : ''}</div>
                                <div class="notif-time">${typeof formatDate === 'function' && r.timestamp ? formatDate(r.timestamp) : (r.timestamp || '')}</div>
                            </div>
                        </div>
                    `).join('');
                }
                if (dot) dot.style.display = 'none';
            } catch (err) {
                list.innerHTML = '<div class="notif-empty">Couldn\'t load activity</div>';
            }
        }
    });

    document.addEventListener('click', (e) => {
        if (!panel.contains(e.target) && e.target !== btn) {
            panel.classList.remove('open');
        }
    });
}

// ============================================
// Premium Interaction Layer
// (top-bar scroll shadow, button ripples, scroll reveal —
//  all additive, no markup changes required, reduced-motion aware)
// ============================================
const _prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function initTopBarScrollShadow() {
    const topBar = document.querySelector('.top-bar');
    if (!topBar) return;
    const onScroll = () => {
        topBar.classList.toggle('scrolled', window.scrollY > 4);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
}

function initButtonRipples() {
    if (_prefersReducedMotion) return;
    const selector = '.btn-primary, .btn-secondary, .btn-danger, .btn-icon, .join-btn, .chart-btn, .filter-btn, .dur-btn, .ctrl-btn';
    document.addEventListener('click', (e) => {
        const btn = e.target.closest(selector);
        if (!btn || btn.disabled) return;

        const rect = btn.getBoundingClientRect();
        const size = Math.max(rect.width, rect.height);
        const ripple = document.createElement('span');
        ripple.className = 'ripple';
        ripple.style.width = ripple.style.height = `${size}px`;
        ripple.style.left = `${e.clientX - rect.left - size / 2}px`;
        ripple.style.top = `${e.clientY - rect.top - size / 2}px`;

        const prevPosition = getComputedStyle(btn).position;
        if (prevPosition === 'static') btn.style.position = 'relative';
        btn.appendChild(ripple);
        ripple.addEventListener('animationend', () => ripple.remove());
    });
}

function initScrollReveal() {
    const targets = document.querySelectorAll('.chart-container, .stat-card, .table-container:not(.animate-slide-up)');
    if (!targets.length || _prefersReducedMotion || !('IntersectionObserver' in window)) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add('in-view');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });

    targets.forEach((el) => {
        // Only add the reveal hook to content below the first viewport,
        // so above-the-fold content still appears instantly on load.
        if (el.getBoundingClientRect().top > window.innerHeight * 0.9) {
            el.classList.add('reveal-on-scroll');
            observer.observe(el);
        }
    });
}

// ============================================
// Event Listeners
// ============================================
function setupEventListeners() {
    // Global keyboard shortcuts
    document.addEventListener('keydown', function(e) {
        // Escape key to close modals
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal.active').forEach(modal => {
                modal.classList.remove('active');
            });
        }
    });
    
    // Auto-hide flash messages
    document.querySelectorAll('.flash-message').forEach(msg => {
        setTimeout(() => {
            msg.style.opacity = '0';
            setTimeout(() => msg.remove(), 300);
        }, 5000);
    });
}

// ============================================
// System Health Check
// ============================================
async function checkSystemHealth() {
    try {
        const response = await fetch('/api/health');
        const data = await response.json();
        
        if (data.status !== 'healthy') {
            showToast('System health check failed', 'error');
        }
    } catch (error) {
        console.error('Health check error:', error);
    }
}

// ============================================
// Toast Notifications
// ============================================
function showToast(message, type = 'success', duration = 3000) {
    // Remove existing toasts
    const existingToasts = document.querySelectorAll('.toast');
    existingToasts.forEach(toast => toast.remove());
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : 'info-circle'}"></i>
        <span>${message}</span>
    `;
    document.body.appendChild(toast);
    
    // Trigger show animation
    requestAnimationFrame(() => {
        toast.classList.add('show');
    });
    
    // Auto-hide
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// ============================================
// Date/Time Utilities
// ============================================
function formatDate(date) {
    if (typeof date === 'string') {
        date = new Date(date);
    }
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

function formatTime(date) {
    if (typeof date === 'string') {
        date = new Date(date);
    }
    return date.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit'
    });
}

function formatDateTime(date) {
    return `${formatDate(date)} at ${formatTime(date)}`;
}

function formatDuration(seconds) {
    if (!seconds) return '00:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

// ============================================
// API Helpers
// ============================================
async function apiRequest(url, options = {}) {
    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || data.message || 'API request failed');
        }
        
        return data;
    } catch (error) {
        console.error('API Error:', error);
        showToast(error.message || 'Something went wrong', 'error');
        throw error;
    }
}

// ============================================
// Patient Management
// ============================================
async function loadPatientSelect(selectId) {
    try {
        const patients = await apiRequest('/api/patients');
        const select = document.getElementById(selectId);
        if (!select) return;
        
        // Clear existing options
        select.innerHTML = '<option value="">Select Patient</option>';
        
        patients.forEach(patient => {
            const option = document.createElement('option');
            option.value = patient.id;
            option.textContent = patient.name;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading patients:', error);
    }
}

// ============================================
// Form Validation
// ============================================
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return true;
    
    const inputs = form.querySelectorAll('input[required], select[required], textarea[required]');
    let isValid = true;
    
    inputs.forEach(input => {
        if (!input.value.trim()) {
            input.classList.add('error');
            isValid = false;
        } else {
            input.classList.remove('error');
        }
    });
    
    if (!isValid) {
        showToast('Please fill in all required fields', 'error');
    }
    
    return isValid;
}

// ============================================
// Number Formatting
// ============================================
function formatPercentage(value) {
    return `${Math.round(value)}%`;
}

function formatAngle(value) {
    return `${Math.round(value)}°`;
}

function formatNumber(value, decimals = 1) {
    return Number(value).toFixed(decimals);
}

// ============================================
// Chart Color Palette
// ============================================
const ChartColors = {
    green: '#48BB78',
    greenLight: 'rgba(72, 187, 120, 0.2)',
    blue: '#63B3ED',
    blueLight: 'rgba(99, 179, 237, 0.2)',
    orange: '#ED8936',
    orangeLight: 'rgba(237, 137, 54, 0.2)',
    red: '#F56565',
    redLight: 'rgba(245, 101, 101, 0.2)',
    purple: '#9F7AEA',
    purpleLight: 'rgba(159, 122, 234, 0.2)',
    cyan: '#4FD1C5',
    cyanLight: 'rgba(79, 209, 197, 0.2)',
    grey: '#718096',
    greyLight: 'rgba(113, 128, 150, 0.2)'
};

// ============================================
// Export for use in other files
// ============================================
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        AppState,
        showToast,
        formatDate,
        formatTime,
        formatDateTime,
        formatDuration,
        apiRequest,
        loadPatientSelect,
        validateForm,
        formatPercentage,
        formatAngle,
        formatNumber,
        ChartColors
    };
}
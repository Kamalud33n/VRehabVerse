/**
 * Shared auth helpers - included on every protected page.
 * Handles: current-user info in the topbar avatar, the styled logout
 * confirmation popup (Yes/No), and calling /auth/logout for real.
 */

let __currentUser = null;

/**
 * Shared first-letter-avatar helpers. Used everywhere a profile picture
 * can appear (topbar avatar, topbar quick-view popup, Settings /
 * Admin Profile personal-details photo) so the "no picture" fallback is
 * always the same: the first letter of the user's name, not a generic
 * icon. Example: "Kamaludeen" -> "K", "John Doe" -> "J".
 */
function getAvatarInitial(fullName) {
    const trimmed = (fullName || '').trim();
    return trimmed ? trimmed.charAt(0).toUpperCase() : '?';
}

/**
 * Returns the inner HTML for an avatar container: the actual profile
 * picture if one is set, otherwise a first-letter avatar styled to match
 * the existing circular avatar containers (which already provide the
 * background/color via CSS - this just fills the content).
 */
function getAvatarInnerHTML(fullName, picturePath) {
    if (picturePath) {
        const safeName = (fullName || '').replace(/"/g, '&quot;');
        return `<img src="${picturePath}" alt="${safeName}" style="width:100%;height:100%;object-fit:cover;border-radius:inherit;">`;
    }
    return `<span style="font-weight:700;">${getAvatarInitial(fullName)}</span>`;
}

async function loadCurrentUser() {
    try {
        const res = await fetch('/auth/me', { credentials: 'same-origin' });
        if (!res.ok) {
            window.location.href = '/login';
            return null;
        }
        __currentUser = await res.json();
        applyCurrentUserToTopbar(__currentUser);
        return __currentUser;
    } catch (e) {
        console.error('loadCurrentUser:', e);
        return null;
    }
}

function applyCurrentUserToTopbar(user) {
    if (!user) return;
    const avatar = document.querySelector('.topbar-avatar');
    if (avatar) {
        avatar.innerHTML = getAvatarInnerHTML(user.full_name, user.profile_picture_path);
        avatar.title = `${user.full_name}${user.hospital_name ? ' · ' + user.hospital_name : ''}`;
        avatar.style.cursor = 'pointer';
        avatar.onclick = () => openTopbarProfilePopup(user);
    }
}

/**
 * Top-bar avatar click - a quick read-only "who am I" popup. It only ever
 * shows personal details (name, role, email, phone, hospital) and never
 * navigates anywhere else. Editing profile / security / hospital / support
 * stuff all lives on the Settings page (or Admin Profile for admins).
 */
function ensureTopbarProfileModal() {
    if (document.getElementById('topbarProfileModal')) return;

    const modal = document.createElement('div');
    modal.id = 'topbarProfileModal';
    modal.className = 'modal';
    modal.style.display = 'none';
    modal.innerHTML = `
        <div class="modal-content profile-modal-content" style="max-width:400px;">
            <div class="modal-header">
                <h3 style="margin:0;font-size:15px;font-weight:700;">My profile</h3>
                <button class="modal-close" id="topbarProfileCloseBtn"><i class="fas fa-times"></i></button>
            </div>
            <div style="padding:22px;display:flex;flex-direction:column;gap:16px;">
                <div style="display:flex;align-items:center;gap:14px;">
                    <div id="topbarProfilePic" style="width:56px;height:56px;border-radius:50%;background:var(--primary-tint);color:var(--primary);display:flex;align-items:center;justify-content:center;font-size:1.4rem;overflow:hidden;flex-shrink:0;">
                        <span style="font-weight:700;">?</span>
                    </div>
                    <div>
                        <div id="topbarProfileName" style="font-size:1.05rem;font-weight:800;color:var(--text-primary);">—</div>
                        <div id="topbarProfileRole" style="font-size:12.5px;color:var(--text-secondary);">—</div>
                    </div>
                </div>
                <div style="display:flex;flex-direction:column;gap:10px;font-size:13px;">
                    <div><span style="color:var(--text-muted);">Email</span><div id="topbarProfileEmail" style="font-weight:600;color:var(--text-primary);">—</div></div>
                    <div><span style="color:var(--text-muted);">Phone</span><div id="topbarProfilePhone" style="font-weight:600;color:var(--text-primary);">—</div></div>
                    <div id="topbarProfileHospitalRow" style="display:none;"><span style="color:var(--text-muted);">Hospital</span><div id="topbarProfileHospital" style="font-weight:600;color:var(--text-primary);">—</div></div>
                </div>
            </div>
        </div>`;
    document.body.appendChild(modal);

    document.getElementById('topbarProfileCloseBtn').onclick = () => modal.style.display = 'none';
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.style.display = 'none'; });
}

function openTopbarProfilePopup(user) {
    ensureTopbarProfileModal();
    document.getElementById('topbarProfileName').textContent = user.full_name || '—';
    document.getElementById('topbarProfileRole').textContent = user.role === 'super_admin' ? 'Super Admin' : (user.job_title || 'Therapist');
    document.getElementById('topbarProfileEmail').textContent = user.email || '—';
    const phone = [user.country_code, user.phone_number].filter(Boolean).join(' ');
    document.getElementById('topbarProfilePhone').textContent = phone || '—';

    const hospitalRow = document.getElementById('topbarProfileHospitalRow');
    if (user.hospital_name) {
        document.getElementById('topbarProfileHospital').textContent = user.hospital_name;
        hospitalRow.style.display = '';
    } else {
        hospitalRow.style.display = 'none';
    }

    const pic = document.getElementById('topbarProfilePic');
    pic.innerHTML = getAvatarInnerHTML(user.full_name, user.profile_picture_path);

    document.getElementById('topbarProfileModal').style.display = 'flex';
}

function ensureLogoutModal() {
    if (document.getElementById('logoutConfirmModal')) return;

    const modal = document.createElement('div');
    modal.id = 'logoutConfirmModal';
    modal.className = 'auth-modal-overlay';
    modal.innerHTML = `
        <div class="auth-modal-card">
            <div class="auth-modal-icon"><i class="fas fa-sign-out-alt"></i></div>
            <h3>Are you sure you want to logout?</h3>
            <p>You'll need to sign in again to access your dashboard.</p>
            <div class="auth-modal-actions">
                <button class="auth-modal-btn auth-modal-btn-ghost" id="logoutCancelBtn">No</button>
                <button class="auth-modal-btn auth-modal-btn-danger" id="logoutConfirmBtn">Yes, logout</button>
            </div>
        </div>`;
    document.body.appendChild(modal);

    document.getElementById('logoutCancelBtn').onclick = () => modal.classList.remove('open');
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.remove('open'); });
    document.getElementById('logoutConfirmBtn').onclick = async () => {
        try {
            await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
        } catch (e) {
            console.error('logout:', e);
        } finally {
            window.location.href = '/';
        }
    };
}

function handleLogout() {
    ensureLogoutModal();
    document.getElementById('logoutConfirmModal').classList.add('open');
}

document.addEventListener('DOMContentLoaded', () => {
    ensureLogoutModal();
    loadCurrentUser();
});
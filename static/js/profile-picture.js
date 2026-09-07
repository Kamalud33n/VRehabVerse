/**
 * Shared profile-picture handling: crop editor modal, upload, and delete.
 * Included on any page that lets the user manage their own profile
 * picture (Settings, Admin Profile). Depends on auth.js already being
 * loaded first, for getAvatarInnerHTML()/getAvatarInitial().
 *
 * Output image is always a fixed-size square JPEG - the backend also
 * normalizes it again server-side (belt and suspenders), so the final
 * saved file is guaranteed to be a consistent profile-picture format
 * regardless of what the user originally selected.
 */

const PROFILE_PIC_OUTPUT_SIZE = 480; // px, square output sent to the backend
const CROP_STAGE_SIZE = 280;         // px, on-screen crop viewport (CSS: .crop-editor-stage)

let _cropState = null; // { img, scale, minScale, offsetX, offsetY, dragging, lastX, lastY }
let _cropResolve = null;

function _ensureCropModal() {
    if (document.getElementById('profilePicCropModal')) return;

    const modal = document.createElement('div');
    modal.id = 'profilePicCropModal';
    modal.className = 'modal';
    modal.style.display = 'none';
    modal.innerHTML = `
        <div class="modal-content profile-modal-content" style="max-width:380px;">
            <div class="modal-header">
                <h3 style="margin:0;font-size:15px;font-weight:700;">Adjust your photo</h3>
                <button class="modal-close" id="cropModalCloseBtn"><i class="fas fa-times"></i></button>
            </div>
            <div style="padding:22px;">
                <div class="crop-editor-stage" id="cropEditorStage">
                    <canvas id="cropEditorCanvas" width="${CROP_STAGE_SIZE}" height="${CROP_STAGE_SIZE}"></canvas>
                </div>
                <input type="range" class="crop-editor-zoom" id="cropZoomSlider" min="1" max="3" step="0.01" value="1">
                <div class="crop-editor-hint">Drag to reposition &middot; use the slider to zoom</div>
            </div>
            <div class="modal-footer">
                <button class="btn-secondary" id="cropCancelBtn">Cancel</button>
                <button class="btn-primary" id="cropSaveBtn"><i class="fas fa-check"></i> Save photo</button>
            </div>
        </div>`;
    document.body.appendChild(modal);

    const stage = document.getElementById('cropEditorStage');
    const canvas = document.getElementById('cropEditorCanvas');
    const zoomSlider = document.getElementById('cropZoomSlider');

    function draw() {
        if (!_cropState) return;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, CROP_STAGE_SIZE, CROP_STAGE_SIZE);
        const { img, scale, offsetX, offsetY } = _cropState;
        const w = img.width * scale;
        const h = img.height * scale;
        ctx.drawImage(img, offsetX, offsetY, w, h);
    }

    function clampOffsets() {
        if (!_cropState) return;
        const { img, scale } = _cropState;
        const w = img.width * scale;
        const h = img.height * scale;
        // Keep the image covering the whole circular stage - never let a
        // gap show at the edges, never let it drift further than needed.
        _cropState.offsetX = Math.min(0, Math.max(CROP_STAGE_SIZE - w, _cropState.offsetX));
        _cropState.offsetY = Math.min(0, Math.max(CROP_STAGE_SIZE - h, _cropState.offsetY));
    }

    function onPointerDown(e) {
        if (!_cropState) return;
        _cropState.dragging = true;
        const p = e.touches ? e.touches[0] : e;
        _cropState.lastX = p.clientX;
        _cropState.lastY = p.clientY;
    }
    function onPointerMove(e) {
        if (!_cropState || !_cropState.dragging) return;
        const p = e.touches ? e.touches[0] : e;
        const dx = p.clientX - _cropState.lastX;
        const dy = p.clientY - _cropState.lastY;
        _cropState.lastX = p.clientX;
        _cropState.lastY = p.clientY;
        _cropState.offsetX += dx;
        _cropState.offsetY += dy;
        clampOffsets();
        draw();
        e.preventDefault();
    }
    function onPointerUp() {
        if (_cropState) _cropState.dragging = false;
    }

    stage.addEventListener('mousedown', onPointerDown);
    window.addEventListener('mousemove', onPointerMove);
    window.addEventListener('mouseup', onPointerUp);
    stage.addEventListener('touchstart', onPointerDown, { passive: true });
    stage.addEventListener('touchmove', onPointerMove, { passive: false });
    stage.addEventListener('touchend', onPointerUp);

    zoomSlider.addEventListener('input', () => {
        if (!_cropState) return;
        const newScale = _cropState.minScale * parseFloat(zoomSlider.value);
        // Zoom around the stage center so the visible crop area doesn't jump.
        const cx = CROP_STAGE_SIZE / 2;
        const cy = CROP_STAGE_SIZE / 2;
        const ratio = newScale / _cropState.scale;
        _cropState.offsetX = cx - (cx - _cropState.offsetX) * ratio;
        _cropState.offsetY = cy - (cy - _cropState.offsetY) * ratio;
        _cropState.scale = newScale;
        clampOffsets();
        draw();
    });

    function closeModal(result) {
        modal.style.display = 'none';
        _cropState = null;
        if (_cropResolve) {
            const resolve = _cropResolve;
            _cropResolve = null;
            resolve(result);
        }
    }

    document.getElementById('cropModalCloseBtn').onclick = () => closeModal(null);
    document.getElementById('cropCancelBtn').onclick = () => closeModal(null);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(null); });

    document.getElementById('cropSaveBtn').onclick = () => {
        if (!_cropState) { closeModal(null); return; }
        const { img, scale, offsetX, offsetY } = _cropState;

        // Render the currently-visible circular crop area onto a fresh
        // fixed-size output canvas, mapping stage pixels -> source image
        // pixels using the same scale/offset the user just set up.
        const out = document.createElement('canvas');
        out.width = PROFILE_PIC_OUTPUT_SIZE;
        out.height = PROFILE_PIC_OUTPUT_SIZE;
        const octx = out.getContext('2d');
        const factor = PROFILE_PIC_OUTPUT_SIZE / CROP_STAGE_SIZE;
        octx.drawImage(
            img,
            offsetX * factor, offsetY * factor,
            img.width * scale * factor, img.height * scale * factor
        );

        out.toBlob((blob) => {
            closeModal(blob);
        }, 'image/jpeg', 0.92);
    };
}

/**
 * Opens the crop editor for a given File (from an <input type="file">).
 * Resolves with a Blob (the cropped, square JPEG) or null if cancelled.
 */
function openProfilePicCropEditor(file) {
    _ensureCropModal();
    return new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const img = new Image();
            img.onload = () => {
                const minScale = Math.max(CROP_STAGE_SIZE / img.width, CROP_STAGE_SIZE / img.height);
                _cropState = {
                    img,
                    scale: minScale,
                    minScale,
                    offsetX: (CROP_STAGE_SIZE - img.width * minScale) / 2,
                    offsetY: (CROP_STAGE_SIZE - img.height * minScale) / 2,
                    dragging: false,
                    lastX: 0,
                    lastY: 0,
                };
                document.getElementById('cropZoomSlider').value = 1;
                _cropResolve = resolve;
                const modal = document.getElementById('profilePicCropModal');
                modal.style.display = 'flex';
                // Draw once the modal is actually visible/laid out.
                requestAnimationFrame(() => {
                    const canvas = document.getElementById('cropEditorCanvas');
                    const ctx = canvas.getContext('2d');
                    ctx.clearRect(0, 0, CROP_STAGE_SIZE, CROP_STAGE_SIZE);
                    ctx.drawImage(img, _cropState.offsetX, _cropState.offsetY, img.width * _cropState.scale, img.height * _cropState.scale);
                });
            };
            img.onerror = () => resolve(null);
            img.src = e.target.result;
        };
        reader.onerror = () => resolve(null);
        reader.readAsDataURL(file);
    });
}

async function uploadProfilePictureBlob(blob) {
    const formData = new FormData();
    formData.append('file', blob, 'profile.jpg');
    const res = await fetch('/api/profile/picture', { method: 'POST', credentials: 'same-origin', body: formData });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Upload failed');
    }
    return res.json();
}

async function deleteProfilePictureRequest() {
    const res = await fetch('/api/profile/picture', { method: 'DELETE', credentials: 'same-origin' });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to remove photo');
    }
    return res.json();
}

/**
 * Renders the current avatar (picture or first-letter fallback) into the
 * given wrap element, and shows/hides the delete button next to it.
 */
function renderProfilePicWrap(wrapId, deleteBtnId, fullName, picturePath) {
    const wrap = document.getElementById(wrapId);
    if (wrap) wrap.innerHTML = getAvatarInnerHTML(fullName, picturePath);

    const delBtn = document.getElementById(deleteBtnId);
    if (delBtn) delBtn.style.display = picturePath ? 'flex' : 'none';
}

/**
 * Wires up the upload input + delete button for a profile-picture control.
 * Call once per page, after the elements exist in the DOM.
 *
 * opts: {
 *   wrapId, uploadInputId, deleteBtnId,
 *   getFullName: () => currently displayed full name (for the fallback initial),
 *   onUpdated: (newPicturePath) => void   // called after a successful upload/delete
 * }
 */
function wireProfilePictureControls(opts) {
    const input = document.getElementById(opts.uploadInputId);
    const delBtn = document.getElementById(opts.deleteBtnId);

    if (input) {
        input.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            input.value = ''; // allow re-selecting the same file later
            if (!file) return;

            const croppedBlob = await openProfilePicCropEditor(file);
            if (!croppedBlob) return; // cancelled

            try {
                const data = await uploadProfilePictureBlob(croppedBlob);
                renderProfilePicWrap(opts.wrapId, opts.deleteBtnId, opts.getFullName(), data.profile_picture_path);
                if (opts.onUpdated) opts.onUpdated(data.profile_picture_path);
            } catch (err) {
                alert(err.message || 'Upload failed');
            }
        });
    }

    if (delBtn) {
        delBtn.addEventListener('click', async () => {
            if (!confirm('Remove your profile picture?')) return;
            try {
                await deleteProfilePictureRequest();
                renderProfilePicWrap(opts.wrapId, opts.deleteBtnId, opts.getFullName(), null);
                if (opts.onUpdated) opts.onUpdated(null);
            } catch (err) {
                alert(err.message || 'Failed to remove photo');
            }
        });
    }
}

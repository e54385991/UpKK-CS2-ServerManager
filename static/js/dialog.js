/**
 * Dialog Utility for CS2 Server Manager
 * Provides Bootstrap modal-based dialogs to replace alert()
 * Supports i18n and different dialog types (success, error, warning, info)
 */

class DialogManager {
    constructor() {
        this.modalId = 'globalDialogModal';
        this.modal = null;
        this.bsModal = null;
        this.currentConfirmHandler = null;
        this.currentCancelHandler = null;
        this.initializeModal();
    }

    /**
     * Initialize the Bootstrap modal element
     */
    initializeModal() {
        // Check if modal already exists
        let existingModal = document.getElementById(this.modalId);
        if (existingModal) {
            existingModal.remove();
        }

        // Create modal HTML structure
        const modalHTML = `
            <div class="modal fade" id="${this.modalId}" tabindex="-1" aria-labelledby="${this.modalId}Label" aria-hidden="true">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title" id="${this.modalId}Label">
                                <i class="bi" id="${this.modalId}Icon"></i>
                                <span id="${this.modalId}Title"></span>
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body" id="${this.modalId}Body">
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn" id="${this.modalId}ConfirmBtn" data-bs-dismiss="modal"></button>
                            <button type="button" class="btn btn-secondary" id="${this.modalId}CancelBtn" data-bs-dismiss="modal" style="display: none;"></button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Add modal to body
        document.body.insertAdjacentHTML('beforeend', modalHTML);

        // Get modal element
        this.modal = document.getElementById(this.modalId);
        
        // Initialize Bootstrap modal
        this.bsModal = new bootstrap.Modal(this.modal);
    }

    /**
     * Get translated text using i18n if available
     */
    translate(key, fallback) {
        if (window.i18n && window.i18n.isInitialized) {
            return window.i18n.t(key) || fallback;
        }
        return fallback;
    }

    /**
     * Show a dialog with custom options
     * @param {Object} options - Dialog options
     * @param {string} options.type - Dialog type: 'success', 'error', 'warning', 'info', 'confirm'
     * @param {string} options.title - Dialog title (can be i18n key)
     * @param {string} options.message - Dialog message
     * @param {Function} options.onConfirm - Callback for confirm button
     * @param {Function} options.onCancel - Callback for cancel button
     * @param {string} options.confirmText - Custom confirm button text
     * @param {string} options.cancelText - Custom cancel button text
     */
    show(options = {}) {
        const {
            type = 'info',
            title = '',
            message = '',
            onConfirm = null,
            onCancel = null,
            confirmText = null,
            cancelText = null
        } = options;

        // Set icon and header color based on type
        const iconElement = document.getElementById(`${this.modalId}Icon`);
        const headerElement = this.modal.querySelector('.modal-header');
        const confirmBtn = document.getElementById(`${this.modalId}ConfirmBtn`);
        const cancelBtn = document.getElementById(`${this.modalId}CancelBtn`);

        // Reset classes
        headerElement.className = 'modal-header';
        confirmBtn.className = 'btn';
        iconElement.className = 'bi';

        // Configure based on type
        switch (type) {
            case 'success':
                headerElement.classList.add('bg-success', 'text-white');
                iconElement.classList.add('bi-check-circle-fill');
                confirmBtn.classList.add('btn-success');
                break;
            case 'error':
                headerElement.classList.add('bg-danger', 'text-white');
                iconElement.classList.add('bi-exclamation-triangle-fill');
                confirmBtn.classList.add('btn-danger');
                break;
            case 'warning':
                headerElement.classList.add('bg-warning', 'text-dark');
                iconElement.classList.add('bi-exclamation-circle-fill');
                confirmBtn.classList.add('btn-warning');
                break;
            case 'confirm':
                headerElement.classList.add('bg-primary', 'text-white');
                iconElement.classList.add('bi-question-circle-fill');
                confirmBtn.classList.add('btn-primary');
                cancelBtn.style.display = 'inline-block';
                break;
            default: // info
                headerElement.classList.add('bg-info', 'text-white');
                iconElement.classList.add('bi-info-circle-fill');
                confirmBtn.classList.add('btn-info');
        }

        // Set title
        const titleElement = document.getElementById(`${this.modalId}Title`);
        titleElement.textContent = title || this.getDefaultTitle(type);

        // Set message (support for HTML content with basic sanitization)
        const bodyElement = document.getElementById(`${this.modalId}Body`);
        // Check if message contains HTML tags
        if (typeof message === 'string' && (message.includes('<') && message.includes('>'))) {
            // Create a temporary div for sanitization
            const tempDiv = document.createElement('div');
            tempDiv.textContent = message; // This escapes HTML
            // Allow only <br> and &nbsp; by replacing escaped versions
            const sanitized = tempDiv.innerHTML
                .replace(/&lt;br&gt;/g, '<br>')
                .replace(/&amp;nbsp;/g, '&nbsp;')
                .replace(/&lt;strong&gt;/g, '<strong>')
                .replace(/&lt;\/strong&gt;/g, '</strong>');
            bodyElement.innerHTML = sanitized;
        } else {
            // For plain text, preserve line breaks and spaces
            const textContent = String(message);
            // Convert newlines to <br> for display
            if (textContent.includes('\n')) {
                bodyElement.innerHTML = textContent
                    .split('\n')
                    .map(line => line.replace(/ /g, '&nbsp;'))
                    .join('<br>');
            } else {
                bodyElement.textContent = message;
            }
        }

        // Set button texts
        confirmBtn.textContent = confirmText || this.translate('dialog.ok', 'OK');
        cancelBtn.textContent = cancelText || this.translate('dialog.cancel', 'Cancel');

        // Hide cancel button for non-confirm types
        if (type !== 'confirm') {
            cancelBtn.style.display = 'none';
        }

        // Remove previous event listeners if they exist
        if (this.currentConfirmHandler) {
            confirmBtn.removeEventListener('click', this.currentConfirmHandler);
        }
        if (this.currentCancelHandler) {
            cancelBtn.removeEventListener('click', this.currentCancelHandler);
        }

        // Store and add new event listeners
        this.currentConfirmHandler = onConfirm ? () => { onConfirm(); } : null;
        this.currentCancelHandler = onCancel ? () => { onCancel(); } : null;

        if (this.currentConfirmHandler) {
            confirmBtn.addEventListener('click', this.currentConfirmHandler);
        }

        if (this.currentCancelHandler) {
            cancelBtn.addEventListener('click', this.currentCancelHandler);
        }

        // Show modal
        this.bsModal.show();
    }

    /**
     * Get default title based on dialog type
     */
    getDefaultTitle(type) {
        switch (type) {
            case 'success':
                return this.translate('dialog.success', 'Success');
            case 'error':
                return this.translate('dialog.error', 'Error');
            case 'warning':
                return this.translate('dialog.warning', 'Warning');
            case 'confirm':
                return this.translate('dialog.confirm', 'Confirm');
            default:
                return this.translate('dialog.info', 'Information');
        }
    }

    /**
     * Convenience method for success dialog
     */
    success(message, title = null, onConfirm = null) {
        this.show({
            type: 'success',
            title: title,
            message: message,
            onConfirm: onConfirm
        });
    }

    /**
     * Convenience method for error dialog
     */
    error(message, title = null, onConfirm = null) {
        this.show({
            type: 'error',
            title: title,
            message: message,
            onConfirm: onConfirm
        });
    }

    /**
     * Convenience method for warning dialog
     */
    warning(message, title = null, onConfirm = null) {
        this.show({
            type: 'warning',
            title: title,
            message: message,
            onConfirm: onConfirm
        });
    }

    /**
     * Convenience method for info dialog
     */
    info(message, title = null, onConfirm = null) {
        this.show({
            type: 'info',
            title: title,
            message: message,
            onConfirm: onConfirm
        });
    }

    /**
     * Convenience method for confirm dialog
     */
    confirm(message, onConfirm = null, onCancel = null, title = null) {
        this.show({
            type: 'confirm',
            title: title,
            message: message,
            onConfirm: onConfirm,
            onCancel: onCancel
        });
    }

    /**
     * Hide the dialog
     */
    hide() {
        this.bsModal.hide();
    }
}

// Create global instance
window.dialogManager = null;

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.dialogManager = new DialogManager();
    });
} else {
    window.dialogManager = new DialogManager();
}

// Provide convenience functions in global scope
window.showSuccess = (message, title, onConfirm) => window.dialogManager?.success(message, title, onConfirm);
window.showError = (message, title, onConfirm) => window.dialogManager?.error(message, title, onConfirm);
window.showWarning = (message, title, onConfirm) => window.dialogManager?.warning(message, title, onConfirm);
window.showInfo = (message, title, onConfirm) => window.dialogManager?.info(message, title, onConfirm);
window.showConfirm = (message, onConfirm, onCancel, title) => window.dialogManager?.confirm(message, onConfirm, onCancel, title);

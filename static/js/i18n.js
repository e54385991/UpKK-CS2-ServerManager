/**
 * Simple i18n implementation for CS2 Server Manager
 * Supports English and Chinese with browser language detection
 */

class I18n {
    constructor() {
        this.currentLocale = null;
        this.translations = {};
        this.fallbackLocale = 'en-US';
        this.supportedLocales = ['en-US', 'zh-CN'];
        // Version for cache busting - update when translations change
        this.version = '1.0.15';
        // Track if translations are loaded
        this.isInitialized = false;
    }

    /**
     * Initialize i18n with locale detection
     */
    async init() {
        // Try to get locale from localStorage first
        let savedLocale = localStorage.getItem('locale');
        
        // If no saved locale, detect from browser
        if (!savedLocale) {
            savedLocale = this.detectBrowserLocale();
        }
        
        // Set the locale
        await this.setLocale(savedLocale);
        
        // Mark as initialized
        this.isInitialized = true;
        
        // Apply translations to the page
        this.applyTranslations();
        
        // Watch for dynamic content changes
        this.observeDOMChanges();
        
        // Dispatch initialization complete event for Alpine.js and other components
        window.dispatchEvent(new CustomEvent('i18nReady', { detail: { locale: this.currentLocale } }));
    }

    /**
     * Detect browser locale and return supported locale
     */
    detectBrowserLocale() {
        const browserLang = navigator.language || navigator.userLanguage;
        
        // Check if browser language is Chinese
        if (browserLang.startsWith('zh')) {
            return 'zh-CN';
        }
        
        // Check for exact match
        if (this.supportedLocales.includes(browserLang)) {
            return browserLang;
        }
        
        // Check for language prefix match (e.g., 'en-GB' -> 'en-US')
        const langPrefix = browserLang.split('-')[0];
        const matchingLocale = this.supportedLocales.find(locale => 
            locale.startsWith(langPrefix)
        );
        
        if (matchingLocale) {
            return matchingLocale;
        }
        
        // Default to English
        return this.fallbackLocale;
    }

    /**
     * Set current locale and load translations
     */
    async setLocale(locale) {
        if (!this.supportedLocales.includes(locale)) {
            locale = this.fallbackLocale;
        }
        
        this.currentLocale = locale;
        
        // Save to localStorage
        localStorage.setItem('locale', locale);
        
        // Load translations if not already loaded
        if (!this.translations[locale]) {
            await this.loadTranslations(locale);
        }
        
        // Update HTML lang attribute
        document.documentElement.lang = locale;
        
        // Dispatch locale change event
        window.dispatchEvent(new CustomEvent('localeChanged', { detail: { locale } }));
    }

    /**
     * Load translations from JSON file
     */
    async loadTranslations(locale) {
        try {
            // Add version parameter for cache busting
            const response = await fetch(`/static/locales/${locale}.json?v=${this.version}`);
            if (!response.ok) {
                throw new Error(`Failed to load locale: ${locale}`);
            }
            this.translations[locale] = await response.json();
        } catch (error) {
            console.error(`Error loading translations for ${locale}:`, error);
            // Load fallback locale if current locale fails
            if (locale !== this.fallbackLocale) {
                await this.loadTranslations(this.fallbackLocale);
                this.currentLocale = this.fallbackLocale;
            }
        }
    }

    /**
     * Get translation for a key
     * @param {string} key - Translation key in dot notation (e.g., 'nav.home')
     * @param {object} params - Optional parameters for string interpolation
     */
    t(key, params = {}) {
        // If not initialized yet, return undefined to allow fallbacks in expressions
        // This prevents showing raw keys like "serverDetail.none" before translations load
        if (!this.isInitialized) {
            return undefined;
        }
        
        const keys = key.split('.');
        let value = this.translations[this.currentLocale];
        
        // Traverse the translation object
        for (const k of keys) {
            if (value && typeof value === 'object') {
                value = value[k];
            } else {
                value = undefined;
                break;
            }
        }
        
        // Fallback to English if translation not found
        if (value === undefined && this.currentLocale !== this.fallbackLocale) {
            let fallbackValue = this.translations[this.fallbackLocale];
            for (const k of keys) {
                if (fallbackValue && typeof fallbackValue === 'object') {
                    fallbackValue = fallbackValue[k];
                } else {
                    fallbackValue = undefined;
                    break;
                }
            }
            value = fallbackValue;
        }
        
        // Return key if no translation found (only warn after initialization)
        if (value === undefined) {
            console.warn(`Translation not found for key: ${key}`);
            return key;
        }
        
        // Handle string interpolation
        if (typeof value === 'string' && Object.keys(params).length > 0) {
            return value.replace(/\{(\w+)\}/g, (match, paramKey) => {
                return params[paramKey] !== undefined ? params[paramKey] : match;
            });
        }
        
        return value;
    }

    /**
     * Apply translations to elements with data-i18n attribute
     */
    applyTranslations() {
        // Translate elements with data-i18n attribute
        document.querySelectorAll('[data-i18n]').forEach(element => {
            const key = element.getAttribute('data-i18n');
            const translation = this.t(key);
            
            // Check if we should update text content or a specific attribute
            const attr = element.getAttribute('data-i18n-attr');
            if (attr) {
                element.setAttribute(attr, translation);
            } else {
                element.textContent = translation;
            }
        });

        // Translate elements with data-i18n-placeholder attribute
        document.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
            const key = element.getAttribute('data-i18n-placeholder');
            const translation = this.t(key);
            element.placeholder = translation;
        });

        // Translate elements with data-i18n-title attribute
        document.querySelectorAll('[data-i18n-title]').forEach(element => {
            const key = element.getAttribute('data-i18n-title');
            const translation = this.t(key);
            element.title = translation;
        });

        // Translate elements with data-i18n-html attribute (for HTML content)
        document.querySelectorAll('[data-i18n-html]').forEach(element => {
            const key = element.getAttribute('data-i18n-html');
            const translation = this.t(key);
            element.innerHTML = translation;
        });
    }

    /**
     * Observe DOM changes and apply translations to new elements
     */
    observeDOMChanges() {
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === 1) { // Element node
                        // Check if the node itself has i18n attributes
                        if (node.hasAttribute && node.hasAttribute('data-i18n')) {
                            this.translateElement(node);
                        }
                        // Check descendants
                        if (node.querySelectorAll) {
                            node.querySelectorAll('[data-i18n], [data-i18n-placeholder], [data-i18n-title], [data-i18n-html]')
                                .forEach(el => this.translateElement(el));
                        }
                    }
                });
            });
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    /**
     * Translate a single element
     */
    translateElement(element) {
        if (element.hasAttribute('data-i18n')) {
            const key = element.getAttribute('data-i18n');
            const translation = this.t(key);
            const attr = element.getAttribute('data-i18n-attr');
            if (attr) {
                element.setAttribute(attr, translation);
            } else {
                element.textContent = translation;
            }
        }
        
        if (element.hasAttribute('data-i18n-placeholder')) {
            const key = element.getAttribute('data-i18n-placeholder');
            element.placeholder = this.t(key);
        }
        
        if (element.hasAttribute('data-i18n-title')) {
            const key = element.getAttribute('data-i18n-title');
            element.title = this.t(key);
        }
        
        if (element.hasAttribute('data-i18n-html')) {
            const key = element.getAttribute('data-i18n-html');
            element.innerHTML = this.t(key);
        }
    }

    /**
     * Get current locale
     */
    getLocale() {
        return this.currentLocale;
    }

    /**
     * Get all supported locales
     */
    getSupportedLocales() {
        return this.supportedLocales;
    }

    /**
     * Get locale display name
     */
    getLocaleDisplayName(locale) {
        const displayNames = {
            'en-US': 'English',
            'zh-CN': '中文'
        };
        return displayNames[locale] || locale;
    }
}

// Create global i18n instance
const i18n = new I18n();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => i18n.init());
} else {
    i18n.init();
}

// Make i18n available globally
window.i18n = i18n;

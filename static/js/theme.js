/**
 * Theme Management System
 * Handles light/dark/auto theme switching with local storage only
 */

(function () {
  'use strict';

  // Theme Manager Class
  // Storage key — must match the FOUC script in base.html
  const THEME_KEY = 'equiper-theme';

  class ThemeManager {
    constructor() {
      // Get theme from localStorage or default to 'light'
      this.currentTheme = localStorage.getItem(THEME_KEY) || 'light';
      this.init();
    }

    init() {
      // Set initial theme
      this.applyTheme(this.currentTheme);

      // Setup event listeners
      this.setupEventListeners();

      // Listen for system theme changes if in auto mode
      if (this.currentTheme === 'auto') {
        this.watchSystemTheme();
      }
    }

    setupEventListeners() {
      // Theme toggle button in header
      const themeToggle = document.getElementById('themeToggle');
      if (themeToggle) {
        themeToggle.addEventListener('click', () => this.toggleTheme());
      }
    }

    applyTheme(theme) {
      const html = document.documentElement;

      // Remove preload class to enable transitions
      html.classList.remove('preload');

      // Set the theme
      html.setAttribute('data-theme', theme);
      this.currentTheme = theme;

      // Store in localStorage
      localStorage.setItem(THEME_KEY, theme);
    }

    toggleTheme() {
      // Cycle through: light -> dark -> light
      let newTheme;
      switch (this.currentTheme) {
        case 'light':
          newTheme = 'dark';
          break;
        case 'dark':
          newTheme = 'light';
          break;
        case 'auto':
          newTheme = 'light';
          break;
        default:
          newTheme = 'light';
      }

      this.applyTheme(newTheme);
    }

    watchSystemTheme() {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      mediaQuery.addEventListener('change', (e) => {
        if (this.currentTheme === 'auto') {
          // Force re-render when system theme changes
          this.applyTheme('auto');
        }
      });
    }

    showNotification(message, type = 'info') {
      // Delegates to the shared toast system (static/js/notify.js)
      window.notify(message, type);
    }
  }

  // Initialize theme manager when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      window.themeManager = new ThemeManager();
    });
  } else {
    window.themeManager = new ThemeManager();
  }

})();

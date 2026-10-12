const { Tray, Menu, nativeImage, app } = require("electron");
const path = require("path");
const fs = require("fs");
const debugLogger = require("./debugLogger");
const { i18nMain } = require("./i18nMain");

// OW-VOICE
// OW-TRAY begin
const OW_COL = {
  idle: [138, 143, 152],
  listening: [229, 50, 45],
  processing: [240, 160, 0],
  error: [179, 38, 30],
};
const OW_BAR_X = [4, 10, 16, 22, 28];
const OW_PROFILE = [0.6, 0.85, 1, 0.85, 0.6];
function owRRectD(px, py, cx, cy, hw, hh, r) {
  const qx = Math.abs(px - cx) - hw + r, qy = Math.abs(py - cy) - hh + r;
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
}
function owSegD(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - ax - t * dx, py - ay - t * dy);
}
// state: idle | listening | processing | error. level 0..1 (listening only). phase: animation counter.
function owTrayBitmap(state, level, phase) {
  const S = 32, buf = Buffer.alloc(S * S * 4);
  const col = OW_COL[state] || OW_COL.idle;
  const shapes = []; // { d(px,py) -> signed distance in px, a: 0..1 opacity }
  if (state === "processing") {
    for (let i = 0; i < 3; i++) {
      const on = (phase % 3) === i;
      shapes.push({ d: (x, y) => Math.hypot(x - (8 + 8 * i), y - 16) - 3.4, a: on ? 1 : 0.35 });
    }
  } else {
    for (let i = 0; i < 5; i++) {
      let h = 9;
      if (state === "listening" && level > 0) {
        const wob = 0.7 + 0.3 * Math.sin(phase * 1.3 + i * 1.7);
        h = 9 + 19 * level * OW_PROFILE[i] * wob;
      } else if (state === "error") {
        h = [10, 16, 22, 16, 10][i];
      }
      const a = state === "error" ? 0.55 : 1;
      shapes.push({ d: (x, y) => owRRectD(x, y, OW_BAR_X[i], 16, 2, h / 2, 2), a });
    }
    if (state === "error") shapes.push({ d: (x, y) => owSegD(x, y, 5, 27, 27, 5) - 1.8, a: 1 });
  }
  for (let y = 0; y < S; y++) {
    for (let x = 0; x < S; x++) {
      let a = 0;
      for (const s of shapes) a = Math.max(a, s.a * Math.max(0, Math.min(1, 0.5 - s.d(x + 0.5, y + 0.5))));
      const o = (y * S + x) * 4;
      buf[o] = Math.round(col[2] * a); buf[o + 1] = Math.round(col[1] * a); buf[o + 2] = Math.round(col[0] * a);
      buf[o + 3] = Math.round(255 * a);
    }
  }
  return buf;
}
// OW-TRAY end

class TrayManager {
  constructor() {
    this.tray = null;
    this.mainWindow = null;
    this.controlPanelWindow = null;
    this.windowManager = null;
    this.attachedControlPanels = new WeakSet();
  }

  setWindows(mainWindow, controlPanelWindow) {
    this.mainWindow = mainWindow;
    this.controlPanelWindow = controlPanelWindow;

    if (this.mainWindow) {
      this.mainWindow.on("show", () => this.updateTrayMenu?.());
      this.mainWindow.on("hide", () => this.updateTrayMenu?.());
      this.mainWindow.on("minimize", () => this.updateTrayMenu?.());
      this.mainWindow.on("restore", () => this.updateTrayMenu?.());
    }

    if (this.controlPanelWindow) {
      this.attachControlPanelListeners(this.controlPanelWindow);
    }

    this.updateTrayMenu?.();
  }

  setWindowManager(windowManager) {
    this.windowManager = windowManager;
  }

  setCreateControlPanelCallback(callback) {
    this.createControlPanelCallback = callback;
  }

  attachControlPanelListeners(window) {
    if (!window || this.attachedControlPanels.has(window)) {
      return;
    }

    this.attachedControlPanels.add(window);

    window.on("show", () => {
      this.updateTrayMenu?.();
    });

    window.on("hide", () => {
      this.updateTrayMenu?.();
    });

    window.on("destroyed", () => {
      this.controlPanelWindow = null;
      this.updateTrayMenu?.();
    });
  }

  async showControlPanelFromTray() {
    try {
      if (this.windowManager) {
        this.controlPanelWindow = this.windowManager.controlPanelWindow || this.controlPanelWindow;
      }
      this.attachControlPanelListeners(this.controlPanelWindow);

      if (this.controlPanelWindow && !this.controlPanelWindow.isDestroyed()) {
        // Show dock icon on macOS when control panel opens
        if (process.platform === "darwin" && app.dock) {
          app.dock.show();
        }
        if (this.controlPanelWindow.isMinimized()) {
          this.controlPanelWindow.restore();
        }
        if (!this.controlPanelWindow.isVisible()) {
          this.controlPanelWindow.show();
        }
        this.controlPanelWindow.focus();
        if (this.controlPanelWindow.webContents.isCrashed()) {
          this.controlPanelWindow.webContents.reload();
        }
        return;
      }

      if (this.createControlPanelCallback) {
        await this.createControlPanelCallback();
        if (this.windowManager) {
          this.controlPanelWindow =
            this.windowManager.controlPanelWindow || this.controlPanelWindow;
        }
        this.attachControlPanelListeners(this.controlPanelWindow);

        if (this.controlPanelWindow && !this.controlPanelWindow.isDestroyed()) {
          this.controlPanelWindow.show();
          this.controlPanelWindow.focus();
        }
        return;
      }

      debugLogger.error("No control panel callback available", undefined, "tray");
    } catch (error) {
      debugLogger.error("Failed to open control panel", { error: error?.message }, "tray");
    }
  }

  async createTray() {
    try {
      const trayIcon = await this.loadTrayIcon();
      if (!trayIcon || trayIcon.isEmpty()) {
        debugLogger.error("Failed to load tray icon", undefined, "tray");
        return;
      }

      this.tray = new Tray(trayIcon);

      if (process.platform === "darwin") {
        this.tray.setIgnoreDoubleClickEvents(true);
      }

      this.updateTrayMenu();
      this.setupTrayEventHandlers();
      this.setVoiceState("idle");
    } catch (error) {
      debugLogger.error("Error creating tray icon", { error: error.message }, "tray");
    }
  }

  async loadTrayIcon() {
    const platform = process.platform;
    const isDevelopment = process.env.NODE_ENV === "development";

    const candidatePaths = [];

    if (platform === "darwin") {
      if (isDevelopment) {
        candidatePaths.push(path.join(__dirname, "..", "assets", "iconTemplate@3x.png"));
      } else {
        candidatePaths.push(
          path.join(process.resourcesPath, "src", "assets", "iconTemplate@3x.png"),
          path.join(process.resourcesPath, "assets", "iconTemplate@3x.png"),
          path.join(
            process.resourcesPath,
            "app.asar.unpacked",
            "src",
            "assets",
            "iconTemplate@3x.png"
          ),
          path.join(__dirname, "..", "..", "src", "assets", "iconTemplate@3x.png"),
          path.join(app.getAppPath(), "src", "assets", "iconTemplate@3x.png")
        );
      }
    } else {
      const fileName = platform === "win32" ? "icon.ico" : "icon.png";
      if (isDevelopment) {
        candidatePaths.push(
          path.join(__dirname, "..", "assets", fileName),
          path.join(__dirname, "..", "assets", "icon.png")
        );
      } else {
        candidatePaths.push(
          path.join(process.resourcesPath, "src", "assets", fileName),
          path.join(process.resourcesPath, "assets", fileName),
          path.join(process.resourcesPath, "app.asar.unpacked", "src", "assets", fileName),
          path.join(__dirname, "..", "..", "src", "assets", fileName),
          path.join(app.getAppPath(), "src", "assets", fileName)
        );
      }
    }

    for (const testPath of candidatePaths) {
      try {
        if (fs.existsSync(testPath)) {
          const icon = nativeImage.createFromPath(testPath);
          if (icon && !icon.isEmpty()) {
            if (platform === "darwin") {
              icon.setTemplateImage(true);
            }
            debugLogger.debug("Using tray icon", { path: testPath }, "tray");
            return icon;
          }
        }
      } catch (error) {
        debugLogger.error(
          "Error checking tray icon path",
          { path: testPath, error: error.message },
          "tray"
        );
      }
    }

    debugLogger.error("Could not find tray icon in any expected location", undefined, "tray");
    return this.createFallbackIcon();
  }

  createFallbackIcon() {
    try {
      // Create a simple 16x16 PNG icon programmatically
      const { createCanvas } = require("canvas");
      const canvas = createCanvas(16, 16);
      const ctx = canvas.getContext("2d");

      ctx.fillStyle = "#000000";
      ctx.beginPath();
      ctx.arc(8, 8, 6, 0, 2 * Math.PI);
      ctx.fill();

      const buffer = canvas.toBuffer("image/png");
      const fallbackIcon = nativeImage.createFromBuffer(buffer);
      debugLogger.info("Created fallback tray icon", undefined, "tray");
      return fallbackIcon;
    } catch (fallbackError) {
      debugLogger.warn("Canvas not available, creating minimal fallback icon", undefined, "tray");
      // Create a minimal 16x16 black square PNG as fallback
      const pngData = Buffer.from([
        0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44,
        0x52, 0x00, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x10, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90,
        0x91, 0x68, 0x36, 0x00, 0x00, 0x00, 0x0c, 0x49, 0x44, 0x41, 0x54, 0x28, 0x53, 0x63, 0x08,
        0x05, 0x00, 0x00, 0x02, 0x00, 0x01, 0xe5, 0x27, 0xde, 0xfc, 0x00, 0x00, 0x00, 0x00, 0x49,
        0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
      ]);

      const fallbackIcon = nativeImage.createFromBuffer(pngData);
      debugLogger.info("Created minimal fallback tray icon", undefined, "tray");
      return fallbackIcon;
    }
  }


  // OW-VOICE: voice-driven waveform icon (see make_voice_patch.py).
  setVoiceState(state) {
    if (process.platform !== "win32") return;
    global.__owTrayMgr = this; // lets the test harness read the live state
    this._owState = state;
    this._owLevel = 0;
    this._owKey = null;
    if (this._owTimer) clearInterval(this._owTimer);
    this._owTimer = null;
    if (state === "processing") {
      this._owTimer = setInterval(() => {
        this._owPhase = (this._owPhase || 0) + 1;
        this._owRender();
      }, 250);
    }
    this._owRender();
  }

  setVoiceLevel(level) {
    if (process.platform !== "win32" || this._owState !== "listening") return;
    if (level === 0 && this._owLevel === 0) return;
    this._owLevel = level;
    if (level > 0) this._owPhase = (this._owPhase || 0) + 1;
    this._owRender();
  }

  _owRender() {
    if (!this.tray) return;
    try {
      const state = this._owState || "idle";
      const key = state + ":" + Math.round((this._owLevel || 0) * 40) + ":" + (this._owPhase || 0);
      if (key === this._owKey) return;
      this._owKey = key;
      const img = nativeImage.createFromBitmap(
        owTrayBitmap(state, this._owLevel || 0, this._owPhase || 0),
        { width: 32, height: 32, scaleFactor: 2 }
      );
      this._owLastImg = img;
      this.tray.setImage(img);
    } catch (error) {
      debugLogger.error("voice tray icon failed", { error: error.message }, "tray");
    }
  }

  buildContextMenuTemplate() {
    const dictationVisible = this.windowManager?.isDictationPanelVisible?.() ?? false;

    return [
      {
        label: dictationVisible
          ? i18nMain.t("tray.toggleDictation.hide")
          : i18nMain.t("tray.toggleDictation.show"),
        click: () => {
          if (!this.windowManager) return;
          if (this.windowManager.isDictationPanelVisible()) {
            this.windowManager.hideDictationPanel();
          } else {
            this.windowManager.showDictationPanel({ focus: true });
          }
          this.updateTrayMenu();
        },
      },
      {
        label: i18nMain.t("tray.openControlPanel"),
        click: async () => {
          await this.showControlPanelFromTray();
        },
      },
      { type: "separator" },
      {
        label: i18nMain.t("tray.quit"),
        click: () => {
          debugLogger.info("Quitting app via tray menu", undefined, "tray");
          app.quit();
        },
      },
    ];
  }

  updateTrayMenu() {
    if (!this.tray) return;

    const contextMenu = Menu.buildFromTemplate(this.buildContextMenuTemplate());
    this.tray.setToolTip(i18nMain.t("tray.tooltip"));
    this.tray.setContextMenu(contextMenu);
  }

  setupTrayEventHandlers() {
    if (!this.tray) {
      return;
    }

    if (process.platform !== "darwin") {
      this.tray.on("click", () => {
        void this.showControlPanelFromTray();
      });
    }

    this.tray.on("destroyed", () => {
      debugLogger.debug("Tray icon destroyed", undefined, "tray");
      this.tray = null;
    });
  }
}

module.exports = TrayManager;

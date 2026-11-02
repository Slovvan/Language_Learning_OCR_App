"""Ventana de configuración: atajos globales, teclas de la captura y comportamiento de la capa.

Los cambios se aplican al momento (no hay «Aceptar»): cada control escribe en AppSettings, que avisa
a quien lo necesite (atajos globales, barra de la capa…).
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
                             QCheckBox, QLabel, QListWidget, QScrollArea, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

import UI.profiles as profiles

from UI.hotkeys import (MAX_KEYS, global_key_token, global_label, is_risky, normalize_global, normalize_overlay,
                     overlay_key_from_event)
from UI.settings import DEFAULTS, GLOBAL_ACTIONS, OVERLAY_ACTIONS, RANGES

TOOLBAR_CHOICES = (("hover", "En viñeta: una pestaña que se abre al pasar el mouse (no tapa la pantalla)"),
                   ("always", "Siempre visible"),
                   ("hidden", "Oculta (solo con las teclas)"))


MODE_CHOICES = (("frozen", "Congelar la pantalla: eliges una región cada vez (el juego queda tapado por la captura)"),
                ("live", "En vivo: una región fija que se vigila sin congelar ni interrumpir el juego"))
TRIGGER_CHOICES = (("auto", "Automático: leer cuando el texto cambie y se quede quieto"),
                   ("button", "Solo con el botón «Leer ahora» (o su atajo)"),
                   ("click", "Al hacer clic o pulsar Enter/Espacio/flechas/Ctrl en el juego"))
SIDE_CHOICES = (("auto", "Automático (donde quepa)"), ("below", "Debajo de la región"), ("above", "Encima"),
                ("right", "A la derecha"), ("left", "A la izquierda"))
LAYER_CHOICES = (("original", "El texto original reconocido, como en la captura congelada (permite consultar palabras)"),
                 ("translation", "La traducción (sustituye al texto del juego)"))
PANEL_SHOW_CHOICES = (("always", "Abierto: aparece junto a la región"), ("hidden", "Oculto (se abre con su atajo)"))
PANEL_MODE_CHOICES = (("log", "Historial: guarda las últimas lecturas y deja quitar las erróneas con ✕"),
                      ("last", "Solo la última lectura"))
FRAME_CHOICES = (("always", "Mostrar un marco alrededor de la región"), ("hidden", "Sin marco"))

# (clave, texto, ayuda, sufijo)
LIVE_NUMBERS = (
    ("live.sensitivity", "Sensibilidad al cambio", "1 = solo cambios grandes · 10 = cualquier cambio pequeño", ""),
    ("live.interval_ms", "Revisar la región cada", "Menos tiempo = reacciona antes pero gasta más CPU", " ms"),
    ("live.stable_ms", "Esperar a que el texto se quede quieto", "Evita leer a medias el texto que aparece letra a letra", " ms"),
    ("live.min_gap_ms", "Separación mínima entre lecturas", "Evita lecturas seguidas cuando el texto parpadea", " ms"),
    ("live.log_max", "Lecturas que guarda el historial", "Las más antiguas se van quitando", ""),
    ("live.panel_hide_s", "Ocultar el panel tras", "0 = no se oculta solo", " s"),
    ("live.panel_font", "Tamaño de letra del panel", "", " px"),
    ("live.panel_opacity", "Opacidad del panel", "", " %"),
)


class KeyCaptureButton(QPushButton):
    """Botón que muestra un atajo y, al pulsarlo, espera la nueva combinación."""
    captured = pyqtSignal(str)             # atajo nuevo (ya normalizado)
    rejected = pyqtSignal(str)             # motivo por el que no se aceptó lo pulsado

    def __init__(self, kind: str, value: str = ""):
        super().__init__()
        self.kind = kind                   # "global" (pynput, de 1 a 3 teclas) o "overlay" (Qt, una tecla)
        self._value = value
        self._waiting = False
        self._down = set()                 # teclas que siguen pulsadas mientras se graba un atajo global
        self._seen = []                    # ...y todas las que se pulsaron, en orden
        self.setMinimumWidth(190)
        self.clicked.connect(self._start)
        self._show()

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str):
        self._value = value
        self._waiting = False
        self.releaseKeyboard()
        self._show()

    def label_of(self, value: str) -> str:
        return global_label(value) if self.kind == "global" else value

    def _show(self):
        self.setText("Pulsa la combinación…" if self._waiting else (self.label_of(self._value) or "(sin atajo)"))

    def _start(self):
        self._waiting = True
        self._down, self._seen = set(), []
        self.grabKeyboard()
        self.setFocus()
        self._show()

    def _stop(self):
        self._waiting = False
        self.releaseKeyboard()
        self._show()

    def focusOutEvent(self, e):
        if self._waiting:
            self._stop()
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if not self._waiting:
            super().keyPressEvent(e)
            return
        key = e.key()
        if self.kind == "global":
            # Atajo de 1 a 3 teclas: se graba todo lo que se pulsa y se cierra al soltar la última.
            if key == Qt.Key_Escape and not self._seen:
                self._stop()
                return
            if e.isAutoRepeat():
                return
            token = global_key_token(e.modifiers(), key)
            if token is not None:
                self._down.add(token)
                if token not in self._seen:
                    self._seen.append(token)
                self.setText(" + ".join(global_label(t) for t in self._seen) + " …")
            return
        combo = overlay_key_from_event(e.modifiers(), key)
        if combo is None:                  # solo un modificador: se sigue esperando
            return
        self._stop()
        self.captured.emit(combo)

    def keyReleaseEvent(self, e):
        if not self._waiting or self.kind != "global":
            super().keyReleaseEvent(e)
            return
        if e.isAutoRepeat():
            return
        token = global_key_token(e.modifiers(), e.key())
        self._down.discard(token)
        if self._down or not self._seen:
            return
        seen, self._seen = self._seen, []
        self._stop()
        if len(seen) > MAX_KEYS:
            self.rejected.emit(f"Un atajo puede tener como máximo {MAX_KEYS} teclas.")
            return
        combo = normalize_global("+".join(seen))
        if combo is None:
            self.rejected.emit("Esa combinación no es válida (necesita al menos una tecla que no sea Ctrl, Alt, Mayús o Win).")
            return
        self.captured.emit(combo)


class SettingsDialog(QDialog):
    profile_save = pyqtSignal(str)         # guardar la región fija actual con este nombre
    profile_load = pyqtSignal(str)         # usar el perfil de este nombre
    profile_delete = pyqtSignal(str)

    def __init__(self, settings, hotkeys=None, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.settings = settings
        self.hotkeys = hotkeys
        self.setWindowTitle("Configuración")
        self.resize(700, 600)
        self._buttons = {}                 # clave de ajuste -> KeyCaptureButton
        self._combos = {}                  # clave -> QComboBox
        self._spins = {}                   # clave -> QSpinBox
        self._checks = {}                  # clave -> QCheckBox
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #b3261e;")

        tabs = QTabWidget()
        tabs.addTab(self._global_tab(), "Atajos globales")
        tabs.addTab(self._overlay_tab(), "Teclas de la captura")
        tabs.addTab(self._capture_tab(), "Captura")
        tabs.addTab(self._live_tab(), "Modo en vivo")
        self.tabs = tabs

        reset = QPushButton("Restaurar todo")
        reset.clicked.connect(self._reset_all)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(reset)
        bottom.addStretch(1)
        bottom.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        lay.addWidget(self.note)
        lay.addLayout(bottom)
        self.settings.changed.connect(lambda _k: self._sync())

    # ------------------------------------------------------------------ pestañas
    def _rows(self, specs, kind):
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        for row, (key, name, help_text) in enumerate(specs):
            setting = f"hotkey.{key}" if kind == "global" else f"overlay.key.{key}"
            label = QLabel(f"<b>{name}</b>" + (f"<br><span style='color:gray'>{help_text}</span>" if help_text else ""))
            label.setWordWrap(True)
            btn = KeyCaptureButton(kind, self.settings.get(setting))
            btn.captured.connect(lambda combo, s=setting, k=kind: self._assign(s, k, combo))
            btn.rejected.connect(self.note.setText)
            default = QPushButton("Restaurar")
            default.clicked.connect(lambda _c=False, s=setting: self.settings.reset(s))
            grid.addWidget(label, row, 0)
            grid.addWidget(btn, row, 1)
            grid.addWidget(default, row, 2)
            self._buttons[setting] = btn
        return grid

    def _global_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Funcionan aunque el juego tenga el foco. Pulsa el botón y luego la combinación: de 1 a 3 teclas "
                      "(una sola como F9 o Pausa, dos como Ctrl+F9, tres como Ctrl+Mayús+K). Si un juego se "
                      "interrumpe con Alt o Win, usa una tecla F, Pausa o el teclado numérico.")
        info.setWordWrap(True)
        lay.addWidget(info)
        if self.hotkeys is not None and not self.hotkeys.available:
            warn = QLabel("⚠ Los atajos globales no están disponibles: falta «pynput» (pip install pynput).")
            warn.setStyleSheet("color: #b3261e;")
            lay.addWidget(warn)
        lay.addLayout(self._rows([(k, n, h) for k, n, h, _d in GLOBAL_ACTIONS], "global"))
        lay.addStretch(1)
        return w

    def _overlay_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Teclas que se usan con la captura abierta. Pulsa un botón y luego la tecla nueva "
                      "(vale una sola tecla).")
        info.setWordWrap(True)
        lay.addWidget(info)
        lay.addLayout(self._rows([(k, n, "") for k, n, _d in OVERLAY_ACTIONS], "overlay"))
        lay.addStretch(1)
        return w

    def _capture_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.combo_toolbar = self._combo("ui.toolbar", TOOLBAR_CHOICES)
        form.addRow("Barra de controles\n(modos de OCR, unir líneas):", self.combo_toolbar)
        self.combo_mode = self._combo("capture.mode", MODE_CHOICES)
        form.addRow("Al capturar:", self.combo_mode)
        return w

    def set_mode_locked(self, locked: bool):
        """Con el modo en vivo en marcha no se cambia de modo: hay que detenerlo primero."""
        box = self._combos.get("capture.mode")
        if box is not None:
            box.setEnabled(not locked)
            box.setToolTip("Detén el modo en vivo para cambiar de modo." if locked else "")

    def _combo(self, key: str, choices) -> QComboBox:
        box = QComboBox()
        for value, text in choices:
            box.addItem(text, value)
        box.setCurrentIndex(max(0, box.findData(self.settings.get(key))))
        box.currentIndexChanged.connect(lambda _i, k=key, b=box: self.settings.set(k, b.currentData()))
        self._combos[key] = box
        return box

    def _live_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("En el modo en vivo se marca una vez la región donde sale el texto (por ejemplo, el cuadro de "
                      "diálogo de una novela visual) y la app la vigila sin tapar el juego: el texto y la traducción "
                      "salen en un panel junto a ella. Útil en novelas visuales, cinemáticas y juegos donde la "
                      "captura congelada interrumpe.")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        form.addRow("Cuándo leer:", self._combo("live.trigger", TRIGGER_CHOICES))
        form.addRow("", self._check("live.read_freezes", "«Leer ahora» congela la pantalla un momento (como el modo congelar)"))
        form.addRow("Texto sobre la región:", self._check("live.layer", "Dibujar el texto encima de la región, sin congelar el juego"))
        form.addRow("Qué se dibuja encima:", self._combo("live.layer_content", LAYER_CHOICES))
        form.addRow("Panel:", self._combo("live.panel_show", PANEL_SHOW_CHOICES))
        form.addRow("Contenido del panel:", self._combo("live.panel_mode", PANEL_MODE_CHOICES))
        form.addRow("", self._check("live.panel_original", "Mostrar también el texto original en el panel (si no, solo la traducción)"))
        reset_size = QPushButton("Tamaño automático del panel")
        reset_size.setToolTip("El tamaño del panel se cambia arrastrando su esquina inferior derecha")
        reset_size.clicked.connect(lambda: (self.settings.reset("live.panel_w"), self.settings.reset("live.panel_h")))
        form.addRow("", reset_size)
        for key, text, tip, suffix in LIVE_NUMBERS:
            spin = QSpinBox()
            lo, hi = RANGES[key]
            spin.setRange(lo, hi)
            spin.setSuffix(suffix)
            spin.setValue(self.settings.get(key))
            spin.setToolTip(tip)
            spin.valueChanged.connect(lambda v, k=key: self.settings.set(k, v))
            self._spins[key] = spin
            form.addRow(text + ":", spin)
        form.addRow("Lado del panel:", self._combo("live.panel_side", SIDE_CHOICES))
        form.addRow("Marco:", self._combo("live.show_frame", FRAME_CHOICES))
        lay.addLayout(form)
        lay.addWidget(self._profiles_box())
        lay.addStretch(1)
        area = QScrollArea()                 # son muchas opciones: la pestaña se desplaza
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(w)
        return area

    def _check(self, key: str, text: str) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(bool(self.settings.get(key)))
        box.toggled.connect(lambda on, k=key: self.settings.set(k, bool(on)))
        self._checks[key] = box
        return box

    def _profiles_box(self) -> QGroupBox:
        box = QGroupBox("Perfiles por juego (región, idioma y modo de OCR)")
        lay = QVBoxLayout(box)
        self.profile_list = QListWidget()
        self.profile_list.setMaximumHeight(90)
        self.profile_list.itemDoubleClicked.connect(lambda item: self.profile_load.emit(item.text()))
        row = QHBoxLayout()
        save = QPushButton("Guardar la región actual…")
        load = QPushButton("Usar")
        delete = QPushButton("Eliminar")
        save.clicked.connect(self._save_profile)
        load.clicked.connect(lambda: self._with_profile(self.profile_load))
        delete.clicked.connect(lambda: self._with_profile(self.profile_delete))
        for b in (save, load, delete):
            row.addWidget(b)
        lay.addWidget(self.profile_list)
        lay.addLayout(row)
        self._fill_profiles()
        return box

    def _fill_profiles(self):
        self.profile_list.clear()
        self.profile_list.addItems(sorted(profiles.load(self.settings)))

    def _save_profile(self):
        name, ok = QInputDialog.getText(self, "Nuevo perfil", "Nombre del juego:")
        if ok and name.strip():
            self.profile_save.emit(name.strip())

    def _with_profile(self, signal):
        item = self.profile_list.currentItem()
        if item is not None:
            signal.emit(item.text())

    def refresh_profiles(self):
        self._fill_profiles()

    # ------------------------------------------------------------------ cambios
    def _assign(self, setting: str, kind: str, combo: str):
        norm = normalize_global(combo) if kind == "global" else normalize_overlay(combo)
        if not norm:
            self.note.setText("Esa combinación no es válida.")
            return
        prefix = "hotkey." if kind == "global" else "overlay.key."
        for other, btn in self._buttons.items():
            if other != setting and other.startswith(prefix) and btn.value == norm:
                self.note.setText(f"«{btn.label_of(norm)}» ya está asignada a otra acción. Cambia esa primero.")
                return
        self.note.setText("")
        if kind == "global" and is_risky(norm):
            self.note.setText(f"Aviso: «{global_label(norm)}» es una tecla normal y también le llegará al juego. "
                              "Mejor una tecla F, Pausa o una combinación.")
        self.settings.set(setting, norm)

    def _reset_all(self):
        self.settings.reset_all()
        self.note.setText("")

    def _sync(self):
        for setting, btn in self._buttons.items():
            btn.set_value(self.settings.get(setting))
        for key, box in self._combos.items():
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findData(self.settings.get(key))))
            box.blockSignals(False)
        for key, spin in self._spins.items():
            spin.blockSignals(True)
            spin.setValue(self.settings.get(key))
            spin.blockSignals(False)
        for key, box in self._checks.items():
            box.blockSignals(True)
            box.setChecked(bool(self.settings.get(key)))
            box.blockSignals(False)
        self._fill_profiles()

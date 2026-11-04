"""Ventana de configuración: tres pestañas (Atajos, Modo en vivo, Apariencia). Los cambios se aplican al momento."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDialog, QFrame, QScrollArea, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox,
                             QTabWidget, QVBoxLayout, QWidget)

from UI.app_settings import (ACTIONS, get_ocr_bar, KEY_SCOPES, NUMBERS, default_bindings, default_keys, get_number, load_bindings,
                          load_keys)
from UI.hotkeys import (MAX_KEYS, global_key_token, global_label, is_risky, normalize_global, normalize_overlay,
                     overlay_key_from_event)


class SingleKeyButton(QPushButton):
    """Muestra una tecla (con o sin Ctrl/Alt/Mayús) y espera la nueva al pulsarlo. Esc cancela."""
    captured = pyqtSignal(str)

    def __init__(self, value: str = ""):
        super().__init__()
        self._value = value
        self._waiting = False
        self.setMinimumWidth(200)
        self.clicked.connect(self._start)
        self._show()

    def set_value(self, value: str):
        self._value = value
        self._waiting = False
        self.releaseKeyboard()
        self._show()

    def _show(self):
        self.setText("Pulsa la tecla…" if self._waiting else (self._value or "(sin tecla)"))

    def _start(self):
        self._waiting = True
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
        if e.key() == Qt.Key_Escape and e.modifiers() == Qt.NoModifier:
            self._stop()                                    # Esc cancela (para dejar Esc como tecla, queda por defecto)
            return
        combo = overlay_key_from_event(e.modifiers(), e.key())
        if combo is None:                                   # solo un modificador: se sigue esperando
            return
        self._stop()
        self.captured.emit(combo)


class KeyCaptureButton(QPushButton):
    """Muestra un atajo; al pulsarlo espera la nueva combinación (de 1 a 3 teclas) y se cierra al soltar todo."""
    captured = pyqtSignal(str)
    rejected = pyqtSignal(str)

    def __init__(self, value: str = ""):
        super().__init__()
        self._value = value
        self._waiting = False
        self._down, self._seen = set(), []
        self.setMinimumWidth(200)
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

    def _show(self):
        self.setText("Pulsa la combinación…" if self._waiting else (global_label(self._value) or "(sin atajo)"))

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
        if e.key() == Qt.Key_Escape and not self._seen:
            self._stop()
            return
        if e.isAutoRepeat():
            return
        token = global_key_token(e.modifiers(), e.key())
        if token is not None:
            self._down.add(token)
            if token not in self._seen:
                self._seen.append(token)
            self.setText(" + ".join(global_label(t) for t in self._seen) + " …")

    def keyReleaseEvent(self, e):
        if not self._waiting:
            super().keyReleaseEvent(e)
            return
        if e.isAutoRepeat():
            return
        self._down.discard(global_key_token(e.modifiers(), e.key()))
        if self._down or not self._seen:
            return
        seen, self._seen = self._seen, []
        self._stop()
        if len(seen) > MAX_KEYS:
            self.rejected.emit(f"Un atajo puede tener como máximo {MAX_KEYS} teclas.")
            return
        combo = normalize_global("+".join(seen))
        if combo is None:
            self.rejected.emit("Esa combinación no sirve: necesita al menos una tecla que no sea solo Ctrl, Alt, Mayús o Win.")
            return
        self.captured.emit(combo)


# (clave, texto, ayuda, sufijo, paso)
LIVE_ROWS = (
    ("live_sensitivity", "Sensibilidad al cambio", "1 = solo cambios grandes · 10 = cualquier cambio pequeño", "", 1),
    ("live_stable_ms", "Esperar a que el texto se quede quieto", "Evita leer a medias un texto que aparece letra a letra", " ms", 50),
    ("live_min_gap_ms", "Separación mínima entre lecturas", "Evita lecturas seguidas si el texto parpadea", " ms", 100),
)
PANEL_ROWS = (
    ("live_history_max", "Lecturas que guarda el historial", "Con «Historial» marcado; las más antiguas se van quitando", "", 5),
    ("live_panel_hide_s", "Segundos que dura el panel temporal", "Con «Panel temporal» marcado", " s", 1),
)


class SettingsDialog(QDialog):
    changed = pyqtSignal()               # algo cambió: la ventana principal lo aplica

    def __init__(self, settings, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.WindowCloseButtonHint)
        self.settings = settings
        self.setWindowTitle("Configuración")
        self.resize(640, 560)
        self._buttons = {}
        self._spins = {}
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setMinimumHeight(34)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._hotkeys_tab(), "Atajos globales")
        self.tabs.addTab(self._frozen_tab(), "Captura congelada")
        self.tabs.addTab(self._live_tab(), "Modo en vivo")
        self.tabs.addTab(self._look_tab(), "Apariencia")
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.note)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("Cerrar")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        layout.addLayout(row)

    # ------------------------------------------------------------------ utilidades
    def _say(self, text: str, error: bool = False):
        self.note.setStyleSheet("color: #b3261e;" if error else "color: #555;")
        self.note.setText(text)

    @staticmethod
    def _page(*widgets):
        page = QWidget()
        lay = QVBoxLayout(page)
        for w in widgets:
            lay.addWidget(w) if isinstance(w, QWidget) else lay.addLayout(w)
        lay.addStretch(1)
        return page

    @staticmethod
    def _intro(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet("color: #666;")
        return lab

    def _spin(self, key, suffix, step):
        default, lo, hi = NUMBERS[key]
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setValue(get_number(self.settings, key))
        spin.valueChanged.connect(lambda v, k=key: self._set_number(k, v))
        self._spins[key] = spin
        return spin

    def _set_number(self, key, value):
        self.settings.setValue(key, int(value))
        self.changed.emit()

    def _number_form(self, rows):
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for i, (key, text, help_, suffix, step) in enumerate(rows):
            grid.addWidget(QLabel(text), i * 2, 0)
            grid.addWidget(self._spin(key, suffix, step), i * 2, 1, Qt.AlignLeft)
            grid.addWidget(self._intro(help_), i * 2 + 1, 0, 1, 2)
        return grid

    def _reset_button(self, keys, text="Restablecer esta pestaña"):
        btn = QPushButton(text)
        btn.clicked.connect(lambda: self._reset_numbers(keys))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        return row

    def _reset_numbers(self, keys):
        for key in keys:
            self._spins[key].setValue(NUMBERS[key][0])        # dispara valueChanged y guarda
        self._say("Valores restablecidos.")

    # ------------------------------------------------------------------ pestañas
    def _hotkeys_tab(self):
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        saved = load_bindings(self.settings)
        for i, (action, (_default, name)) in enumerate(ACTIONS.items()):
            grid.addWidget(QLabel(name), i, 0)
            btn = KeyCaptureButton(saved.get(action, ""))
            btn.captured.connect(lambda combo, a=action: self._set_hotkey(a, combo))
            btn.rejected.connect(lambda why: self._say(why, True))
            self._buttons[action] = btn
            grid.addWidget(btn, i, 1)
            clear = QPushButton("Quitar")
            clear.setToolTip("Dejar esta acción sin atajo")
            clear.clicked.connect(lambda _c=False, a=action: self._set_hotkey(a, ""))
            grid.addWidget(clear, i, 2)
        reset = QPushButton("Restablecer atajos")
        reset.clicked.connect(self._reset_hotkeys)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(reset)
        return self._page(
            self._intro("Funcionan con el juego en primer plano. Pulsa un botón y teclea la combinación (de 1 a 3 teclas: "
                        "por ejemplo F9, Ctrl+Mayús+K o Z+X); se guarda al soltar las teclas. Esc cancela."),
            grid, row)

    def _set_hotkey(self, action, combo):
        if combo:
            for other, value in load_bindings(self.settings).items():
                if other != action and value == combo:
                    self._say(f"«{global_label(combo)}» ya se usa para «{ACTIONS[other][1]}». Quita o cambia esa primero.", True)
                    return
        self.settings.setValue(f"hk/{action}", combo)
        self._buttons[action].set_value(combo)
        if combo and is_risky(combo):
            self._say("Ojo: una tecla suelta también le llega al juego (los atajos no la bloquean). "
                      "Es más seguro usarla con Ctrl, Alt o Mayús.")
        else:
            self._say("Atajo guardado.")
        self.changed.emit()

    def _reset_hotkeys(self):
        for action, combo in default_bindings().items():
            self.settings.setValue(f"hk/{action}", combo)
            self._buttons[action].set_value(combo)
        self._say("Atajos restablecidos.")
        self.changed.emit()

    # --- teclas con el foco ---
    def _keys_group(self, scope, title, intro):
        self._key_buttons = getattr(self, "_key_buttons", {})
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        saved = load_keys(self.settings, scope)
        for i, (action, (_d, name)) in enumerate(KEY_SCOPES[scope].items()):
            grid.addWidget(QLabel(name), i, 0)
            btn = SingleKeyButton(saved.get(action, ""))
            btn.captured.connect(lambda combo, sc=scope, a=action: self._set_key(sc, a, combo))
            self._key_buttons[(scope, action)] = btn
            grid.addWidget(btn, i, 1)
            clear = QPushButton("Quitar")
            clear.clicked.connect(lambda _c=False, sc=scope, a=action: self._set_key(sc, a, ""))
            grid.addWidget(clear, i, 2)
        reset = QPushButton("Restablecer teclas")
        reset.clicked.connect(lambda: self._reset_keys(scope))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(reset)
        return [QLabel(f"<b>{title}</b>"), self._intro(intro), grid, row]

    def _set_key(self, scope, action, combo):
        if combo:
            for other, value in load_keys(self.settings, scope).items():
                if other != action and value == combo:
                    self._say(f"«{combo}» ya se usa para «{KEY_SCOPES[scope][other][1]}». Quita o cambia esa primero.", True)
                    return
        self.settings.setValue(f"key/{scope}/{action}", combo)
        self._key_buttons[(scope, action)].set_value(combo)
        self._say("Tecla guardada." if combo else "Acción sin tecla (los atajos globales siguen funcionando).")
        self.changed.emit()

    def _reset_keys(self, scope):
        for action, key in default_keys(scope).items():
            self.settings.setValue(f"key/{scope}/{action}", key)
            self._key_buttons[(scope, action)].set_value(key)
        self._say("Teclas restablecidas.")
        self.changed.emit()

    def _frozen_tab(self):
        return self._page(*self._keys_group(
            "frozen", "Teclas con la captura abierta",
            "Se usan con la pantalla congelada en primer plano. Una tecla sola o con Ctrl, Alt o Mayús. "
            "Pulsa un botón y la tecla nueva; Esc cancela. El clic derecho (elegir otra región) no se cambia."))

    def _live_tab(self):
        keys = [r[0] for r in LIVE_ROWS + PANEL_ROWS]
        inner = QWidget()
        lay = QVBoxLayout(inner)
        for item in self._keys_group("live", "Teclas con la región o el panel en foco",
                                     "Haz clic en la región o el panel para darle el foco; desde el juego usa los atajos globales."):
            lay.addWidget(item) if isinstance(item, QWidget) else lay.addLayout(item)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        lay.addWidget(line)
        lay.addWidget(QLabel("<b>Lectura automática</b>"))
        lay.addLayout(self._number_form(LIVE_ROWS))
        lay.addWidget(QLabel("<b>Panel</b>"))
        lay.addLayout(self._number_form(PANEL_ROWS))
        lay.addLayout(self._reset_button(keys, "Restablecer estos valores"))
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        return scroll

    def _look_tab(self):
        font = self._spin("live_panel_font", " px", 1)
        opacity = QSlider(Qt.Horizontal)
        opacity.setRange(NUMBERS["live_text_opacity"][1], NUMBERS["live_text_opacity"][2])
        opacity.setValue(get_number(self.settings, "live_text_opacity"))
        value = QLabel(f"{opacity.value()} %")
        opacity.valueChanged.connect(lambda v: (value.setText(f"{v} %"), self._set_number("live_text_opacity", v)))
        self._opacity = opacity
        row = QHBoxLayout()
        row.addWidget(opacity, 1)
        row.addWidget(value)
        style = QComboBox()
        style.addItem("Ventana junto a la región (siempre visible)", False)
        style.addItem("Viñeta: una pestaña que se abre al pasar el mouse", True)
        style.setCurrentIndex(1 if self.settings.value("live_panel_tab", False) in (True, "true", "1", 1) else 0)
        style.currentIndexChanged.connect(lambda _i: (self.settings.setValue("live_panel_tab", bool(style.currentData())),
                                                       self.changed.emit()))
        self._panel_style = style
        bar = QComboBox()
        bar.addItem("Viñeta: una píldora «あ Modo ▾» que se abre al pasar el mouse", "hover")
        bar.addItem("Siempre visible", "always")
        bar.addItem("Oculta (solo con las teclas 1 / 2 / 3)", "hidden")
        bar.setCurrentIndex(max(0, bar.findData(get_ocr_bar(self.settings))))
        bar.currentIndexChanged.connect(lambda _i: (self.settings.setValue("ocr_bar", bar.currentData()), self.changed.emit()))
        self._ocr_bar = bar
        form = QFormLayout()
        form.addRow("Panel de traducción", style)
        form.addRow("Barra de modos OCR (japonés)", bar)
        form.addRow("Tamaño de letra del panel", font)
        form.addRow("Opacidad del fondo del texto sobre la región", row)
        reset = QPushButton("Restablecer esta pestaña")
        reset.clicked.connect(lambda: (font.setValue(NUMBERS["live_panel_font"][0]), style.setCurrentIndex(0), bar.setCurrentIndex(0),
                                       opacity.setValue(NUMBERS["live_text_opacity"][0]), self._say("Valores restablecidos.")))
        rrow = QHBoxLayout()
        rrow.addStretch(1)
        rrow.addWidget(reset)
        return self._page(
            self._intro("En modo viñeta el panel se pliega a una pestaña pequeña junto a la región («● Traducción» avisa de una lectura nueva) y se abre al pasar el mouse. "
                        "La opacidad menor deja ver un poco el original a través del texto dibujado sobre la región "
                        "(el texto sigue nítido)."),
            form, rrow)

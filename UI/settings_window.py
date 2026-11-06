"""Ventana de configuración: Atajos globales, Captura congelada, Modo en vivo, Apariencia y Perfiles. Los cambios se aplican al momento."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDialog, QFrame, QInputDialog, QListWidget, QMessageBox, QScrollArea, QFormLayout, QGridLayout,
                             QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

import UI.profiles as profiles
from UI.live_options import checkbox

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
    profile_use = pyqtSignal(str)        # «Usar» un perfil
    profile_save = pyqtSignal(str)       # «Guardar la configuración actual» con ese nombre
    profiles_changed = pyqtSignal()      # se borró o renombró un perfil

    def __init__(self, settings, live_options, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.WindowCloseButtonHint)
        self.settings = settings
        self.live_options = live_options
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
        self.tabs.addTab(self._profiles_tab(), "Perfiles")
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

    def _section(self, lay, title, intro=None):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #ccc;")
        lay.addWidget(line)
        lay.addWidget(QLabel(f"<b>{title}</b>"))
        if intro:
            lay.addWidget(self._intro(intro))

    def _checks(self, lay, keys):
        for key in keys:
            box = checkbox(self.live_options, key)
            self._checkboxes[key] = box
            lay.addWidget(box)

    def _live_tab(self):
        self._checkboxes = {}
        keys = [r[0] for r in LIVE_ROWS + PANEL_ROWS]
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.addWidget(QLabel("<b>Lectura</b>"))
        lay.addWidget(self._intro("Las mismas casillas están en la tuerca ⚙ del panel, para cambiarlas sin abrir esta ventana."))
        self._checks(lay, ("live_auto", "live_freeze_read", "live_freeze_auto"))
        lay.addLayout(self._number_form(LIVE_ROWS))

        self._section(lay, "Panel de traducción")
        self._checks(lay, ("live_show_original", "live_history", "live_save_log", "live_over", "live_panel_temp"))
        lay.addLayout(self._number_form(PANEL_ROWS))

        self._section(lay, "Pausa del juego",
                      "Al congelar la pantalla se pulsa la tecla de pausa de tu juego, y otra vez al descongelar. "
                      "Pon aquí la misma tecla que usa el juego. Se envía como si la pulsaras tú a la ventana en primer plano: "
                      "usa los atajos globales para que el foco siga en el juego (si haces clic en el panel, la tecla le llega al panel).")
        self._checks(lay, ("live_pause_on",))
        self._pause_btn = KeyCaptureButton(str(self.settings.value("live_pause_key", "")))
        self._pause_btn.captured.connect(self._set_pause_key)
        self._pause_btn.rejected.connect(lambda why: self._say(why, True))
        clear = QPushButton("Quitar")
        clear.clicked.connect(lambda: self._set_pause_key(""))
        row = QHBoxLayout()
        row.addWidget(QLabel("Tecla de pausa del juego"))
        row.addWidget(self._pause_btn)
        row.addWidget(clear)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(self._intro("Es una tecla que alterna (pausa / reanuda). Si el juego ya estaba en pausa al congelar, se reanudaría; "
                                  "en ese caso desmarca la casilla."))

        self._section(lay, "Teclas con la región o el panel en foco",
                      "Haz clic en la región o el panel para darle el foco; desde el juego usa los atajos globales.")
        for item in self._keys_group("live", "", "")[2:]:
            lay.addWidget(item) if isinstance(item, QWidget) else lay.addLayout(item)
        lay.addLayout(self._reset_button(keys, "Restablecer los valores numéricos"))
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        return scroll

    def _set_pause_key(self, combo):
        self.settings.setValue("live_pause_key", combo)
        self._pause_btn.set_value(combo)
        if combo and is_risky(combo):
            self._say("Tecla de pausa guardada. Ojo: es una tecla suelta; si el juego la usa para otra cosa, se activará igual.")
        else:
            self._say("Tecla de pausa guardada." if combo else "Sin tecla de pausa: la casilla «Pausar el juego» no hará nada.")
        self.changed.emit()

    def reload_pause_key(self):
        """La tecla de pausa cambió desde fuera (al aplicar un perfil)."""
        self._pause_btn.set_value(str(self.settings.value("live_pause_key", "")))

    # --- perfiles ---
    def _profiles_tab(self):
        self._profile_list = QListWidget()
        self._profile_list.currentItemChanged.connect(lambda *_: self._profile_buttons())
        self._profile_list.itemDoubleClicked.connect(lambda _i: self._profile_use())
        self.btn_p_use = QPushButton("Usar")
        self.btn_p_use.setToolTip("Aplica idioma, modo de OCR, opciones, tecla de pausa y la región fija del perfil")
        self.btn_p_rename = QPushButton("Renombrar")
        self.btn_p_delete = QPushButton("Borrar")
        self.btn_p_save = QPushButton("Guardar la configuración actual como…")
        self.btn_p_save.setToolTip("Guarda el idioma, el modo, las opciones, la tecla de pausa y la región fija actuales")
        self.btn_p_use.clicked.connect(self._profile_use)
        self.btn_p_rename.clicked.connect(self._profile_rename)
        self.btn_p_delete.clicked.connect(self._profile_delete)
        self.btn_p_save.clicked.connect(self._profile_save)
        self._profile_detail = self._intro("")
        self._profile_detail.setMinimumHeight(48)
        self._profile_list.currentItemChanged.connect(lambda *_: self._profile_describe())
        side = QVBoxLayout()
        for b in (self.btn_p_use, self.btn_p_rename, self.btn_p_delete):
            side.addWidget(b)
        side.addStretch(1)
        body = QHBoxLayout()
        body.addWidget(self._profile_list, 1)
        body.addLayout(side)
        page = self._page(
            self._intro("Un perfil recuerda, para cada juego: la región fija, el idioma, el modo de OCR, las casillas del modo en vivo "
                        "y la tecla de pausa. Elígelo también desde la ventana principal (selector «Perfil»)."),
            body, self._profile_detail, self.btn_p_save)
        self.reload_profiles()
        return page

    def reload_profiles(self, select: str = None):
        current = select or (self._profile_list.currentItem().text() if self._profile_list.currentItem() else
                             str(self.settings.value("profile_active", "")))
        self._profile_list.blockSignals(True)
        self._profile_list.clear()
        for name in profiles.load(self.settings):
            self._profile_list.addItem(name)
        for i in range(self._profile_list.count()):
            if self._profile_list.item(i).text() == current:
                self._profile_list.setCurrentRow(i)
        self._profile_list.blockSignals(False)
        self._profile_buttons()
        self._profile_describe()

    def _profile_name(self):
        item = self._profile_list.currentItem()
        return item.text() if item else None

    def _profile_buttons(self):
        has = self._profile_name() is not None
        for b in (self.btn_p_use, self.btn_p_rename, self.btn_p_delete):
            b.setEnabled(has)

    def _profile_describe(self):
        name = self._profile_name()
        data = profiles.load(self.settings).get(name) if name else None
        if not data:
            self._profile_detail.setText("Aún no hay perfiles." if self._profile_list.count() == 0 else "Elige un perfil.")
            return
        bits = []
        if data.get("lang"):
            bits.append(f"idioma «{data['lang']}»")
        if data.get("mode"):
            bits.append(f"modo «{data['mode']}»")
        bits.append("con región fija" if "box" in data else "sin región")
        key = (data.get("pause") or {}).get("key", "")
        if key:
            bits.append(f"pausa {global_label(key)}")
        self._profile_detail.setText(f"«{name}»: " + ", ".join(bits) + ".")

    def _profile_use(self):
        name = self._profile_name()
        if name:
            self.profile_use.emit(name)
            self._say(f"Perfil «{name}» aplicado.")

    def _profile_save(self):
        name, ok = QInputDialog.getText(self, "Guardar perfil", "Nombre del juego o perfil:", text=self._profile_name() or "")
        name = name.strip()
        if not ok or not name:
            return
        if name in profiles.load(self.settings) and QMessageBox.question(
                self, "Guardar perfil", f"Ya existe «{name}». ¿Reemplazarlo con la configuración actual?") != QMessageBox.Yes:
            return
        self.profile_save.emit(name)
        self.reload_profiles(select=name)
        self._say(f"Perfil «{name}» guardado.")

    def _profile_rename(self):
        old = self._profile_name()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Renombrar perfil", "Nombre nuevo:", text=old)
        new = new.strip()
        if not ok or not new or new == old:
            return
        if not profiles.rename(self.settings, old, new):
            self._say(f"Ya existe un perfil llamado «{new}».", True)
            return
        if str(self.settings.value("profile_active", "")) == old:
            self.settings.setValue("profile_active", new)
        self.reload_profiles(select=new)
        self.profiles_changed.emit()
        self._say("Perfil renombrado.")

    def _profile_delete(self):
        name = self._profile_name()
        if not name or QMessageBox.question(self, "Borrar perfil", f"¿Borrar el perfil «{name}»?") != QMessageBox.Yes:
            return
        profiles.delete(self.settings, name)
        if str(self.settings.value("profile_active", "")) == name:
            self.settings.setValue("profile_active", "")
        self.reload_profiles()
        self.profiles_changed.emit()
        self._say("Perfil borrado.")

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
        scale = QSlider(Qt.Horizontal)
        scale.setRange(NUMBERS["popup_scale"][1], NUMBERS["popup_scale"][2])
        scale.setSingleStep(5)
        scale.setPageStep(10)
        scale.setValue(get_number(self.settings, "popup_scale"))
        scale_value = QLabel(f"{scale.value()} %")
        scale.valueChanged.connect(lambda v: (scale_value.setText(f"{v} %"), self._set_number("popup_scale", v)))
        self._popup_scale = scale
        scale_row = QHBoxLayout()
        scale_row.addWidget(scale, 1)
        scale_row.addWidget(scale_value)
        form = QFormLayout()
        form.addRow("Panel de traducción", style)
        form.addRow("Barra de modos OCR (japonés)", bar)
        form.addRow("Tamaño de letra del panel", font)
        form.addRow("Opacidad del fondo del texto sobre la región", row)
        form.addRow("Tamaño de letra del diccionario (ventanita de palabras)", scale_row)
        reset = QPushButton("Restablecer esta pestaña")
        reset.clicked.connect(lambda: (font.setValue(NUMBERS["live_panel_font"][0]), style.setCurrentIndex(0), bar.setCurrentIndex(0),
                                       opacity.setValue(NUMBERS["live_text_opacity"][0]), scale.setValue(NUMBERS["popup_scale"][0]), self._say("Valores restablecidos.")))
        rrow = QHBoxLayout()
        rrow.addStretch(1)
        rrow.addWidget(reset)
        return self._page(
            self._intro("En modo viñeta el panel se pliega a una pestaña pequeña junto a la región («● Traducción» avisa de una lectura nueva) y se abre al pasar el mouse. "
                        "La opacidad menor deja ver un poco el original a través del texto dibujado sobre la región "
                        "(el texto sigue nítido)."),
            form, rrow)

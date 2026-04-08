# views/main_local.py - Menú moderno de local
import json
import os
from datetime import datetime

from PyQt5.QtCore import QByteArray, Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from models import problemas_model as pm

# Colores base
GOLD = "#FFD700"
GOLD_DARK = "#C9A040"
BG_DARK = "#0f0f14"
BG_CARD = "#1a1a22"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#A0A0B0"


class MenuCard(QFrame):
    """Tarjeta moderna interactiva"""

    def __init__(self, icon, title, description, color="#C9A040"):
        super().__init__()
        self.color = color
        self._base_title = title

        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"""
            QFrame {{
                background: {BG_CARD};
                border-radius: 18px;
                border: 2px solid transparent;
            }}
            QFrame:hover {{
                border: 2px solid {color};
                background: #222232;
            }}
        """
        )

        # Efecto de sombra suave
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 90))
        shadow.setOffset(0, 8)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(8)
        self._layout = layout

        # Icono
        icon_label = QLabel(icon)
        icon_label.setStyleSheet(f"font-size: 40px; color: {color};")
        layout.addWidget(icon_label)
        self._icon_label = icon_label

        # Título
        title_label = QLabel(title)
        title_label.setFont(QFont("Segoe UI", 15, QFont.Bold))
        title_label.setStyleSheet(f"color: {TEXT_PRIMARY};")
        layout.addWidget(title_label)
        self._title_label = title_label

        # Descripción
        desc_label = QLabel(description)
        desc_label.setFont(QFont("Segoe UI", 10))
        desc_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        self._desc_label = desc_label

        layout.addStretch()

    def mousePressEvent(self, event):
        if hasattr(self, "click_callback"):
            self.click_callback()
        super().mousePressEvent(event)

    def set_title_badge(self, count: int):
        if count and count > 0:
            self._title_label.setText(f"{self._base_title} ({count})")
        else:
            self._title_label.setText(self._base_title)

    def apply_density(self, density: str):
        if density == "compact":
            self.setMinimumHeight(100)
            self._layout.setContentsMargins(14, 12, 14, 12)
            self._layout.setSpacing(5)
            self._icon_label.setStyleSheet(f"font-size: 26px; color: {self.color};")
            self._title_label.setFont(QFont("Segoe UI", 12, QFont.Bold))
            self._desc_label.setFont(QFont("Segoe UI", 9))
        elif density == "wide":
            self.setMinimumHeight(130)
            self._layout.setContentsMargins(22, 18, 22, 18)
            self._layout.setSpacing(8)
            self._icon_label.setStyleSheet(f"font-size: 36px; color: {self.color};")
            self._title_label.setFont(QFont("Segoe UI", 15, QFont.Bold))
            self._desc_label.setFont(QFont("Segoe UI", 10))
        else:
            self.setMinimumHeight(120)
            self._layout.setContentsMargins(18, 15, 18, 15)
            self._layout.setSpacing(7)
            self._icon_label.setStyleSheet(f"font-size: 32px; color: {self.color};")
            self._title_label.setFont(QFont("Segoe UI", 13, QFont.Bold))
            self._desc_label.setFont(QFont("Segoe UI", 10))


class StatusTile(QFrame):
    def __init__(self, caption: str, value: str):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet(
            """
            QFrame {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 16px;
            }
        """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        caption_label = QLabel(caption)
        caption_label.setStyleSheet("color:#9aa0b4; font-size:11px; font-weight:600;")
        layout.addWidget(caption_label)

        value_label = QLabel(value)
        value_label.setFont(QFont("Segoe UI", 16, QFont.Bold))
        value_label.setStyleSheet("color:#f3f4f6;")
        value_label.setWordWrap(True)
        layout.addWidget(value_label)
        layout.addStretch()
        self.value_label = value_label

    def set_value(self, value: str):
        self.value_label.setText(value)


class LocalWindow(QMainWindow):
    """Panel principal de local - Versión moderna"""

    def __init__(self, username: str, role: str, local: str):
        super().__init__()
        self.username = username
        self.role = role or "local"
        self.local = local or ""
        self.child = None

        self.setWindowTitle(f"Manarey — {self.local}")
        self.setProperty("manarey_no_scale", True)
        self.setProperty("manarey_no_autoresize", True)
        self.setProperty("manarey_no_kiosk", True)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setMinimumSize(760, 520)
        self._initial_maximize_pending = True
        self._presence_timer = None
        self._restore_window_state()

        # Fondo con gradiente elegante
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 {BG_DARK}, stop: 1 #1b1b2f
                );
            }}
        """
        )

        self.setup_ui()

    def setup_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(scroll)

        central = QWidget()
        central.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        scroll.setWidget(central)

        root = QVBoxLayout(central)
        self.root = root
        root.setContentsMargins(34, 26, 34, 24)
        root.setSpacing(22)

        # ───── HEADER ─────
        header = QHBoxLayout()
        header.setSpacing(12)

        title = QLabel(f"🏪 {self.local}")
        title.setFont(QFont("Segoe UI", 26, QFont.Black))
        title.setStyleSheet(f"color: {GOLD};")
        header.addWidget(title)
        self.title_label = title
        header.addStretch()

        # Controles de ventana (minimizar / cerrar)
        window_controls = QHBoxLayout()
        window_controls.setSpacing(6)
        btn_min = QPushButton("—")
        btn_min.setCursor(Qt.PointingHandCursor)
        btn_min.setMinimumSize(30, 26)
        btn_min.setStyleSheet(
            """
            QPushButton {
                background: #2a2a31;
                color: #e5e7eb;
                border: 1px solid #3a3a44;
                border-radius: 6px;
                font-weight: 900;
            }
            QPushButton:hover { background: #3a3a44; }
        """
        )
        btn_min.clicked.connect(self.showMinimized)
        window_controls.addWidget(btn_min)

        btn_close = QPushButton("✕")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setMinimumSize(30, 26)
        btn_close.setStyleSheet(
            """
            QPushButton {
                background: #3a1f1f;
                color: #fecaca;
                border: 1px solid #4a2323;
                border-radius: 6px;
                font-weight: 900;
            }
            QPushButton:hover { background: #4a2323; }
        """
        )
        btn_close.clicked.connect(self.close)
        window_controls.addWidget(btn_close)

        header.addLayout(window_controls)

        root.addLayout(header)

        subheader = QVBoxLayout()
        subheader.setSpacing(10)

        welcome_note = QLabel(
            "Que tengas un gran dia de ventas. Si ves una actualizacion, instalala para mantener Manarey al dia."
        )
        welcome_note.setFont(QFont("Segoe UI", 11))
        welcome_note.setStyleSheet("color:#9aa0b4;")
        welcome_note.setWordWrap(True)
        self.welcome_note = welcome_note
        subheader.addWidget(welcome_note)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(10)
        meta_row.addStretch()

        local_badge = QLabel(self.local)
        local_badge.setFont(QFont("Segoe UI", 11, QFont.Bold))
        local_badge.setStyleSheet(f"color: {GOLD_DARK};")
        meta_row.addWidget(local_badge)
        self.local_badge = local_badge

        welcome = QLabel(f"Bienvenido, {self.username}")
        welcome.setFont(QFont("Segoe UI", 11, QFont.Bold))
        welcome.setStyleSheet(f"color: {TEXT_SECONDARY};")
        meta_row.addWidget(welcome)
        self.welcome_label = welcome

        btn_logout = QPushButton("Volver al inicio de sesion")
        btn_logout.setCursor(Qt.PointingHandCursor)
        btn_logout.setStyleSheet(
            """
            QPushButton {
                background: #3a3a40;
                color: #ffb3b3;
                border: 1px solid #4a4a50;
                border-radius: 10px;
                padding: 9px 14px;
            }
            QPushButton:hover { background: #4a4a50; }
        """
        )
        btn_logout.clicked.connect(self._go_login)
        meta_row.addWidget(btn_logout)
        self.logout_button = btn_logout
        subheader.addLayout(meta_row)

        root.addLayout(subheader)

        hero = QFrame()
        hero.setStyleSheet(
            f"""
            QFrame {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 rgba(214,179,106,0.10),
                    stop: 1 rgba(255,255,255,0.02)
                );
                border: 1px solid rgba(214,179,106,0.14);
                border-radius: 22px;
            }}
        """
        )
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(24, 22, 24, 22)
        hero_layout.setSpacing(18)
        self.hero_layout = hero_layout

        hero_eyebrow = QLabel("PANEL DEL LOCAL")
        hero_eyebrow.setStyleSheet(
            "color:#d6b36a; font-size:11px; font-weight:700; letter-spacing:1px;"
        )
        hero_layout.addWidget(hero_eyebrow)

        hero_title = QLabel(f"Todo listo para operar en {self.local}")
        hero_title.setFont(QFont("Segoe UI", 22, QFont.Black))
        hero_title.setStyleSheet("color:#f9fafb;")
        hero_layout.addWidget(hero_title)
        self.hero_title = hero_title

        hero_text = QLabel(
            "Accede a ventas, stock, envios y seguimiento diario desde un solo lugar. El panel se adapta a tu pantalla y mantiene a la vista lo importante."
        )
        hero_text.setStyleSheet("color:#b2b8c8; font-size:12px; line-height:1.45em;")
        hero_text.setWordWrap(True)
        hero_layout.addWidget(hero_text)
        self.hero_text = hero_text

        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        self.stats_row = stats_row

        today_text = datetime.now().strftime("%d/%m/%Y")
        self.status_jornada = StatusTile("Jornada", today_text)
        self.status_messages = StatusTile("Mensajes", "Sin revisar")
        self.status_updates = StatusTile("Actualizaciones", "Al dia")
        stats_row.addWidget(self.status_jornada)
        stats_row.addWidget(self.status_messages)
        stats_row.addWidget(self.status_updates)
        hero_layout.addLayout(stats_row)

        root.addWidget(hero)

        section_label = QLabel("Accesos rapidos")
        section_label.setStyleSheet(
            "color:#9aa0b4; font-size:12px; font-weight:700; letter-spacing:1px;"
        )
        root.addWidget(section_label)
        self.section_label = section_label

        grid = QGridLayout()
        self.cards_grid = grid
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)

        cards = [
            (
                "🧾",
                "Emitir Boleta",
                "Registra una venta y genera automáticamente la boleta.",
                "#10B981",
                self.open_boleta,
            ),
            (
                "📦",
                "Gestionar Stock",
                "Consulta y administra el stock de tu local.",
                GOLD,
                self.open_stock,
            ),
            (
                "💰",
                "Historial de Ventas",
                "Visualiza las ventas realizadas en tu local.",
                "#F59E0B",
                self.open_ventas_history,
            ),
            (
                "🕒",
                "Historial de Movimientos",
                "Revisa los cambios realizados en el inventario.",
                "#60A5FA",
                self.open_history,
            ),
            (
                "🚚",
                "Envios",
                "Ventas con envio e impresion de remitos.",
                "#22C55E",
                self.open_envios,
            ),
            (
                "CHAT",
                "Informar Problemas",
                "Envia inconvenientes al administrador.",
                "#38BDF8",
                self.open_problemas,
            ),
        ]

        self.menu_cards = []
        for icon, title, desc, color, callback in cards:
            card = MenuCard(icon, title, desc, color)
            card.click_callback = callback
            if title == "Informar Problemas":
                self.card_problemas = card
            self.menu_cards.append(card)

        self._reflow_cards()

        root.addLayout(grid)
        root.addStretch()

        # Boton de actualizacion (aparece si hay update pendiente)
        self.update_btn = QPushButton("Descargar actualizacion")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        try:
            self.update_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        except Exception:
            pass
        self.update_btn.setStyleSheet(
            """
            QPushButton {
                background: #1f2937;
                color: #facc15;
                border: 1px solid #facc15;
                border-radius: 10px;
                padding: 8px 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #111827;
            }
        """
        )
        self.update_btn.clicked.connect(self._on_update_click)
        self.update_btn.hide()

        self._start_presence_pings()
        QTimer.singleShot(1500, self._check_updates)
        self._messages_timer = QTimer(self)
        self._messages_timer.timeout.connect(self._update_messages_badge)
        self._messages_timer.start(60000)
        QTimer.singleShot(2000, self._update_messages_badge)

        bottom = QHBoxLayout()
        bottom.addWidget(self.update_btn)
        bottom.addStretch()
        root.addLayout(bottom)

    # ───── Navegación ─────
    def _push_child(self, win, title: str):
        self.child = win
        self.child.setWindowTitle(title)

        def back():
            try:
                self.child.hide()
            except Exception:
                pass
            self.child = None
            self.show()
            self.raise_()
            self.activateWindow()

        if hasattr(self.child, "back_command") and self.child.back_command is None:
            self.child.back_command = back
        if hasattr(self.child, "set_back_command"):
            try:
                self.child.set_back_command(back)
            except Exception:
                pass

        self.hide()
        self.child.show()

    # ───── Acciones de botones ─────
    def open_stock(self):
        try:
            from views.stock_view import StockView

            win = StockView(
                self.username, self.role, self.local, back_command=self.on_back
            )
            self._push_child(win, "Gestión de Stock")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir Stock:\n{e}")

    def open_history(self):
        try:
            from views.history_view import HistoryWindow

            win = HistoryWindow(
                self.username, self.role, self.local, back_command=self.on_back
            )
            self._push_child(win, "Historial de Movimientos")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir Historial:\n{e}")

    def open_ventas_history(self):
        try:
            from views.ventas_history_view import VentasWindow

            win = VentasWindow(
                self.username, self.role, self.local, back_command=self.on_back
            )
            self._push_child(win, "Historial de Ventas")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir Ventas:\n{e}")

    def open_boleta(self):
        try:
            from views.boleta_view import BoletaView

            win = BoletaView(self.username, self.role, self.local, back_command=None)
            self._push_child(win, "Emitir Boleta")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir Boleta:\n{e}")

    def open_envios(self):
        try:
            from views.envios_view import EnviosWindow

            win = EnviosWindow(
                self.username, self.role, self.local, back_command=self.on_back
            )
            self._push_child(win, "Envios")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir Envios:\n{e}")

    def open_problemas(self):
        try:
            from views.problemas_view import ProblemasWindow

            win = ProblemasWindow(
                self.username, self.role, self.local, back_command=self.on_back
            )
            self._push_child(win, "Informar Problemas")
            QTimer.singleShot(1200, self._update_messages_badge)
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"No se pudo abrir Informar Problemas:\n{e}"
            )

    def _update_messages_badge(self):
        try:
            if not hasattr(self, "card_problemas"):
                return
            unread = pm.count_local_unread(self.local)
            self.card_problemas.set_title_badge(unread)
            if hasattr(self, "status_messages"):
                self.status_messages.set_value(
                    f"{unread} pendientes" if unread > 0 else "Todo visto"
                )
        except Exception:
            pass

    def open_settings(self):
        try:
            from views.settings_dialog import SettingsDialog

            dlg = SettingsDialog(self)
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir ajustes:\n{e}")

    def on_back(self):
        if self.child:
            self.child.close()
        self.show()

    def closeEvent(self, event):
        try:
            if self.child:
                self.child.close()
        except Exception:
            pass
        self._persist_window_state()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_maximize_pending:
            self._initial_maximize_pending = False
            QTimer.singleShot(0, self._force_fill_screen)
            return
        self._apply_responsive_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _card_columns(self) -> int:
        w = max(1, self.width())
        if w < 820:
            return 1
        if w < 1240:
            return 2
        if w < 1720:
            return 3
        return 4

    def _reflow_cards(self):
        if not hasattr(self, "cards_grid"):
            return
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
        cols = self._card_columns()
        for idx, card in enumerate(getattr(self, "menu_cards", [])):
            self.cards_grid.addWidget(card, idx // cols, idx % cols)
        for col in range(cols):
            self.cards_grid.setColumnStretch(col, 1)

    def _apply_responsive_layout(self):
        try:
            w = max(1, self.width())
            if w < 860:
                self.root.setContentsMargins(14, 14, 14, 14)
                self.root.setSpacing(14)
                self.cards_grid.setHorizontalSpacing(12)
                self.cards_grid.setVerticalSpacing(12)
                self.title_label.setFont(QFont("Segoe UI", 20, QFont.Black))
                self.welcome_note.setFont(QFont("Segoe UI", 10))
                self.local_badge.setFont(QFont("Segoe UI", 10, QFont.Bold))
                self.welcome_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
                self.logout_button.setText("Cerrar sesion")
                self.hero_layout.setContentsMargins(18, 18, 18, 18)
                self.hero_layout.setSpacing(14)
                self.hero_title.setFont(QFont("Segoe UI", 18, QFont.Black))
                self.hero_text.setStyleSheet(
                    "color:#b2b8c8; font-size:11px; line-height:1.35em;"
                )
                self.stats_row.setSpacing(10)
                for card in self.menu_cards:
                    card.apply_density("compact")
            elif w < 1260:
                self.root.setContentsMargins(20, 18, 20, 18)
                self.root.setSpacing(16)
                self.cards_grid.setHorizontalSpacing(14)
                self.cards_grid.setVerticalSpacing(14)
                self.title_label.setFont(QFont("Segoe UI", 22, QFont.Black))
                self.welcome_note.setFont(QFont("Segoe UI", 10))
                self.local_badge.setFont(QFont("Segoe UI", 10, QFont.Bold))
                self.welcome_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
                self.logout_button.setText("Volver al inicio")
                self.hero_layout.setContentsMargins(20, 20, 20, 20)
                self.hero_layout.setSpacing(16)
                self.hero_title.setFont(QFont("Segoe UI", 20, QFont.Black))
                self.hero_text.setStyleSheet(
                    "color:#b2b8c8; font-size:12px; line-height:1.4em;"
                )
                self.stats_row.setSpacing(12)
                for card in self.menu_cards:
                    card.apply_density("regular")
            else:
                self.root.setContentsMargins(30, 24, 30, 22)
                self.root.setSpacing(20)
                self.cards_grid.setHorizontalSpacing(18)
                self.cards_grid.setVerticalSpacing(18)
                self.title_label.setFont(QFont("Segoe UI", 26, QFont.Black))
                self.welcome_note.setFont(QFont("Segoe UI", 11))
                self.local_badge.setFont(QFont("Segoe UI", 11, QFont.Bold))
                self.welcome_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
                self.logout_button.setText("Volver al inicio de sesion")
                self.hero_layout.setContentsMargins(24, 22, 24, 22)
                self.hero_layout.setSpacing(18)
                self.hero_title.setFont(QFont("Segoe UI", 22, QFont.Black))
                self.hero_text.setStyleSheet(
                    "color:#b2b8c8; font-size:12px; line-height:1.45em;"
                )
                self.stats_row.setSpacing(14)
                density = "wide" if w >= 1720 else "regular"
                for card in self.menu_cards:
                    card.apply_density(density)
            self._reflow_cards()
        except Exception:
            pass

    def _force_fill_screen(self):
        try:
            screen = self.screen()
            if screen is not None:
                geo = screen.geometry()
                self.setGeometry(geo)
            self.showFullScreen()
        except Exception:
            pass
        QTimer.singleShot(0, self._apply_responsive_layout)
        QTimer.singleShot(120, self._apply_responsive_layout)

    def _prefs_path(self):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return os.path.join(appdata, "Manarey", "user_prefs.json")
        return os.path.join(
            os.path.expanduser("~"), ".manarey_prefs", "user_prefs.json"
        )

    def _restore_window_state(self):
        try:
            p_path = self._prefs_path()
            if not os.path.exists(p_path):
                return
            with open(p_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            prefs = data.get("main_local_window", {})
            geo = prefs.get("geometry")
            if geo:
                arr = QByteArray.fromBase64(geo.encode())
                if not arr.isEmpty():
                    self.restoreGeometry(arr)
            if prefs.get("maximized"):
                self.showMaximized()
        except Exception:
            pass

    def _persist_window_state(self):
        try:
            p_path = self._prefs_path()
            os.makedirs(os.path.dirname(p_path), exist_ok=True)
            data = {}
            if os.path.exists(p_path):
                with open(p_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            data["main_local_window"] = {
                "geometry": bytes(self.saveGeometry().toBase64()).decode(),
                "maximized": bool(self.isMaximized()),
            }
            with open(p_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _go_login(self):
        """Cierra este menú y vuelve a la pantalla de login."""
        try:
            if self.child:
                self.child.close()
        except Exception:
            pass
        try:
            self.hide()
            from views.login_view import LoginWindow

            self._login_win = LoginWindow(skip_autologin=True)
            self._login_win.show()
        except Exception:
            self.close()

    def _start_presence_pings(self):
        try:
            from PyQt5.QtCore import QTimer

            from models import user_model

            def ping():
                try:
                    user_model.update_last_seen(self.username)
                except Exception:
                    pass

            ping()  # immediately
            self._presence_timer = QTimer(self)
            self._presence_timer.setInterval(60_000)  # 1 min
            self._presence_timer.timeout.connect(ping)
            self._presence_timer.start()
        except Exception:
            pass

    # -------------------------
    # Actualizaciones
    # -------------------------
    def _parse_version(self, v: str):
        try:
            parts = str(v).split(".")
            return tuple(int(p) for p in parts)
        except Exception:
            return (0,)

    def _check_updates(self):
        """Refresca el indicador de actualizacion pendiente."""
        try:
            from updater import refresh_update_state

            refresh_update_state()
        except Exception:
            pass
        self._refresh_update_button()

    def _refresh_update_button(self):
        try:
            from updater import get_pending_update

            pending = get_pending_update()
            if pending:
                ver = pending.get("version") or ""
                if ver:
                    self.update_btn.setText(f"Descargar actualizacion {ver}")
                else:
                    self.update_btn.setText("Descargar actualizacion")
                self.update_btn.show()
                if hasattr(self, "status_updates"):
                    self.status_updates.set_value(
                        f"Version {ver}" if ver else "Lista para descargar"
                    )
            else:
                self.update_btn.hide()
                if hasattr(self, "status_updates"):
                    self.status_updates.set_value("Al dia")
        except Exception:
            if hasattr(self, "status_updates"):
                self.status_updates.set_value("Sin datos")

    def _on_update_click(self):
        try:
            from updater import start_update_from_pending

            start_update_from_pending(self)
        except Exception:
            QMessageBox.warning(
                self, "Actualizacion", "No se pudo iniciar la actualizacion."
            )

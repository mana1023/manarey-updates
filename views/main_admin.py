# views/main_admin.py
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Tuple

from PyQt5.QtCore import QByteArray, QDate, Qt, QTimer, QUrl
from PyQt5.QtGui import QColor, QDesktopServices, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models import db as db_mod
from models import problemas_model as pm
from models import ventas_model as vm

BG = "#0f131a"
BG_ALT = "#141b26"
CARD_BG = "#151d2a"
CARD_BORDER = "rgba(255,255,255,0.08)"
TEXT = "#e5e7eb"
TEXT_MUTED = "#a1a1aa"
ACCENT = "#f59e0b"


class AdminCard(QFrame):
    def __init__(self, icon: str, title: str, desc: str, color: str, callback=None):
        super().__init__()
        self._base_title = title
        self._callback = callback

        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(120)
        self.setStyleSheet(
            f"""
            QFrame {{
                background: {CARD_BG};
                border: 1px solid {CARD_BORDER};
                border-radius: 16px;
            }}
            QFrame:hover {{
                border: 1px solid {color};
                background: #1b2535;
            }}
        """
        )

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 6)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet(f"font-size: 22px; color: {color};")
        layout.addWidget(icon_lbl)

        self.title_lbl = QLabel(title)
        self.title_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.title_lbl.setStyleSheet(f"color: {TEXT};")
        layout.addWidget(self.title_lbl)

        desc_lbl = QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(desc_lbl)

        layout.addStretch()

    def set_title_badge(self, count: int):
        if count and count > 0:
            self.title_lbl.setText(f"{self._base_title} ({count})")
        else:
            self.title_lbl.setText(self._base_title)

    def mousePressEvent(self, event):
        if self._callback:
            self._callback()
        super().mousePressEvent(event)


# ----------------------------------------------------------
# Ventana principal de Administrador
# Acepta (username, role, local) como pedía login_view.py
# ----------------------------------------------------------
class AdminWindow(QMainWindow):
    def __init__(self, username: str, role: str, local: str = None):
        super().__init__()
        self.username = username
        self.role = role or "admin"
        self.local = local or ""  # local “por defecto” de trabajo (si aplica)
        self.child = None  # ventana hija (Stock/Historial/etc.)
        self._presence_timer = None

        self.setWindowTitle("Panel de Administración")
        self.setProperty("manarey_no_scale", True)
        self.setProperty("manarey_no_autoresize", True)
        self.setProperty("manarey_no_kiosk", True)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setMinimumSize(860, 580)
        self._initial_maximize_pending = True
        self._restore_window_state()
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {BG}, stop:1 {BG_ALT});
            }}
            QLabel {{ color:{TEXT}; }}
            QPushButton {{ font-size:16px; font-weight:600; }}
        """
        )

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(scroll)

        central = QWidget(self)
        central.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        scroll.setWidget(central)
        self.root = QVBoxLayout(central)
        self.root.setContentsMargins(22, 18, 22, 22)
        self.root.setSpacing(16)

        # Encabezado
        head_wrap = QFrame()
        head_wrap.setStyleSheet(
            f"""
            QFrame {{
                background: #0b111b;
                border: 1px solid {CARD_BORDER};
                border-radius: 16px;
                padding: 14px;
            }}
        """
        )
        head_lay = QVBoxLayout(head_wrap)
        head_lay.setContentsMargins(16, 14, 16, 14)
        head_lay.setSpacing(6)

        head = QLabel(f"Administrador — {self.username}")
        head.setFont(QFont("Segoe UI", 26, QFont.Black))
        head.setStyleSheet(f"color:{ACCENT};")
        head_lay.addWidget(head)

        sub_text = "Centro de control de ventas, stock y mensajes."
        if self.local:
            sub_text = f"Local por defecto: {self.local} · {sub_text}"
        sub = QLabel(sub_text)
        sub.setStyleSheet(f"color:{TEXT_MUTED}; font-size: 12px;")
        head_lay.addWidget(sub)

        self.root.addWidget(head_wrap)

        self.root.addSpacing(8)

        section = QLabel("Acciones principales")
        section.setStyleSheet(
            f"color:{TEXT_MUTED}; font-size: 12px; letter-spacing: 1px;"
        )
        self.root.addWidget(section)

        grid = QGridLayout()
        self.cards_grid = grid
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        cards = [
            (
                "📦",
                "Gestionar stock",
                "Administra inventario, transferencias y ediciones.",
                "#22c55e",
                self.open_stock,
            ),
            (
                "🧾",
                "Historial de ventas",
                "Revisa ventas, pagos y devoluciones.",
                "#f59e0b",
                self.open_sales_history,
            ),
            (
                "🕒",
                "Historial de movimientos",
                "Auditoría completa de cambios.",
                "#60a5fa",
                self.open_history,
            ),
            (
                "👥",
                "Gestionar usuarios",
                "Altas, bajas y roles del sistema.",
                "#e879f9",
                self.open_users,
            ),
            (
                "📣",
                "Mandar actualizaciones",
                "Envía avisos a todos los locales.",
                "#38bdf8",
                self.open_updates,
            ),
            (
                "💬",
                "Mensajes locales",
                "Inbox de mensajes de locales.",
                "#facc15",
                self.open_problemas,
            ),
            (
                "🔐",
                "Configurar retiros",
                "Contraseña y reglas de retiro.",
                "#f97316",
                self._open_cash_settings,
            ),
        ]

        self.card_problemas = None
        self.admin_cards = []
        for icon, title, desc, color, callback in cards:
            card = AdminCard(icon, title, desc, color, callback)
            if title == "Mensajes locales":
                self.card_problemas = card
            self.admin_cards.append(card)

        self._reflow_cards()

        self.root.addLayout(grid)

        # Resumen rápido debajo de los botones
        self._add_summary()

        self.root.addStretch()
        self._start_presence_pings()
        QTimer.singleShot(1200, self._check_updates)
        QTimer.singleShot(1500, self._notify_new_messages)
        self._messages_timer = QTimer(self)
        self._messages_timer.timeout.connect(self._update_messages_badge)
        self._messages_timer.start(60000)
        QTimer.singleShot(2000, self._update_messages_badge)
        self._last_unread_count = -1

        # Cerrar sesión
        row_logout = QHBoxLayout()
        row_logout.addStretch()

        btn_settings = QPushButton("⚙️")
        btn_settings.setCursor(Qt.PointingHandCursor)
        btn_settings.setMinimumSize(40, 40)
        btn_settings.setToolTip("Ajustes de pantalla")
        btn_settings.setStyleSheet(
            "QPushButton{background:transparent;color:#a0a0b0;border:1px solid #4a4a50;border-radius:10px;font-size:18px;} QPushButton:hover{background:#3a3a40;color:white;}"
        )
        btn_settings.clicked.connect(self.open_settings)
        row_logout.addWidget(btn_settings)

        btn_logout = QPushButton("Cerrar sesión / Volver al inicio de sesion")
        btn_logout.setCursor(Qt.PointingHandCursor)
        btn_logout.setStyleSheet(
            "QPushButton{background:#3a3a40;color:#ffb3b3;border:1px solid #4a4a50;border-radius:10px;padding:9px 14px;}"
        )
        btn_logout.clicked.connect(self._go_login)
        row_logout.addWidget(btn_logout)
        self.root.addLayout(row_logout)

    @staticmethod
    def _norm_local(loc: str) -> str:
        """Normaliza nombre de local para agrupar/mostrar."""
        loc = (loc or "").strip()
        return loc if loc else "Sin local"

    def _start_presence_pings(self):
        try:
            from models import update_center, user_model

            def ping():
                try:
                    user_model.update_last_seen(self.username)
                except Exception:
                    pass
                try:
                    update_center.latest_update()
                except Exception:
                    pass

            ping()
            self._presence_timer = QTimer(self)
            self._presence_timer.setInterval(60_000)
            self._presence_timer.timeout.connect(ping)
            self._presence_timer.start()
        except Exception:
            pass

    def _start_presence_ping(self):
        return self._start_presence_pings()

    def _get_config_path(self) -> Path:
        for p in db_mod.CONFIG_PATHS:
            try:
                if p.exists():
                    return p
            except Exception:
                continue
        return db_mod.CONFIG_PATHS[-1]

    def _load_config(self) -> dict:
        for p in db_mod.CONFIG_PATHS:
            try:
                if p.exists():
                    return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
        return {}

    def _save_config(self, cfg: dict) -> Tuple[bool, str]:
        try:
            path = self._get_config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            return True, "OK"
        except Exception as e:
            return False, str(e)

    def _open_cash_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Configurar retiros")
        lay = QFormLayout(dlg)

        new_pass = QLineEdit()
        new_pass.setEchoMode(QLineEdit.Password)
        confirm_pass = QLineEdit()
        confirm_pass.setEchoMode(QLineEdit.Password)

        lay.addRow("Nueva contraseña:", new_pass)
        lay.addRow("Confirmar:", confirm_pass)

        btns = QHBoxLayout()
        ok_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        lay.addRow(btns)

        def _save():
            p1 = (new_pass.text() or "").strip()
            p2 = (confirm_pass.text() or "").strip()
            if not p1 or not p2:
                QMessageBox.warning(
                    dlg, "Configurar retiros", "La contraseña no puede estar vacía."
                )
                return
            if p1 != p2:
                QMessageBox.warning(
                    dlg, "Configurar retiros", "Las contraseñas no coinciden."
                )
                return
            ok, msg = vm.set_cash_withdraw_password(p1)
            if not ok:
                QMessageBox.warning(
                    dlg, "Configurar retiros", f"No se pudo guardar: {msg}"
                )
                return
            QMessageBox.information(
                dlg, "Configurar retiros", "Contraseña actualizada."
            )
            dlg.accept()

        ok_btn.clicked.connect(_save)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec_()

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
        """Consulta app_updates y avisa si hay versión nueva para el propio admin."""
        try:
            from models import update_center
            from version import __version__

            latest = update_center.latest_update()
            if not latest:
                return
            current_v = self._parse_version(__version__)
            remote_v = self._parse_version(latest.get("version"))
            if remote_v <= current_v:
                return

            msg = f"Hay una nueva versión disponible: {latest.get('version')}\n\n"
            if latest.get("changelog"):
                msg += f"Notas:\n{latest.get('changelog')}\n\n"
            msg += "¿Descargar ahora?"
            buttons = QMessageBox.Yes | QMessageBox.No
            if latest.get("mandatory"):
                msg += "\n(Esta actualización es obligatoria)."
            resp = QMessageBox.question(self, "Actualización disponible", msg, buttons)
            if resp == QMessageBox.Yes:
                url = latest.get("download_url")
                if url:
                    QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass

    # -------------------------
    # Navegación a sub-vistas
    # -------------------------
    def _push_view(self, child: QMainWindow, title: str):
        """
        Oculta el menú de admin y muestra una vista hija (Stock/Historial/etc.)
        con un callback para volver.
        """
        self.child = child
        self.child.setWindowTitle(title)

        # armo botón “Volver” dentro de la vista hija con callback a _back_to_menu()
        def back():
            try:
                self.child.close()
            except Exception:
                pass
            self.child = None
            self.show()
            self.raise_()
            self.activateWindow()

        # si la vista hija soporta un atributo back_command, lo usamos
        if hasattr(self.child, "back_command") and self.child.back_command is None:
            self.child.back_command = back

        # si la vista hija define un método set_back_command, también lo aceptamos
        if hasattr(self.child, "set_back_command"):
            try:
                self.child.set_back_command(back)
            except Exception:
                pass

        self.hide()
        self.child.show()

    def _back_to_menu(self):
        """Vuelve al menú de admin (por si lo llaman directamente)."""
        if self.child is not None:
            try:
                self.child.close()
            except Exception:
                pass
            self.child = None
        self.show()
        self.raise_()
        self.activateWindow()

    # -------------------------
    # Acciones de los botones
    # -------------------------
    def open_stock(self):
        """
        Abre la vista de Stock (por local) usando views/stock_view.StockView.
        """
        try:
            from views.stock_view import StockView
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"No se pudo abrir la vista de stock:\n{e}"
            )
            return
        child = StockView(
            self.username, self.role, self.local, back_command=self._back_to_menu
        )
        title_local = (
            self.local
            if self.local
            else ("Administrador" if self.role == "admin" else "Local")
        )
        self._push_view(child, f"Stock de {title_local}")

    def open_inventory(self):
        """Abre el inventario consolidado (una fila por producto con cantidades por local)."""
        try:
            win = AdminInventoryWindow(self)
            self._push_view(win, "Inventario consolidado")
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"No se pudo abrir inventario consolidado:\n{e}"
            )

    def open_history(self):
        """
        Abre la vista de historial. Si existe views.history_view.HistoryWindow
        con firma (username, role, local, back_command), la usamos.
        """
        try:
            from views.history_view import HistoryWindow
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir el historial:\n{e}")
            return

        child = HistoryWindow(
            self.username, self.role, self.local or "", back_command=self._back_to_menu
        )
        self._push_view(child, "Historial de movimientos")

    def open_sales_history(self):
        """
        Abre la vista de historial de ventas (todas o por local).
        """
        try:
            from views.ventas_history_view import VentasWindow
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"No se pudo abrir historial de ventas:\n{e}"
            )
            return

        child = VentasWindow(
            self.username,
            self.role,
            self.local or "Todos",
            back_command=self._back_to_menu,
        )
        self._push_view(child, "Historial de ventas")

    def open_users(self):
        """Abre la vista de administración de usuarios."""
        try:
            from views.user_admin import UserAdminWindow
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"No se pudo abrir administración de usuarios:\n{e}"
            )
            return
        child = UserAdminWindow(self, back_command=self._back_to_menu)
        self._push_view(child, "Administración de usuarios")

    def on_back_from_boleta(self):
        try:
            self.boleta_win.close()
        except Exception:
            pass
        self.show()
        self.raise_()
        self.activateWindow()

    def open_updates(self):
        """
        Abre la vista de “Mandar actualizaciones” si existe; si no, muestra un aviso.
        """
        try:
            dlg = UpdateDialog(self)
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Falló ‘Mandar actualizaciones’:\n{e}")

    def open_problemas(self):
        """Abre la vista de mensajes de locales."""
        try:
            from views.problemas_view import ProblemasWindow
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"No se pudo abrir Mensajes locales:\n{e}"
            )
            return
        child = ProblemasWindow(
            self.username,
            self.role,
            self.local or "Todos",
            back_command=self._back_to_menu,
        )
        self._push_view(child, "Mensajes de locales")
        QTimer.singleShot(1200, self._update_messages_badge)

    def open_settings(self):
        try:
            from views.settings_dialog import SettingsDialog

            dlg = SettingsDialog(self)
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir ajustes:\n{e}")

    def _summary_card(self, title, value):
        box = QGroupBox(title)
        v = QVBoxLayout()
        box.setLayout(v)
        box.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                padding: 10px;
                background: #141b26;
                color: #a1a1aa;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
        """
        )
        val_label = QLabel(value)
        val_label.setFont(QFont("Segoe UI", 16, QFont.Bold))
        val_label.setStyleSheet("color:#e5e7eb;")
        v.addWidget(val_label)
        return box

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_maximize_pending:
            self._initial_maximize_pending = False
            QTimer.singleShot(0, self._force_fill_screen)
            return
        self._apply_responsive_layout()

    def _card_columns(self) -> int:
        w = max(1, self.width())
        if w < 860:
            return 1
        if w < 1280:
            return 2
        if w < 1740:
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
        for idx, card in enumerate(getattr(self, "admin_cards", [])):
            self.cards_grid.addWidget(card, idx // cols, idx % cols)
        for col in range(cols):
            self.cards_grid.setColumnStretch(col, 1)

    def _apply_responsive_layout(self):
        try:
            w = max(1, self.width())
            if w < 980:
                self.root.setContentsMargins(16, 14, 16, 16)
                self.root.setSpacing(12)
            else:
                self.root.setContentsMargins(22, 18, 22, 22)
                self.root.setSpacing(16)
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

    def _add_summary(self):
        """Agrega tarjetas de resumen debajo de los botones con filtro de tiempo."""
        self.summary_group = QGroupBox("Resumen rápido")
        summary_layout = QVBoxLayout()
        self.summary_group.setLayout(summary_layout)
        self.summary_group.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 14px;
                margin-top: 8px;
                padding: 12px;
                color: #e5e7eb;
                background: #0b111b;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #a1a1aa;
                font-weight: 700;
            }
        """
        )

        # Filtro de tiempo
        filter_row = QHBoxLayout()
        lbl = QLabel("Periodo:")
        filter_row.addWidget(lbl)

        self.summary_range = QComboBox()
        self.summary_range.addItems(
            ["Hoy", "Últimos 7 días", "Últimos 30 días", "Personalizado"]
        )
        self.summary_range.currentIndexChanged.connect(self._on_summary_range_changed)
        filter_row.addWidget(self.summary_range)

        self.summary_start = QDateEdit(calendarPopup=True)
        self.summary_start.setDisplayFormat("yyyy-MM-dd")
        self.summary_start.setDate(QDate.currentDate().addDays(-6))
        self.summary_start.setEnabled(False)
        filter_row.addWidget(QLabel("Desde:"))
        filter_row.addWidget(self.summary_start)

        self.summary_end = QDateEdit(calendarPopup=True)
        self.summary_end.setDisplayFormat("yyyy-MM-dd")
        self.summary_end.setDate(QDate.currentDate())
        self.summary_end.setEnabled(False)
        filter_row.addWidget(QLabel("Hasta:"))
        filter_row.addWidget(self.summary_end)

        self.summary_apply = QPushButton("Aplicar")
        self.summary_apply.setCursor(Qt.PointingHandCursor)
        self.summary_apply.clicked.connect(self._refresh_summary)
        self.summary_apply.setEnabled(False)
        filter_row.addWidget(self.summary_apply)

        filter_row.addStretch()
        summary_layout.addLayout(filter_row)

        # Contenedor de tarjetas
        self.summary_cards = QHBoxLayout()
        summary_layout.addLayout(self.summary_cards)
        self.root.addWidget(self.summary_group)

        # Top productos
        top_wrap = QGroupBox("Productos más vendidos")
        top_wrap.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                padding: 10px;
                background: #101827;
                color: #a1a1aa;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #e5e7eb;
                font-weight: 700;
            }
        """
        )
        top_lay = QVBoxLayout(top_wrap)
        self.top_table = QTableWidget(0, 3)
        self.top_table.setHorizontalHeaderLabels(["#", "Producto", "Cantidad"])
        self.top_table.verticalHeader().setVisible(False)
        self.top_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.top_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.top_table.setStyleSheet(
            """
            QTableWidget {
                background: #0b111b;
                color: #e5e7eb;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
            }
            QHeaderView::section {
                background: #0b111b;
                color: #e5e7eb;
                font-weight: 700;
                padding: 6px;
                border: none;
                border-bottom: 1px solid rgba(255,255,255,0.08);
            }
        """
        )
        header = self.top_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.top_table.setMinimumHeight(180)
        self.top_table.setMaximumHeight(320)
        top_lay.addWidget(self.top_table)
        summary_layout.addWidget(top_wrap)

        self._refresh_summary()

    def _on_summary_range_changed(self, idx):
        custom = self.summary_range.currentText() == "Personalizado"
        for widget in (self.summary_start, self.summary_end, self.summary_apply):
            widget.setEnabled(custom)
        if not custom:
            self._refresh_summary()

    def _notify_new_messages(self):
        try:
            unread = pm.count_admin_unread(self.username)
            if unread > 0:
                try:
                    QApplication.beep()
                except Exception:
                    pass
                QMessageBox.warning(
                    self,
                    "Mensajes de locales",
                    f"Tienes {unread} mensaje(s) nuevos de locales.\nPuedes verlos en 'Mensajes locales'.",
                )
        except Exception:
            pass

    def _update_messages_badge(self):
        try:
            unread = pm.count_admin_unread(self.username)
            if hasattr(self, "card_problemas") and self.card_problemas:
                self.card_problemas.set_title_badge(unread)
            # Notificar si aumentan mientras está en otra vista
            try:
                in_messages = False
                if self.child is not None:
                    in_messages = self.child.__class__.__name__ == "ProblemasWindow"
                if (
                    not in_messages
                    and unread > 0
                    and unread != self._last_unread_count
                    and self._last_unread_count >= 0
                ):
                    try:
                        QApplication.beep()
                    except Exception:
                        pass
                    QMessageBox.warning(
                        self,
                        "Mensajes de locales",
                        f"Tienes {unread} mensaje(s) nuevos de locales.\nPuedes verlos en 'Mensajes locales'.",
                    )
            finally:
                self._last_unread_count = unread
        except Exception:
            pass

    def _get_summary_dates(self):
        today = date.today()
        choice = self.summary_range.currentText()
        if choice == "Hoy":
            return today, today
        if choice == "Últimos 7 días":
            return today - timedelta(days=6), today
        if choice == "Últimos 30 días":
            return today - timedelta(days=29), today
        # Personalizado
        start = self.summary_start.date().toPyDate()
        end = self.summary_end.date().toPyDate()
        if start > end:
            start, end = end, start
        return start, end

    def _refresh_summary(self):
        """Recarga las tarjetas de resumen según el filtro de fechas."""
        start, end = self._get_summary_dates()
        try:
            stats = get_sales_stats(start.isoformat(), end.isoformat())
        except Exception:
            stats = {"total_ventas": 0, "total_productos": 0}
        try:
            stock = get_stock_status()
        except Exception:
            stock = {"stock_bajo": 0, "stock_agotado": 0}

        # limpiar tarjetas previas
        while self.summary_cards.count():
            item = self.summary_cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for title, value in [
            ("Ventas totales", f"${stats.get('total_ventas', 0):,.2f}"),
            ("Unidades vendidas", f"{stats.get('total_productos', 0):,}"),
            ("Stock bajo", str(stock.get("stock_bajo", 0))),
            ("Agotados", str(stock.get("stock_agotado", 0))),
        ]:
            self.summary_cards.addWidget(self._summary_card(title, value))

        # Top productos vendidos (sin filtro temporal)
        try:
            top = list_top_products(limit=8) or []
        except Exception:
            top = []
        self.top_table.setRowCount(0)
        for i, row in enumerate(top, start=1):
            r = self.top_table.rowCount()
            self.top_table.insertRow(r)
            self.top_table.setItem(r, 0, QTableWidgetItem(str(i)))
            self.top_table.setItem(
                r, 1, QTableWidgetItem(str(row.get("producto") or ""))
            )
            self.top_table.setItem(
                r, 2, QTableWidgetItem(str(row.get("cantidad") or 0))
            )

    def _build_consolidated_inventory(self):
        """Devuelve (headers, rows) de inventario consolidado para reuso."""
        try:
            locales_raw = get_all_locals() or []
            products = []
            if locales_raw:
                for loc in locales_raw:
                    products.extend(list_products_by_local(loc))
            else:
                products = list_products_by_local(None)
        except Exception:
            products = []
            locales_raw = []

        locales = list(dict.fromkeys([self._norm_local(l) for l in locales_raw]))
        headers = [
            "Producto",
            "Categoría",
            "Medida",
            "Estado",
            "Color",
            "Precio costo",
            "Precio venta",
            "Código",
            "Descripción",
            "Actualizado",
            "Total",
        ] + locales

        merged = {}
        for p in products:
            key = (
                p.get("nombre") or "",
                p.get("categoria") or "",
                p.get("medida") or "",
                p.get("estado") or "",
                p.get("color") or "",
            )
            entry = merged.setdefault(
                key,
                {
                    "locales": {},
                    "precio_costo": p.get("precio_costo") or 0,
                    "precio_venta": p.get("precio_venta") or 0,
                    "codigo": p.get("codigo") or "",
                    "descripcion": p.get("descripcion") or "",
                    "updated_at": p.get("updated_at") or "",
                },
            )
            loc = self._norm_local(p.get("local"))
            qty = int(p.get("cantidad") or 0)
            entry["locales"][loc] = entry["locales"].get(loc, 0) + qty
            if not entry["precio_costo"]:
                entry["precio_costo"] = p.get("precio_costo") or 0
            if not entry["precio_venta"]:
                entry["precio_venta"] = p.get("precio_venta") or 0
            if not entry["codigo"]:
                entry["codigo"] = p.get("codigo") or ""
            if not entry["descripcion"]:
                entry["descripcion"] = p.get("descripcion") or ""
            upd = str(p.get("updated_at") or "")
            if upd > entry["updated_at"]:
                entry["updated_at"] = upd

        rows = list(merged.items())
        return headers, rows

    def _refresh_inventory_table(self):
        """Construye tabla consolidada: una fila por producto con cantidades por local."""
        headers, rows = self._build_consolidated_inventory()
        table = self.inventory_table
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))

        locales = headers[11:]

        for idx, (key, data) in enumerate(rows):
            (nombre, categoria, medida, estado, color) = key
            precio_costo = data.get("precio_costo") or 0
            precio_venta = data.get("precio_venta") or 0
            codigo = data.get("codigo") or ""
            descripcion = data.get("descripcion") or ""
            updated_at = data.get("updated_at") or ""
            total_qty = sum(data["locales"].values())
            min_stock = 5
            item_bg = None
            if total_qty <= 0:
                item_bg = QColor("#ffdddd")
            elif total_qty <= min_stock:
                item_bg = QColor("#ffffcc")

            def _set(col, text, background=None):
                item = QTableWidgetItem(text)
                if background:
                    item.setBackground(background)
                table.setItem(idx, col, item)

            _set(0, nombre, item_bg)
            _set(1, categoria, item_bg)
            _set(2, medida, item_bg)
            _set(3, estado, item_bg)
            _set(4, color, item_bg)
            _set(5, f"${precio_costo:,.2f}", item_bg)
            _set(6, f"${precio_venta:,.2f}", item_bg)
            _set(7, codigo, item_bg)
            _set(8, descripcion, item_bg)
            _set(9, str(updated_at), item_bg)
            _set(10, str(total_qty), item_bg)
            for col_idx, loc in enumerate(locales, start=11):
                qty = data["locales"].get(loc, 0)
                bg = item_bg
                _set(col_idx, str(qty), bg)

    # -------------------------
    # Limpieza al cerrar
    # -------------------------
    def closeEvent(self, event):
        # intenta cerrar la hija si quedó abierta
        try:
            if self.child is not None:
                self.child.close()
        except Exception:
            pass
        self._persist_window_state()
        super().closeEvent(event)

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
            prefs = data.get("main_admin_window", {})
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
            data["main_admin_window"] = {
                "geometry": bytes(self.saveGeometry().toBase64()).decode(),
                "maximized": bool(self.isMaximized()),
            }
            with open(p_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _go_login(self):
        """Cierra el panel y abre la pantalla de login."""
        try:
            if self.child:
                self.child.close()
                self.child = None
        except Exception:
            pass
        try:
            self.hide()
            from views.login_view import LoginWindow

            self._login_win = LoginWindow(skip_autologin=True)
            self._login_win.show()
        except Exception:
            self.close()


class AdminInventoryWindow(QMainWindow):
    """Ventana de inventario consolidado para administradores."""

    def __init__(self, parent_admin: AdminWindow):
        super().__init__(parent=parent_admin)
        self.parent_admin = parent_admin
        self.setWindowTitle("Inventario consolidado")
        self.resize(1100, 700)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        summary = QGroupBox("Resumen rápido")
        s_layout = QHBoxLayout()
        summary.setLayout(s_layout)
        try:
            stats = get_sales_stats()
            stock = get_stock_status()
        except Exception:
            stats = {"total_ventas": 0, "total_productos": 0}
            stock = {"stock_bajo": 0, "stock_agotado": 0}
        for title, value in [
            ("Ventas totales", f"${stats.get('total_ventas', 0):,.2f}"),
            ("Unidades vendidas", f"{stats.get('total_productos', 0):,}"),
            ("Stock bajo", str(stock.get("stock_bajo", 0))),
            ("Agotados", str(stock.get("stock_agotado", 0))),
        ]:
            card = parent_admin._summary_card(title, value)
            s_layout.addWidget(card)
        layout.addWidget(summary)

        refresh_row = QHBoxLayout()
        refresh_row.addStretch()
        refresh_btn = QPushButton("Refrescar")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_inventory_table)
        refresh_row.addWidget(refresh_btn)
        layout.addLayout(refresh_row)

        self.table = QTableWidget()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        self._refresh_inventory_table()

    def _refresh_inventory_table(self):
        headers, rows = self.parent_admin._build_consolidated_inventory()
        table = self.table
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))

        locales = headers[11:]

        for idx, (key, data) in enumerate(rows):
            (nombre, categoria, medida, estado, color) = key
            precio_costo = data.get("precio_costo") or 0
            precio_venta = data.get("precio_venta") or 0
            codigo = data.get("codigo") or ""
            descripcion = data.get("descripcion") or ""
            updated_at = data.get("updated_at") or ""
            total_qty = sum(data["locales"].values())
            min_stock = 5
            item_bg = None
            if total_qty <= 0:
                item_bg = QColor("#ffdddd")
            elif total_qty <= min_stock:
                item_bg = QColor("#ffffcc")

            def _set(col, text, background=None):
                item = QTableWidgetItem(text)
                if background:
                    item.setBackground(background)
                table.setItem(idx, col, item)

            _set(0, nombre, item_bg)
            _set(1, categoria, item_bg)
            _set(2, medida, item_bg)
            _set(3, estado, item_bg)
            _set(4, color, item_bg)
            _set(5, f"${precio_costo:,.2f}", item_bg)
            _set(6, f"${precio_venta:,.2f}", item_bg)
            _set(7, codigo, item_bg)
            _set(8, descripcion, item_bg)
            _set(9, str(updated_at), item_bg)
            _set(10, str(total_qty), item_bg)
            for col_idx, loc in enumerate(locales, start=11):
                qty = data["locales"].get(loc, 0)
                bg = item_bg
                _set(col_idx, str(qty), bg)


# Compatibilidad con login_view: acepta un dict de usuario
class MainAdminWindow(AdminWindow):
    def __init__(self, user: dict):
        username = (user or {}).get("username", "")
        role = (user or {}).get("role", "admin")
        local = (user or {}).get("local") or ""
        super().__init__(username, role, local)


class UpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mandar actualización")
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QFormLayout(self)

        self.version = QLineEdit()
        self.download_url = QLineEdit()
        self.checksum = QLineEdit()
        self.mandatory = QCheckBox("Forzar actualización (mandatory)")
        self.changelog = QTextEdit()
        self.changelog.setPlaceholderText("Notas de la versión (opcional)")

        layout.addRow("Versión", self.version)
        layout.addRow("URL de descarga", self.download_url)
        layout.addRow("Checksum (sha256)", self.checksum)
        layout.addRow("", self.mandatory)
        layout.addRow("Changelog", self.changelog)

        btns = QHBoxLayout()
        btn_save = QPushButton("Publicar")
        btn_cancel = QPushButton("Cancelar")
        btn_save.clicked.connect(self._save)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addRow(btns)

    def _save(self):
        version = self.version.text().strip()
        url = self.download_url.text().strip()
        checksum = self.checksum.text().strip()
        changelog = self.changelog.toPlainText().strip()
        mandatory = self.mandatory.isChecked()

        if not version or not url:
            QMessageBox.warning(self, "Validación", "Versión y URL son obligatorios.")
            return
        try:
            from models import update_center

            ok = update_center.publish_update(
                version,
                url,
                changelog=changelog,
                checksum=checksum,
                mandatory=mandatory,
            )
            if ok:
                QMessageBox.information(
                    self, "Publicado", "Actualización guardada en la tabla app_updates."
                )
                self.accept()
            else:
                QMessageBox.warning(
                    self, "Error", "No se pudo guardar la actualización."
                )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo publicar: {e}")


from PyQt5.QtWidgets import QGroupBox, QHeaderView, QTableWidget, QTableWidgetItem

from models.firestore_db import (
    get_all_locals,
    get_sales_stats,
    get_stock_status,
    list_products_by_local,
    list_top_products,
)

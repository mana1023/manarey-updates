# views/problemas_view.py
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models import problemas_model as pm
from models.firestore_db import get_all_locals

BG = "#0f172a"
BG_ALT = "#111827"
TEXT = "#e5e7eb"
PRIMARY = "#38BDF8"


class ProblemasWindow(QMainWindow):
    def __init__(self, username: str, role: str, local: str, back_command=None):
        super().__init__()
        self.username = username or ""
        self.role = role or "local"
        self.local = (local or "").strip()
        self.back_command = back_command
        self._setup_ui()
        self.load_data()

    def _setup_ui(self):
        self.setWindowTitle("Informar Problemas")
        self.resize(980, 720)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(14)
        if self.back_command:
            back_btn = QPushButton("← Volver")
            back_btn.setCursor(Qt.PointingHandCursor)
            back_btn.setStyleSheet(
                "QPushButton{background:#34343a;color:#C9A040;border:1px solid #3e3e44;"
                "border-radius:10px;padding:8px 14px;font-weight:700;}"
                "QPushButton:hover{background:#3e3e44;}"
            )
            back_btn.clicked.connect(self.back_command)
            header.addWidget(back_btn)
        title = QLabel(
            "Informar Problemas" if self.role != "admin" else "Mensajes de Locales"
        )
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title.setStyleSheet(f"color:{PRIMARY};")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        filter_row = QHBoxLayout()
        if self.role == "admin":
            filter_row.addWidget(QLabel("Local:"))
            self.local_combo = QComboBox()
            self.local_combo.addItem("Todos")
            locales = [l for l in (get_all_locals() or []) if l]
            if self.local and self.local not in locales:
                locales.append(self.local)
            for loc in locales:
                self.local_combo.addItem(loc)
            self.local_combo.currentIndexChanged.connect(self.load_data)
            self.local_combo.currentIndexChanged.connect(self._update_send_state)
            filter_row.addWidget(self.local_combo)
        else:
            lbl = QLabel(f"Local: {self.local or 'Sin local'}")
            lbl.setStyleSheet(f"color:{TEXT};")
            filter_row.addWidget(lbl)

        refresh_btn = QPushButton("Refrescar")
        refresh_btn.clicked.connect(self.load_data)
        filter_row.addWidget(refresh_btn)
        self.delete_btn = QPushButton("Borrar mensaje")
        self.delete_btn.clicked.connect(self._delete_selected)
        filter_row.addWidget(self.delete_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        self.table = QTableWidget()
        self._setup_table(self.table)
        layout.addWidget(self.table)

        compose = QVBoxLayout()
        compose.setSpacing(6)
        compose.addWidget(QLabel("Nuevo mensaje:"))

        self.message_input = QTextEdit()
        self.message_input.setPlaceholderText("Describe el problema con detalle...")
        self.message_input.setFixedHeight(120)
        self.message_input.setStyleSheet(
            "QTextEdit{background:#0b1220;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:8px;}"
        )
        compose.addWidget(self.message_input)

        if self.role == "admin":
            targets = QVBoxLayout()
            targets.setSpacing(6)
            self.send_all_chk = QCheckBox("Enviar a todos los locales")
            self.send_all_chk.stateChanged.connect(self._update_send_state)
            targets.addWidget(self.send_all_chk)

            targets.addWidget(QLabel("Enviar a locales seleccionados:"))
            self.locals_list = QListWidget()
            self.locals_list.setSelectionMode(QAbstractItemView.MultiSelection)
            locales = [l for l in (get_all_locals() or []) if l]
            if self.local and self.local not in locales:
                locales.append(self.local)
            for loc in sorted(set(locales)):
                item = QListWidgetItem(loc)
                self.locals_list.addItem(item)
            self.locals_list.itemSelectionChanged.connect(self._update_send_state)
            self.locals_list.setFixedHeight(120)
            self.locals_list.setStyleSheet(
                "QListWidget{background:#0b1220;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:6px;}"
            )
            targets.addWidget(self.locals_list)
            compose.addLayout(targets)

        send_row = QHBoxLayout()
        self.send_btn = QPushButton("Enviar mensaje")
        self.send_btn.clicked.connect(self._send_message)
        send_row.addWidget(self.send_btn)
        send_row.addStretch()
        compose.addLayout(send_row)
        layout.addLayout(compose)

        self._update_send_state()

    def _setup_table(self, table: QTableWidget):
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Fecha", "Local", "Usuario", "Rol", "Mensaje"])
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setWordWrap(True)
        table.setStyleSheet(
            f"""
            QTableWidget {{
                background: {BG};
                alternate-background-color: {BG_ALT};
                color: {TEXT};
                gridline-color: #334155;
                border: 1px solid #1f2937;
                border-radius: 10px;
            }}
            QHeaderView::section {{
                background: {BG};
                color: {TEXT};
                font-weight: 700;
                padding: 10px;
                border: none;
                border-bottom: 2px solid {PRIMARY};
            }}
        """
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)

    def _get_filter_local(self) -> str | None:
        if self.role != "admin":
            return self.local or None
        if not hasattr(self, "local_combo"):
            return None
        selected = self.local_combo.currentText()
        if selected in ("Todos", "Todos los locales"):
            return None
        return selected

    def _update_send_state(self):
        if self.role == "admin":
            if hasattr(self, "send_all_chk") and self.send_all_chk.isChecked():
                if hasattr(self, "locals_list"):
                    self.locals_list.setEnabled(False)
                self.send_btn.setEnabled(True)
                return
            if hasattr(self, "locals_list"):
                self.locals_list.setEnabled(True)
                self.send_btn.setEnabled(len(self.locals_list.selectedItems()) > 0)
                return
        if self.role == "admin" and hasattr(self, "local_combo"):
            selected = self.local_combo.currentText()
            self.send_btn.setEnabled(selected not in ("Todos", "Todos los locales"))

    def load_data(self):
        local_filter = self._get_filter_local()
        last_seen = None
        if self.role != "admin":
            try:
                last_seen = pm.get_local_last_seen(self.local)
            except Exception:
                last_seen = None
        mensajes = pm.list_mensajes(local_filter)
        self.table.setRowCount(len(mensajes))
        for i, msg in enumerate(mensajes):
            fecha = QTableWidgetItem(str(msg.get("created_at") or ""))
            local = QTableWidgetItem(str(msg.get("local") or ""))
            usuario = QTableWidgetItem(str(msg.get("usuario") or ""))
            rol = QTableWidgetItem(str(msg.get("rol") or ""))
            texto = QTableWidgetItem(str(msg.get("mensaje") or ""))

            if str(msg.get("rol") or "").lower() == "admin":
                rol.setForeground(QColor("#34d399"))
            else:
                rol.setForeground(QColor("#fbbf24"))

            # guardar ID en la primera celda
            try:
                fecha.setData(Qt.UserRole, msg.get("id"))
            except Exception:
                pass
            self.table.setItem(i, 0, fecha)
            self.table.setItem(i, 1, local)
            self.table.setItem(i, 2, usuario)
            self.table.setItem(i, 3, rol)
            self.table.setItem(i, 4, texto)

            # Resaltar mensajes no leídos para locales
            if self.role != "admin":
                try:
                    is_admin_msg = str(msg.get("rol") or "").lower() == "admin"
                    created_at = str(msg.get("created_at") or "")
                    unread = False
                    if is_admin_msg and created_at:
                        if last_seen:
                            unread = created_at > last_seen
                        else:
                            unread = True
                    if unread:
                        for col in range(5):
                            it = self.table.item(i, col)
                            if it:
                                it.setBackground(QColor("#1f2937"))
                                it.setForeground(QColor("#fbbf24"))
                                f = it.font()
                                f.setBold(True)
                                it.setFont(f)
                except Exception:
                    pass
        self.table.resizeRowsToContents()
        if mensajes:
            self.table.scrollToBottom()
        if self.role == "admin":
            try:
                pm.mark_admin_seen(self.username)
            except Exception:
                pass
        else:
            try:
                pm.mark_local_seen(self.local)
            except Exception:
                pass

    def _send_message(self):
        mensaje = self.message_input.toPlainText().strip()
        if len(mensaje) < 10:
            QMessageBox.warning(
                self, "Mensaje", "Por favor explica el problema con mas detalle."
            )
            return

        target_local = self.local or "Sin local"
        if self.role == "admin":
            locales = []
            if hasattr(self, "send_all_chk") and self.send_all_chk.isChecked():
                locales = [l for l in (get_all_locals() or []) if l]
            elif hasattr(self, "locals_list"):
                locales = [i.text() for i in self.locals_list.selectedItems()]
            locales = sorted(set([l for l in locales if l]))
            if not locales:
                QMessageBox.warning(
                    self,
                    "Mensaje",
                    "Selecciona uno o más locales para enviar el mensaje.",
                )
                return
            errors = []
            for loc in locales:
                ok, msg = pm.add_mensaje(loc, self.username, self.role, mensaje)
                if not ok:
                    errors.append(f"{loc}: {msg}")
            if errors:
                QMessageBox.warning(
                    self, "Mensaje", "No se pudo enviar a:\n" + "\n".join(errors)
                )
                return
        else:
            ok, msg = pm.add_mensaje(target_local, self.username, self.role, mensaje)
            if not ok:
                QMessageBox.warning(self, "Mensaje", f"No se pudo enviar: {msg}")
                return

        self.message_input.clear()
        self.load_data()

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Borrar mensaje", "Selecciona un mensaje para borrar."
            )
            return
        id_item = self.table.item(row, 0)
        msg_id = None
        try:
            msg_id = id_item.data(Qt.UserRole) if id_item else None
        except Exception:
            msg_id = None
        if not msg_id:
            QMessageBox.warning(
                self, "Borrar mensaje", "No se pudo obtener el ID del mensaje."
            )
            return
        confirm = QMessageBox.question(
            self,
            "Borrar mensaje",
            "¿Borrar el mensaje seleccionado?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        ok, msg = pm.delete_mensajes_scoped(
            [msg_id], self.role, self.username, self.local
        )
        if not ok:
            QMessageBox.warning(self, "Borrar mensaje", f"No se pudo borrar: {msg}")
            return
        self.load_data()

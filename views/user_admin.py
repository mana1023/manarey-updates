# views/user_admin.py
import json
import logging
import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models import db as db_mod
from models.db import get_connection
from models.firestore_db import get_all_locals

_audit_logger = logging.getLogger("manarey.audit")
import re

# Username validation (same rules as login)
_USERNAME_RE = re.compile(r"^[\w\s@.+-]{1,150}$")


def _validate_username(u: str) -> bool:
    return bool(u and _USERNAME_RE.match(u))


def _validate_password(p: str, creating=True) -> bool:
    if creating:
        return bool(p and 6 <= len(p) <= 128)
    else:
        return bool(not p or (6 <= len(p) <= 128))


class UserEditDialog(QDialog):
    def __init__(self, parent=None, user=None):
        super().__init__(parent)
        self.setWindowTitle("Usuario")
        self.user = user
        layout = QFormLayout(self)

        info = QLabel(
            "Aquí podés crear usuarios nuevos, cambiar contraseñas, asignar roles\n"
            "y definir el local del usuario."
        )
        info.setStyleSheet("color:#9ca3af; font-size:11px;")
        layout.addRow(info)

        self.username = QLineEdit()
        self.username.setPlaceholderText("usuario")

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("mínimo 6 caracteres")

        self.password_confirm = QLineEdit()
        self.password_confirm.setEchoMode(QLineEdit.Password)
        self.password_confirm.setPlaceholderText("repetir contraseña")

        self.role = QComboBox()
        self.role.addItems(["admin", "local"])
        self.local = QLineEdit()
        self.local.setPlaceholderText("ej: Vidriera, Depósito, Local 2")

        layout.addRow("Usuario", self.username)
        layout.addRow("Contraseña (dejar en blanco para no cambiar)", self.password)
        layout.addRow("Confirmar contraseña", self.password_confirm)

        show_pass = QCheckBox("Mostrar contraseña")
        show_pass.toggled.connect(
            lambda v: (
                self.password.setEchoMode(
                    QLineEdit.Normal if v else QLineEdit.Password
                ),
                self.password_confirm.setEchoMode(
                    QLineEdit.Normal if v else QLineEdit.Password
                ),
            )
        )
        layout.addRow("", show_pass)
        layout.addRow("Rol", self.role)
        layout.addRow("Local", self.local)

        btns = QHBoxLayout()
        ok = QPushButton("Guardar")
        cancel = QPushButton("Cancelar")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addRow(btns)

        if user:
            self.username.setText(user.get("username") or "")
            self.username.setEnabled(False)
            self.role.setCurrentText(user.get("role") or "local")
            self.local.setText(user.get("local") or "")

    def get_data(self):
        return {
            "username": self.username.text().strip(),
            "password": self.password.text(),
            "password_confirm": self.password_confirm.text(),
            "role": self.role.currentText(),
            "local": self.local.text().strip() or None,
        }


class UserAdminWindow(QMainWindow):
    def __init__(self, parent=None, back_command=None):
        super().__init__(parent)
        self.back_command = back_command
        self.setWindowTitle("Administración de usuarios")
        self.resize(980, 620)
        central = QWidget(self)
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        head_row = QHBoxLayout()
        if self.back_command:
            btn_back = QPushButton("← Volver")
            btn_back.setCursor(Qt.PointingHandCursor)
            btn_back.setStyleSheet(
                "QPushButton{background:#34343a;color:#C9A040;border:1px solid #3e3e44;"
                "border-radius:10px;padding:8px 14px;font-weight:700;}"
                "QPushButton:hover{background:#3e3e44;}"
            )
            btn_back.clicked.connect(self._go_back)
            head_row.addWidget(btn_back)
        head = QLabel("Gestión de usuarios")
        head.setStyleSheet("font-size:20px; font-weight:800;")
        head_row.addWidget(head)
        head_row.addStretch()
        v.addLayout(head_row)
        subtitle = QLabel("Crear usuarios, cambiar contraseñas, roles y locales.")
        subtitle.setStyleSheet("color:#9ca3af; font-size:12px;")
        v.addWidget(subtitle)

        helper = QFrame()
        helper.setStyleSheet(
            "QFrame{background:#111827;border:1px solid #1f2937;border-radius:10px;}"
        )
        helper_lay = QHBoxLayout(helper)
        helper_lay.setContentsMargins(12, 10, 12, 10)
        helper_text = QLabel(
            "Tip: Seleccioná un usuario en la tabla para editar su rol, local o contraseña."
        )
        helper_text.setStyleSheet("color:#e5e7eb;")
        helper_lay.addWidget(helper_text)
        v.addWidget(helper)
        # Search + pagination controls
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Buscar por usuario o local...")
        search_btn = QPushButton("Buscar")
        search_btn.clicked.connect(self.load_users)
        search_row.addWidget(self.search_input)
        search_row.addWidget(search_btn)
        v.addLayout(search_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Usuario", "Role", "Local", "Última actividad", "Online"]
        )
        self.table.setSelectionBehavior(self.table.SelectRows)
        v.addWidget(self.table)

        # Pagination
        self._page = 0
        self._page_size = 20
        pag_row = QHBoxLayout()
        self.prev_btn = QPushButton("Anterior")
        self.next_btn = QPushButton("Siguiente")
        self.page_label = QLabel("Página 1")
        self.prev_btn.clicked.connect(self._on_prev)
        self.next_btn.clicked.connect(self._on_next)
        pag_row.addWidget(self.prev_btn)
        pag_row.addWidget(self.next_btn)
        pag_row.addStretch()
        pag_row.addWidget(self.page_label)
        v.addLayout(pag_row)

        row = QHBoxLayout()
        btn_add = QPushButton("Añadir")
        btn_edit = QPushButton("✏️ Editar usuario")
        btn_delete = QPushButton("🗑️ Eliminar usuario")
        btn_refresh = QPushButton("🔄 Refrescar")
        row.addWidget(btn_add)
        row.addWidget(btn_edit)
        row.addWidget(btn_delete)
        row.addStretch()
        row.addWidget(btn_refresh)
        v.addLayout(row)

        btn_add.clicked.connect(self.add_user)
        btn_edit.clicked.connect(self.edit_user)
        btn_delete.clicked.connect(self.delete_user)
        btn_refresh.clicked.connect(self.load_users)

        # --- Locales: direcciones y teléfonos ---
        locales_frame = QFrame()
        locales_frame.setStyleSheet(
            "QFrame{background:#0f172a;border:1px solid #1f2937;border-radius:10px;}"
        )
        locales_layout = QVBoxLayout(locales_frame)
        locales_layout.setContentsMargins(12, 10, 12, 10)
        locales_title = QLabel("Locales: dirección y teléfono (boletas)")
        locales_title.setStyleSheet("font-size:14px; font-weight:800; color:#e5e7eb;")
        locales_layout.addWidget(locales_title)

        self.locales_table = QTableWidget(0, 3)
        self.locales_table.setHorizontalHeaderLabels(["Local", "Dirección", "Teléfono"])
        self.locales_table.setSelectionBehavior(self.locales_table.SelectRows)
        locales_layout.addWidget(self.locales_table)

        locales_btns = QHBoxLayout()
        btn_add_local = QPushButton("Agregar local")
        btn_save_locales = QPushButton("Guardar locales")
        locales_btns.addWidget(btn_add_local)
        locales_btns.addWidget(btn_save_locales)
        locales_btns.addStretch()
        locales_layout.addLayout(locales_btns)

        btn_add_local.clicked.connect(self._add_local_row)
        btn_save_locales.clicked.connect(self._save_locales_info)
        v.addWidget(locales_frame)

        self.load_users()
        self._load_locales_info()

    def _load_config(self) -> dict:
        for p in db_mod.CONFIG_PATHS:
            try:
                if p.exists():
                    return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
        return {}

    def _save_config(self, data: dict) -> bool:
        try:
            target = None
            for p in db_mod.CONFIG_PATHS:
                if p.exists():
                    target = p
                    break
            if target is None:
                target = db_mod.CONFIG_PATHS[0]
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return True
        except Exception:
            return False

    def _load_locales_info(self):
        data = self._load_config()
        locales_info = data.get("locales_info", {}) if isinstance(data, dict) else {}
        locales_set = set(locales_info.keys())
        try:
            locales_set.update(
                {
                    (l or "").strip()
                    for l in (get_all_locals() or [])
                    if (l or "").strip()
                }
            )
        except Exception:
            pass
        locales_list = sorted([l for l in locales_set if l])

        self.locales_table.setRowCount(0)
        for loc in locales_list:
            info = locales_info.get(loc, {}) or {}
            row = self.locales_table.rowCount()
            self.locales_table.insertRow(row)
            self.locales_table.setItem(row, 0, QTableWidgetItem(loc))
            self.locales_table.setItem(
                row, 1, QTableWidgetItem(info.get("direccion", ""))
            )
            self.locales_table.setItem(
                row, 2, QTableWidgetItem(info.get("telefono", ""))
            )

    def _add_local_row(self):
        row = self.locales_table.rowCount()
        self.locales_table.insertRow(row)
        self.locales_table.setItem(row, 0, QTableWidgetItem(""))
        self.locales_table.setItem(row, 1, QTableWidgetItem(""))
        self.locales_table.setItem(row, 2, QTableWidgetItem(""))

    def _save_locales_info(self):
        data = self._load_config()
        locales_info = {}
        for r in range(self.locales_table.rowCount()):
            local_item = self.locales_table.item(r, 0)
            if not local_item:
                continue
            local_name = (local_item.text() or "").strip()
            if not local_name:
                continue
            direccion = ""
            telefono = ""
            dir_item = self.locales_table.item(r, 1)
            tel_item = self.locales_table.item(r, 2)
            if dir_item:
                direccion = (dir_item.text() or "").strip()
            if tel_item:
                telefono = (tel_item.text() or "").strip()
            locales_info[local_name] = {"direccion": direccion, "telefono": telefono}
        if isinstance(data, dict):
            data["locales_info"] = locales_info
        if self._save_config(data):
            QMessageBox.information(self, "Locales", "Datos de locales guardados.")
        else:
            QMessageBox.warning(self, "Locales", "No se pudo guardar la configuración.")

    def load_users(self):
        try:
            conn = get_connection()
            cur = conn.cursor()
            # Apply search filter and pagination
            q = self.search_input.text().strip()
            params = []
            ph = (
                "?"
                if hasattr(cur, "execute") and not getattr(cur, "mogrify", None)
                else "%s"
            )
            sql = "SELECT username, role, local, last_seen FROM usuarios"
            if q:
                sql += f" WHERE username LIKE {ph} OR local LIKE {ph}"
                params.extend([f"%{q}%", f"%{q}%"])
            sql += f" ORDER BY username LIMIT {ph} OFFSET {ph}"
            params.extend([self._page_size, self._page * self._page_size])
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo leer usuarios:\n{e}")
            return

        self.table.setRowCount(0)
        from datetime import datetime, timedelta

        for r in rows:
            try:
                username = r[0]
                role = r[1]
                local = r[2] or ""
                last_seen = r[3]
            except Exception:
                username = r["username"]
                role = r["role"]
                local = r["local"] or ""
                last_seen = r["last_seen"]

            try:
                if last_seen:
                    last_seen = (
                        last_seen
                        if isinstance(last_seen, str)
                        else last_seen.isoformat(sep=" ", timespec="seconds")
                    )
            except Exception:
                last_seen = str(last_seen or "")

            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QTableWidgetItem(username))
            self.table.setItem(row_idx, 1, QTableWidgetItem(role))
            self.table.setItem(row_idx, 2, QTableWidgetItem(local))
            self.table.setItem(row_idx, 3, QTableWidgetItem(last_seen or ""))

            # Determinar si está online (últimos 5 minutos)
            online = "No"
            if last_seen:
                try:
                    # last_seen expected in format YYYY-MM-DD HH:MM:SS
                    dt = datetime.fromisoformat(last_seen)
                    if datetime.now() - dt <= timedelta(minutes=5):
                        online = "Sí"
                except Exception:
                    online = "??"
            self.table.setItem(row_idx, 4, QTableWidgetItem(online))
        # update page label
        self.page_label.setText(f"Página {self._page + 1}")
        # enable/disable prev/next
        self.prev_btn.setEnabled(self._page > 0)
        # rough enable next (if rows == page_size then maybe more)
        self.next_btn.setEnabled(len(rows) >= self._page_size)

    def _go_back(self):
        if callable(self.back_command):
            try:
                self.back_command()
                return
            except Exception:
                pass
        self.close()

    def set_back_command(self, cb):
        self.back_command = cb

    def _selected_username(self):
        sel = self.table.selectedItems()
        if not sel:
            return None
        return sel[0].text()

    def add_user(self):
        dlg = UserEditDialog(self, user=None)
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not _validate_username(data["username"]):
            QMessageBox.warning(
                self,
                "Aviso",
                "Nombre de usuario inválido. Usa letras, números y símbolos @ . + - (max 150).",
            )
            return
        if not _validate_password(data["password"], creating=True):
            QMessageBox.warning(
                self, "Aviso", "Contraseña inválida. Mínimo 6 caracteres."
            )
            return
        try:
            from models import auth as auth_mod
            from models import user_model

            # Preparar datos
            hashed = (
                auth_mod.hash_password(data["password"])
                if auth_mod
                else data["password"]
            )
            user_data = {
                "username": data["username"],
                "password": hashed,
                "role": data["role"],
                "local": data["local"],
            }

            # Usar Dual Write
            if user_model.add_user_dual(user_data):
                self.load_users()
                try:
                    _audit_logger.info(
                        "user_created",
                        extra={
                            "username": data["username"],
                            "role": data["role"],
                            "local": data["local"],
                        },
                    )
                except Exception:
                    pass
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    "Hubo un problema al crear el usuario en algunos sistemas.",
                )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo crear usuario:\n{e}")

    def edit_user(self):
        username = self._selected_username()
        if not username:
            QMessageBox.information(
                self, "Seleccionar", "Selecciona un usuario para editar"
            )
            return
        # cargar usuario (lectura desde SQL está bien para la UI actual)
        try:
            conn = get_connection()
            cur = conn.cursor()
            ph = (
                "?"
                if hasattr(cur, "execute") and not getattr(cur, "mogrify", None)
                else "%s"
            )
            cur.execute(
                f"SELECT username, role, local FROM usuarios WHERE username = {ph}",
                (username,),
            )
            row = cur.fetchone()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo leer usuario:\n{e}")
            return
        try:
            user = {"username": row[0], "role": row[1], "local": row[2]}
        except Exception:
            user = {
                "username": row["username"],
                "role": row["role"],
                "local": row["local"],
            }

        dlg = UserEditDialog(self, user=user)
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.get_data()
        try:
            from models import auth as auth_mod
            from models import user_model

            if data["password"] and not _validate_password(
                data["password"], creating=False
            ):
                QMessageBox.warning(
                    self, "Aviso", "Contraseña inválida (mínimo 6 caracteres)."
                )
                return

            update_data = {"role": data["role"], "local": data["local"]}
            if data["password"]:
                hashed = (
                    auth_mod.hash_password(data["password"])
                    if auth_mod
                    else data["password"]
                )
                update_data["password"] = hashed

            # Usar Dual Write
            if user_model.update_user_dual(username, update_data):
                self.load_users()
                try:
                    _audit_logger.info(
                        "user_updated",
                        extra={
                            "username": username,
                            "role": data["role"],
                            "local": data["local"],
                        },
                    )
                except Exception:
                    pass
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    "Hubo un problema al actualizar el usuario en algunos sistemas.",
                )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo actualizar usuario:\n{e}")

    def delete_user(self):
        username = self._selected_username()
        if not username:
            QMessageBox.information(
                self, "Seleccionar", "Selecciona un usuario para eliminar"
            )
            return
        if username.lower() == "administrador" or username.lower() == "admin":
            QMessageBox.warning(
                self, "Prohibido", "No se puede eliminar el usuario Administrador"
            )
            return
        r = QMessageBox.question(
            self,
            "Confirmar",
            f"¿Eliminar usuario {username}? Esta acción es irreversible.",
        )
        if r != QMessageBox.Yes:
            return
        try:
            from models import user_model

            # Usar Dual Write
            if user_model.delete_user_dual(username):
                self.load_users()
                try:
                    _audit_logger.info("user_deleted", extra={"username": username})
                except Exception:
                    pass
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    "Hubo un problema al eliminar el usuario en algunos sistemas.",
                )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo eliminar usuario:\n{e}")

    def _on_prev(self):
        if self._page > 0:
            self._page -= 1
            self.load_users()

    def _on_next(self):
        self._page += 1
        self.load_users()

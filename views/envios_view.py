# views/envios_view.py
import os
import platform
import subprocess
from datetime import datetime, timedelta

from PyQt5.QtCore import QAbstractTableModel, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models import ventas_historial_model as vhm
from models import ventas_model as vm
from models.firestore_db import get_all_locals

BG = "#0f172a"
BG_ALT = "#111827"
TEXT = "#e5e7eb"
PRIMARY = "#ffc107"
GREEN = "#10b981"


def _parse_envio_datetime(value):
    s = str(value or "").strip()
    if not s:
        return None
    if "." in s:
        s = s.split(".")[0]
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def _format_programada_label(value):
    dt = _parse_envio_datetime(value)
    if not dt:
        return "Hoy"
    today = datetime.now().date()
    if dt.date() == today:
        return "Hoy"
    return ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"][
        dt.weekday()
    ]


class EnviosTableModel(QAbstractTableModel):
    HEADERS = [
        "Remito",
        "Fecha",
        "A partir de",
        "Local",
        "Entrega",
        "Cliente",
        "Direccion",
        "Pago envio local",
        "Accion",
    ]

    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = list(rows or [])
        self._bulk_mode = False
        self._bulk_selected = set()

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def set_bulk_state(self, bulk_mode: bool, selected_ids: set):
        self._bulk_mode = bool(bulk_mode)
        self._bulk_selected = set(selected_ids or [])
        if self._rows:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._rows) - 1, 0)
            self.dataChanged.emit(
                top_left, bottom_right, [Qt.DisplayRole, Qt.ForegroundRole]
            )

    def rowCount(self, parent=None):
        return len(self._rows)

    def columnCount(self, parent=None):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            try:
                return self.HEADERS[section]
            except Exception:
                return None
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._rows):
            return None
        v = self._rows[row]
        remito_ok = int(v.get("remito_impreso") or 0) == 1
        venta_id = int(v.get("id") or 0)

        if role == Qt.DisplayRole:
            if col == 0:
                if self._bulk_mode:
                    return "?" if venta_id in self._bulk_selected else ""
                return "?" if remito_ok else ""
            if col == 1:
                return str(v.get("fecha") or "")
            if col == 2:
                return str(v.get("_programada_fmt") or "")
            if col == 3:
                return str(v.get("local") or "")
            if col == 4:
                return str(v.get("entrega_local") or "")
            if col == 5:
                return str(v.get("cliente_nombre") or "")
            if col == 6:
                return str(v.get("_direccion_fmt") or "")
            if col == 7:
                return str(v.get("_pago_envio_fmt") or "")
            if col == 8:
                return ""

        if role == Qt.TextAlignmentRole:
            if col in (0, 2, 3, 4, 7):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.ForegroundRole:
            if col == 0:
                if self._bulk_mode:
                    return QColor(PRIMARY)
                return QColor(GREEN) if remito_ok else QColor(TEXT)
            return QColor(TEXT)

        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled

    def venta_id_at(self, row):
        if 0 <= row < len(self._rows):
            try:
                return int(self._rows[row].get("id") or 0)
            except Exception:
                return 0
        return 0


class EnviosWindow(QMainWindow):
    def __init__(self, username: str, role: str, local: str, back_command=None):
        super().__init__()
        self.username = username
        self.role = role
        self.local = local or "Todos"
        self.back_command = back_command
        self._bulk_select_mode = False
        self._bulk_selected_ids = set()
        self._cobros_unlocked = False
        self._domicilio_rows = []
        self._load_counter = 0
        self._current_load_id = 0
        self._load_thread = None
        self._setup_ui()
        self.load_data()

    def _setup_ui(self):
        self.setWindowTitle("Envios")
        self.resize(1100, 780)
        self.setWindowState(self.windowState() | Qt.WindowMaximized)

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
        title = QLabel("Envios")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title.setStyleSheet(f"color:{PRIMARY};")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        filters = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Buscar por cliente o numero de venta")
        self.search_input.textChanged.connect(self.load_data)
        filters.addWidget(self.search_input)

        refresh_btn = QPushButton("Refrescar")
        refresh_btn.clicked.connect(self.load_data)
        filters.addWidget(refresh_btn)

        self.cobros_btn = QPushButton("Cobros domicilio")
        self.cobros_btn.clicked.connect(self._toggle_cobros_panel)
        filters.addWidget(self.cobros_btn)
        filters.addStretch()
        layout.addLayout(filters)

        self.cobros_panel = QWidget()
        self.cobros_panel.setVisible(False)
        self.cobros_panel.setStyleSheet(
            f"background:{BG_ALT};border:1px solid #1f2937;border-radius:12px;"
        )
        cobros_layout = QVBoxLayout(self.cobros_panel)
        cobros_layout.setContentsMargins(14, 14, 14, 14)
        cobros_layout.setSpacing(10)

        cobros_header = QHBoxLayout()
        self.cobros_total_label = QLabel("Cobrado en casa pendiente (sin envio): $0")
        self.cobros_total_label.setStyleSheet(
            f"color:{PRIMARY}; font-weight:900; font-size:15px;"
        )
        cobros_header.addWidget(self.cobros_total_label)
        cobros_header.addStretch()

        self.cobros_withdraw_selected_btn = QPushButton("Retirar seleccionados")
        self.cobros_withdraw_selected_btn.clicked.connect(
            self._retirar_cobros_seleccionados
        )
        cobros_header.addWidget(self.cobros_withdraw_selected_btn)

        self.cobros_withdraw_all_btn = QPushButton("Retirar todo")
        self.cobros_withdraw_all_btn.clicked.connect(self._retirar_todos_cobros)
        cobros_header.addWidget(self.cobros_withdraw_all_btn)
        cobros_layout.addLayout(cobros_header)

        self.cobros_table = QTableWidget()
        self.cobros_table.setColumnCount(7)
        self.cobros_table.setHorizontalHeaderLabels(
            [
                "Fecha cobro",
                "Local",
                "Venta",
                "Cliente",
                "Monto productos",
                "Direccion",
                "Acciones",
            ]
        )
        self.cobros_table.verticalHeader().setVisible(False)
        self.cobros_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cobros_table.setSelectionMode(QAbstractItemView.MultiSelection)
        self.cobros_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cobros_table.setStyleSheet(
            "QTableWidget{background:#0f172a;color:#e5e7eb;border:1px solid #1f2937;border-radius:10px;}"
            "QHeaderView::section{background:#0b1220;color:#e5e7eb;font-weight:700;padding:8px;border:none;}"
        )
        cobros_header_view = self.cobros_table.horizontalHeader()
        cobros_header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        cobros_header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        cobros_header_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        cobros_header_view.setSectionResizeMode(3, QHeaderView.Stretch)
        cobros_header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        cobros_header_view.setSectionResizeMode(5, QHeaderView.Stretch)
        cobros_header_view.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        cobros_layout.addWidget(self.cobros_table)
        layout.addWidget(self.cobros_panel)

        pendientes_header = QHBoxLayout()
        pendientes_label = QLabel("Restantes")
        pendientes_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
        pendientes_label.setStyleSheet(f"color:{TEXT};")
        pendientes_header.addWidget(pendientes_label)
        pendientes_header.addStretch()
        self.print_all_btn = QPushButton("Imprimir todo")
        self.print_all_btn.clicked.connect(self._on_bulk_print_clicked)
        pendientes_header.addWidget(self.print_all_btn)
        self.toggle_programados_btn = QPushButton("Ver programados")
        self.toggle_programados_btn.clicked.connect(self._toggle_programados)
        pendientes_header.addWidget(self.toggle_programados_btn)
        layout.addLayout(pendientes_header)

        self.table_pendientes = QTableView()
        self.pendientes_model = EnviosTableModel([], self)
        self.table_pendientes.setModel(self.pendientes_model)
        self._setup_table(self.table_pendientes)
        self.table_pendientes.clicked.connect(self._on_pendientes_cell_clicked)
        layout.addWidget(self.table_pendientes)

        self.programados_label = QLabel("Programados")
        self.programados_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.programados_label.setStyleSheet(f"color:{TEXT};")
        self.programados_label.setVisible(False)
        layout.addWidget(self.programados_label)

        self.table_programados = QTableView()
        self.programados_model = EnviosTableModel([], self)
        self.table_programados.setModel(self.programados_model)
        self._setup_table(self.table_programados)
        self.table_programados.setVisible(False)
        layout.addWidget(self.table_programados)

        entregados_label = QLabel("Entregados")
        entregados_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
        entregados_label.setStyleSheet(f"color:{TEXT};")
        layout.addWidget(entregados_label)

        self.table_entregados = QTableView()
        self.entregados_model = EnviosTableModel([], self)
        self.table_entregados.setModel(self.entregados_model)
        self._setup_table(self.table_entregados)
        layout.addWidget(self.table_entregados)

    def _setup_table(self, table: QTableView):
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(38)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setStyleSheet(
            f"""
            QTableView {{
                background: {BG};
                alternate-background-color: {BG_ALT};
                color: {TEXT};
                gridline-color: #334155;
                border: 1px solid #1f2937;
                border-radius: 10px;
            }}
            QTableView::item {{
                color: {TEXT};
                padding: 6px 10px;
                background: {BG};
            }}
            QTableView::item:alternate {{
                background: {BG_ALT};
            }}
            QTableView::item:selected {{
                background: {PRIMARY};
                color: #111827;
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
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.Interactive)
        header.resizeSection(8, 220)

    def load_data(self):
        search = self.search_input.text().strip() if self.search_input else ""
        if self._bulk_select_mode:
            self._disable_bulk_mode()
        self._load_counter += 1
        self._current_load_id = self._load_counter
        if self._load_thread and self._load_thread.isRunning():
            try:
                self._load_thread.requestInterruption()
            except Exception:
                pass
        self._load_thread = EnviosLoadWorker(search, self._current_load_id, self)
        self._load_thread.data_loaded.connect(self._on_envios_loaded)
        self._load_thread.start()

    def _on_envios_loaded(self, load_id, pendientes, programados, entregados):
        if load_id != self._current_load_id:
            return
        self._fill_table(self.table_pendientes, pendientes, entregado=False)
        self._fill_table(self.table_programados, programados, entregado=False)
        self._fill_table(self.table_entregados, entregados, entregado=True)
        if self._cobros_unlocked:
            self._refresh_cobros_domicilio()

    def _toggle_programados(self):
        visible = not self.table_programados.isVisible()
        self.table_programados.setVisible(visible)
        self.programados_label.setVisible(visible)
        self.toggle_programados_btn.setText(
            "Ocultar programados" if visible else "Ver programados"
        )

    def closeEvent(self, event):
        try:
            if self._load_thread and self._load_thread.isRunning():
                self._load_thread.requestInterruption()
                self._load_thread.wait(200)
        except Exception:
            pass
        super().closeEvent(event)

    def _fill_table(self, table: QTableView, ventas: list, entregado: bool):
        # Preparar filas con campos calculados
        rows = []
        for v in ventas or []:
            v = dict(v)
            v["_direccion_fmt"] = self._format_direccion(v)
            fp_envio = (v.get("forma_pago_envio") or "").strip()
            v["_pago_envio_fmt"] = f"Si ({fp_envio})" if fp_envio else "No"
            v["_programada_fmt"] = _format_programada_label(v.get("entrega_programada"))
            rows.append(v)

        if table is self.table_pendientes:
            model = self.pendientes_model
        elif table is self.table_programados:
            model = self.programados_model
        else:
            model = self.entregados_model
        model.set_rows(rows)

        # Botones de accion en columna 8
        for row_idx, venta in enumerate(rows):
            actions = QWidget()
            actions.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(actions)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            btn_print = QPushButton("Imprimir")
            btn_print.setStyleSheet(
                "background:#1f2937;color:#e5e7eb;font-weight:800;"
                "border-radius:8px;padding:6px 12px;"
            )
            btn_print.setMinimumWidth(90)
            btn_print.clicked.connect(
                lambda _=None, vid=venta.get("id"): self.imprimir_remito(vid)
            )
            row_layout.addWidget(btn_print)

            if entregado:
                btn_cancel = QPushButton("Anular entrega")
                btn_cancel.setStyleSheet(
                    "background:#dc2626;color:white;font-weight:700;"
                )
                btn_cancel.clicked.connect(
                    lambda _=None, vid=venta.get("id"): self.anular_entrega(vid)
                )
                row_layout.addWidget(btn_cancel)
            else:
                btn_deliver = QPushButton("Entregar")
                btn_deliver.setStyleSheet(
                    "background:#16a34a;color:white;font-weight:800;"
                    "border-radius:8px;padding:6px 12px;"
                )
                btn_deliver.setMinimumWidth(90)
                btn_deliver.clicked.connect(
                    lambda _=None, vid=venta.get("id"): self.marcar_entregado(vid)
                )
                row_layout.addWidget(btn_deliver)

            row_layout.addStretch()
            table.setIndexWidget(model.index(row_idx, 8), actions)

        if table is self.table_pendientes and self._bulk_select_mode:
            self._refresh_remito_selection_markers()

    def _on_bulk_print_clicked(self):
        if not self._bulk_select_mode:
            if self.pendientes_model.rowCount() == 0:
                QMessageBox.information(
                    self, "Envios", "No hay envios pendientes para imprimir."
                )
                return
            self._enable_bulk_mode()
            return
        self._print_bulk_selected()

    def _enable_bulk_mode(self):
        self._bulk_select_mode = True
        self._bulk_selected_ids = set()
        for row in range(self.pendientes_model.rowCount()):
            venta_id = self.pendientes_model.venta_id_at(row)
            if venta_id:
                self._bulk_selected_ids.add(int(venta_id))
        self._update_bulk_button()
        self._refresh_remito_selection_markers()

    def _disable_bulk_mode(self):
        self._bulk_select_mode = False
        self._bulk_selected_ids = set()
        self._update_bulk_button()
        self._refresh_remito_selection_markers()

    def _update_bulk_button(self):
        if hasattr(self, "print_all_btn"):
            self.print_all_btn.setText(
                "Imprimir" if self._bulk_select_mode else "Imprimir todo"
            )

    def _on_pendientes_cell_clicked(self, index):
        if not self._bulk_select_mode:
            return
        row = index.row()
        col = index.column()
        if col != 0:
            return
        venta_id = self.pendientes_model.venta_id_at(row)
        if not venta_id:
            return
        if int(venta_id) in self._bulk_selected_ids:
            self._bulk_selected_ids.discard(int(venta_id))
        else:
            self._bulk_selected_ids.add(int(venta_id))
        self._refresh_remito_selection_markers()

    def _refresh_remito_selection_markers(self):
        self.pendientes_model.set_bulk_state(
            self._bulk_select_mode, self._bulk_selected_ids
        )

    def _print_bulk_selected(self):
        selected_ids = list(self._bulk_selected_ids)
        if not selected_ids:
            QMessageBox.warning(
                self, "Envios", "No hay remitos seleccionados para imprimir."
            )
            return
        ok, path_or_msg, errors, included_ids = vm.generar_pdf_remitos(selected_ids)
        if not ok:
            msg = path_or_msg
            if errors:
                msg = f"{msg}\n\n" + "\n".join(errors[:5])
            QMessageBox.warning(self, "Envios", msg)
            return

        self._open_pdf(path_or_msg)
        for venta_id in included_ids:
            vm.marcar_remito_impreso(int(venta_id), True)

        self._disable_bulk_mode()
        self.load_data()

    def _format_direccion(self, venta: dict) -> str:
        calle = str(venta.get("cliente_calle") or "").strip()
        numero = str(venta.get("cliente_numero") or "").strip()
        localidad = str(venta.get("cliente_localidad") or "").strip()
        entre = str(venta.get("entre_calles") or "").strip()
        base = " ".join(p for p in [calle, numero] if p)
        if localidad:
            base = f"{base} - {localidad}" if base else localidad
        if entre:
            base = f"{base} (entre {entre})"
        return base

    def imprimir_remito(self, venta_id):
        if not venta_id:
            return
        ok, path_or_msg = vm.generar_pdf_remito(int(venta_id))
        if not ok:
            QMessageBox.warning(
                self, "Remito", f"No se pudo generar el remito: {path_or_msg}"
            )
            return
        self._open_pdf(path_or_msg)
        vm.marcar_remito_impreso(int(venta_id), True)
        self.load_data()

    def marcar_entregado(self, venta_id):
        if not venta_id:
            return
        venta = vm.get_venta_detalle(int(venta_id))
        if not venta:
            QMessageBox.warning(self, "Entregas", "Venta no encontrada.")
            return
        items = venta.get("items") or []
        if not items:
            QMessageBox.warning(self, "Entregas", "La venta no tiene productos.")
            return

        locals_list = []
        try:
            locals_list = [l for l in (get_all_locals() or []) if l]
        except Exception:
            locals_list = []
        if self.local and self.local not in locals_list:
            locals_list.insert(0, self.local)

        selections = self._seleccionar_locales_entrega(items, locals_list, venta)
        if selections is None:
            return

        ok, msg = vm.marcar_entrega(
            int(venta_id),
            True,
            usuario=self.username,
            local_entrega=self.local,
            local_por_item=selections,
        )
        if not ok:
            QMessageBox.warning(self, "Entregas", msg)
            return
        self.load_data()

    def _seleccionar_locales_entrega(self, items: list, locals_list: list, venta: dict):
        dlg = QDialog(self)
        dlg.setWindowTitle("Entregar - Seleccionar local")
        dlg.resize(1200, 680)
        dlg.setMinimumSize(1100, 620)
        lay = QVBoxLayout(dlg)

        info = QLabel("Selecciona el local desde donde sale cada producto:")
        info.setStyleSheet(f"color:{TEXT}; font-weight:700;")
        lay.addWidget(info)

        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels(
            [
                "Producto",
                "Categoria",
                "Color",
                "Fabricante",
                "Medida",
                "Estado",
                "Cantidad",
                "Local de entrega",
            ]
        )
        table.setRowCount(0)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.setStyleSheet(
            "QTableWidget{background:#111827;color:#e5e7eb;border:1px solid #1f2937;border-radius:8px;}"
            "QHeaderView::section{background:#0b1220;color:#e5e7eb;font-weight:700;padding:6px;border:none;}"
        )

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.resizeSection(0, 260)
        header.resizeSection(1, 150)
        header.resizeSection(2, 110)
        header.resizeSection(3, 160)
        header.resizeSection(4, 120)
        header.resizeSection(5, 100)
        header.resizeSection(6, 90)
        header.resizeSection(7, 140)

        row_meta = []
        blocked_rows = 0

        def _item_key(it):
            try:
                did = int(it.get("id") or 0)
            except Exception:
                did = 0
            if did > 0:
                return ("detail", did)
            try:
                pid = int(it.get("producto_id") or 0)
            except Exception:
                pid = 0
            return ("pid", pid)

        def _get_allowed_locals(it):
            allowed = []
            for l in locals_list or []:
                if vm.get_available_qty_for_item_local(it, l) > 0:
                    allowed.append(l)
            return allowed

        def _get_allocated_qty(key, local, skip_row=None):
            total = 0
            for idx, meta in enumerate(row_meta):
                if skip_row is not None and idx == skip_row:
                    continue
                if meta.get("key") == key and (meta.get("local") or "") == (
                    local or ""
                ):
                    total += int(meta.get("qty") or 0)
            return total

        def _add_row(it, qty, default_local):
            nonlocal blocked_rows
            row = table.rowCount()
            table.insertRow(row)

            nombre = it.get("producto_nombre") or it.get("nombre") or "Producto"
            categoria = it.get("producto_categoria") or it.get("categoria") or ""
            color = it.get("producto_color") or it.get("color") or ""
            fabricante = it.get("producto_fabricante") or it.get("fabricante") or ""
            medida = it.get("producto_medida") or it.get("medida") or ""
            estado = it.get("producto_estado") or it.get("estado") or ""

            table.setItem(row, 0, QTableWidgetItem(str(nombre)))
            table.setItem(row, 1, QTableWidgetItem(str(categoria)))
            table.setItem(row, 2, QTableWidgetItem(str(color)))
            table.setItem(row, 3, QTableWidgetItem(str(fabricante)))
            table.setItem(row, 4, QTableWidgetItem(str(medida)))
            table.setItem(row, 5, QTableWidgetItem(str(estado)))
            qty_item = QTableWidgetItem(str(qty))
            qty_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, 6, qty_item)

            allowed = _get_allowed_locals(it)
            if not allowed:
                empty_lbl = QLabel("SIN STOCK")
                empty_lbl.setStyleSheet("color:#ef4444;font-weight:700;")
                empty_lbl.setAlignment(Qt.AlignCenter)
                table.setCellWidget(row, 7, empty_lbl)
                blocked_rows += 1
                meta = {"item": it, "qty": int(qty), "local": "", "key": _item_key(it)}
                row_meta.append(meta)
                return

            combo = QComboBox()
            combo.addItems(allowed)
            if default_local in allowed:
                combo.setCurrentText(default_local)
            combo.setFixedWidth(120)
            table.setCellWidget(row, 7, combo)
            meta = {
                "item": it,
                "qty": int(qty),
                "local": combo.currentText(),
                "key": _item_key(it),
            }
            row_meta.append(meta)

            def _handle_change():
                nonlocal blocked_rows
                current_row = row
                if current_row >= len(row_meta):
                    return
                meta = row_meta[current_row]
                it_local = meta.get("item")
                local_sel = combo.currentText()
                meta["local"] = local_sel

                stock = vm.get_available_qty_for_item_local(it_local, local_sel)
                allocated_other = _get_allocated_qty(
                    meta["key"], local_sel, skip_row=current_row
                )
                available = max(0, int(stock) - int(allocated_other))

                if available <= 0:
                    QMessageBox.warning(
                        self, "Entregas", "Ese local no tiene stock disponible."
                    )
                    # buscar otro local con disponible
                    found = None
                    for l in _get_allowed_locals(it_local):
                        st = vm.get_available_qty_for_item_local(it_local, l)
                        if (
                            st
                            - _get_allocated_qty(meta["key"], l, skip_row=current_row)
                            > 0
                        ):
                            found = l
                            break
                    if found:
                        combo.setCurrentText(found)
                    return

                qty_now = int(meta.get("qty") or 0)
                if qty_now > available:
                    meta["qty"] = available
                    table.item(current_row, 6).setText(str(available))
                    remaining = qty_now - available
                    QMessageBox.information(
                        self,
                        "Entregas",
                        f"En {local_sel} hay {available}. Se duplico la fila para completar {remaining}.",
                    )
                    _add_row(it_local, remaining, default_local="")

            combo.currentTextChanged.connect(_handle_change)

        # Crear filas iniciales
        for it in items:
            try:
                qty = int(it.get("cantidad") or 0)
            except Exception:
                qty = 0
            stock_local = it.get("stock_local") or venta.get("local") or ""
            default_local = (
                self.local or stock_local or (locals_list[0] if locals_list else "")
            )
            if default_local not in locals_list and default_local:
                locals_list.insert(0, default_local)
            _add_row(it, qty, default_local)

        lay.addWidget(table)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Confirmar")
        btns.button(QDialogButtonBox.Cancel).setText("Cancelar")
        lay.addWidget(btns)

        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        if dlg.exec_() != QDialog.Accepted:
            return None
        if blocked_rows > 0:
            QMessageBox.warning(
                self,
                "Entregas",
                "Hay productos sin stock en ningun local. No se puede confirmar la entrega.",
            )
            return None

        selections = []
        for meta in row_meta:
            it = meta.get("item") or {}
            qty = int(meta.get("qty") or 0)
            local_sel = (meta.get("local") or "").strip()
            if qty <= 0 or not local_sel:
                QMessageBox.warning(
                    self, "Entregas", "Faltan locales para completar la entrega."
                )
                return None
            entrega_pid = vm.get_delivery_pid_for_local(it, local_sel)
            selections.append(
                {
                    "detalle_id": it.get("id"),
                    "producto_id": it.get("producto_id"),
                    "cantidad": qty,
                    "local": local_sel,
                    "entrega_producto_id": entrega_pid,
                }
            )
        return selections

    def anular_entrega(self, venta_id):
        if not venta_id:
            return
        motivo, ok = QInputDialog.getText(self, "Anular entrega", "Motivo:")
        if not ok:
            return
        motivo = (motivo or "").strip()
        if not motivo:
            QMessageBox.warning(self, "Anular entrega", "Debe ingresar un motivo")
            return
        ok, msg = vm.anular_entrega_con_devolucion(int(venta_id), motivo, self.username)
        if not ok:
            QMessageBox.warning(self, "Anular entrega", msg)
            return
        self.load_data()

    def _open_pdf(self, filepath):
        try:
            if platform.system() == "Windows":
                os.startfile(filepath)
            elif platform.system() == "Darwin":
                subprocess.call(["open", filepath])
            else:
                subprocess.call(["xdg-open", filepath])
        except Exception as e:
            QMessageBox.warning(self, "Remito", f"No se pudo abrir el PDF: {e}")

    def _toggle_cobros_panel(self):
        if self._cobros_unlocked:
            self._cobros_unlocked = False
            self.cobros_panel.setVisible(False)
            self.cobros_btn.setText("Cobros domicilio")
            return
        pwd, ok = QInputDialog.getText(
            self, "Cobros domicilio", "Contraseña:", QLineEdit.Password
        )
        if not ok:
            return
        if (pwd or "").strip() != self._get_cash_password():
            QMessageBox.warning(self, "Cobros domicilio", "Contraseña incorrecta.")
            return
        self._cobros_unlocked = True
        self.cobros_panel.setVisible(True)
        self.cobros_btn.setText("Ocultar cobros")
        self._refresh_cobros_domicilio()

    def _get_cash_password(self) -> str:
        return vm.get_cash_withdraw_password(default="Manavella10")

    def _refresh_cobros_domicilio(self):
        rows = vm.get_domicilio_pagos_pending(
            self.search_input.text().strip() if self.search_input else ""
        )
        self._domicilio_rows = list(rows or [])
        total = 0.0
        for row in self._domicilio_rows:
            try:
                total += float(row.get("monto_productos") or row.get("monto") or 0)
            except Exception:
                pass
        self.cobros_total_label.setText(
            f"Cobrado en casa pendiente (sin envio): ${self._fmt_money(total)}"
        )
        self._fill_cobros_table(self._domicilio_rows)

    def _fill_cobros_table(self, rows: list):
        self.cobros_table.setRowCount(0)
        for row_idx, row in enumerate(rows or []):
            self.cobros_table.insertRow(row_idx)
            self.cobros_table.setItem(
                row_idx, 0, QTableWidgetItem(str(row.get("created_at") or ""))
            )
            self.cobros_table.setItem(
                row_idx, 1, QTableWidgetItem(str(row.get("local") or ""))
            )
            self.cobros_table.setItem(
                row_idx,
                2,
                QTableWidgetItem(
                    str(row.get("numero_venta") or row.get("venta_id") or "")
                ),
            )
            self.cobros_table.setItem(
                row_idx, 3, QTableWidgetItem(str(row.get("cliente_nombre") or ""))
            )
            self.cobros_table.setItem(
                row_idx,
                4,
                QTableWidgetItem(
                    f"${self._fmt_money(row.get('monto_productos') or row.get('monto') or 0)}"
                ),
            )
            self.cobros_table.setItem(
                row_idx, 5, QTableWidgetItem(str(row.get("direccion") or ""))
            )

            actions = QWidget()
            actions.setStyleSheet("background: transparent;")
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setSpacing(6)

            venta_id = int(row.get("venta_id") or 0)

            boleta_btn = QPushButton("Boleta")
            boleta_btn.setStyleSheet(
                "background:#1f2937;color:#e5e7eb;font-weight:800;border-radius:8px;padding:5px 10px;"
            )
            boleta_btn.clicked.connect(
                lambda _=None, vid=venta_id: self._abrir_boleta(vid)
            )
            actions_layout.addWidget(boleta_btn)

            remito_btn = QPushButton("Remito")
            remito_btn.setStyleSheet(
                "background:#1f2937;color:#e5e7eb;font-weight:800;border-radius:8px;padding:5px 10px;"
            )
            remito_btn.clicked.connect(
                lambda _=None, vid=venta_id: self.imprimir_remito(vid)
            )
            actions_layout.addWidget(remito_btn)

            actions_layout.addStretch()
            self.cobros_table.setCellWidget(row_idx, 6, actions)

        self.cobros_table.resizeRowsToContents()

    def _selected_domicilio_pago_ids(self) -> list:
        ids = []
        for item in self.cobros_table.selectionModel().selectedRows():
            row_idx = item.row()
            if 0 <= row_idx < len(self._domicilio_rows):
                try:
                    ids.append(int(self._domicilio_rows[row_idx].get("id") or 0))
                except Exception:
                    pass
        return [v for v in ids if v > 0]

    def _retirar_cobros_seleccionados(self):
        ids = self._selected_domicilio_pago_ids()
        if not ids:
            QMessageBox.warning(
                self, "Cobros domicilio", "Selecciona uno o mas cobros."
            )
            return
        ok = QMessageBox.question(
            self,
            "Cobros domicilio",
            f"Retirar {len(ids)} cobro(s) seleccionados?",
        )
        if ok != QMessageBox.Yes:
            return
        status, msg, total, count = vm.retirar_domicilio_pagos(ids, self.username)
        if not status:
            QMessageBox.warning(self, "Cobros domicilio", msg)
            return
        QMessageBox.information(
            self,
            "Cobros domicilio",
            f"Retiro registrado.\nCobros: {count}\nTotal: ${self._fmt_money(total)}",
        )
        self._refresh_cobros_domicilio()

    def _retirar_todos_cobros(self):
        ids = [
            int(r.get("id") or 0)
            for r in self._domicilio_rows
            if int(r.get("id") or 0) > 0
        ]
        if not ids:
            QMessageBox.information(
                self, "Cobros domicilio", "No hay cobros pendientes para retirar."
            )
            return
        ok = QMessageBox.question(
            self,
            "Cobros domicilio",
            f"Retirar todos los cobros pendientes ({len(ids)})?",
        )
        if ok != QMessageBox.Yes:
            return
        status, msg, total, count = vm.retirar_domicilio_pagos(ids, self.username)
        if not status:
            QMessageBox.warning(self, "Cobros domicilio", msg)
            return
        QMessageBox.information(
            self,
            "Cobros domicilio",
            f"Retiro registrado.\nCobros: {count}\nTotal: ${self._fmt_money(total)}",
        )
        self._refresh_cobros_domicilio()

    def _abrir_boleta(self, venta_id: int):
        if not venta_id:
            return
        ok, path_or_msg = vm.generar_pdf_boleta(int(venta_id))
        if not ok:
            QMessageBox.warning(
                self, "Boleta", f"No se pudo generar la boleta: {path_or_msg}"
            )
            return
        self._open_pdf(path_or_msg)

    def _fmt_money(self, value) -> str:
        try:
            return f"{int(float(value or 0)):,}".replace(",", ".")
        except Exception:
            return str(value or 0)


class EnviosLoadWorker(QThread):
    data_loaded = pyqtSignal(int, list, list, list)

    def __init__(self, search: str, load_id: int, parent=None):
        super().__init__(parent)
        self.search = search
        self.load_id = load_id

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            ventas = vhm.get_ventas_por_local_fast(
                "Todos", filtro_fecha="todo", search=self.search
            )
        except Exception:
            ventas = []
        ventas = [v for v in (ventas or []) if int(v.get("incluye_envio") or 0) == 1]
        pendientes = []
        programados = []
        entregados = []
        cutoff_dt = datetime.now() - timedelta(days=4)
        today = datetime.now().date()
        for v in ventas:
            if int(v.get("entrega_entregado") or 0) == 1:
                entrega_dt = _parse_envio_datetime(
                    v.get("entrega_fecha") or v.get("fecha")
                )
                if entrega_dt and entrega_dt < cutoff_dt:
                    continue
                entregados.append(v)
            else:
                programada_dt = _parse_envio_datetime(v.get("entrega_programada"))
                if programada_dt and programada_dt.date() > today:
                    programados.append(v)
                else:
                    pendientes.append(v)

        pendientes.sort(key=lambda v: str(v.get("fecha") or ""), reverse=True)
        entregados.sort(
            key=lambda v: str(v.get("entrega_fecha") or v.get("fecha") or ""),
            reverse=True,
        )
        programados.sort(
            key=lambda v: str(v.get("entrega_programada") or v.get("fecha") or ""),
            reverse=False,
        )
        try:
            if self.isInterruptionRequested():
                return
            self.data_loaded.emit(self.load_id, pendientes, programados, entregados)
        except Exception:
            pass

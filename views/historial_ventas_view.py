# views/historial_ventas_view.py
import os
import platform
import subprocess

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models import ventas_model as vm

# Colores
DORADO = "#C9A040"
DARK = "#1f1f22"
CARD = "#232327"
BORDER = "#34343a"
TEXT = "#ECECF1"
MUTED = "#c9c9cf"


def _fmt_money(v):
    try:
        return "{:,}".format(int(v)).replace(",", ".")
    except:
        return str(v)


# ==================== DIÁLOGO DE DETALLE DE VENTA ====================
class VentaDetalleDialog(QDialog):
    """Muestra el detalle completo de una venta"""

    def __init__(self, venta_id: int, parent=None):
        super().__init__(parent)
        self.venta_id = venta_id

        self.setWindowTitle(f"Detalle de Venta #{venta_id}")
        self.setModal(True)
        self.resize(700, 600)
        self.setStyleSheet(f"QDialog{{background:{CARD};}} QLabel{{color:{TEXT};}}")

        self.load_and_display()

    def load_and_display(self):
        """Carga y muestra el detalle de la venta"""
        venta = vm.get_venta_detalle(self.venta_id)

        if not venta:
            QMessageBox.critical(
                self, "Error", "No se pudo cargar el detalle de la venta"
            )
            self.reject()
            return

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        # Título
        title = QLabel(f"Venta #{venta['id']:06d}")
        title.setFont(QFont("Segoe UI", 20, QFont.Black))
        title.setStyleSheet(f"color:{DORADO};")
        layout.addWidget(title)

        # Información general
        info_frame = QFrame()
        info_frame.setStyleSheet(
            f"background:{DARK};border:1px solid {BORDER};border-radius:10px;padding:12px;"
        )
        info_layout = QVBoxLayout(info_frame)

        info_layout.addWidget(self.create_info_label("Fecha", venta["fecha"]))
        info_layout.addWidget(self.create_info_label("Local", venta["local"]))
        info_layout.addWidget(self.create_info_label("Vendedor", venta["usuario"]))

        layout.addWidget(info_frame)

        # Datos del cliente
        cliente_title = QLabel("Cliente")
        cliente_title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        cliente_title.setStyleSheet(f"color:{DORADO};")
        layout.addWidget(cliente_title)

        cliente_frame = QFrame()
        cliente_frame.setStyleSheet(
            f"background:{DARK};border:1px solid {BORDER};border-radius:10px;padding:12px;"
        )
        cliente_layout = QVBoxLayout(cliente_frame)

        cliente_layout.addWidget(
            self.create_info_label("Nombre", venta["cliente_nombre"])
        )
        if venta["cliente_telefono"]:
            cliente_layout.addWidget(
                self.create_info_label("Teléfono", venta["cliente_telefono"])
            )

        if venta["cliente_calle"]:
            direccion = venta["cliente_calle"]
            if venta["cliente_numero"]:
                direccion += f" {venta['cliente_numero']}"
            if venta["cliente_localidad"]:
                direccion += f", {venta['cliente_localidad']}"
            cliente_layout.addWidget(self.create_info_label("Dirección", direccion))

        layout.addWidget(cliente_frame)

        # Productos
        productos_title = QLabel("Productos")
        productos_title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        productos_title.setStyleSheet(f"color:{DORADO};")
        layout.addWidget(productos_title)

        # Tabla de productos
        productos_table = QTableWidget()
        productos_table.setColumnCount(4)
        productos_table.setHorizontalHeaderLabels(
            ["Producto", "Cantidad", "P. Unit.", "Subtotal"]
        )
        productos_table.setRowCount(len(venta["items"]))
        productos_table.setMaximumHeight(200)

        productos_table.setStyleSheet(
            f"""
            QTableWidget {{
                background:{DARK};
                color:{TEXT};
                gridline-color:{BORDER};
                border:1px solid {BORDER};
                border-radius:8px;
            }}
            QHeaderView::section {{
                background:{DARK};
                color:{DORADO};
                font-weight:700;
                padding:8px;
                border:none;
            }}
        """
        )
        productos_table.setEditTriggers(QTableWidget.NoEditTriggers)
        productos_table.verticalHeader().setVisible(False)

        header = productos_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.resizeSection(1, 80)
        header.resizeSection(2, 100)
        header.resizeSection(3, 100)

        for i, item in enumerate(venta["items"]):
            # Producto
            nombre = item["producto_nombre"]
            if item["producto_categoria"]:
                nombre += f" ({item['producto_categoria']})"
            productos_table.setItem(i, 0, QTableWidgetItem(nombre))

            # Cantidad
            qty_item = QTableWidgetItem(str(item["cantidad"]))
            qty_item.setTextAlignment(Qt.AlignCenter)
            productos_table.setItem(i, 1, qty_item)

            # Precio unitario
            price_item = QTableWidgetItem(f"${_fmt_money(item['precio_unitario'])}")
            price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            productos_table.setItem(i, 2, price_item)

            # Subtotal
            subtotal_item = QTableWidgetItem(f"${_fmt_money(item['subtotal'])}")
            subtotal_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            productos_table.setItem(i, 3, subtotal_item)

        layout.addWidget(productos_table)

        # Totales
        totales_frame = QFrame()
        totales_frame.setStyleSheet(
            f"background:{DARK};border:1px solid {BORDER};border-radius:10px;padding:12px;"
        )
        totales_layout = QVBoxLayout(totales_frame)

        subtotal_label = QLabel(f"Subtotal: ${_fmt_money(venta['subtotal'])}")
        subtotal_label.setFont(QFont("Segoe UI", 12))
        totales_layout.addWidget(subtotal_label)

        if venta["descuento_aplicado"] and venta["descuento_aplicado"] > 0:
            desc_text = f"Descuento ({venta['descuento_tipo']}: {venta['descuento_valor']}): -${_fmt_money(venta['descuento_aplicado'])}"
            desc_label = QLabel(desc_text)
            desc_label.setFont(QFont("Segoe UI", 12))
            desc_label.setStyleSheet("color:#ff6b6b;")
            totales_layout.addWidget(desc_label)

        total_label = QLabel(f"TOTAL: ${_fmt_money(venta['total'])}")
        total_label.setFont(QFont("Segoe UI", 16, QFont.Black))
        total_label.setStyleSheet(f"color:{DORADO};")
        totales_layout.addWidget(total_label)

        layout.addWidget(totales_frame)

        # Botones
        buttons = QHBoxLayout()
        buttons.addStretch()
        generate_btn = QPushButton("Ver boleta (PDF temporal)")
        generate_btn.setStyleSheet(
            f"QPushButton{{background:{DORADO};color:#171717;border:none;"
            "border-radius:10px;padding:10px 20px;font-weight:700;}}"
        )
        generate_btn.clicked.connect(self.generate_pdf)
        buttons.addWidget(generate_btn)

        close_btn = QPushButton("Cerrar")
        close_btn.setStyleSheet(
            f"QPushButton{{background:{BORDER};color:{TEXT};border:none;"
            "border-radius:10px;padding:10px 20px;font-weight:700;}}"
        )
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)

        layout.addLayout(buttons)

    def create_info_label(self, label: str, value: str):
        """Crea un label de información"""
        widget = QLabel(f"<b>{label}:</b> {value}")
        widget.setStyleSheet(f"color:{TEXT};")
        return widget

    def open_pdf(self, filepath):
        """Abre el PDF"""
        try:
            if platform.system() == "Windows":
                os.startfile(filepath)
            elif platform.system() == "Darwin":
                subprocess.call(["open", filepath])
            else:
                subprocess.call(["xdg-open", filepath])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo abrir el PDF: {e}")

    def generate_pdf(self):
        """Genera el PDF de la boleta en una ubicación temporal"""
        success, result = vm.generar_pdf_boleta(self.venta_id)
        if success:
            QMessageBox.information(
                self, "PDF Generado", f"PDF temporal listo:\n{result}"
            )
            self.open_pdf(result)
        else:
            QMessageBox.critical(self, "Error", result)


# ==================== VISTA PRINCIPAL DE HISTORIAL ====================
class HistorialVentasView(QMainWindow):
    """Vista del historial de ventas"""

    def __init__(self, username: str, role: str, local: str, back_command=None):
        super().__init__()
        self.username = username
        self.role = role
        self.local = local
        self.back_command = back_command

        self.setWindowTitle("Historial de Ventas")
        self.resize(1200, 750)
        self.setStyleSheet(f"QMainWindow{{background:{DARK};}} QLabel{{color:{TEXT};}}")

        self.setup_ui()
        self.load_ventas()

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        # Header
        header = QHBoxLayout()

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

        title = QLabel("Historial de Ventas")
        title.setFont(QFont("Segoe UI", 24, QFont.Black))
        title.setStyleSheet(f"color:{DORADO};")
        header.addWidget(title)
        header.addStretch()

        layout.addLayout(header)

        # Estadísticas
        self.create_stats_section(layout)

        # Filtros
        self.create_filters_section(layout)

        # Tabla
        self.create_table_section(layout)

    def create_stats_section(self, layout):
        """Crea sección de estadísticas"""
        stats_frame = QFrame()
        stats_frame.setStyleSheet(
            f"""
            QFrame{{background:{CARD};border:1px solid {BORDER};border-radius:12px;padding:16px;}}
        """
        )

        stats_layout = QHBoxLayout(stats_frame)
        stats_layout.setSpacing(30)

        self.total_ventas_label = self.create_stat_card("Total Ventas", "0")
        stats_layout.addWidget(self.total_ventas_label)

        self.total_ingresos_label = self.create_stat_card("Ingresos", "$0")
        stats_layout.addWidget(self.total_ingresos_label)

        self.total_descuentos_label = self.create_stat_card("Descuentos", "$0")
        stats_layout.addWidget(self.total_descuentos_label)

        self.promedio_label = self.create_stat_card("Promedio", "$0")
        stats_layout.addWidget(self.promedio_label)

        stats_layout.addStretch()

        layout.addWidget(stats_frame)

    def create_stat_card(self, title: str, value: str):
        """Crea una tarjeta de estadística"""
        widget = QWidget()
        card_layout = QVBoxLayout(widget)
        card_layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"color:{MUTED};font-size:11px;")
        card_layout.addWidget(title_label)

        value_label = QLabel(value)
        value_label.setFont(QFont("Segoe UI", 18, QFont.Black))
        value_label.setStyleSheet(f"color:{DORADO};")
        card_layout.addWidget(value_label)

        widget.value_label = value_label  # Para actualizar después
        return widget

    def create_filters_section(self, layout):
        """Crea sección de filtros"""
        filters = QHBoxLayout()
        filters.setSpacing(12)

        # Filtro de período
        self.periodo_combo = QComboBox()
        self.periodo_combo.addItems(["Hoy", "Semana", "Mes", "Todo"])
        self.periodo_combo.setCurrentIndex(2)  # Mes por defecto
        self.periodo_combo.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:1px solid {BORDER};"
            "border-radius:10px;padding:8px 12px;min-width:120px;}}"
        )
        self.periodo_combo.currentIndexChanged.connect(self.load_ventas)
        filters.addWidget(QLabel("Período:"))
        filters.addWidget(self.periodo_combo)

        filters.addStretch()

        # Botón buscar
        search_btn = QPushButton("Refrescar")
        search_btn.setStyleSheet(
            f"QPushButton{{background:{DORADO};color:#171717;border:none;"
            "border-radius:10px;padding:8px 16px;font-weight:700;}}"
        )
        search_btn.clicked.connect(self.load_ventas)
        filters.addWidget(search_btn)

        layout.addLayout(filters)

    def create_table_section(self, layout):
        """Crea tabla de ventas"""
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Fecha", "Cliente", "Productos", "Descuento", "Total", "Acciones"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        self.table.setStyleSheet(
            f"""
            QTableWidget {{
                background:{CARD};
                color:{TEXT};
                gridline-color:{BORDER};
                border:1px solid {BORDER};
                border-radius:10px;
            }}
            QTableWidget::item {{
                padding:10px;
            }}
            QTableWidget::item:selected {{
                background:rgba(201,160,64,0.3);
            }}
            QHeaderView::section {{
                background:{DARK};
                color:{DORADO};
                font-weight:700;
                padding:10px;
                border:none;
                border-bottom:2px solid {DORADO};
            }}
        """
        )

        header = self.table.horizontalHeader()
        header.resizeSection(0, 80)  # ID
        header.resizeSection(1, 150)  # Fecha
        header.setSectionResizeMode(2, QHeaderView.Stretch)  # Cliente
        header.resizeSection(3, 100)  # Productos
        header.resizeSection(4, 120)  # Descuento
        header.resizeSection(5, 120)  # Total
        header.resizeSection(6, 180)  # Acciones

        layout.addWidget(self.table)

    def load_ventas(self):
        """Carga las ventas según los filtros"""
        periodo_map = {0: "dia", 1: "semana", 2: "mes", 3: "todo"}

        periodo = periodo_map.get(self.periodo_combo.currentIndex(), "mes")

        # Cargar ventas
        ventas = vm.get_ventas(
            local=self.local if self.role != "admin" else None, filtro_periodo=periodo
        )

        # Actualizar estadísticas
        stats = vm.get_estadisticas_ventas(
            local=self.local if self.role != "admin" else None, periodo=periodo
        )

        self.update_stats(stats)

        # Poblar tabla
        self.populate_table(ventas)

    def update_stats(self, stats):
        """Actualiza las estadísticas"""
        self.total_ventas_label.value_label.setText(str(stats["total_ventas"]))
        self.total_ingresos_label.value_label.setText(
            f"${_fmt_money(stats['total_ingresos'])}"
        )
        self.total_descuentos_label.value_label.setText(
            f"${_fmt_money(stats['total_descuentos'])}"
        )
        self.promedio_label.value_label.setText(
            f"${_fmt_money(stats['promedio_venta'])}"
        )

    def populate_table(self, ventas):
        """Puebla la tabla con ventas"""
        self.table.setRowCount(len(ventas))

        for i, venta in enumerate(ventas):
            # ID
            id_item = QTableWidgetItem(f"#{venta['id']:06d}")
            id_item.setData(Qt.UserRole, venta["id"])
            id_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 0, id_item)

            # Fecha
            fecha = venta["fecha"][:16]  # Sin segundos
            self.table.setItem(i, 1, QTableWidgetItem(fecha))

            # Cliente
            self.table.setItem(i, 2, QTableWidgetItem(venta["cliente_nombre"]))

            # Cantidad de productos
            detalle = vm.get_venta_detalle(venta["id"])
            num_items = len(detalle["items"]) if detalle else 0
            items_item = QTableWidgetItem(f"{num_items} items")
            items_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 3, items_item)

            # Descuento
            if venta["descuento_aplicado"] and venta["descuento_aplicado"] > 0:
                desc_text = f"-${_fmt_money(venta['descuento_aplicado'])}"
                desc_item = QTableWidgetItem(desc_text)
                desc_item.setForeground(QColor("#ff6b6b"))
            else:
                desc_item = QTableWidgetItem("-")
                desc_item.setForeground(QColor(MUTED))
            desc_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 4, desc_item)

            # Total
            total_item = QTableWidgetItem(f"${_fmt_money(venta['total'])}")
            total_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            total_item.setFont(QFont("Segoe UI", 11, QFont.Bold))
            total_item.setForeground(QColor(DORADO))
            self.table.setItem(i, 5, total_item)

            # Botones de acción
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 4, 4, 4)
            actions_layout.setSpacing(6)

            # Ver detalle
            detail_btn = QPushButton("Ver")
            detail_btn.setStyleSheet(
                f"QPushButton{{background:{DORADO};color:#171717;border:none;"
                "border-radius:8px;padding:6px 12px;font-weight:700;}}"
            )
            detail_btn.clicked.connect(lambda _, vid=venta["id"]: self.show_detail(vid))
            actions_layout.addWidget(detail_btn)

            # PDF
            pdf_btn = QPushButton("PDF")
            pdf_btn.setStyleSheet(
                f"QPushButton{{background:{BORDER};color:{TEXT};border:none;"
                "border-radius:8px;padding:6px 12px;font-weight:700;}}"
            )
            pdf_btn.clicked.connect(lambda _, vid=venta["id"]: self.handle_pdf(vid))
            actions_layout.addWidget(pdf_btn)

            self.table.setCellWidget(i, 6, actions_widget)

    def show_detail(self, venta_id):
        """Muestra el detalle de una venta"""
        dialog = VentaDetalleDialog(venta_id, self)
        dialog.exec_()

    def handle_pdf(self, venta_id):
        """Genera y abre el PDF de la boleta (temporal)"""
        success, result = vm.generar_pdf_boleta(venta_id)
        if success:
            self.open_pdf(result)
        else:
            QMessageBox.critical(self, "Error", result)

    def open_pdf(self, filepath):
        """Abre el PDF"""
        try:
            if platform.system() == "Windows":
                os.startfile(filepath)
            elif platform.system() == "Darwin":
                subprocess.call(["open", filepath])
            else:
                subprocess.call(["xdg-open", filepath])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo abrir el PDF: {e}")

"""
Vista de administración para Manarey.
Permite ver estadísticas globales, gestionar usuarios y monitorear locales.
"""
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime

from PyQt5.QtCore import QDate, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QIcon
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from models.boletas_model import generar_boleta_pdf_a4_duplicada
from models.firestore_db import (
    get_all_locals,
    get_sales_stats,
    get_stock_status,
    list_historial_by_local,
    list_products_by_local,
    list_top_products,
    list_users,
    list_ventas,
)
from styles import ThemeManager

# ... (rest of imports)


logger = logging.getLogger(__name__)


class AdminView(QMainWindow):
    """Vista de administración principal."""

    def __init__(self, username: str, role: str, parent=None, back_command=None):
        super().__init__(parent)
        self.username = username
        self.role = role
        self.back_command = back_command
        self.inventory_data = []
        self.setWindowTitle(f"Panel de Administración - {username} ({role})")
        self.setMinimumSize(1024, 768)

        # Configurar tema
        self.theme = ThemeManager()
        self.setStyleSheet(self.theme.get_stylesheet())

        # Configurar ventana principal
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # Barra de estado
        self.statusBar().showMessage("Conectado al servidor")

        # Inicializar UI
        self.init_ui()

        # Actualizar datos iniciales
        self.refresh_data()

        # Configurar temporizador para actualización automática
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.refresh_data)
        self.update_timer.start(30000)  # Actualizar cada 30 segundos

    def set_back_command(self, back_command):
        """Permite inyectar callback de regreso al menú anterior."""
        self.back_command = back_command

    def _go_back(self):
        """Ejecuta el callback de regreso si está definido, o cierra la vista."""
        if self.back_command:
            try:
                self.back_command()
                return
            except Exception:
                pass
        self.close()

    def init_ui(self):
        """Inicializa la interfaz de usuario."""
        # Header con botón volver + título
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
            back_btn.clicked.connect(self._go_back)
            header.addWidget(back_btn)
        title_lbl = QLabel(f"Panel de Administración — {self.username}")
        title_lbl.setStyleSheet("color:#C9A040;font-size:20px;font-weight:800;")
        header.addWidget(title_lbl)
        header.addStretch()
        self.main_layout.addLayout(header)

        # Barra de herramientas (sin botón volver)
        self.setup_toolbar()

        # Pestañas principales
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        # Pestaña de Resumen
        self.setup_summary_tab()

        # Pestaña de Ventas
        self.setup_sales_tab()

        # Pestaña de Inventario
        self.setup_inventory_tab()

        # Pestaña de Usuarios (solo para administradores)
        if self.role == "admin":
            self.setup_users_tab()

    def setup_toolbar(self):
        """Configura la barra de herramientas."""
        toolbar = self.addToolBar("Herramientas")

        # Botón de actualizar
        refresh_btn = QPushButton("Refrescar")
        refresh_btn.setIcon(QIcon(":/icons/refresh.png"))
        refresh_btn.clicked.connect(self.refresh_data)
        toolbar.addWidget(refresh_btn)

        toolbar.addSeparator()

        # Selector de rango de fechas
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())

        toolbar.addWidget(QLabel("Desde:"))
        toolbar.addWidget(self.date_from)
        toolbar.addWidget(QLabel("Hasta:"))
        toolbar.addWidget(self.date_to)

        # Botón para aplicar filtro de fechas
        apply_btn = QPushButton("Aplicar")
        apply_btn.clicked.connect(self.apply_date_filter)
        toolbar.addWidget(apply_btn)

    def setup_summary_tab(self):
        """Configura la pestaña de resumen."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Estadísticas rápidas
        stats_group = QGroupBox("Resumen General")
        stats_layout = QHBoxLayout()

        # Tarjetas de estadísticas
        self.stats_cards = {
            "total_ventas": self.create_stat_card(
                "Ventas Totales", "$0", "Ventas en el período"
            ),
            "total_productos": self.create_stat_card(
                "Productos Vendidos", "0", "Unidades vendidas"
            ),
            "stock_bajo": self.create_stat_card(
                "Stock Bajo", "0", "Productos con stock mínimo"
            ),
            "stock_agotado": self.create_stat_card(
                "Agotados", "0", "Productos sin stock"
            ),
        }

        for card in self.stats_cards.values():
            stats_layout.addWidget(card)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # Gráficos (simulados con etiquetas por ahora)
        charts_group = QGroupBox("Tendencias")
        charts_layout = QVBoxLayout()

        self.sales_chart = QLabel("Gráfico de ventas (simulado)")
        self.sales_chart.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.sales_chart.setMinimumHeight(200)
        self.sales_chart.setStyleSheet(
            "background-color: #f0f0f0; border: 1px solid #ccc; padding: 12px;"
        )

        charts_layout.addWidget(self.sales_chart)
        charts_group.setLayout(charts_layout)
        layout.addWidget(charts_group)

        self.tabs.addTab(tab, "Resumen")

    def setup_sales_tab(self):
        """Configura la pestaña de ventas."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Tabla de ventas recientes
        self.sales_table = QTableWidget()
        self.sales_table.setColumnCount(7)
        self.sales_table.setHorizontalHeaderLabels(
            ["Fecha", "Nº Boleta", "Cliente", "Total", "Pago", "Vendedor", "Local"]
        )
        self.sales_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sales_table.setSelectionBehavior(QTableWidget.SelectRows)

        layout.addWidget(QLabel("Ventas Recientes (Boletas)"))
        layout.addWidget(self.sales_table)

        # Botones de acción
        btn_layout = QHBoxLayout()
        self.reprint_btn = QPushButton("🖨️ Reimprimir Boleta")
        self.reprint_btn.clicked.connect(self.reprint_selected_sale)
        btn_layout.addWidget(self.reprint_btn)
        btn_layout.addStretch()
        layout.addWidget(QWidget())  # Spacer
        layout.addLayout(btn_layout)

        # Filtros de ventas
        filter_group = QGroupBox("Filtros")
        filter_layout = QHBoxLayout()

        self.sales_search = QLineEdit()
        self.sales_search.setPlaceholderText("Buscar cliente o boleta...")
        self.sales_search.textChanged.connect(self.filter_sales)

        self.sales_status = QComboBox()
        self.sales_status.addItems(["Todas", "Completadas", "Pendientes", "Canceladas"])
        self.sales_status.currentIndexChanged.connect(self.filter_sales)

        filter_layout.addWidget(QLabel("Buscar:"))
        filter_layout.addWidget(self.sales_search)
        filter_layout.addWidget(QLabel("Estado:"))
        filter_layout.addWidget(self.sales_status)

        filter_group.setLayout(filter_layout)
        layout.insertWidget(1, filter_group)

        self.tabs.addTab(tab, "Ventas")

    def setup_inventory_tab(self):
        """Configura la pestaña de inventario."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Filtros
        filter_layout = QHBoxLayout()

        self.inv_search = QLineEdit()
        self.inv_search.setPlaceholderText("Buscar producto...")
        self.inv_search.textChanged.connect(self.filter_inventory)

        self.inv_category = QComboBox()
        self.inv_category.addItem("Todas las categorías", "")
        self.inv_category.currentIndexChanged.connect(self.filter_inventory)

        self.inv_local = QComboBox()
        self.inv_local.addItem("Todos los locales", "")
        self.inv_local.currentIndexChanged.connect(self.filter_inventory)

        self.inv_estado = QComboBox()
        self.inv_estado.addItem("Todos los estados", "")
        self.inv_estado.currentIndexChanged.connect(self.filter_inventory)

        self.inv_low_stock = QCheckBox("Solo stock bajo")
        self.inv_low_stock.stateChanged.connect(self.filter_inventory)

        filter_layout.addWidget(QLabel("Buscar:"))
        filter_layout.addWidget(self.inv_search)
        filter_layout.addWidget(QLabel("Categoría:"))
        filter_layout.addWidget(self.inv_category)
        filter_layout.addWidget(QLabel("Local:"))
        filter_layout.addWidget(self.inv_local)
        filter_layout.addWidget(QLabel("Estado:"))
        filter_layout.addWidget(self.inv_estado)
        filter_layout.addWidget(self.inv_low_stock)
        filter_layout.addStretch()

        layout.addLayout(filter_layout)

        # Tabla de inventario
        self.inventory_table = QTableWidget()
        # Las columnas se ajustarán dinámicamente según los locales
        self.inventory_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )

        layout.addWidget(self.inventory_table)

        # Botones de acción
        btn_layout = QHBoxLayout()

        self.export_btn = QPushButton("Exportar a Excel")
        self.export_btn.clicked.connect(self.export_inventory)

        self.print_btn = QPushButton("Imprimir")
        self.print_btn.clicked.connect(self.print_inventory)

        btn_layout.addWidget(self.export_btn)
        btn_layout.addWidget(self.print_btn)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        self.tabs.addTab(tab, "Inventario")

    @staticmethod
    def _norm_local(loc: str) -> str:
        """Normaliza nombre de local para agrupar/filtrar."""
        loc = (loc or "").strip()
        return loc if loc else "Sin local"

    def setup_users_tab(self):
        """Configura la pestaña de usuarios (solo para administradores)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Botones de acción
        btn_layout = QHBoxLayout()

        self.add_user_btn = QPushButton("Agregar Usuario")
        self.add_user_btn.clicked.connect(self.show_add_user_dialog)

        self.edit_user_btn = QPushButton("Editar Usuario")
        self.edit_user_btn.clicked.connect(self.edit_selected_user)

        self.delete_user_btn = QPushButton("Eliminar Usuario")
        self.delete_user_btn.clicked.connect(self.delete_selected_user)

        btn_layout.addWidget(self.add_user_btn)
        btn_layout.addWidget(self.edit_user_btn)
        btn_layout.addWidget(self.delete_user_btn)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Tabla de usuarios
        self.users_table = QTableWidget()
        self.users_table.setColumnCount(5)
        self.users_table.setHorizontalHeaderLabels(
            ["Usuario", "Nombre", "Rol", "Local", "Último acceso"]
        )
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.users_table.setSelectionBehavior(QTableWidget.SelectRows)

        layout.addWidget(self.users_table)

        self.tabs.addTab(tab, "Usuarios")

    # ===== MÉTODOS AUXILIARES =====

    def create_stat_card(self, title: str, value: str, description: str) -> QWidget:
        """Crea una tarjeta de estadística."""
        card = QGroupBox(title)
        layout = QVBoxLayout(card)

        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        value_label.setAlignment(Qt.AlignCenter)

        desc_label = QLabel(description)
        desc_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(value_label)
        layout.addWidget(desc_label)

        return card

    def refresh_data(self):
        """Actualiza todos los datos de la interfaz."""
        self.statusBar().showMessage("Actualizando datos...")

        try:
            # Obtener estadísticas de ventas
            start_date = self.date_from.date().toPyDate()
            end_date = self.date_to.date().toPyDate()

            stats = get_sales_stats(start_date=start_date, end_date=end_date)

            # Actualizar tarjetas de estadísticas
            self.stats_cards["total_ventas"].findChild(QLabel).setText(
                f"${stats.get('total_ventas', 0):,.2f}"
            )
            self.stats_cards["total_productos"].findChild(QLabel).setText(
                f"{stats.get('total_productos', 0):,}"
            )

            # Obtener estado del inventario
            inventory = get_stock_status()
            self.stats_cards["stock_bajo"].findChild(QLabel).setText(
                str(inventory.get("stock_bajo", 0))
            )
            self.stats_cards["stock_agotado"].findChild(QLabel).setText(
                str(inventory.get("stock_agotado", 0))
            )

            # Actualizar tablas
            self.update_sales_table()
            self.update_inventory_table()

            if self.role == "admin":
                self.update_users_table()

            # Actualizar top vendedores en el gráfico simulado
            self._update_trends()

            self.statusBar().showMessage("Datos actualizados")

        except Exception as e:
            logger.error(f"Error al actualizar datos: {e}")
            self.statusBar().showMessage(f"Error: {str(e)}")

    def apply_date_filter(self):
        """Aplica el filtro de fechas y actualiza los datos."""
        self.refresh_data()

    def filter_sales(self):
        """Filtra la tabla de ventas según los criterios seleccionados."""
        try:
            if not hasattr(self, "sales_data"):
                return
            search = (self.sales_search.text() or "").strip().lower()
            status = (self.sales_status.currentText() or "").strip()

            filtered = []
            for sale in self.sales_data:
                cliente = sale.get("cliente") or {}
                cliente_nombre = (
                    cliente.get("nombre") if isinstance(cliente, dict) else str(cliente)
                ) or ""
                cliente_tel = (
                    cliente.get("telefono") if isinstance(cliente, dict) else ""
                ) or ""
                numero = str(sale.get("numero_venta") or "")
                vendedor = str(sale.get("vendedor") or "")
                local = str(sale.get("local") or "")
                pago = sale.get("pago") or {}
                try:
                    monto_pend = float(pago.get("monto_pendiente") or 0)
                except Exception:
                    monto_pend = 0
                estado = str(sale.get("estado") or "").strip().lower()

                if search:
                    haystack = " ".join(
                        [numero, cliente_nombre, cliente_tel, vendedor, local]
                    ).lower()
                    if search not in haystack:
                        continue

                if status and status != "Todas":
                    if status == "Completadas":
                        if estado and "cancel" in estado:
                            continue
                        if monto_pend > 0:
                            continue
                    elif status == "Pendientes":
                        if estado and "cancel" in estado:
                            continue
                        if monto_pend <= 0:
                            continue
                    elif status == "Canceladas":
                        if not estado or "cancel" not in estado:
                            continue

                filtered.append(sale)

            self._populate_sales_table(filtered)
        except Exception as e:
            logger.error(f"Error filtrando ventas: {e}")

    def filter_inventory(self):
        """Filtra la tabla de inventario según los criterios seleccionados."""
        try:
            if not hasattr(self, "inventory_data"):
                return
            search = (self.inv_search.text() or "").strip().lower()
            cat = self.inv_category.currentData()
            loc = self.inv_local.currentData()
            estado = self.inv_estado.currentData()
            low_only = self.inv_low_stock.isChecked()

            cat_norm = (cat or "").strip().lower()
            loc_norm = (loc or "").strip().lower()
            estado_norm = (estado or "").strip().lower()

            filtered = []
            for p in self.inventory_data:
                if cat_norm and (p.get("categoria") or "").strip().lower() != cat_norm:
                    continue
                if (
                    loc_norm
                    and self._norm_local(p.get("local")).strip().lower() != loc_norm
                ):
                    continue
                if (
                    estado_norm
                    and (p.get("estado") or "").strip().lower() != estado_norm
                ):
                    continue
                if low_only and int(p.get("cantidad") or 0) > 5:
                    continue
                if search:
                    haystack = " ".join(
                        [
                            str(p.get("nombre") or ""),
                            str(p.get("categoria") or ""),
                            str(p.get("fabricante") or ""),
                            str(p.get("material") or ""),
                            str(p.get("medida") or ""),
                            str(p.get("color") or ""),
                            str(p.get("codigo") or ""),
                            str(p.get("descripcion") or ""),
                        ]
                    ).lower()
                    if search not in haystack:
                        continue
                filtered.append(p)

            self._populate_inventory_table(filtered)
        except Exception as e:
            logger.error(f"Error filtrando inventario: {e}")

    def update_sales_table(self):
        """Actualiza la tabla de ventas con datos del servidor (Boletas)."""
        try:
            # Obtener ventas recientes (Boletas)
            start = self.date_from.date().toPyDate()
            end = self.date_to.date().toPyDate()
            sales = list_ventas(limit=200, start_date=start, end_date=end)

            self.sales_data = sales
            self._populate_sales_table(sales)
            # Reaplicar filtros activos (si hay)
            if (self.sales_search.text() or "").strip() or (
                self.sales_status.currentText() or ""
            ) != "Todas":
                self.filter_sales()

        except Exception as e:
            logger.error(f"Error al actualizar tabla de ventas: {e}")

    def _populate_sales_table(self, sales):
        self.sales_table.setRowCount(len(sales))
        # Guardar referencia a los datos para reimpresión
        self.current_sales_data = sales
        for i, sale in enumerate(sales):
            fecha = sale.get("fecha", "")
            if isinstance(fecha, datetime):
                fecha_str = fecha.strftime("%d/%m/%Y %H:%M")
            else:
                fecha_str = str(fecha)
            cliente = sale.get("cliente", {})
            cliente_nombre = (
                cliente.get("nombre", "N/A")
                if isinstance(cliente, dict)
                else str(cliente)
            )
            pago = sale.get("pago", {})
            tipo_pago = (
                pago.get("tipo_abono", "N/A") if isinstance(pago, dict) else "N/A"
            )
            self.sales_table.setItem(i, 0, QTableWidgetItem(fecha_str))
            self.sales_table.setItem(
                i, 1, QTableWidgetItem(str(sale.get("numero_venta", "N/A")))
            )
            self.sales_table.setItem(i, 2, QTableWidgetItem(cliente_nombre))
            self.sales_table.setItem(
                i, 3, QTableWidgetItem(f"${sale.get('total', 0):,.2f}")
            )
            self.sales_table.setItem(i, 4, QTableWidgetItem(tipo_pago))
            self.sales_table.setItem(i, 5, QTableWidgetItem(sale.get("vendedor", "")))
            self.sales_table.setItem(i, 6, QTableWidgetItem(sale.get("local", "")))

    def reprint_selected_sale(self):
        """Reimprime la boleta de la venta seleccionada."""
        selected = self.sales_table.selectedItems()
        if not selected:
            QMessageBox.warning(
                self, "Advertencia", "Seleccione una venta para reimprimir"
            )
            return

        row = selected[0].row()
        if not hasattr(self, "current_sales_data") or row >= len(
            self.current_sales_data
        ):
            return

        sale_data = self.current_sales_data[row]

        try:
            # Preparar datos para el generador de PDF
            # El generador espera ciertas claves, aseguramos compatibilidad
            boleta_dict = sale_data.copy()

            # Mapear campos si es necesario
            if "numero_venta" in boleta_dict and "numero_boleta" not in boleta_dict:
                boleta_dict["numero_boleta"] = boleta_dict["numero_venta"]
            if "fecha" in boleta_dict and "fecha_emision" not in boleta_dict:
                boleta_dict["fecha_emision"] = boleta_dict["fecha"]

            # Generar nombre de archivo
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = (
                f"boleta_{boleta_dict.get('numero_boleta', 'reprint')}_{timestamp}.pdf"
            )

            # Guardar en carpeta temporal (no se persiste en el proyecto)
            temp_dir = tempfile.mkdtemp(prefix="manarey_boleta_")
            out_path = os.path.join(temp_dir, filename)

            success, msg = generar_boleta_pdf_a4_duplicada(boleta_dict, out_path)

            if success:
                QMessageBox.information(
                    self, "Éxito", f"Boleta reimpresa (temporal):\n{out_path}"
                )
                # Abrir el PDF automáticamente
                if sys.platform == "win32":
                    os.startfile(out_path)
                elif sys.platform == "darwin":
                    subprocess.call(["open", out_path])
                else:
                    subprocess.call(["xdg-open", out_path])
            else:
                QMessageBox.critical(self, "Error", f"Error al generar PDF: {msg}")

        except Exception as e:
            logger.error(f"Error al reimprimir boleta: {e}")
            QMessageBox.critical(self, "Error", f"Error inesperado al reimprimir: {e}")

    def update_inventory_table(self):
        """Actualiza la tabla de inventario con datos del servidor."""
        try:
            # Obtener todos los productos de todos los locales (dinámico)
            products = []
            # Traer todos los locales conocidos
            locales_raw = get_all_locals() or []
            # Si no hay locales detectados, intentar sin filtro
            if not locales_raw:
                products = list_products_by_local(None)
            else:
                for local in locales_raw:
                    products.extend(list_products_by_local(local))
            self.inventory_data = products
            # Normalizar lista de locales (incluyendo los que vengan en productos)
            locales_norm = {
                self._norm_local(p.get("local"))
                for p in products
                if p.get("local") is not None
            }
            locales_norm.update({self._norm_local(l) for l in locales_raw})
            self.inventory_locals = sorted(locales_norm)

            # Actualizar opciones de filtros dinámicos
            self._update_inventory_filters(self.inventory_locals, products)

            # Pintar tabla (respetar filtros activos)
            has_filters = (
                (self.inv_search.text() or "").strip()
                or (self.inv_category.currentData() or "")
                or (self.inv_local.currentData() or "")
                or (self.inv_estado.currentData() or "")
                or self.inv_low_stock.isChecked()
            )
            if has_filters:
                self.filter_inventory()
            else:
                self._populate_inventory_table(products)
        except Exception as e:
            logger.error(f"Error al actualizar tabla de inventario: {e}")

    def _update_inventory_filters(self, locales, products):
        """Refresca combos de filtros con datos actuales."""
        try:
            # Locales
            self.inv_local.blockSignals(True)
            current_local = self.inv_local.currentData()
            self.inv_local.clear()
            self.inv_local.addItem("Todos los locales", "")
            for loc in sorted(
                {self._norm_local(l) for l in (locales or []) if l or l == ""}
            ):
                self.inv_local.addItem(loc, loc)
            # Restaurar selección si posible
            idx = self.inv_local.findData(current_local)
            if idx >= 0:
                self.inv_local.setCurrentIndex(idx)
            self.inv_local.blockSignals(False)

            # Categorías
            self.inv_category.blockSignals(True)
            current_cat = self.inv_category.currentData()
            self.inv_category.clear()
            self.inv_category.addItem("Todas las categorías", "")
            cats = sorted(
                {
                    (p.get("categoria") or "").strip()
                    for p in products
                    if p.get("categoria")
                }
            )
            for c in cats:
                self.inv_category.addItem(c, c)
            idx = self.inv_category.findData(current_cat)
            if idx >= 0:
                self.inv_category.setCurrentIndex(idx)
            self.inv_category.blockSignals(False)

            # Estados
            self.inv_estado.blockSignals(True)
            current_estado = self.inv_estado.currentData()
            self.inv_estado.clear()
            self.inv_estado.addItem("Todos los estados", "")
            estados = sorted(
                {(p.get("estado") or "").strip() for p in products if p.get("estado")}
            )
            for st in estados:
                self.inv_estado.addItem(st, st)
            idx = self.inv_estado.findData(current_estado)
            if idx >= 0:
                self.inv_estado.setCurrentIndex(idx)
            self.inv_estado.blockSignals(False)
        except Exception as e:
            logger.error(f"Error actualizando filtros de inventario: {e}")

    def _populate_inventory_table(self, products):
        """Dibuja la tabla de inventario con la lista dada (merge por producto y cantidades por local)."""
        # Agrupar productos iguales (ignorando local)
        locale_names = [
            self._norm_local(l)
            for l in (
                self.inventory_locals if hasattr(self, "inventory_locals") else []
            )
        ]
        locale_names = list(dict.fromkeys(locale_names))  # mantener orden y únicos
        base_cols = [
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
        ]
        headers = base_cols + locale_names
        self.inventory_table.setColumnCount(len(headers))
        self.inventory_table.setHorizontalHeaderLabels(headers)

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
            # Precio: conservar primero no nulo, o actualizar si no había
            if not entry["precio_costo"]:
                entry["precio_costo"] = p.get("precio_costo") or 0
            if not entry["precio_venta"]:
                entry["precio_venta"] = p.get("precio_venta") or 0
            # Código/desc: conservar primero no vacío
            if not entry["codigo"]:
                entry["codigo"] = p.get("codigo") or ""
            if not entry["descripcion"]:
                entry["descripcion"] = p.get("descripcion") or ""
            # Fecha: mantener la más reciente (lexicográfica ISO)
            upd = str(p.get("updated_at") or "")
            if upd > entry["updated_at"]:
                entry["updated_at"] = upd

        rows = list(merged.items())
        self.inventory_table.setRowCount(len(rows))

        for idx, (key, data) in enumerate(rows):
            (nombre, categoria, medida, estado, color) = key
            precio_costo = data.get("precio_costo") or 0
            precio_venta = data.get("precio_venta") or 0
            codigo = data.get("codigo") or ""
            descripcion = data.get("descripcion") or ""
            updated_at = data.get("updated_at") or ""
            total_qty = sum(data["locales"].values())
            min_stock = 5
            item_bg = QColor("white")
            if total_qty <= 0:
                item_bg = QColor("#ffdddd")
            elif total_qty <= min_stock:
                item_bg = QColor("#ffffcc")

            def _set(col, text, background=None):
                item = QTableWidgetItem(text)
                if background:
                    item.setBackground(background)
                self.inventory_table.setItem(idx, col, item)

            _set(0, nombre)
            _set(1, categoria)
            _set(2, medida)
            _set(3, estado)
            _set(4, color)
            _set(5, f"${precio_costo:,.2f}")
            _set(6, f"${precio_venta:,.2f}")
            _set(7, codigo)
            _set(8, descripcion)
            _set(9, str(updated_at))
            _set(10, str(total_qty), item_bg)

            # Cantidades por local en columnas dinámicas
            for col_idx, loc in enumerate(locale_names, start=len(base_cols)):
                qty = data["locales"].get(loc, 0)
                bg = item_bg if qty <= min_stock else None
                _set(col_idx, str(qty), bg)

    def _update_trends(self):
        """Actualiza el panel de tendencias con top de productos vendidos."""
        try:
            top = list_top_products(limit=5)
            if not top:
                self.sales_chart.setText("Sin datos de ventas para mostrar tendencias.")
                return
            lines = ["Top 5 productos más vendidos:"]
            for idx, item in enumerate(top, 1):
                lines.append(
                    f"{idx}. {item.get('producto') or 'N/A'} — {item.get('cantidad', 0)} uds"
                )
            self.sales_chart.setText("\n".join(lines))
        except Exception as e:
            logger.error(f"Error actualizando tendencias: {e}")
            self.sales_chart.setText("Error al cargar tendencias")

    def update_users_table(self):
        """Actualiza la tabla de usuarios con datos del servidor."""
        try:
            users = list_users()
            self.users_table.setRowCount(len(users))

            for i, user in enumerate(users):
                self.users_table.setItem(
                    i, 0, QTableWidgetItem(user.get("username", ""))
                )
                self.users_table.setItem(i, 1, QTableWidgetItem(user.get("nombre", "")))
                self.users_table.setItem(i, 2, QTableWidgetItem(user.get("rol", "")))
                self.users_table.setItem(i, 3, QTableWidgetItem(user.get("local", "")))

                last_login = user.get("last_login", "")
                if last_login:
                    if isinstance(last_login, str):
                        self.users_table.setItem(i, 4, QTableWidgetItem(last_login))
                    else:
                        self.users_table.setItem(
                            i,
                            4,
                            QTableWidgetItem(last_login.strftime("%Y-%m-%d %H:%M")),
                        )
                else:
                    self.users_table.setItem(i, 4, QTableWidgetItem("Nunca"))

        except Exception as e:
            logger.error(f"Error al actualizar tabla de usuarios: {e}")

    def show_add_user_dialog(self):
        """Muestra el diálogo para agregar un nuevo usuario."""
        from views.user_dialog import UserDialog

        dialog = UserDialog(self)
        if dialog.exec_() == UserDialog.Accepted:
            # Aquí iría la lógica para guardar el nuevo usuario
            QMessageBox.information(self, "Éxito", "Usuario creado correctamente")
            self.refresh_data()

    def edit_selected_user(self):
        """Edita el usuario seleccionado."""
        selected = self.users_table.selectedItems()
        if not selected:
            QMessageBox.warning(
                self, "Advertencia", "Seleccione un usuario para editar"
            )
            return

        username = self.users_table.item(selected[0].row(), 0).text()
        from views.user_dialog import UserDialog

        dialog = UserDialog(self, username=username, edit_mode=True)
        if dialog.exec_() == UserDialog.Accepted:
            # Aquí iría la lógica para actualizar el usuario
            QMessageBox.information(self, "Éxito", "Usuario actualizado correctamente")
            self.refresh_data()

    def delete_selected_user(self):
        """Elimina el usuario seleccionado."""
        selected = self.users_table.selectedItems()
        if not selected:
            QMessageBox.warning(
                self, "Advertencia", "Seleccione un usuario para eliminar"
            )
            return

        username = self.users_table.item(selected[0].row(), 0).text()

        reply = QMessageBox.question(
            self,
            "Confirmar eliminación",
            f"¿Está seguro de que desea eliminar al usuario {username}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # Aquí iría la lógica para eliminar el usuario
            QMessageBox.information(self, "Éxito", "Usuario eliminado correctamente")
            self.refresh_data()

    def export_inventory(self):
        """Exporta el inventario a un archivo Excel."""
        # Implementar exportación a Excel
        QMessageBox.information(
            self, "En desarrollo", "La exportación a Excel estará disponible pronto"
        )

    def print_inventory(self):
        """Imprime el inventario actual."""
        # Implementar impresión
        QMessageBox.information(
            self, "En desarrollo", "La impresión estará disponible pronto"
        )

    def closeEvent(self, event):
        """Maneja el cierre de la ventana."""
        # Detener el temporizador de actualización
        self.update_timer.stop()
        event.accept()

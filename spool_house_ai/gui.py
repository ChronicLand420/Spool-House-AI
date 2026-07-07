from __future__ import annotations

import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

try:
    from PySide6.QtCore import QThread, Qt, QUrl, Signal
    from PySide6.QtGui import QDesktopServices, QFontMetrics, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QSizePolicy,
        QPushButton,
        QProgressBar,
        QScrollArea,
        QSplitter,
        QVBoxLayout,
        QWidget,
        QDoubleSpinBox,
        QSpinBox,
        QTextEdit,
    )
except ModuleNotFoundError as error:
    missing_name = error.name or "PySide6"
    print(f"Missing GUI dependency: {missing_name}")
    print("Install GUI dependencies with: python -m pip install -r requirements.txt")
    raise SystemExit(1) from error

from spool_house_ai.config import AppConfig, load_config
from spool_house_ai.logging_setup import configure_logging
from spool_house_ai.pipeline import ImagePipeline


ROOMS = [
    "Intake Room",
    "Cleanup Lab",
    "Detail Analyzer",
    "Vector Workshop",
    "Mesh Forge",
    "Render Bay",
    "Output Vault",
]


def _elide_text(widget: QWidget, text: str, reserve_px: int = 24) -> str:
    width = max(80, widget.width() - reserve_px)
    return QFontMetrics(widget.font()).elidedText(text, Qt.ElideMiddle, width)


class GuiLogHandler(logging.Handler):
    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        self.callback(self.format(record))


class DropQueue(QListWidget):
    files_added = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        files = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.lower() in {".png", ".jpg", ".jpeg"}
        ]
        if files:
            self.files_added.emit(files)


class RoomCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.title = title
        self.setObjectName("roomCard")
        self.setMaximumHeight(150)
        self.setMinimumWidth(165)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        self.header = QLabel(title)
        self.header.setObjectName("roomTitle")
        self.status = QLabel("Idle")
        self.status.setObjectName("roomStatus")
        self.status.setWordWrap(False)
        self.status.setMinimumHeight(20)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.thumb = QLabel()
        self.thumb.setFixedSize(110, 74)
        self.thumb.setFixedSize(98, 58)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setText("preview")
        self.thumb.setObjectName("thumb")
        layout.addWidget(self.header)
        layout.addWidget(self.thumb)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        self.set_state("idle", "Idle", None)

    def set_state(self, state: str, message: str, thumbnail: Path | None) -> None:
        icons = {"idle": "[ ]", "active": "[*]", "done": "[OK]", "failed": "[!]"}
        progress = {"idle": 0, "active": 55, "done": 100, "failed": 100}
        self.header.setText(f"{icons.get(state, '[ ]')} {self.title}")
        self.status.setText(message)
        self.progress.setValue(progress.get(state, 0))
        status_labels = {
            "idle": "Waiting",
            "active": "Running",
            "done": "Warning" if "warning" in message.lower() else "Done",
            "failed": "Failed",
        }
        status_text = status_labels.get(state, "Waiting")
        self.header.setText(self.title)
        full_status = f"{status_text} - {message}"
        self.status.setText(status_text)
        self.status.setToolTip(full_status)
        self.header.setToolTip(self.title)
        self.setProperty("state", "warning" if status_text == "Warning" else state)
        self.style().unpolish(self)
        self.style().polish(self)
        if thumbnail and thumbnail.exists():
            pixmap = QPixmap(str(thumbnail))
            if not pixmap.isNull():
                self.thumb.setPixmap(pixmap.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


class PipelineWorker(QThread):
    stage_changed = Signal(str, str, str, str)
    log_line = Signal(str)
    finished_job = Signal(bool, str, str, str, str)

    def __init__(self, config: AppConfig, image_path: Path) -> None:
        super().__init__()
        self.config = config
        self.image_path = image_path

    def run(self) -> None:
        logger = logging.getLogger(f"spool_house_ai.gui_worker.{id(self)}")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        handler = GuiLogHandler(self.log_line.emit)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        pipeline = ImagePipeline(config=self.config, logger=logger)

        def on_stage(room: str, state: str, message: str, thumbnail: Path | None) -> None:
            self.stage_changed.emit(room, state, message, str(thumbnail or ""))

        ok = pipeline.process(self.image_path, stage_callback=on_stage)
        output_dir = self.config.output_dir / self.image_path.stem
        self.finished_job.emit(ok, str(output_dir), self.image_path.stem, str(self.image_path), self.config.stl.stl_backend)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_config(Path("config/config.yaml"))
        self.logger = configure_logging(self.config.log_dir)
        self.worker: PipelineWorker | None = None
        self.current_output_dir: Path | None = None
        self.current_stem = ""
        self.rooms: dict[str, RoomCard] = {}
        self.log_expanded = False
        self.version = _load_version(self.config.project_root)
        self.setWindowTitle("Spool House AI")
        self.resize(1360, 820)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        title = QLabel("Spool House AI")
        title.setObjectName("appTitle")
        root.addWidget(title)
        if self.version:
            subtitle = QLabel(self.version)
            subtitle.setObjectName("appSubtitle")
            root.addWidget(subtitle)
        creator_credit = QLabel("Built by ChronicLand420")
        creator_credit.setObjectName("creatorCredit")
        root.addWidget(creator_credit)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(self._left_panel())
        main_splitter.addWidget(self._bunker_panel())
        main_splitter.addWidget(self._settings_panel())
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setSizes([300, 650, 430])

        log_panel = QWidget()
        self.log_panel = log_panel
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(6)
        log_header = QHBoxLayout()
        log_title = QLabel("Status Log")
        log_title.setObjectName("sectionTitle")
        self.log_summary = QLabel("Ready")
        self.log_summary.setObjectName("statusSummary")
        self.log_summary.setWordWrap(False)
        self.log_toggle_button = QPushButton("Show Log")
        self.log_toggle_button.setObjectName("secondaryButton")
        self.log_toggle_button.setFixedWidth(96)
        self.log_toggle_button.clicked.connect(self.toggle_log)
        log_header.addWidget(log_title)
        log_header.addWidget(self.log_summary, 1)
        log_header.addWidget(self.log_toggle_button)
        log_layout.addLayout(log_header)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMinimumHeight(96)
        self.logs.setMaximumHeight(190)
        log_layout.addWidget(self.logs)

        self.vertical_splitter = QSplitter(Qt.Vertical)
        self.vertical_splitter.addWidget(main_splitter)
        self.vertical_splitter.addWidget(log_panel)
        self.vertical_splitter.setChildrenCollapsible(False)
        root.addWidget(self.vertical_splitter, 1)
        self.setCentralWidget(central)
        self._apply_style()
        self.set_log_expanded(False)

    def _left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("leftScroll")

        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel.setMinimumWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)
        add_button = QPushButton("Add Image")
        add_button.setObjectName("secondaryButton")
        add_button.clicked.connect(self.add_image)
        self.queue = DropQueue()
        self.queue.files_added.connect(self.add_files)
        self.queue.setMinimumHeight(110)
        self.generate_button = QPushButton("Generate Product")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.clicked.connect(self.generate)
        self.open_output_button = QPushButton("Open Output Folder")
        self.open_stl_button = QPushButton("Open STL")
        self.open_svg_button = QPushButton("Open SVG")
        self.open_preview_button = QPushButton("Open Preview")
        self.copy_svg_button = QPushButton("Copy SVG Path")
        self.copy_stl_button = QPushButton("Copy STL Path")
        self.copy_mesh_report_button = QPushButton("Copy Mesh Report Path")
        self.copy_job_status_button = QPushButton("Copy Job Status Path")
        self.output_buttons = [
            self.open_output_button,
            self.open_stl_button,
            self.open_svg_button,
            self.open_preview_button,
            self.copy_svg_button,
            self.copy_stl_button,
            self.copy_mesh_report_button,
            self.copy_job_status_button,
        ]
        for button in self.output_buttons:
            button.setEnabled(False)
        self.open_output_button.clicked.connect(lambda: self.open_path(self.current_output_dir))
        self.open_stl_button.clicked.connect(lambda: self.open_named_output(".stl"))
        self.open_svg_button.clicked.connect(lambda: self.open_named_output(".svg"))
        self.open_preview_button.clicked.connect(lambda: self.open_named_output("_preview.png"))
        self.copy_svg_button.clicked.connect(lambda: self.copy_named_output(".svg"))
        self.copy_stl_button.clicked.connect(lambda: self.copy_named_output(".stl"))
        self.copy_mesh_report_button.clicked.connect(lambda: self.copy_output_path("mesh_report.json"))
        self.copy_job_status_button.clicked.connect(lambda: self.copy_output_path("job_status.json"))
        queue_title = QLabel("Image Queue")
        queue_title.setObjectName("sectionTitle")
        layout.addWidget(queue_title)
        queue_note = QLabel("Generate processes the first queued item.")
        queue_note.setObjectName("mutedText")
        layout.addWidget(queue_note)
        layout.addWidget(add_button)
        layout.addWidget(self.queue, 1)
        layout.addWidget(self.generate_button)
        output_title = QLabel("Outputs")
        output_title.setObjectName("sectionTitle")
        layout.addWidget(output_title)
        output_grid = QGridLayout()
        output_grid.setHorizontalSpacing(8)
        output_grid.setVerticalSpacing(8)
        output_grid.addWidget(self.open_output_button, 0, 0, 1, 2)
        output_grid.addWidget(self.open_stl_button, 1, 0)
        output_grid.addWidget(self.open_svg_button, 1, 1)
        output_grid.addWidget(self.open_preview_button, 2, 0, 1, 2)
        output_grid.addWidget(self.copy_stl_button, 3, 0)
        output_grid.addWidget(self.copy_svg_button, 3, 1)
        output_grid.addWidget(self.copy_mesh_report_button, 4, 0, 1, 2)
        output_grid.addWidget(self.copy_job_status_button, 5, 0, 1, 2)
        layout.addLayout(output_grid)
        review_title = QLabel("Review")
        review_title.setObjectName("sectionTitle")
        layout.addWidget(review_title)
        self.review_stage = self._combo(["original", "cleaned", "body", "holes", "details", "vector", "STL"])
        self.review_stage.currentTextChanged.connect(self.refresh_review)
        self.review_warning = QLabel("")
        self.review_warning.setWordWrap(True)
        self.review_before = QLabel("before")
        self.review_after = QLabel("after")
        for label in [self.review_before, self.review_after]:
            label.setFixedSize(118, 82)
            label.setAlignment(Qt.AlignCenter)
            label.setObjectName("thumb")
        compare_row = QHBoxLayout()
        compare_row.addWidget(self.review_before)
        compare_row.addWidget(self.review_after)
        self.geometry_report_view = QTextEdit()
        self.geometry_report_view.setReadOnly(True)
        self.geometry_report_view.setMinimumHeight(72)
        self.geometry_report_view.setMaximumHeight(120)
        layout.addWidget(self.review_stage)
        layout.addWidget(self.review_warning)
        layout.addLayout(compare_row)
        layout.addWidget(self.geometry_report_view)
        scroll.setWidget(panel)
        return scroll

    def _bunker_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(12)
        columns = 3
        for index, room in enumerate(ROOMS):
            card = RoomCard(room)
            self.rooms[room] = card
            grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        scroll.setWidget(panel)
        return scroll

    def _settings_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel.setMinimumWidth(330)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        self.stl_backend = self._backend_combo()
        self.product_mode = self._combo(["flat_relief", "keychain", "wall_art"], self.config.stl.product_mode)
        self.detail_mode = self._combo(
            ["silhouette_only", "preserve_holes", "raised_details", "engraved_details", "layered_color_relief"],
            self.config.stl.detail_mode,
        )
        self.extrusion_height = self._double_spin(0.2, 20.0, self.config.stl.extrusion_height_mm)
        self.base_height = self._double_spin(0.2, 10.0, self.config.stl.base_height_mm)
        self.threshold = self._spin(0, 255, self.config.silhouette.threshold_value)
        self.smoothing = self._spin(0, 25, self.config.silhouette.smoothing_strength)
        self.min_area = self._double_spin(0.0, 5000.0, self.config.silhouette.min_contour_area)
        self.simplify = self._double_spin(0.0, 20.0, self.config.silhouette.simplify_tolerance)
        self.detail_height = self._double_spin(0.0, 10.0, self.config.stl.detail_height_mm)
        self.engraving_depth = self._double_spin(0.0, 10.0, self.config.stl.engraving_depth_mm)
        self.preserve_holes = QCheckBox("preserve_holes")
        self.preserve_holes.setChecked(self.config.silhouette.preserve_holes)
        self.preserve_details = QCheckBox("preserve_internal_details")
        self.preserve_details.setChecked(self.config.silhouette.preserve_internal_details)
        self.background_removal = QCheckBox("background_removal_enabled")
        self.background_removal.setChecked(self.config.pipeline.background_removal_enabled)
        self.keychain_hole = QCheckBox("add_keychain_hole")
        self.keychain_hole.setChecked(self.config.stl.add_keychain_hole)
        self.keychain_diameter = self._double_spin(1.0, 20.0, self.config.stl.keychain_hole_diameter_mm)
        self.output_scale = self._double_spin(10.0, 300.0, self.config.stl.output_scale_mm)

        layout.addWidget(self._form_group("STL Backend", [("Backend", self.stl_backend)]))
        layout.addWidget(self._form_group("Product", [("Product mode", self.product_mode), ("Detail mode", self.detail_mode)]))
        layout.addWidget(
            self._form_group(
                "Dimensions",
                [
                    ("Output scale mm", self.output_scale),
                    ("Base height mm", self.base_height),
                    ("Extrusion height mm", self.extrusion_height),
                    ("Detail height mm", self.detail_height),
                    ("Engraving depth mm", self.engraving_depth),
                ],
            )
        )
        cleanup_group = self._form_group(
            "Cleanup / Vector",
            [
                ("Threshold", self.threshold),
                ("Smoothing", self.smoothing),
                ("Min contour area", self.min_area),
                ("Simplify tolerance", self.simplify),
            ],
        )
        cleanup_layout = cleanup_group.layout()
        cleanup_layout.addRow(self.preserve_holes)
        cleanup_layout.addRow(self.preserve_details)
        cleanup_layout.addRow(self.background_removal)
        layout.addWidget(cleanup_group)

        keychain_group = self._form_group("Keychain", [("Hole diameter mm", self.keychain_diameter)])
        keychain_group.layout().addRow(self.keychain_hole)
        layout.addWidget(keychain_group)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _combo(self, values: list[str], current_value: str | None = None) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        if current_value is not None:
            index = combo.findText(current_value)
            if index >= 0:
                combo.setCurrentIndex(index)
        return combo

    def _form_group(self, title: str, rows: list[tuple[str, QWidget]]) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName("settingsGroup")
        form = QFormLayout(group)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for label_text, widget in rows:
            label = QLabel(label_text)
            label.setObjectName("formLabel")
            form.addRow(label, widget)
        return group

    def _backend_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem("auto_vector_first - Try vector, fallback to raster", "auto_vector_first")
        combo.addItem("vector_extrusion - Vector experimental", "vector_extrusion")
        combo.addItem("raster_heightfield - Stable raster fallback", "raster_heightfield")
        index = combo.findData(self.config.stl.stl_backend)
        combo.setCurrentIndex(max(0, index))
        return combo

    def _double_spin(self, minimum: float, maximum: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setValue(value)
        return spin

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def add_image(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add Image", str(self.config.input_dir), "Images (*.png *.jpg *.jpeg)")
        self.add_files([Path(file) for file in files])

    def add_files(self, files: list[Path]) -> None:
        for file in files:
            item = QListWidgetItem(_elide_text(self.queue, str(file)))
            item.setData(Qt.UserRole, str(file))
            item.setToolTip(str(file))
            self.queue.addItem(item)

    def generate(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Spool House AI", "A job is already running.")
            return
        if self.queue.count() == 0:
            QMessageBox.information(self, "Spool House AI", "Add an image first.")
            return
        first_item = self.queue.item(0)
        image_path = Path(first_item.data(Qt.UserRole) or first_item.text())
        self._append_status_path("Selected input", image_path)
        self.logs.append(f"Requested STL backend: {self._selected_stl_backend()}")
        self.logs.append("Queue mode: processing the first queued item only.")
        self._set_status_summary(f"Running - backend {self._selected_stl_backend()}")
        self.reset_rooms()
        self.generate_button.setEnabled(False)
        config = self._config_from_controls()
        self.worker = PipelineWorker(config, image_path)
        self.worker.stage_changed.connect(self.update_room)
        self.worker.log_line.connect(self.logs.append)
        self.worker.finished_job.connect(self.job_finished)
        self.worker.start()

    def _config_from_controls(self) -> AppConfig:
        product_mode = self.product_mode.currentText()
        detail_mode = self.detail_mode.currentText()
        pipeline = replace(
            self.config.pipeline,
            product_mode=product_mode,
            detail_mode=detail_mode,
            background_removal_enabled=self.background_removal.isChecked(),
        )
        silhouette = replace(
            self.config.silhouette,
            threshold_value=self.threshold.value(),
            smoothing_strength=self.smoothing.value(),
            min_contour_area=self.min_area.value(),
            simplify_tolerance=self.simplify.value(),
            preserve_holes=self.preserve_holes.isChecked(),
            preserve_internal_details=self.preserve_details.isChecked(),
            detail_mode=detail_mode,
            detail_height_mm=self.detail_height.value(),
            engraving_depth_mm=self.engraving_depth.value(),
        )
        svg = replace(self.config.svg, min_contour_area=self.min_area.value(), simplify_tolerance=self.simplify.value())
        stl = replace(
            self.config.stl,
            stl_backend=self._selected_stl_backend(),
            product_mode=product_mode,
            detail_mode=detail_mode,
            extrusion_height_mm=self.extrusion_height.value(),
            base_height_mm=self.base_height.value(),
            detail_height_mm=self.detail_height.value(),
            engraving_depth_mm=self.engraving_depth.value(),
            preserve_holes=self.preserve_holes.isChecked(),
            add_keychain_hole=self.keychain_hole.isChecked(),
            keychain_hole_diameter_mm=self.keychain_diameter.value(),
            output_scale_mm=self.output_scale.value(),
        )
        return replace(self.config, pipeline=pipeline, silhouette=silhouette, svg=svg, stl=stl)

    def update_room(self, room: str, state: str, message: str, thumbnail: str) -> None:
        if room in self.rooms:
            self.rooms[room].set_state(state, message, Path(thumbnail) if thumbnail else None)

    def job_finished(self, ok: bool, output_dir: str, stem: str, input_path: str, requested_backend: str) -> None:
        self.current_output_dir = Path(output_dir)
        self.current_stem = stem
        self.generate_button.setEnabled(True)
        self._update_output_buttons()
        mesh_report_path = self.current_output_dir / "mesh_report.json"
        job_status_path = self.current_output_dir / "job_status.json"
        self._append_status_path("Input file", Path(input_path))
        self._append_status_path("Output folder", self.current_output_dir)
        self.logs.append(f"Requested STL backend: {requested_backend}")
        if mesh_report_path.exists():
            self._append_status_path("Mesh report", mesh_report_path)
            self._append_mesh_report_summary(mesh_report_path)
        if job_status_path.exists():
            self._append_status_path("Job status", job_status_path)
        if not mesh_report_path.exists():
            self._set_status_summary("Done" if ok else "Warnings - check log")
        self.logs.append("Job complete." if ok else "Job complete with warnings. Check logs.")
        self.refresh_review()

    def reset_rooms(self) -> None:
        for room in self.rooms.values():
            room.set_state("idle", "Idle", None)

    def open_named_output(self, suffix: str) -> None:
        self.open_path(self._named_output_path(suffix))

    def copy_named_output(self, suffix: str) -> None:
        self.copy_path(self._named_output_path(suffix))

    def copy_output_path(self, filename: str) -> None:
        if not self.current_output_dir:
            return
        self.copy_path(self.current_output_dir / filename)

    def _named_output_path(self, suffix: str) -> Path | None:
        if not self.current_output_dir:
            return None
        name = f"{self.current_stem}{suffix}" if suffix.startswith("_") else f"{self.current_stem}{suffix}"
        return self.current_output_dir / name

    def open_path(self, path: Path | None) -> None:
        if path and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def copy_path(self, path: Path | None) -> None:
        if path and path.exists():
            QApplication.clipboard().setText(str(path))
            self._append_status_path("Copied path", path)

    def _update_output_buttons(self) -> None:
        if not self.current_output_dir:
            for button in self.output_buttons:
                button.setEnabled(False)
            return
        svg_path = self._named_output_path(".svg")
        stl_path = self._named_output_path(".stl")
        preview_path = self._named_output_path("_preview.png")
        mesh_report_path = self.current_output_dir / "mesh_report.json"
        job_status_path = self.current_output_dir / "job_status.json"
        availability = {
            self.open_output_button: self.current_output_dir.exists(),
            self.open_stl_button: bool(stl_path and stl_path.exists()),
            self.open_svg_button: bool(svg_path and svg_path.exists()),
            self.open_preview_button: bool(preview_path and preview_path.exists()),
            self.copy_stl_button: bool(stl_path and stl_path.exists()),
            self.copy_svg_button: bool(svg_path and svg_path.exists()),
            self.copy_mesh_report_button: mesh_report_path.exists(),
            self.copy_job_status_button: job_status_path.exists(),
        }
        for button, enabled in availability.items():
            button.setEnabled(enabled)

    def toggle_log(self) -> None:
        self.set_log_expanded(not self.log_expanded)

    def set_log_expanded(self, expanded: bool) -> None:
        self.log_expanded = expanded
        self.logs.setVisible(expanded)
        self.log_toggle_button.setText("Hide Log" if expanded else "Show Log")
        if expanded:
            self.log_panel.setMinimumHeight(150)
            self.log_panel.setMaximumHeight(260)
            self.vertical_splitter.setSizes([610, 190])
        else:
            self.log_panel.setMinimumHeight(44)
            self.log_panel.setMaximumHeight(58)
            self.vertical_splitter.setSizes([760, 48])

    def _set_status_summary(self, text: str, tooltip: str | None = None) -> None:
        self.log_summary.setText(_elide_text(self.log_summary, text, reserve_px=20))
        self.log_summary.setToolTip(tooltip or text)

    def _selected_stl_backend(self) -> str:
        data = self.stl_backend.currentData()
        return str(data or self.stl_backend.currentText())

    def _append_status_path(self, label: str, path: Path) -> None:
        full_text = f"{label}: {path}"
        self.logs.append(_elide_text(self.logs, full_text, reserve_px=40))
        self.logs.setToolTip(full_text)

    def _append_mesh_report_summary(self, report_path: Path) -> None:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self.logs.append(f"Could not read mesh report: {error}")
            return

        bounds = report.get("bounding_box_mm")
        requested_backend = report.get("requested_backend")
        actual_backend = report.get("actual_backend")
        if requested_backend:
            self.logs.append(f"Mesh requested backend: {requested_backend}")
        if actual_backend:
            self.logs.append(f"Mesh actual backend: {actual_backend}")
        if report.get("fallback_used"):
            self.logs.append(f"Mesh fallback reason: {report.get('fallback_reason') or 'unknown'}")
        if bounds:
            self.logs.append(f"Mesh bounds mm: {bounds}")
        self.logs.append(f"Mesh watertight: {report.get('watertight')}")
        self.logs.append(
            "Mesh edges: "
            f"open={report.get('open_edge_count')}, "
            f"overused={report.get('overused_edge_count')}, "
            f"non_manifold={report.get('non_manifold_edge_count')}"
        )
        for warning in report.get("warnings") or []:
            self.logs.append(f"Mesh warning: {warning}")
        for failure in report.get("failures") or []:
            self.logs.append(f"Mesh failure: {failure}")
        warning_count = len(report.get("warnings") or [])
        failure_count = len(report.get("failures") or [])
        mesh_result = "watertight" if report.get("watertight") else "not watertight"
        if failure_count:
            job_state = "Failed"
        elif warning_count:
            job_state = "Warning"
        else:
            job_state = "Done"
        summary = (
            f"{job_state} - backend {actual_backend or requested_backend or 'unknown'} - "
            f"mesh {mesh_result} - warnings {warning_count}"
        )
        self._set_status_summary(summary, tooltip=summary)

    def refresh_review(self) -> None:
        if not self.current_output_dir or not self.current_output_dir.exists():
            return
        original = self.current_output_dir / f"{self.current_stem}_preview_original.png"
        stage_files = {
            "original": original,
            "cleaned": self.current_output_dir / f"{self.current_stem}_preview_cleaned.png",
            "body": self.current_output_dir / f"{self.current_stem}_preview_body_mask.png",
            "holes": self.current_output_dir / f"{self.current_stem}_preview_hole_mask.png",
            "details": self.current_output_dir / f"{self.current_stem}_preview_detail_mask.png",
            "vector": self.current_output_dir / f"{self.current_stem}_preview_svg.png",
            "STL": self.current_output_dir / f"{self.current_stem}_preview_stl.png",
        }
        self._set_label_pixmap(self.review_before, original)
        self._set_label_pixmap(self.review_after, stage_files.get(self.review_stage.currentText(), original))
        report_path = self.current_output_dir / "geometry_report.txt"
        if report_path.exists():
            report = report_path.read_text(encoding="utf-8")
            self.geometry_report_view.setPlainText(report)
            self.review_warning.setText("Warning: smoothing fallback used" if "fallback used: true" in report else "")

    def _set_label_pixmap(self, label: QLabel, path: Path) -> None:
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                label.setPixmap(pixmap.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #111318; color: #e8eaed; font-family: Segoe UI; font-size: 10.5pt; }
            #appTitle { font-size: 28px; font-weight: 700; color: #A855F7; padding: 4px 8px 0 8px; }
            #appSubtitle { color: #8e98a8; padding: 0 8px 6px 8px; }
            #creatorCredit { color: #9aa4b2; font-size: 9pt; padding: 0 8px 8px 8px; }
            #sectionTitle { color: #f2f4f7; font-size: 12pt; font-weight: 700; margin-top: 6px; }
            #mutedText { color: #9aa4b2; font-size: 9pt; }
            #statusSummary { color: #c7d0dd; font-size: 9.5pt; padding: 8px 10px 0 10px; }
            #sidePanel { background: #181b22; border: 1px solid #2a303a; border-radius: 8px; }
            QScrollArea { border: 0; background: #111318; }
            QSplitter::handle { background: #1c212b; width: 5px; height: 5px; }
            QPushButton { background: #2b313d; color: #f2f4f7; border: 1px solid #3a4352; padding: 8px 10px; border-radius: 5px; font-weight: 600; }
            QPushButton:hover { background: #343c49; }
            QPushButton#primaryButton { background: #A855F7; color: #111318; border: 1px solid #7E22CE; font-weight: 800; }
            QPushButton#primaryButton:hover { background: #C084FC; }
            QPushButton:disabled { background: #20242c; color: #687386; border: 1px solid #2a303a; }
            QListWidget, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #0d0f14; border: 1px solid #303744; color: #eef1f5; border-radius: 5px; padding: 5px; }
            QComboBox, QSpinBox, QDoubleSpinBox { min-height: 28px; }
            QGroupBox#settingsGroup { background: #151922; border: 1px solid #2a303a; border-radius: 8px; margin-top: 12px; padding-top: 10px; font-weight: 700; color: #A855F7; }
            QGroupBox#settingsGroup::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            #formLabel { color: #aeb7c5; font-weight: 500; }
            QCheckBox { color: #dce1e8; spacing: 8px; }
            #roomCard { background: #181c24; border: 1px solid #303744; border-radius: 8px; }
            #roomCard[state="active"] { border-color: #A855F7; background: #202331; }
            #roomCard[state="done"] { border-color: #55b47a; }
            #roomCard[state="warning"] { border-color: #A855F7; }
            #roomCard[state="failed"] { border-color: #e56b6f; }
            #roomTitle { font-weight: 700; color: #f2f4f7; }
            #roomStatus { color: #9aa4b2; font-size: 9pt; }
            #thumb { background: #0d0f14; border: 1px solid #303744; border-radius: 5px; color: #687386; }
            #connector { color: #596272; font-size: 13px; }
            QProgressBar { border: 0; height: 6px; background: #272d38; border-radius: 3px; }
            QProgressBar::chunk { background: #A855F7; border-radius: 3px; }
            """
        )


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def _load_version(project_root: Path) -> str:
    version_path = project_root / "VERSION"
    if not version_path.exists():
        return ""
    return version_path.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    main()

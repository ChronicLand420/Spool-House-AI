from __future__ import annotations

import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

try:
    from PySide6.QtCore import QThread, Qt, QUrl, Signal
    from PySide6.QtGui import QDesktopServices, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QMainWindow,
        QMessageBox,
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
        layout = QVBoxLayout(self)
        self.header = QLabel(f"○ {title}")
        self.header.setObjectName("roomTitle")
        self.status = QLabel("Idle")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.thumb = QLabel()
        self.thumb.setFixedSize(110, 74)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setText("preview")
        self.thumb.setObjectName("thumb")
        layout.addWidget(self.header)
        layout.addWidget(self.thumb)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        self.set_state("idle", "Idle", None)

    def set_state(self, state: str, message: str, thumbnail: Path | None) -> None:
        icons = {"idle": "○", "active": "●", "done": "✓", "failed": "!"}
        icons = {"idle": "[ ]", "active": "[*]", "done": "[OK]", "failed": "[!]"}
        progress = {"idle": 0, "active": 55, "done": 100, "failed": 100}
        self.header.setText(f"{icons.get(state, '○')} {self.title}")
        self.status.setText(message)
        self.progress.setValue(progress.get(state, 0))
        self.setProperty("state", state)
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
        self.setWindowTitle("Spool House AI V3")
        self.resize(1400, 860)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        title = QLabel("Spool House AI")
        title.setObjectName("appTitle")
        root.addWidget(title)

        splitter = QSplitter()
        splitter.addWidget(self._left_panel())
        splitter.addWidget(self._bunker_panel())
        splitter.addWidget(self._settings_panel())
        splitter.setSizes([260, 820, 320])
        root.addWidget(splitter, 1)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setFixedHeight(145)
        root.addWidget(self.logs)
        self.setCentralWidget(central)
        self._apply_style()

    def _left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        add_button = QPushButton("Add Image")
        add_button.clicked.connect(self.add_image)
        self.queue = DropQueue()
        self.queue.files_added.connect(self.add_files)
        self.generate_button = QPushButton("Generate Product")
        self.generate_button.clicked.connect(self.generate)
        self.open_output_button = QPushButton("Open Output Folder")
        self.open_stl_button = QPushButton("Open STL")
        self.open_svg_button = QPushButton("Open SVG")
        self.open_preview_button = QPushButton("Open Preview")
        for button in [self.open_output_button, self.open_stl_button, self.open_svg_button, self.open_preview_button]:
            button.setEnabled(False)
        self.open_output_button.clicked.connect(lambda: self.open_path(self.current_output_dir))
        self.open_stl_button.clicked.connect(lambda: self.open_named_output(".stl"))
        self.open_svg_button.clicked.connect(lambda: self.open_named_output(".svg"))
        self.open_preview_button.clicked.connect(lambda: self.open_named_output("_preview.png"))
        layout.addWidget(QLabel("Queue: Generate processes the first item"))
        layout.addWidget(add_button)
        layout.addWidget(self.queue, 1)
        layout.addWidget(self.generate_button)
        layout.addWidget(self.open_output_button)
        layout.addWidget(self.open_stl_button)
        layout.addWidget(self.open_svg_button)
        layout.addWidget(self.open_preview_button)
        layout.addWidget(QLabel("Review"))
        self.review_stage = self._combo(["original", "cleaned", "body", "holes", "details", "vector", "STL"])
        self.review_stage.currentTextChanged.connect(self.refresh_review)
        self.review_warning = QLabel("")
        self.review_before = QLabel("before")
        self.review_after = QLabel("after")
        for label in [self.review_before, self.review_after]:
            label.setFixedSize(112, 86)
            label.setAlignment(Qt.AlignCenter)
            label.setObjectName("thumb")
        compare_row = QHBoxLayout()
        compare_row.addWidget(self.review_before)
        compare_row.addWidget(self.review_after)
        self.geometry_report_view = QTextEdit()
        self.geometry_report_view.setReadOnly(True)
        self.geometry_report_view.setFixedHeight(110)
        layout.addWidget(self.review_stage)
        layout.addWidget(self.review_warning)
        layout.addLayout(compare_row)
        layout.addWidget(self.geometry_report_view)
        return panel

    def _bunker_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        grid = QGridLayout(panel)
        for index, room in enumerate(ROOMS):
            card = RoomCard(room)
            self.rooms[room] = card
            grid.addWidget(card, index // 4, (index % 4) * 2)
            if index < len(ROOMS) - 1:
                connector = QLabel("━━━━")
                connector.setObjectName("connector")
                connector.setText("----")
                connector.setAlignment(Qt.AlignCenter)
                grid.addWidget(connector, index // 4, (index % 4) * 2 + 1)
        scroll.setWidget(panel)
        return scroll

    def _settings_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        self.stl_backend = self._backend_combo()
        self.product_mode = self._combo(["flat_relief", "keychain", "wall_art"])
        self.detail_mode = self._combo(["silhouette_only", "preserve_holes", "raised_details", "engraved_details", "layered_color_relief"])
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

        controls = [
            ("STL backend", self.stl_backend),
            ("product_mode", self.product_mode),
            ("detail_mode", self.detail_mode),
            ("extrusion_height_mm", self.extrusion_height),
            ("base_height_mm", self.base_height),
            ("threshold_value", self.threshold),
            ("smoothing_strength", self.smoothing),
            ("min_contour_area", self.min_area),
            ("simplify_tolerance", self.simplify),
            ("detail_height_mm", self.detail_height),
            ("engraving_depth_mm", self.engraving_depth),
            ("keychain_hole_diameter_mm", self.keychain_diameter),
            ("output_scale_mm", self.output_scale),
        ]
        for label, widget in controls:
            layout.addWidget(QLabel(label))
            layout.addWidget(widget)
        layout.addWidget(self.preserve_holes)
        layout.addWidget(self.preserve_details)
        layout.addWidget(self.background_removal)
        layout.addWidget(self.keychain_hole)
        layout.addStretch(1)
        return panel

    def _combo(self, values: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        return combo

    def _backend_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem("raster_heightfield - default/stable", "raster_heightfield")
        combo.addItem("vector_extrusion - experimental/fallback-capable", "vector_extrusion")
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
            self.queue.addItem(str(file))

    def generate(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Spool House AI", "A job is already running.")
            return
        if self.queue.count() == 0:
            QMessageBox.information(self, "Spool House AI", "Add an image first.")
            return
        image_path = Path(self.queue.item(0).text())
        self.logs.append(f"Selected input: {image_path}")
        self.logs.append(f"Requested STL backend: {self._selected_stl_backend()}")
        self.logs.append("Queue mode: processing the first queued item only.")
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
        for button in [self.open_output_button, self.open_stl_button, self.open_svg_button, self.open_preview_button]:
            button.setEnabled(True)
        mesh_report_path = self.current_output_dir / "mesh_report.json"
        self.logs.append(f"Input file: {input_path}")
        self.logs.append(f"Output folder: {self.current_output_dir}")
        self.logs.append(f"Requested STL backend: {requested_backend}")
        if mesh_report_path.exists():
            self.logs.append(f"Mesh report: {mesh_report_path}")
            self._append_mesh_report_summary(mesh_report_path)
        self.logs.append("Job complete." if ok else "Job complete with warnings. Check logs.")
        self.refresh_review()

    def reset_rooms(self) -> None:
        for room in self.rooms.values():
            room.set_state("idle", "Idle", None)

    def open_named_output(self, suffix: str) -> None:
        if not self.current_output_dir:
            return
        name = f"{self.current_stem}{suffix}" if suffix.startswith("_") else f"{self.current_stem}{suffix}"
        self.open_path(self.current_output_dir / name)

    def open_path(self, path: Path | None) -> None:
        if path and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _selected_stl_backend(self) -> str:
        data = self.stl_backend.currentData()
        return str(data or self.stl_backend.currentText())

    def _append_mesh_report_summary(self, report_path: Path) -> None:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self.logs.append(f"Could not read mesh report: {error}")
            return

        bounds = report.get("bounding_box_mm")
        if bounds:
            self.logs.append(f"Mesh bounds mm: {bounds}")
        for warning in report.get("warnings") or []:
            self.logs.append(f"Mesh warning: {warning}")
        for failure in report.get("failures") or []:
            self.logs.append(f"Mesh failure: {failure}")

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
            QWidget { background: #141815; color: #e7dfc6; font-family: Segoe UI; }
            #appTitle { font-size: 28px; font-weight: 700; color: #ffd36a; padding: 8px; }
            #sidePanel { background: #20251f; border: 1px solid #4a563f; border-radius: 6px; }
            QPushButton { background: #d6a846; color: #141815; border: 0; padding: 9px; border-radius: 4px; font-weight: 700; }
            QPushButton:disabled { background: #5c5749; color: #a8a08a; }
            QListWidget, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #0f120f; border: 1px solid #4a563f; color: #f0ead6; }
            #roomCard { background: #242b24; border: 2px solid #4a563f; border-radius: 6px; padding: 6px; }
            #roomCard[state="active"] { border-color: #ffd36a; background: #303821; }
            #roomCard[state="done"] { border-color: #7ecb6b; }
            #roomCard[state="failed"] { border-color: #e26958; }
            #roomTitle { font-weight: 700; color: #ffd36a; }
            #thumb { background: #111511; border: 1px solid #4a563f; color: #817961; }
            #connector { color: #d6a846; font-size: 18px; }
            QProgressBar { border: 1px solid #4a563f; height: 8px; background: #101410; }
            QProgressBar::chunk { background: #d6a846; }
            """
        )


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

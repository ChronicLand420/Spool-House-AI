from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

try:
    from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
    from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QIcon, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QSizePolicy,
        QPushButton,
        QProgressBar,
        QScrollArea,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
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

from spool_house_ai.app_identity import (
    APP_DISPLAY_NAME,
    APP_ORGANIZATION_NAME,
    app_logo_gui_path,
    app_contact_url,
    app_runtime_icon_path,
    app_support_url,
    config_path,
    load_app_version,
    set_windows_app_user_model_id,
)
from spool_house_ai.config import AppConfig, apply_cleanup_preset, load_config, normalize_cleanup_preset
from spool_house_ai.artwork_recommendations import (
    ArtworkRecommendation,
    ArtworkRecommendationCache,
    MIN_RECOMMENDED_THICKNESS_MM,
)
from spool_house_ai.logging_setup import configure_logging
from spool_house_ai.output_paths import JobOutputPaths, build_job_output_paths, build_job_output_paths_for_stem
from spool_house_ai.pipeline import ImagePipeline
from spool_house_ai.processing.filament_layers import calculate_filament_swap_plan
from spool_house_ai.slicer_integration import (
    SLICER_LABELS,
    build_slicer_launch_plan,
    discover_slicer,
    launch_slicer_plan,
    normalize_preferred_slicer,
    select_specific_slicer_input,
)
from spool_house_ai.ui_preferences import (
    UiPreferences,
    default_ui_preferences,
    load_ui_preferences,
    save_ui_preferences,
    ui_preferences_path,
)


ROOMS = [
    "Intake Room",
    "Cleanup Lab",
    "Detail Analyzer",
    "Vector Workshop",
    "Mesh Forge",
    "Render Bay",
    "Output Vault",
]

PRESET_DESCRIPTIONS = {
    "default": "Balanced cleanup for mixed artwork. Keeps likely intentional nearby detail.",
    "clean_logo": "Best for clean logos, bold marks, text logos, and wall art with unwanted floating specks.",
    "detail_preserving": "Keeps more small detached detail for artwork where tiny pieces matter.",
    "drip_logo": "Preserves nearby drips and drops while removing far-away specks.",
    "splatter_logo": "Keeps rough logo texture and near-body splatter detail.",
    "line_art": "For sneaker outlines, coloring-page art, tattoo flash, and clean interior linework.",
    "preserve_floating_islands": "Preserves intentional detached dots, stars, accents, and multipart artwork.",
}

VISIBLE_CLEANUP_PRESETS = [
    ("Default", "default"),
    ("Clean Logo", "clean_logo"),
    ("Detail Preserving", "detail_preserving"),
    ("Drip / Graffiti", "drip_logo"),
    ("Splatter / Rough", "splatter_logo"),
    ("Line Art", "line_art"),
    ("Preserve Floating Islands", "preserve_floating_islands"),
]

VISIBLE_PRODUCT_MODES = [
    ("Flat Relief", "flat_relief"),
    ("Keychain", "keychain"),
    ("Wall Art", "wall_art"),
    ("Lithophane", "lithophane"),
    ("Filament Swap Relief", "filament_swap_relief"),
]

ACCENT_STYLES = {
    "purple": ("#A855F7", "#7E22CE", "#C084FC"),
    "green": ("#22C55E", "#15803D", "#86EFAC"),
    "orange": ("#EC4202", "#B83205", "#FF6A1A"),
    "blue": ("#3B82F6", "#1D4ED8", "#93C5FD"),
    "red": ("#EF4444", "#B91C1C", "#FCA5A5"),
    "pink": ("#EC4899", "#BE185D", "#F9A8D4"),
    "gray": ("#9CA3AF", "#4B5563", "#D1D5DB"),
}

ACCENT_COLOR_OPTIONS = [
    ("Spool Purple", "purple"),
    ("Neon Green", "green"),
    ("Spool House Orange", "orange"),
    ("Blue", "blue"),
    ("Red", "red"),
    ("Pink", "pink"),
    ("Neutral Gray", "gray"),
]

ACCENT_TEXT_COLORS = {
    "blue": "#f8fafc",
    "red": "#f8fafc",
    "gray": "#111318",
}

PREVIEW_SIZES = {
    "small": {"room": (108, 68), "review": (118, 80), "room_height": 150, "spacing": 6, "padding": 9},
    "medium": {"room": (126, 78), "review": (136, 92), "room_height": 168, "spacing": 7, "padding": 10},
    "large": {"room": (148, 92), "review": (158, 106), "room_height": 188, "spacing": 8, "padding": 11},
}


def _elide_text(widget: QWidget, text: str, reserve_px: int = 24) -> str:
    width = max(80, widget.width() - reserve_px)
    return QFontMetrics(widget.font()).elidedText(text, Qt.ElideMiddle, width)


def _brand_logo_label(width: int, height: int) -> QLabel:
    label = QLabel()
    label.setObjectName("brandLogo")
    label.setFixedSize(width, height)
    label.setAlignment(Qt.AlignCenter)
    label.setToolTip("Spool House Studio")
    logo_path = app_logo_gui_path()
    if logo_path.exists():
        pixmap = QPixmap(str(logo_path))
        if not pixmap.isNull():
            label.setPixmap(pixmap.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            return label
    label.setText("Spool\nHouse")
    return label


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
        self.setMaximumHeight(168)
        self.setMinimumWidth(178)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.layout_ref = QVBoxLayout(self)
        self.layout_ref.setContentsMargins(10, 10, 10, 10)
        self.layout_ref.setSpacing(7)
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
        self.thumb.setFixedSize(126, 78)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setText("preview")
        self.thumb.setObjectName("thumb")
        self.layout_ref.addWidget(self.header)
        self.layout_ref.addWidget(self.thumb)
        self.layout_ref.addWidget(self.progress)
        self.layout_ref.addWidget(self.status)
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

    def set_visual_size(self, thumb_width: int, thumb_height: int, max_height: int, padding: int, spacing: int) -> None:
        self.setMaximumHeight(max_height)
        self.layout_ref.setContentsMargins(padding, padding, padding, padding)
        self.layout_ref.setSpacing(spacing)
        self.thumb.setFixedSize(thumb_width, thumb_height)


class CollapsibleSection(QFrame):
    def __init__(self, title: str, expanded: bool = False) -> None:
        super().__init__()
        self.setObjectName("collapsibleSection")
        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName("sectionToggle")
        self.toggle_button.setCheckable(True)
        self.toggle_button.clicked.connect(lambda checked: self.set_expanded(checked))
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 8, 0, 0)
        self.body_layout.setSpacing(10)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.body)

        self.title = title
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle_button.setChecked(expanded)
        self.body.setVisible(expanded)
        prefix = "Hide" if expanded else "Show"
        self.toggle_button.setText(f"{prefix} {self.title}")


class SettingsDialog(QDialog):
    preferences_changed = Signal(object)

    def __init__(self, preferences: UiPreferences, version: str, output_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._syncing = False
        self.last_cleanup_preset = preferences.last_cleanup_preset
        self.default_output_dir = output_dir
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(4)
        title = QLabel("Settings")
        title.setObjectName("dialogTitle")
        intro = QLabel("Customize the app shell without changing production pipeline settings.")
        intro.setObjectName("mutedText")
        intro.setWordWrap(True)
        header_text.addWidget(title)
        header_text.addWidget(intro)
        header_row.addLayout(header_text, 1)
        header_row.addWidget(_brand_logo_label(92, 92), 0, Qt.AlignTop)
        layout.addLayout(header_row)

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)
        self.settings_scroll.setWidget(scroll_content)

        self.theme_combo = self._combo([("Dark", "dark"), ("Light", "light")])
        self.accent_combo = self._combo(ACCENT_COLOR_OPTIONS)
        self.density_combo = self._combo([("Comfortable", "comfortable"), ("Compact", "compact")])
        self.preview_combo = self._combo([("Small", "small"), ("Medium", "medium"), ("Large", "large")])
        self.log_combo = self._combo([("Collapsed", "collapsed"), ("Expanded", "expanded")])

        appearance = QGroupBox("Appearance")
        appearance.setObjectName("settingsGroup")
        appearance_form = QFormLayout(appearance)
        appearance_form.addRow("Theme", self.theme_combo)
        appearance_form.addRow("Accent color", self.accent_combo)
        appearance_form.addRow("UI density", self.density_combo)
        appearance_form.addRow("Preview size", self.preview_combo)
        appearance_form.addRow("Startup log", self.log_combo)
        scroll_layout.addWidget(appearance)

        self.open_output_after = QCheckBox("Open output folder after generation")
        self.show_summary_after = QCheckBox("Show job summary after generation")
        self.use_last_preset = QCheckBox("Use last selected preset on startup")
        workflow = QGroupBox("Workflow Preferences")
        workflow.setObjectName("settingsGroup")
        workflow_layout = QVBoxLayout(workflow)
        workflow_layout.addWidget(self.open_output_after)
        workflow_layout.addWidget(self.show_summary_after)
        workflow_layout.addWidget(self.use_last_preset)
        scroll_layout.addWidget(workflow)

        output_group = QGroupBox("Output Folder")
        output_group.setObjectName("settingsGroup")
        output_layout = QVBoxLayout(output_group)
        output_hint = QLabel("Choose where SVG, STL, previews, and reports are saved.")
        output_hint.setObjectName("mutedText")
        output_hint.setWordWrap(True)
        self.output_folder_edit = QLineEdit()
        self.output_folder_edit.setReadOnly(True)
        self.output_folder_edit.setPlaceholderText(str(output_dir))
        self.output_folder_edit.setToolTip("Leave blank to use the default project output folder.")
        output_button_row = QHBoxLayout()
        self.choose_output_button = QPushButton("Browse")
        self.reset_output_button = QPushButton("Reset to Default")
        self.open_output_root_button = QPushButton("Open")
        self.copy_output_root_button = QPushButton("Copy Path")
        for button in (
            self.choose_output_button,
            self.reset_output_button,
            self.open_output_root_button,
            self.copy_output_root_button,
        ):
            button.setObjectName("secondaryButton")
            output_button_row.addWidget(button)
        output_layout.addWidget(output_hint)
        output_layout.addWidget(self.output_folder_edit)
        output_layout.addLayout(output_button_row)
        scroll_layout.addWidget(output_group)

        slicer_group = QGroupBox("Slicer")
        slicer_group.setObjectName("settingsGroup")
        slicer_layout = QVBoxLayout(slicer_group)
        slicer_hint = QLabel(
            "Choose how Open STL and Open 3MF launch models. SHS opens the selected file only; it does not slice, export G-code, or change slicer profiles."
        )
        slicer_hint.setObjectName("mutedText")
        slicer_hint.setWordWrap(True)
        self.preferred_slicer_combo = self._combo(
            [
                ("System default", "system_default"),
                ("OrcaSlicer", "orca"),
                ("Bambu Studio", "bambu"),
            ]
        )
        slicer_form = QFormLayout()
        slicer_form.addRow("Preferred slicer", self.preferred_slicer_combo)
        slicer_layout.addWidget(slicer_hint)
        slicer_layout.addLayout(slicer_form)

        self.orca_path_edit = QLineEdit()
        self.orca_path_edit.setPlaceholderText("Optional OrcaSlicer executable path")
        self.bambu_path_edit = QLineEdit()
        self.bambu_path_edit.setPlaceholderText("Optional Bambu Studio executable path")
        orca_row = QHBoxLayout()
        self.choose_orca_button = QPushButton("Browse")
        self.choose_orca_button.setObjectName("secondaryButton")
        orca_row.addWidget(self.orca_path_edit, 1)
        orca_row.addWidget(self.choose_orca_button)
        bambu_row = QHBoxLayout()
        self.choose_bambu_button = QPushButton("Browse")
        self.choose_bambu_button.setObjectName("secondaryButton")
        bambu_row.addWidget(self.bambu_path_edit, 1)
        bambu_row.addWidget(self.choose_bambu_button)
        self.detect_slicers_button = QPushButton("Detect Slicers")
        self.detect_slicers_button.setObjectName("secondaryButton")
        self.slicer_detection_status = QLabel("")
        self.slicer_detection_status.setObjectName("mutedText")
        self.slicer_detection_status.setWordWrap(True)
        slicer_layout.addWidget(QLabel("OrcaSlicer executable"))
        slicer_layout.addLayout(orca_row)
        slicer_layout.addWidget(QLabel("Bambu Studio executable"))
        slicer_layout.addLayout(bambu_row)
        slicer_layout.addWidget(self.detect_slicers_button)
        slicer_layout.addWidget(self.slicer_detection_status)
        scroll_layout.addWidget(slicer_group)

        about = QGroupBox("About / Quick Help")
        about.setObjectName("settingsGroup")
        about_layout = QVBoxLayout(about)
        about_header = QHBoxLayout()
        about_header.addWidget(_brand_logo_label(112, 112), 0, Qt.AlignTop)
        about_text = QLabel(
            f"Spool House Studio {version or ''}\n"
            "Built by ChronicLand420\n\n"
            "Workflow: add artwork, pick an artwork style, generate, review outputs, then open the model in your slicer.\n\n"
            f"Default output folder: {output_dir}"
        )
        about_text.setWordWrap(True)
        about_text.setObjectName("mutedText")
        about_header.addWidget(about_text, 1)
        about_layout.addLayout(about_header)
        support_label = QLabel("Support / Contact")
        support_label.setObjectName("supportTitle")
        support_hint = QLabel("Optional links for supporting development or contacting the creator.")
        support_hint.setObjectName("mutedText")
        support_hint.setWordWrap(True)
        support_row = QHBoxLayout()
        self.support_button = QPushButton("Donate / Support")
        self.contact_button = QPushButton("Contact")
        self._configure_external_link_button(
            self.support_button,
            app_support_url(),
            "Support Spool House Studio development",
            "Support link is not configured yet.",
        )
        self._configure_external_link_button(
            self.contact_button,
            app_contact_url(),
            "Contact ChronicLand420 about Spool House Studio",
            "Contact link is not configured yet.",
        )
        support_row.addWidget(self.support_button)
        support_row.addWidget(self.contact_button)
        support_row.addStretch(1)
        about_layout.addSpacing(6)
        about_layout.addWidget(support_label)
        about_layout.addWidget(support_hint)
        about_layout.addLayout(support_row)
        scroll_layout.addWidget(about)
        scroll_layout.addStretch(1)
        layout.addWidget(self.settings_scroll, 1)

        button_row = QHBoxLayout()
        self.reset_button = QPushButton("Reset UI Preferences")
        self.reset_button.setObjectName("secondaryButton")
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("primaryButton")
        button_row.addWidget(self.reset_button)
        button_row.addStretch(1)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.reset_button.clicked.connect(self.reset_preferences)
        self.close_button.clicked.connect(self.accept)
        for combo in [self.theme_combo, self.accent_combo, self.density_combo, self.preview_combo, self.log_combo]:
            combo.currentIndexChanged.connect(self._emit_changed)
        self.preferred_slicer_combo.currentIndexChanged.connect(self._emit_changed)
        for checkbox in [self.open_output_after, self.show_summary_after, self.use_last_preset]:
            checkbox.toggled.connect(self._emit_changed)
        self.orca_path_edit.editingFinished.connect(self._emit_changed)
        self.bambu_path_edit.editingFinished.connect(self._emit_changed)
        self.choose_output_button.clicked.connect(self.choose_output_folder)
        self.reset_output_button.clicked.connect(self.reset_output_folder)
        self.open_output_root_button.clicked.connect(self.open_output_folder)
        self.copy_output_root_button.clicked.connect(self.copy_output_folder)
        self.choose_orca_button.clicked.connect(lambda: self.choose_slicer_executable("orca"))
        self.choose_bambu_button.clicked.connect(lambda: self.choose_slicer_executable("bambu"))
        self.detect_slicers_button.clicked.connect(self.detect_slicers)

        self.set_preferences(preferences)
        self._apply_screen_safe_geometry()

    def _configure_external_link_button(
        self,
        button: QPushButton,
        url: str,
        enabled_tooltip: str,
        disabled_tooltip: str,
    ) -> None:
        button.setObjectName("secondaryButton")
        target = url.strip()
        button.setEnabled(bool(target))
        button.setToolTip(enabled_tooltip if target else disabled_tooltip)
        if target:
            button.clicked.connect(lambda _checked=False, link=target, label=button.text(): self._open_external_link(link, label))

    def _open_external_link(self, url: str, label: str) -> None:
        try:
            opened = QDesktopServices.openUrl(QUrl(url))
        except Exception:
            opened = False
        if not opened:
            QMessageBox.warning(self, "Could Not Open Link", f"Could not open {label}.")

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._apply_screen_safe_geometry()

    def _apply_screen_safe_geometry(self) -> None:
        screen = self.screen()
        if screen is None and self.parentWidget() is not None:
            screen = self.parentWidget().screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(640, min(max(self.sizeHint().height(), 520), 720))
            return

        available = screen.availableGeometry()
        max_width = max(520, int(available.width() * 0.92))
        max_height = max(480, int(available.height() * 0.88))
        target_width = min(max(620, self.sizeHint().width()), max_width)
        target_height = min(max(540, self.sizeHint().height()), max_height)
        self.setMaximumHeight(max_height)
        self.resize(target_width, target_height)

        frame = self.frameGeometry()
        if self.parentWidget() is not None:
            frame.moveCenter(self.parentWidget().frameGeometry().center())
        else:
            frame.moveCenter(available.center())

        if frame.left() < available.left():
            frame.moveLeft(available.left())
        if frame.right() > available.right():
            frame.moveRight(available.right())
        if frame.top() < available.top():
            frame.moveTop(available.top())
        if frame.bottom() > available.bottom():
            frame.moveBottom(available.bottom())
        self.move(frame.topLeft())

    def _combo(self, values: list[tuple[str, str]]) -> QComboBox:
        combo = QComboBox()
        for label, data in values:
            combo.addItem(label, data)
        return combo

    def _set_combo(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def set_preferences(self, preferences: UiPreferences) -> None:
        self._syncing = True
        self.last_cleanup_preset = preferences.last_cleanup_preset
        self._set_combo(self.theme_combo, preferences.appearance_theme)
        self._set_combo(self.accent_combo, preferences.accent_color)
        self._set_combo(self.density_combo, preferences.ui_density)
        self._set_combo(self.preview_combo, preferences.preview_size)
        self._set_combo(self.log_combo, preferences.startup_log_behavior)
        self.open_output_after.setChecked(preferences.open_output_folder_after_generation)
        self.show_summary_after.setChecked(preferences.show_job_summary_after_generation)
        self.use_last_preset.setChecked(preferences.use_last_selected_preset)
        self.output_folder_edit.setText(preferences.output_folder)
        self.output_folder_edit.setToolTip(preferences.output_folder or str(self.default_output_dir))
        self._set_combo(self.preferred_slicer_combo, normalize_preferred_slicer(preferences.preferred_slicer))
        self.orca_path_edit.setText(preferences.orca_executable_path)
        self.bambu_path_edit.setText(preferences.bambu_executable_path)
        self._syncing = False

    def preferences(self) -> UiPreferences:
        return UiPreferences(
            appearance_theme=str(self.theme_combo.currentData() or "dark"),
            accent_color=str(self.accent_combo.currentData() or "purple"),
            ui_density=str(self.density_combo.currentData() or "comfortable"),
            preview_size=str(self.preview_combo.currentData() or "medium"),
            startup_log_behavior=str(self.log_combo.currentData() or "collapsed"),
            open_output_folder_after_generation=self.open_output_after.isChecked(),
            show_job_summary_after_generation=self.show_summary_after.isChecked(),
            use_last_selected_preset=self.use_last_preset.isChecked(),
            last_cleanup_preset=self.last_cleanup_preset,
            output_folder=self.output_folder_edit.text().strip(),
            preferred_slicer=str(self.preferred_slicer_combo.currentData() or "system_default"),
            orca_executable_path=self.orca_path_edit.text().strip(),
            bambu_executable_path=self.bambu_path_edit.text().strip(),
        )

    def reset_preferences(self) -> None:
        self.set_preferences(default_ui_preferences())
        self.preferences_changed.emit(default_ui_preferences())

    def _emit_changed(self) -> None:
        if self._syncing:
            return
        self.preferences_changed.emit(self.preferences())

    def choose_output_folder(self) -> None:
        start_dir = str(self._current_output_folder())
        chosen = QFileDialog.getExistingDirectory(self, "Choose Output Folder", start_dir)
        if not chosen:
            return
        self.output_folder_edit.setText(str(Path(chosen).expanduser().resolve()))
        self.output_folder_edit.setToolTip(self.output_folder_edit.text())
        self._emit_changed()

    def choose_slicer_executable(self, slicer: str) -> None:
        label = SLICER_LABELS.get(slicer, "Slicer")
        current = self.orca_path_edit.text().strip() if slicer == "orca" else self.bambu_path_edit.text().strip()
        start_dir = str(Path(current).parent) if current else str(Path.home())
        chosen, _filter = QFileDialog.getOpenFileName(self, f"Choose {label} Executable", start_dir, "Executables (*.exe)")
        if not chosen:
            return
        resolved = str(Path(chosen).expanduser().resolve())
        if slicer == "orca":
            self.orca_path_edit.setText(resolved)
        else:
            self.bambu_path_edit.setText(resolved)
        self._emit_changed()

    def detect_slicers(self) -> None:
        messages: list[str] = []
        for slicer, edit in (("orca", self.orca_path_edit), ("bambu", self.bambu_path_edit)):
            result = discover_slicer(slicer, configured_path=edit.text().strip())
            label = SLICER_LABELS[slicer]
            if result.found and result.executable_path:
                edit.setText(str(result.executable_path))
                messages.append(f"{label}: {result.discovery_method}")
            else:
                messages.append(f"{label}: not found")
        self.slicer_detection_status.setText("; ".join(messages))
        self._emit_changed()

    def reset_output_folder(self) -> None:
        self.output_folder_edit.clear()
        self.output_folder_edit.setToolTip(str(self.default_output_dir))
        self._emit_changed()

    def open_output_folder(self) -> None:
        try:
            path = self._current_output_folder()
            path.mkdir(parents=True, exist_ok=True)
        except Exception as error:
            QMessageBox.warning(self, APP_DISPLAY_NAME, f"Could not open output folder:\n{error}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def copy_output_folder(self) -> None:
        try:
            path = self._current_output_folder()
        except Exception as error:
            QMessageBox.warning(self, APP_DISPLAY_NAME, f"Could not copy output folder path:\n{error}")
            return
        QApplication.clipboard().setText(str(path))

    def _current_output_folder(self) -> Path:
        raw_path = self.output_folder_edit.text().strip()
        if not raw_path:
            return self.default_output_dir
        return Path(raw_path).expanduser().resolve()


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
        output_dir = build_job_output_paths(self.config.output_dir, self.image_path).job_root
        requested_backend = (
            "lithophane_heightfield"
            if self.config.stl.product_mode == "lithophane"
            else "filament_swap_heightfield"
            if self.config.stl.product_mode == "filament_swap_relief"
            else self.config.stl.stl_backend
        )
        self.finished_job.emit(ok, str(output_dir), self.image_path.stem, str(self.image_path), requested_backend)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_config(config_path())
        self.ui_preferences_path = ui_preferences_path(self.config.project_root)
        self.ui_preferences = load_ui_preferences(self.ui_preferences_path)
        self.logger = configure_logging(self.config.log_dir)
        self.worker: PipelineWorker | None = None
        self.current_output_dir: Path | None = None
        self.current_stem = ""
        self.rooms: dict[str, RoomCard] = {}
        self.settings_dialog: SettingsDialog | None = None
        self._ui_ready = False
        self.log_expanded = False
        self.pending_jobs: list[Path] = []
        self.batch_total = 0
        self.batch_index = 0
        self.batch_success_count = 0
        self.batch_warning_count = 0
        self.batch_failure_count = 0
        self.current_stage = "Waiting"
        self.current_stage_progress_index = 0
        self.recommendation_cache = ArtworkRecommendationCache()
        self.current_recommendation: ArtworkRecommendation | None = None
        self.batch_started_at = 0.0
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setInterval(1000)
        self.runtime_timer.timeout.connect(self.update_runtime_status)
        self.version = load_app_version()
        self.setWindowTitle(APP_DISPLAY_NAME)
        self._apply_window_icon()
        self.resize(1360, 820)
        self._build_ui()
        self._ui_ready = True

    def _apply_window_icon(self) -> None:
        icon_path = app_runtime_icon_path()
        if not icon_path.exists():
            return
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            self.setWindowIcon(icon)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        header = QFrame()
        header.setObjectName("appHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(12)
        header_layout.addWidget(_brand_logo_label(104, 104), 0, Qt.AlignVCenter)
        brand_layout = QVBoxLayout()
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(3)
        title = QLabel(APP_DISPLAY_NAME)
        title.setObjectName("appTitle")
        brand_layout.addWidget(title)
        if self.version:
            subtitle = QLabel(self.version)
            subtitle.setObjectName("appSubtitle")
            brand_layout.addWidget(subtitle)
        creator_credit = QLabel("Built by ChronicLand420")
        creator_credit.setObjectName("creatorCredit")
        brand_layout.addWidget(creator_credit)
        tagline = QLabel("Turn artwork into reviewable SVG, STL, and product-ready output packages.")
        tagline.setObjectName("workflowTagline")
        tagline.setWordWrap(True)
        brand_layout.addWidget(tagline)
        self.header_status_badge = QLabel("Ready")
        self.header_status_badge.setObjectName("headerStatusBadge")
        self.header_status_badge.setProperty("state", "ready")
        self.header_status_badge.setAlignment(Qt.AlignCenter)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("secondaryButton")
        self.settings_button.setToolTip("Open appearance, theme, preview, and workflow preferences.")
        self.settings_button.clicked.connect(self.open_settings)
        header_actions = QVBoxLayout()
        header_actions.setContentsMargins(0, 0, 0, 0)
        header_actions.setSpacing(8)
        header_actions.addWidget(self.header_status_badge)
        header_actions.addWidget(self.settings_button)
        header_layout.addLayout(brand_layout, 1)
        header_layout.addLayout(header_actions, 0)
        root.addWidget(header)

        main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter = main_splitter
        main_splitter.addWidget(self._left_panel())
        main_splitter.addWidget(self._bunker_panel())
        main_splitter.addWidget(self._settings_panel())
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setSizes([330, 700, 430])

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
        self._apply_preview_size()
        self.set_log_expanded(self.ui_preferences.startup_log_behavior == "expanded")

    def _left_panel(self) -> QWidget:
        scroll = QScrollArea()
        self.left_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("leftScroll")

        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel.setMinimumWidth(310)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)
        add_button = QPushButton("Add Image")
        add_button.setObjectName("secondaryButton")
        add_button.clicked.connect(self.add_image)
        self.queue = DropQueue()
        self.queue.files_added.connect(self.add_files)
        self.queue.currentItemChanged.connect(lambda *_args: self.refresh_artwork_recommendation())
        self.queue.setMinimumHeight(110)
        self.generate_button = QPushButton("Generate")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.setToolTip("Generate the selected image, or the first queued image if none is selected.")
        self.generate_button.clicked.connect(self.generate)
        self.generate_all_button = QPushButton("Generate All")
        self.generate_all_button.setObjectName("secondaryButton")
        self.generate_all_button.setToolTip("Process every queued image one at a time.")
        self.generate_all_button.clicked.connect(self.generate_all)
        self.open_output_button = QPushButton("Open Output Folder")
        self.open_output_button.setToolTip("Open the latest job folder after generation.")
        self.open_stl_button = QPushButton("Open STL")
        self.open_stl_button.setToolTip("Open the STL in the configured slicer.")
        self.open_3mf_button = QPushButton("Open 3MF")
        self.open_3mf_button.setToolTip("Open the validated generic 3MF in the configured slicer.")
        self.open_svg_button = QPushButton("Open SVG")
        self.open_preview_button = QPushButton("Open Preview")
        self.open_output_root_button = QPushButton("Open Root Folder")
        self.open_output_root_button.setToolTip("Open the root folder where new job folders are created.")
        self.copy_output_root_button = QPushButton("Copy Root Path")
        self.copy_output_root_button.setToolTip("Copy the root folder where new job folders are created.")
        self.copy_svg_button = QPushButton("Copy SVG Path")
        self.copy_stl_button = QPushButton("Copy STL Path")
        self.copy_mesh_report_button = QPushButton("Copy Mesh Report Path")
        self.copy_job_status_button = QPushButton("Copy Job Status Path")
        self.output_buttons = [
            self.open_output_button,
            self.open_stl_button,
            self.open_3mf_button,
            self.open_svg_button,
            self.open_preview_button,
            self.copy_svg_button,
            self.copy_stl_button,
            self.copy_mesh_report_button,
            self.copy_job_status_button,
        ]
        for button in self.output_buttons:
            button.setEnabled(False)
        self.open_output_button.clicked.connect(self.open_latest_or_root)
        self.open_output_root_button.clicked.connect(self.open_output_root)
        self.copy_output_root_button.clicked.connect(self.copy_output_root)
        self.open_stl_button.clicked.connect(lambda: self.open_named_output(".stl"))
        self.open_3mf_button.clicked.connect(lambda: self.open_named_output(".3mf"))
        self.open_svg_button.clicked.connect(lambda: self.open_named_output(".svg"))
        self.open_preview_button.clicked.connect(lambda: self.open_named_output("_preview.png"))
        self.copy_svg_button.clicked.connect(lambda: self.copy_named_output(".svg"))
        self.copy_stl_button.clicked.connect(lambda: self.copy_named_output(".stl"))
        self.copy_mesh_report_button.clicked.connect(lambda: self.copy_output_path("mesh_report.json"))
        self.copy_job_status_button.clicked.connect(lambda: self.copy_output_path("job_status.json"))
        queue_title = QLabel("1. Add Artwork")
        queue_title.setObjectName("sectionTitle")
        layout.addWidget(queue_title)
        queue_note = QLabel("Drop PNG/JPG artwork here, then generate one image or the full queue.")
        queue_note.setObjectName("mutedText")
        queue_note.setWordWrap(True)
        layout.addWidget(queue_note)
        layout.addWidget(add_button)
        layout.addWidget(self.queue, 1)
        generate_row = QHBoxLayout()
        generate_row.addWidget(self.generate_button)
        generate_row.addWidget(self.generate_all_button)
        layout.addLayout(generate_row)
        output_title = QLabel("Output Vault")
        output_title.setObjectName("sectionTitle")
        layout.addWidget(output_title)
        output_grid = QGridLayout()
        output_grid.setHorizontalSpacing(8)
        output_grid.setVerticalSpacing(8)
        output_grid.addWidget(self.open_output_root_button, 0, 0)
        output_grid.addWidget(self.copy_output_root_button, 0, 1)
        output_grid.addWidget(self.open_output_button, 1, 0, 1, 2)
        output_grid.addWidget(self.open_stl_button, 2, 0)
        output_grid.addWidget(self.open_3mf_button, 2, 1)
        output_grid.addWidget(self.open_svg_button, 3, 0, 1, 2)
        output_grid.addWidget(self.open_preview_button, 4, 0, 1, 2)
        output_grid.addWidget(self.copy_stl_button, 5, 0)
        output_grid.addWidget(self.copy_svg_button, 5, 1)
        output_grid.addWidget(self.copy_mesh_report_button, 6, 0, 1, 2)
        output_grid.addWidget(self.copy_job_status_button, 7, 0, 1, 2)
        layout.addLayout(output_grid)
        review_title = QLabel("Stage Compare")
        review_title.setObjectName("sectionTitle")
        layout.addWidget(review_title)
        self.review_stage = self._combo(["original", "cleaned", "body", "holes", "details", "vector", "review SVG", "STL"])
        self.review_stage.currentTextChanged.connect(self.refresh_review)
        self.review_warning = QLabel("")
        self.review_warning.setWordWrap(True)
        self.review_before = QLabel("before")
        self.review_after = QLabel("after")
        for label in [self.review_before, self.review_after]:
            label.setFixedSize(136, 92)
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

        production_title = QLabel("Production Review")
        production_title.setObjectName("sectionTitle")
        layout.addWidget(production_title)
        self.production_thumbs: dict[str, QLabel] = {}
        production_grid = QGridLayout()
        production_grid.setHorizontalSpacing(8)
        production_grid.setVerticalSpacing(8)
        for index, name in enumerate(["Input", "SVG", "Review SVG", "Preview"]):
            wrapper = QVBoxLayout()
            label = QLabel(name)
            label.setObjectName("mutedText")
            thumb = QLabel(name)
            thumb.setFixedSize(136, 92)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setObjectName("thumb")
            self.production_thumbs[name] = thumb
            wrapper.addWidget(label)
            wrapper.addWidget(thumb)
            cell = QWidget()
            cell.setLayout(wrapper)
            production_grid.addWidget(cell, index // 2, index % 2)
        layout.addLayout(production_grid)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _bunker_panel(self) -> QWidget:
        scroll = QScrollArea()
        self.pipeline_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(10)
        workflow_header = QFrame()
        workflow_header.setObjectName("workflowCard")
        workflow_layout = QVBoxLayout(workflow_header)
        workflow_layout.setContentsMargins(12, 10, 12, 10)
        workflow_layout.setSpacing(3)
        workflow_title = QLabel("Production Pipeline")
        workflow_title.setObjectName("sectionTitle")
        workflow_hint = QLabel("Clean artwork, trace vectors, forge the mesh, and package outputs for review.")
        workflow_hint.setObjectName("mutedText")
        workflow_hint.setWordWrap(True)
        workflow_layout.addWidget(workflow_title)
        workflow_layout.addWidget(workflow_hint)
        outer.addWidget(workflow_header)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setAlignment(Qt.AlignTop)
        columns = 3
        for index, room in enumerate(ROOMS):
            card = RoomCard(room)
            self.rooms[room] = card
            grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        grid.setRowStretch(3, 1)
        outer.addWidget(grid_widget, 1)
        scroll.setWidget(panel)
        return scroll

    def _settings_panel(self) -> QWidget:
        scroll = QScrollArea()
        self.settings_scroll = scroll
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
        self.product_mode = self._combo(VISIBLE_PRODUCT_MODES, self.config.stl.product_mode)
        self.product_mode.currentIndexChanged.connect(self._product_mode_changed)
        self.detail_mode = self._combo(
            [
                ("Silhouette Only", "silhouette_only"),
                ("Preserve Holes", "preserve_holes"),
                ("Raised Details", "raised_details"),
                ("Engraved Details", "engraved_details"),
                ("Layered Color Relief", "layered_color_relief"),
            ],
            self.config.stl.detail_mode,
        )
        initial_preset = normalize_cleanup_preset(self.config.silhouette.cleanup_preset)
        if self.ui_preferences.use_last_selected_preset and self.ui_preferences.last_cleanup_preset:
            initial_preset = normalize_cleanup_preset(self.ui_preferences.last_cleanup_preset)
        self.cleanup_preset = self._combo(VISIBLE_CLEANUP_PRESETS, initial_preset)
        self.cleanup_preset.currentIndexChanged.connect(self._cleanup_preset_changed)
        self.extrusion_height = self._double_spin(0.2, 20.0, self.config.stl.extrusion_height_mm)
        self.base_height = self._double_spin(0.2, 10.0, self.config.stl.base_height_mm)
        self.extrusion_height.valueChanged.connect(lambda *_args: self._update_recommendation_match_state())
        self.base_height.valueChanged.connect(lambda *_args: self._update_recommendation_match_state())
        self.threshold = self._spin(0, 255, self.config.silhouette.threshold_value)
        self.smoothing = self._spin(0, 25, self.config.silhouette.smoothing_strength)
        self.min_area = self._double_spin(0.0, 5000.0, self.config.silhouette.min_contour_area)
        self.simplify = self._double_spin(0.0, 20.0, self.config.silhouette.simplify_tolerance)
        self.min_island_area = self._double_spin(0.0, 10000.0, self.config.silhouette.min_island_area_px)
        self.island_distance = self._double_spin(0.0, 100.0, self.config.silhouette.island_near_body_distance_px)
        self.detail_height = self._double_spin(0.0, 10.0, self.config.stl.detail_height_mm)
        self.engraving_depth = self._double_spin(0.0, 10.0, self.config.stl.engraving_depth_mm)
        self.preserve_holes = QCheckBox("preserve_holes")
        self.preserve_holes.setChecked(self.config.silhouette.preserve_holes)
        self.preserve_details = QCheckBox("preserve_internal_details")
        self.preserve_details.setChecked(self.config.silhouette.preserve_internal_details)
        self.remove_islands = QCheckBox("remove_isolated_islands")
        self.remove_islands.setChecked(self.config.silhouette.remove_small_islands)
        self.preserve_islands_near_body = QCheckBox("preserve_islands_near_body")
        self.preserve_islands_near_body.setChecked(self.config.silhouette.preserve_islands_near_body)
        self.background_removal = QCheckBox("background_removal_enabled")
        self.background_removal.setChecked(self.config.pipeline.background_removal_enabled)
        self.keychain_hole = QCheckBox("add_keychain_hole")
        self.keychain_hole.setChecked(self.config.stl.add_keychain_hole)
        self.keychain_diameter = self._double_spin(1.0, 20.0, self.config.stl.keychain_hole_diameter_mm)
        self.output_scale = self._double_spin(10.0, 300.0, self.config.stl.output_scale_mm)
        self.output_scale.valueChanged.connect(lambda *_args: self.refresh_artwork_recommendation())
        self.lithophane_width = self._double_spin(20.0, 300.0, self.config.stl.lithophane_width_mm)
        self.lithophane_min_thickness = self._double_spin(0.2, 10.0, self.config.stl.lithophane_min_thickness_mm)
        self.lithophane_max_thickness = self._double_spin(0.3, 12.0, self.config.stl.lithophane_max_thickness_mm)
        self.lithophane_max_thickness.setMinimum(self.lithophane_min_thickness.value() + 0.1)
        self.lithophane_min_thickness.valueChanged.connect(
            lambda value: self.lithophane_max_thickness.setMinimum(value + 0.1)
        )
        self.lithophane_max_pixels = self._spin(1000, 250000, self.config.stl.lithophane_max_pixels)
        self.lithophane_invert = QCheckBox("Invert lithophane")
        self.lithophane_invert.setChecked(self.config.stl.lithophane_invert)
        self.lithophane_autocontrast = QCheckBox("Autocontrast")
        self.lithophane_autocontrast.setChecked(self.config.stl.lithophane_autocontrast_enabled)
        self.lithophane_autocontrast_cutoff = self._double_spin(
            0.0,
            20.0,
            self.config.stl.lithophane_autocontrast_cutoff_percent,
        )
        self.lithophane_contrast = self._double_spin(0.5, 2.5, self.config.stl.lithophane_contrast)
        self.lithophane_gamma = self._double_spin(0.5, 2.5, self.config.stl.lithophane_gamma)
        self.lithophane_sharpen = self._double_spin(0.0, 3.0, self.config.stl.lithophane_sharpen_strength)
        self.lithophane_denoise = self._spin(0, 4, self.config.stl.lithophane_denoise_radius_px)
        self.filament_width = self._double_spin(20.0, 300.0, self.config.filament_swap_relief.width_mm)
        self.filament_color_count = self._spin(2, 5, self.config.filament_swap_relief.color_count)
        self.filament_base_height = self._double_spin(0.2, 5.0, self.config.filament_swap_relief.base_height_mm)
        self.filament_layer_step = self._double_spin(0.1, 3.0, self.config.filament_swap_relief.layer_step_mm)
        self.filament_first_layer_height = self._double_spin(
            0.05,
            1.0,
            self.config.filament_swap_relief.first_layer_height_mm,
        )
        self.filament_normal_layer_height = self._double_spin(
            0.05,
            1.0,
            self.config.filament_swap_relief.layer_height_mm,
        )
        self.filament_alignment_mode = self._combo(
            [("Snap up", "snap_up"), ("Snap nearest", "snap_nearest"), ("Strict", "strict")],
            self.config.filament_swap_relief.height_alignment_mode,
        )
        self.filament_palette_color_space = self._combo(
            [("RGB", "rgb"), ("LAB", "lab")],
            self.config.filament_swap_relief.palette_color_space,
        )
        self.filament_island_policy = self._combo(
            [
                ("Preserve all", "preserve_all"),
                ("Remove below threshold", "remove_below_threshold"),
                ("Merge with nearest region", "merge_with_nearest_region"),
                ("Connect within maximum gap", "connect_within_maximum_gap"),
            ],
            self.config.filament_swap_relief.island_policy,
        )
        self.filament_island_policy.currentIndexChanged.connect(self._update_filament_policy_controls)
        self.filament_min_region_area = self._spin(0, 10000, self.config.filament_swap_relief.min_region_area_px)
        self.filament_merge_distance = self._spin(0, 100, self.config.filament_swap_relief.island_merge_max_distance_px)
        self.filament_connect_gap = self._spin(0, 100, self.config.filament_swap_relief.island_connect_max_gap_px)
        self.filament_connection_width = self._spin(1, 20, self.config.filament_swap_relief.island_connection_width_px)
        self.filament_auto_background_ignore = QCheckBox("Auto background ignore")
        self.filament_auto_background_ignore.setChecked(self.config.filament_swap_relief.auto_background_ignore)
        for control in [
            self.filament_color_count,
            self.filament_base_height,
            self.filament_layer_step,
            self.filament_first_layer_height,
            self.filament_normal_layer_height,
        ]:
            signal = control.valueChanged
            signal.connect(self._refresh_filament_color_plan_estimate)
        self.filament_alignment_mode.currentIndexChanged.connect(self._refresh_filament_color_plan_estimate)

        preset_group = self._form_group("Presets", [("Artwork style", self.cleanup_preset)])
        self.preset_help = QLabel("")
        self.preset_help.setObjectName("presetDescription")
        self.preset_help.setWordWrap(True)
        preset_group.layout().addRow(self.preset_help)
        self.recommendation_summary = QLabel("Add/select artwork to get a local recommendation.")
        self.recommendation_summary.setObjectName("presetDescription")
        self.recommendation_summary.setWordWrap(True)
        self.recommendation_reasons = QLabel("")
        self.recommendation_reasons.setObjectName("mutedText")
        self.recommendation_reasons.setWordWrap(True)
        self.apply_recommendation_button = QPushButton("Apply Recommendation")
        self.apply_recommendation_button.setObjectName("secondaryButton")
        self.apply_recommendation_button.setEnabled(False)
        self.apply_recommendation_button.clicked.connect(self.apply_artwork_recommendation)
        preset_group.layout().addRow("Recommendation", self.recommendation_summary)
        preset_group.layout().addRow("", self.recommendation_reasons)
        preset_group.layout().addRow("", self.apply_recommendation_button)
        layout.addWidget(preset_group)
        product_group = self._form_group(
            "Product Setup",
            [
                ("Product", self.product_mode),
                ("Detail handling", self.detail_mode),
                ("Output size mm", self.output_scale),
            ],
        )
        self.lithophane_note = QLabel("Lithophane uses photo brightness for thickness. Cleanup presets are ignored.")
        self.lithophane_note.setObjectName("presetDescription")
        self.lithophane_note.setWordWrap(True)
        product_group.layout().addRow(self.lithophane_note)
        self.filament_swap_note = QLabel(
            "Filament Swap Relief uses detected colors as stepped heights for manual filament swaps. "
            "Cleanup presets, detail handling, and STL backend options are ignored."
        )
        self.filament_swap_note.setObjectName("presetDescription")
        self.filament_swap_note.setWordWrap(True)
        product_group.layout().addRow(self.filament_swap_note)
        layout.addWidget(product_group)

        advanced_section = CollapsibleSection("Advanced Settings", expanded=False)
        self.advanced_section = advanced_section
        advanced_section.body_layout.addWidget(self._form_group("STL Engine", [("Backend", self.stl_backend)]))
        advanced_section.body_layout.addWidget(
            self._form_group(
                "Dimensions",
                [
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
                ("Min island area px", self.min_island_area),
                ("Near-body distance px", self.island_distance),
            ],
        )
        cleanup_layout = cleanup_group.layout()
        cleanup_layout.addRow(self.remove_islands)
        cleanup_layout.addRow(self.preserve_islands_near_body)
        cleanup_layout.addRow(self.preserve_holes)
        cleanup_layout.addRow(self.preserve_details)
        cleanup_layout.addRow(self.background_removal)
        advanced_section.body_layout.addWidget(cleanup_group)

        keychain_group = self._form_group("Keychain", [("Hole diameter mm", self.keychain_diameter)])
        keychain_group.layout().addRow(self.keychain_hole)
        advanced_section.body_layout.addWidget(keychain_group)
        lithophane_group = self._form_group(
            "Lithophane",
            [
                ("Width mm", self.lithophane_width),
                ("Min thickness mm", self.lithophane_min_thickness),
                ("Max thickness mm", self.lithophane_max_thickness),
                ("Max sampled pixels", self.lithophane_max_pixels),
                ("Autocontrast cutoff %", self.lithophane_autocontrast_cutoff),
                ("Contrast", self.lithophane_contrast),
                ("Gamma", self.lithophane_gamma),
                ("Sharpen strength", self.lithophane_sharpen),
                ("Denoise radius px", self.lithophane_denoise),
            ],
        )
        self.lithophane_group = lithophane_group
        lithophane_group.layout().addRow(self.lithophane_invert)
        lithophane_group.layout().addRow(self.lithophane_autocontrast)
        advanced_section.body_layout.addWidget(lithophane_group)
        filament_group = self._form_group(
            "Filament Swap Relief",
            [
                ("Width mm", self.filament_width),
                ("Color count", self.filament_color_count),
                ("Base height mm", self.filament_base_height),
                ("Step height mm", self.filament_layer_step),
                ("First layer height mm", self.filament_first_layer_height),
                ("Normal layer height mm", self.filament_normal_layer_height),
                ("Height alignment", self.filament_alignment_mode),
                ("Palette color space", self.filament_palette_color_space),
                ("Island handling", self.filament_island_policy),
                ("Min region area px", self.filament_min_region_area),
                ("Merge max distance px", self.filament_merge_distance),
                ("Connection max gap px", self.filament_connect_gap),
                ("Connection width px", self.filament_connection_width),
            ],
        )
        self.filament_group = filament_group
        filament_group.layout().addRow(self.filament_auto_background_ignore)
        advanced_section.body_layout.addWidget(filament_group)
        self.filament_plan_group = QGroupBox("Filament Color Plan")
        self.filament_plan_group.setObjectName("settingsGroup")
        plan_layout = QVBoxLayout(self.filament_plan_group)
        plan_layout.setContentsMargins(12, 12, 12, 12)
        plan_layout.setSpacing(8)
        self.filament_plan_note = QLabel("Estimated rows use current settings. Exact colors appear after generation.")
        self.filament_plan_note.setObjectName("mutedText")
        self.filament_plan_note.setWordWrap(True)
        self.filament_plan_table = QTableWidget(0, 10)
        self.filament_plan_table.setHorizontalHeaderLabels(
            ["Order", "Color", "Hex", "Req start", "Aligned start", "First", "Last", "Change before", "Layers", "Warning"]
        )
        self.filament_plan_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.filament_plan_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.filament_plan_table.setMinimumHeight(150)
        self.filament_plan_table.setMaximumHeight(220)
        self.filament_plan_table.horizontalHeader().setStretchLastSection(True)
        self.filament_plan_table.verticalHeader().setVisible(False)
        plan_layout.addWidget(self.filament_plan_note)
        plan_layout.addWidget(self.filament_plan_table)
        advanced_section.body_layout.addWidget(self.filament_plan_group)
        layout.addWidget(advanced_section)
        layout.addStretch(1)
        self._update_preset_help()
        self._product_mode_changed()
        self.refresh_artwork_recommendation()
        scroll.setWidget(panel)
        return scroll

    def _combo(self, values: list, current_value: str | None = None) -> QComboBox:
        combo = QComboBox()
        for value in values:
            if isinstance(value, tuple):
                label, data = value
            else:
                label = data = value
            combo.addItem(str(label), str(data))
        if current_value is not None:
            index = combo.findData(current_value)
            if index < 0:
                index = combo.findText(current_value)
            if index >= 0:
                combo.setCurrentIndex(index)
        return combo

    def _combo_value(self, combo: QComboBox) -> str:
        data = combo.currentData()
        return str(data if data is not None else combo.currentText())

    def _update_preset_help(self) -> None:
        if not hasattr(self, "preset_help"):
            return
        preset = self._combo_value(self.cleanup_preset)
        description = PRESET_DESCRIPTIONS.get(preset, "Choose how aggressively artwork cleanup should remove small artifacts.")
        self.preset_help.setText(description)
        self.cleanup_preset.setToolTip(description)

    def _cleanup_preset_changed(self) -> None:
        self._update_preset_help()
        self._update_recommendation_match_state()
        if not self._ui_ready or not self.ui_preferences.use_last_selected_preset:
            return
        self.ui_preferences = replace(
            self.ui_preferences,
            last_cleanup_preset=self._combo_value(self.cleanup_preset),
        )
        self._save_ui_preferences()
        if self.settings_dialog:
            self.settings_dialog.last_cleanup_preset = self.ui_preferences.last_cleanup_preset

    def refresh_artwork_recommendation(self) -> None:
        if not hasattr(self, "recommendation_summary"):
            return
        image_path = self._selected_or_first_image()
        if image_path is None:
            self.current_recommendation = None
            self.recommendation_summary.setText("Add/select artwork to get a local recommendation.")
            self.recommendation_reasons.setText("")
            self.apply_recommendation_button.setEnabled(False)
            return
        recommendation = self.recommendation_cache.get(
            image_path,
            output_width_mm=self.output_scale.value(),
            product_mode=self._combo_value(self.product_mode),
        )
        self.current_recommendation = recommendation
        if not recommendation.available and recommendation.unavailable_reason and hasattr(self, "logs"):
            self.logs.append(f"Recommendation unavailable: {recommendation.unavailable_reason}")
        self._render_artwork_recommendation(recommendation)

    def apply_artwork_recommendation(self) -> None:
        recommendation = self.current_recommendation
        if recommendation is None or not recommendation.available:
            return
        preset_index = self.cleanup_preset.findData(recommendation.recommended_preset)
        if preset_index >= 0:
            self.cleanup_preset.setCurrentIndex(preset_index)
        target_thickness = max(MIN_RECOMMENDED_THICKNESS_MM, recommendation.recommended_thickness_mm)
        multiplier = _product_height_multiplier(self._combo_value(self.product_mode))
        extrusion = max(self.extrusion_height.minimum(), (target_thickness - self.base_height.value()) / multiplier)
        self.extrusion_height.setValue(min(self.extrusion_height.maximum(), extrusion))
        self._update_recommendation_match_state()
        message = (
            f"Applied recommendation: {_preset_label(recommendation.recommended_preset)}, "
            f"{target_thickness:.1f} mm finished thickness."
        )
        self._set_status_summary(message, tooltip=message)
        self.logs.append(message)

    def _render_artwork_recommendation(self, recommendation: ArtworkRecommendation) -> None:
        if not recommendation.available:
            self.recommendation_summary.setText("Recommendation unavailable for the current selection.")
            self.recommendation_reasons.setText(recommendation.reasons[0] if recommendation.reasons else "")
            self.apply_recommendation_button.setEnabled(False)
            return
        preset_label = _preset_label(recommendation.recommended_preset)
        match_text = (
            "Current selections match."
            if self._recommendation_matches_current(recommendation)
            else "Current selections differ."
        )
        self.recommendation_summary.setText(
            f"{preset_label} / {recommendation.recommended_thickness_mm:.1f} mm / "
            f"{recommendation.confidence.title()} confidence. {match_text}"
        )
        self.recommendation_reasons.setText("Reasons: " + "; ".join(recommendation.reasons))
        self.apply_recommendation_button.setEnabled(not self._recommendation_matches_current(recommendation))

    def _recommendation_matches_current(self, recommendation: ArtworkRecommendation) -> bool:
        if not recommendation.available:
            return False
        preset_matches = self._combo_value(self.cleanup_preset) == recommendation.recommended_preset
        thickness_matches = abs(self._current_finished_thickness_mm() - recommendation.recommended_thickness_mm) <= 0.05
        return preset_matches and thickness_matches

    def _current_finished_thickness_mm(self) -> float:
        return self.base_height.value() + (
            self.extrusion_height.value() * _product_height_multiplier(self._combo_value(self.product_mode))
        )

    def _update_recommendation_match_state(self) -> None:
        if getattr(self, "current_recommendation", None) is not None and hasattr(self, "recommendation_summary"):
            self._render_artwork_recommendation(self.current_recommendation)

    def _product_mode_changed(self) -> None:
        if not hasattr(self, "product_mode"):
            return
        product_mode = self._combo_value(self.product_mode)
        is_lithophane = product_mode == "lithophane"
        is_filament_swap = product_mode == "filament_swap_relief"
        is_special_heightfield = is_lithophane or is_filament_swap
        if hasattr(self, "lithophane_note"):
            self.lithophane_note.setVisible(is_lithophane)
        if hasattr(self, "filament_swap_note"):
            self.filament_swap_note.setVisible(is_filament_swap)
        if hasattr(self, "lithophane_group"):
            self.lithophane_group.setVisible(is_lithophane)
        if hasattr(self, "filament_group"):
            self.filament_group.setVisible(is_filament_swap)
        if hasattr(self, "filament_plan_group"):
            self.filament_plan_group.setVisible(is_filament_swap)
        contour_controls = [
            getattr(self, "cleanup_preset", None),
            getattr(self, "detail_mode", None),
            getattr(self, "stl_backend", None),
            getattr(self, "output_scale", None),
            getattr(self, "base_height", None),
            getattr(self, "extrusion_height", None),
            getattr(self, "threshold", None),
            getattr(self, "smoothing", None),
            getattr(self, "min_area", None),
            getattr(self, "simplify", None),
            getattr(self, "min_island_area", None),
            getattr(self, "island_distance", None),
            getattr(self, "detail_height", None),
            getattr(self, "engraving_depth", None),
            getattr(self, "preserve_holes", None),
            getattr(self, "preserve_details", None),
            getattr(self, "remove_islands", None),
            getattr(self, "preserve_islands_near_body", None),
            getattr(self, "background_removal", None),
            getattr(self, "keychain_hole", None),
            getattr(self, "keychain_diameter", None),
        ]
        lithophane_controls = [
            getattr(self, "lithophane_width", None),
            getattr(self, "lithophane_min_thickness", None),
            getattr(self, "lithophane_max_thickness", None),
            getattr(self, "lithophane_max_pixels", None),
            getattr(self, "lithophane_invert", None),
            getattr(self, "lithophane_autocontrast", None),
            getattr(self, "lithophane_autocontrast_cutoff", None),
            getattr(self, "lithophane_contrast", None),
            getattr(self, "lithophane_gamma", None),
            getattr(self, "lithophane_sharpen", None),
            getattr(self, "lithophane_denoise", None),
        ]
        filament_controls = [
            getattr(self, "filament_width", None),
            getattr(self, "filament_color_count", None),
            getattr(self, "filament_base_height", None),
            getattr(self, "filament_layer_step", None),
            getattr(self, "filament_first_layer_height", None),
            getattr(self, "filament_normal_layer_height", None),
            getattr(self, "filament_alignment_mode", None),
            getattr(self, "filament_palette_color_space", None),
            getattr(self, "filament_island_policy", None),
            getattr(self, "filament_min_region_area", None),
            getattr(self, "filament_merge_distance", None),
            getattr(self, "filament_connect_gap", None),
            getattr(self, "filament_connection_width", None),
            getattr(self, "filament_auto_background_ignore", None),
        ]
        for control in contour_controls:
            if control is not None:
                control.setEnabled(not is_special_heightfield)
        for control in lithophane_controls:
            if control is not None:
                control.setEnabled(is_lithophane)
        for control in filament_controls:
            if control is not None:
                control.setEnabled(is_filament_swap)
        if hasattr(self, "filament_plan_table"):
            self.filament_plan_table.setEnabled(is_filament_swap)
        self._update_filament_policy_controls()
        self._refresh_filament_color_plan_estimate()
        self.refresh_artwork_recommendation()

    def _update_filament_policy_controls(self) -> None:
        if not hasattr(self, "filament_island_policy") or not hasattr(self, "product_mode"):
            return
        is_filament_swap = self._combo_value(self.product_mode) == "filament_swap_relief"
        policy = self._combo_value(self.filament_island_policy)
        threshold_enabled = is_filament_swap and policy in {
            "remove_below_threshold",
            "merge_with_nearest_region",
            "connect_within_maximum_gap",
        }
        merge_enabled = is_filament_swap and policy == "merge_with_nearest_region"
        connect_enabled = is_filament_swap and policy == "connect_within_maximum_gap"
        self.filament_min_region_area.setEnabled(threshold_enabled)
        self.filament_merge_distance.setEnabled(merge_enabled)
        self.filament_connect_gap.setEnabled(connect_enabled)
        self.filament_connection_width.setEnabled(connect_enabled)

    def _refresh_filament_color_plan_estimate(self, *_args: object) -> None:
        if not hasattr(self, "filament_plan_table") or not hasattr(self, "product_mode"):
            return
        if self._combo_value(self.product_mode) != "filament_swap_relief":
            self.filament_plan_table.setRowCount(0)
            return
        colors = [
            {
                "order": index,
                "index": index,
                "cluster_label": index - 1,
                "hex": "TBD",
                "suggested_color_name": f"Color {index}",
            }
            for index in range(1, self.filament_color_count.value() + 1)
        ]
        try:
            plan = calculate_filament_swap_plan(
                colors,
                base_height_mm=self.filament_base_height.value(),
                layer_step_mm=self.filament_layer_step.value(),
                first_layer_height_mm=self.filament_first_layer_height.value(),
                layer_height_mm=self.filament_normal_layer_height.value(),
                height_alignment_mode=self._combo_value(self.filament_alignment_mode),
                height_alignment_tolerance_mm=self.config.filament_swap_relief.height_alignment_tolerance_mm,
                palette_order="estimate",
            )
        except Exception as error:
            self.filament_plan_note.setText(f"Estimated color plan unavailable: {error}")
            self.filament_plan_table.setRowCount(0)
            return
        self.filament_plan_note.setText("Estimated rows use current settings. Exact colors appear after generation.")
        self._populate_filament_color_plan_table(plan, estimated=True)

    def _load_generated_filament_color_plan(self) -> None:
        color_plan_path = self._report_output_path("color_plan.json")
        if not color_plan_path.exists():
            self._refresh_filament_color_plan_estimate()
            return
        try:
            plan = json.loads(color_plan_path.read_text(encoding="utf-8"))
        except Exception as error:
            self.filament_plan_note.setText(f"Could not read generated color plan: {error}")
            return
        self.filament_plan_note.setText("Exact generated color plan from reports/color_plan.json.")
        self._populate_filament_color_plan_table(plan, estimated=False)

    def _populate_filament_color_plan_table(self, plan: dict, *, estimated: bool) -> None:
        colors = plan.get("colors") or []
        self.filament_plan_table.setRowCount(len(colors))
        for row_index, color in enumerate(colors):
            warning = "; ".join(color.get("warnings") or [])
            values = [
                str(color.get("order", color.get("index", row_index + 1))),
                "",
                str(color.get("hex", "TBD")),
                _format_mm_value(color.get("requested_start_z_mm")),
                _format_mm_value(color.get("aligned_start_z_mm")),
                str(color.get("first_layer_using_color", "")),
                str(color.get("last_layer_using_color", "")),
                "" if color.get("change_before_layer") is None else str(color.get("change_before_layer")),
                str(color.get("layer_count", "")),
                "Estimate" if estimated and not warning else warning,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    hex_color = str(color.get("hex", ""))
                    if hex_color.startswith("#") and len(hex_color) == 7:
                        item.setBackground(QColor(hex_color))
                    else:
                        item.setText("TBD")
                self.filament_plan_table.setItem(row_index, column, item)
        self.filament_plan_table.resizeColumnsToContents()

    def open_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.ui_preferences, self.version, self.config.output_dir, self)
            self.settings_dialog.preferences_changed.connect(self.update_ui_preferences)
        else:
            self.settings_dialog.set_preferences(self.ui_preferences)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def update_ui_preferences(self, preferences: UiPreferences) -> None:
        last_preset = preferences.last_cleanup_preset if preferences.use_last_selected_preset else ""
        self.ui_preferences = replace(preferences, last_cleanup_preset=last_preset)
        self._save_ui_preferences()
        self._apply_style()
        self._apply_preview_size()
        self.set_log_expanded(self.ui_preferences.startup_log_behavior == "expanded")
        self._update_output_buttons()

    def _save_ui_preferences(self) -> None:
        save_ui_preferences(self.ui_preferences_path, self.ui_preferences)

    def _effective_output_root(self, notify: bool = False) -> Path:
        raw_path = self.ui_preferences.output_folder.strip()
        if not raw_path:
            return self.config.output_dir
        try:
            output_root = Path(raw_path).expanduser()
            if not output_root.is_absolute():
                output_root = (self.config.project_root / output_root).resolve()
            else:
                output_root = output_root.resolve()
            output_root.mkdir(parents=True, exist_ok=True)
            self._assert_writable_directory(output_root)
            return output_root
        except Exception as error:
            message = f"Output folder unavailable; using default output folder: {error}"
            if notify and self._ui_ready:
                self.logs.append(message)
                self._set_status_summary("Output folder warning", tooltip=message)
            return self.config.output_dir

    def _assert_writable_directory(self, output_root: Path) -> None:
        probe = output_root / ".spool_house_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)

    def open_output_root(self) -> None:
        self.open_path(self._effective_output_root(notify=True))

    def copy_output_root(self) -> None:
        self.copy_path(self._effective_output_root(notify=True), label="Copied output root")

    def open_latest_or_root(self) -> None:
        if self.current_output_dir and self.current_output_dir.exists():
            self.open_path(self.current_output_dir)
            return
        self.open_output_root()

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
        combo.addItem("Auto Vector First (recommended)", "auto_vector_first")
        combo.addItem("Vector Extrusion (experimental)", "vector_extrusion")
        combo.addItem("Raster Heightfield (fallback)", "raster_heightfield")
        combo.setToolTip("Advanced mesh engine choice. Auto Vector First tries clean vector output, then falls back to raster if needed.")
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
        had_selection = self.queue.currentItem() is not None
        for file in files:
            item = QListWidgetItem(_elide_text(self.queue, str(file)))
            item.setData(Qt.UserRole, str(file))
            item.setToolTip(str(file))
            self.queue.addItem(item)
        if files and not had_selection:
            self.queue.setCurrentRow(0)
        elif files:
            self.refresh_artwork_recommendation()

    def generate(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, APP_DISPLAY_NAME, "A job is already running.")
            return
        if self.queue.count() == 0:
            QMessageBox.information(self, APP_DISPLAY_NAME, "Add an image first.")
            return
        image_path = self._selected_or_first_image()
        if image_path is None:
            QMessageBox.information(self, APP_DISPLAY_NAME, "Add an image first.")
            return
        self._start_jobs([image_path], batch_mode=False)

    def generate_all(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, APP_DISPLAY_NAME, "A job is already running.")
            return
        jobs = self._all_queue_images()
        if not jobs:
            QMessageBox.information(self, APP_DISPLAY_NAME, "Add images first.")
            return
        self._start_jobs(jobs, batch_mode=True)

    def _start_jobs(self, jobs: list[Path], batch_mode: bool) -> None:
        self.pending_jobs = jobs
        self.batch_total = len(jobs)
        self.batch_index = 0
        self.batch_success_count = 0
        self.batch_warning_count = 0
        self.batch_failure_count = 0
        self.batch_started_at = time.monotonic()
        self.current_stage = "Waiting"
        self.current_stage_progress_index = 0
        self.runtime_timer.start()
        self._set_processing_buttons_enabled(False)
        mode_text = "all queued items" if batch_mode else "one selected item"
        self.logs.append(f"Queue mode: processing {mode_text}.")
        self._start_next_job()

    def _start_next_job(self) -> None:
        if self.batch_index >= self.batch_total:
            self._finish_batch()
            return
        image_path = self.pending_jobs[self.batch_index]
        self._append_status_path("Selected input", image_path)
        product_mode = self._combo_value(self.product_mode)
        requested_backend = (
            "lithophane_heightfield"
            if product_mode == "lithophane"
            else "filament_swap_heightfield"
            if product_mode == "filament_swap_relief"
            else self._selected_stl_backend()
        )
        self.logs.append(f"Requested STL backend: {requested_backend}")
        self.current_stage = "Intake Room"
        self.current_stage_progress_index = 0
        self.update_runtime_status()
        self.reset_rooms()
        config = self._config_from_controls()
        self._append_status_path("Output root", config.output_dir)
        self.worker = PipelineWorker(config, image_path)
        self.worker.stage_changed.connect(self.update_room)
        self.worker.log_line.connect(self.logs.append)
        self.worker.finished_job.connect(self.job_finished)
        self.worker.start()

    def _selected_or_first_image(self) -> Path | None:
        item = self.queue.currentItem() or self.queue.item(0)
        if item is None:
            return None
        return Path(item.data(Qt.UserRole) or item.text())

    def _all_queue_images(self) -> list[Path]:
        jobs: list[Path] = []
        for index in range(self.queue.count()):
            item = self.queue.item(index)
            jobs.append(Path(item.data(Qt.UserRole) or item.text()))
        return jobs

    def _set_processing_buttons_enabled(self, enabled: bool) -> None:
        self.generate_button.setEnabled(enabled)
        self.generate_all_button.setEnabled(enabled)

    def _config_from_controls(self) -> AppConfig:
        product_mode = self._combo_value(self.product_mode)
        detail_mode = self._combo_value(self.detail_mode)
        pipeline = replace(
            self.config.pipeline,
            product_mode=product_mode,
            detail_mode=detail_mode,
            background_removal_enabled=self.background_removal.isChecked(),
        )
        silhouette = replace(
            self.config.silhouette,
            cleanup_preset=self._combo_value(self.cleanup_preset),
            threshold_value=self.threshold.value(),
            smoothing_strength=self.smoothing.value(),
            min_contour_area=self.min_area.value(),
            simplify_tolerance=self.simplify.value(),
            remove_small_islands=self.remove_islands.isChecked(),
            min_island_area_px=self.min_island_area.value(),
            preserve_islands_near_body=self.preserve_islands_near_body.isChecked(),
            island_near_body_distance_px=self.island_distance.value(),
            preserve_holes=self.preserve_holes.isChecked(),
            preserve_internal_details=self.preserve_details.isChecked(),
            detail_mode=detail_mode,
            detail_height_mm=self.detail_height.value(),
            engraving_depth_mm=self.engraving_depth.value(),
        )
        silhouette = apply_cleanup_preset(silhouette)
        svg = replace(
            self.config.svg,
            min_contour_area=silhouette.min_contour_area,
            simplify_tolerance=silhouette.simplify_tolerance,
        )
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
            lithophane_width_mm=self.lithophane_width.value(),
            lithophane_min_thickness_mm=self.lithophane_min_thickness.value(),
            lithophane_max_thickness_mm=self.lithophane_max_thickness.value(),
            lithophane_invert=self.lithophane_invert.isChecked(),
            lithophane_max_pixels=self.lithophane_max_pixels.value(),
            lithophane_autocontrast_enabled=self.lithophane_autocontrast.isChecked(),
            lithophane_autocontrast_cutoff_percent=self.lithophane_autocontrast_cutoff.value(),
            lithophane_contrast=self.lithophane_contrast.value(),
            lithophane_gamma=self.lithophane_gamma.value(),
            lithophane_sharpen_strength=self.lithophane_sharpen.value(),
            lithophane_denoise_radius_px=self.lithophane_denoise.value(),
        )
        filament_swap_relief = replace(
            self.config.filament_swap_relief,
            width_mm=self.filament_width.value(),
            color_count=self.filament_color_count.value(),
            base_height_mm=self.filament_base_height.value(),
            layer_step_mm=self.filament_layer_step.value(),
            first_layer_height_mm=self.filament_first_layer_height.value(),
            layer_height_mm=self.filament_normal_layer_height.value(),
            height_alignment_mode=self._combo_value(self.filament_alignment_mode),
            auto_background_ignore=self.filament_auto_background_ignore.isChecked(),
            min_region_area_px=self.filament_min_region_area.value(),
            palette_color_space=self._combo_value(self.filament_palette_color_space),
            island_policy=self._combo_value(self.filament_island_policy),
            island_merge_max_distance_px=self.filament_merge_distance.value(),
            island_connect_max_gap_px=self.filament_connect_gap.value(),
            island_connection_width_px=self.filament_connection_width.value(),
        )
        return replace(
            self.config,
            output_dir=self._effective_output_root(notify=True),
            pipeline=pipeline,
            silhouette=silhouette,
            svg=svg,
            stl=stl,
            filament_swap_relief=filament_swap_relief,
        )

    def update_room(self, room: str, state: str, message: str, thumbnail: str) -> None:
        if room in self.rooms:
            self.rooms[room].set_state(state, message, Path(thumbnail) if thumbnail else None)
            room_index = ROOMS.index(room)
            self.current_stage = room
            self.current_stage_progress_index = room_index + (1 if state in {"done", "failed"} else 0)
            self.update_runtime_status()

    def job_finished(self, ok: bool, output_dir: str, stem: str, input_path: str, requested_backend: str) -> None:
        self.current_output_dir = Path(output_dir)
        self.current_stem = stem
        self._update_output_buttons()
        mesh_report_path = self._report_output_path("mesh_report.json")
        job_status_path = self._report_output_path("job_status.json")
        self._append_status_path("Input file", Path(input_path))
        self._append_status_path("Output folder", self.current_output_dir)
        self.logs.append(f"Done - files saved to: {self.current_output_dir}")
        self.logs.append(f"Requested STL backend: {requested_backend}")
        job_warning_count = 0
        if mesh_report_path.exists():
            self._append_status_path("Mesh report", mesh_report_path)
            self._append_mesh_report_summary(mesh_report_path)
        if job_status_path.exists():
            self._append_status_path("Job status", job_status_path)
            job_warning_count = self._append_job_status_summary(job_status_path)
            self._load_generated_filament_color_plan()
        if not mesh_report_path.exists():
            self._set_status_summary("Done" if ok else "Warnings - check log")
        if ok:
            self.batch_success_count += 1
            self.batch_warning_count += job_warning_count
            self.logs.append("Job complete with warnings." if job_warning_count else "Job complete.")
        else:
            self.batch_failure_count += 1
            self.logs.append("Job complete with warnings or failures. Continuing if batch jobs remain.")
        self.refresh_review()
        self.batch_index += 1
        if self.batch_index < self.batch_total:
            self.logs.append(f"Starting next queued image ({self.batch_index + 1}/{self.batch_total}).")
            self._start_next_job()
        else:
            self._finish_batch()

    def _finish_batch(self) -> None:
        self.runtime_timer.stop()
        elapsed = time.monotonic() - self.batch_started_at if self.batch_started_at else 0.0
        total = self.batch_total
        summary = (
            f"Done - {self.batch_success_count}/{total} succeeded - "
            f"{self.batch_warning_count} warnings, {self.batch_failure_count} failures - "
            f"elapsed {_format_duration(elapsed)}"
        )
        if self.current_output_dir:
            summary = f"{summary} - files saved to: {self.current_output_dir}"
        self._set_status_summary(summary, tooltip=summary)
        self.logs.append(f"Batch complete: {self.batch_success_count}/{total} succeeded; elapsed {_format_duration(elapsed)}.")
        self._set_processing_buttons_enabled(True)
        self.worker = None
        if self.current_output_dir and self.ui_preferences.open_output_folder_after_generation:
            self.open_path(self.current_output_dir)
        if self.current_output_dir and self.ui_preferences.show_job_summary_after_generation:
            self.open_path(self._report_output_path("job_summary.md"))

    def reset_rooms(self) -> None:
        for room in self.rooms.values():
            room.set_state("idle", "Idle", None)

    def open_named_output(self, suffix: str) -> None:
        if suffix in {".stl", ".3mf"}:
            self.open_model_output_in_slicer(suffix.lstrip("."))
            return
        self.open_path(self._named_output_path(suffix))

    def open_model_output_in_slicer(self, file_format: str) -> None:
        paths = self._current_job_paths()
        job_status = self._current_job_status()
        selection = select_specific_slicer_input(paths, job_status, file_format)
        if not selection.success:
            QMessageBox.warning(self, APP_DISPLAY_NAME, selection.error)
            self.logs.append(selection.error)
            return

        plan = build_slicer_launch_plan(
            selection,
            preferred_slicer=self.ui_preferences.preferred_slicer,
            orca_executable_path=self.ui_preferences.orca_executable_path,
            bambu_executable_path=self.ui_preferences.bambu_executable_path,
        )
        if not plan.can_launch:
            QMessageBox.warning(self, APP_DISPLAY_NAME, plan.error)
            self.logs.append(plan.error)
            return

        result = launch_slicer_plan(
            plan,
            system_default_launcher=lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))),
        )
        if result.launched:
            self._set_status_summary(result.message, tooltip=result.message)
            self.logs.append(result.message)
        else:
            message = result.error or f"Could not open the selected {file_format.upper()} in a slicer."
            QMessageBox.warning(self, APP_DISPLAY_NAME, message)
            self.logs.append(message)

    def copy_named_output(self, suffix: str) -> None:
        self.copy_path(self._named_output_path(suffix))

    def copy_output_path(self, filename: str) -> None:
        if not self.current_output_dir:
            return
        self.copy_path(self._report_output_path(filename))

    def _named_output_path(self, suffix: str) -> Path | None:
        if not self.current_output_dir:
            return None
        paths = self._current_job_paths()
        name = f"{self.current_stem}{suffix}" if suffix.startswith("_") else f"{self.current_stem}{suffix}"
        if paths and suffix == ".stl":
            return self._first_existing_path(paths.stl_path, self.current_output_dir / name)
        if paths and suffix == ".3mf":
            return self._first_existing_path(paths.generic_3mf_path, self.current_output_dir / name)
        if paths and suffix == ".svg":
            return self._first_existing_path(paths.svg_path, self.current_output_dir / name)
        if paths and suffix == "_preview.png":
            return self._first_existing_path(paths.preview_path, self.current_output_dir / name)
        if paths:
            return self._first_existing_path(paths.previews_dir / name, self.current_output_dir / name)
        return self.current_output_dir / name

    def _current_job_paths(self) -> JobOutputPaths | None:
        if not self.current_output_dir or not self.current_stem:
            return None
        return build_job_output_paths_for_stem(self.current_output_dir.parent, self.current_stem)

    def _current_job_status(self) -> dict | None:
        if not self.current_output_dir:
            return None
        status_path = self._report_output_path("job_status.json")
        if not status_path.exists():
            return None
        try:
            raw = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _report_output_path(self, filename: str) -> Path:
        if not self.current_output_dir:
            return Path(filename)
        paths = self._current_job_paths()
        if paths:
            return self._first_existing_path(paths.reports_dir / filename, self.current_output_dir / filename)
        return self.current_output_dir / filename

    def _preview_output_path(self, filename: str) -> Path:
        if not self.current_output_dir:
            return Path(filename)
        paths = self._current_job_paths()
        if paths:
            return self._first_existing_path(paths.previews_dir / filename, self.current_output_dir / filename)
        return self.current_output_dir / filename

    def _svg_output_path(self, filename: str) -> Path:
        if not self.current_output_dir:
            return Path(filename)
        paths = self._current_job_paths()
        if paths:
            return self._first_existing_path(paths.svg_dir / filename, self.current_output_dir / filename)
        return self.current_output_dir / filename

    def _first_existing_path(self, *paths: Path) -> Path:
        for path in paths:
            if path.exists():
                return path
        return paths[0]

    def open_path(self, path: Path | None) -> None:
        if path and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def copy_path(self, path: Path | None, label: str = "Copied path") -> None:
        if path and path.exists():
            QApplication.clipboard().setText(str(path))
            self._append_status_path(label, path)

    def _update_output_buttons(self) -> None:
        if not self.current_output_dir:
            for button in self.output_buttons:
                button.setEnabled(False)
            self.open_output_button.setEnabled(True)
            return
        svg_path = self._named_output_path(".svg")
        stl_path = self._named_output_path(".stl")
        three_mf_path = self._named_output_path(".3mf")
        preview_path = self._named_output_path("_preview.png")
        mesh_report_path = self._report_output_path("mesh_report.json")
        job_status_path = self._report_output_path("job_status.json")
        paths = self._current_job_paths()
        status = self._current_job_status()
        stl_selection = select_specific_slicer_input(paths, status, "stl")
        three_mf_selection = select_specific_slicer_input(paths, status, "3mf")
        availability = {
            self.open_output_button: self.current_output_dir.exists(),
            self.open_stl_button: bool(stl_path and stl_path.exists() and stl_selection.success),
            self.open_3mf_button: bool(three_mf_path and three_mf_path.exists() and three_mf_selection.success),
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

    def update_runtime_status(self) -> None:
        if self.batch_total <= 0 or not self.runtime_timer.isActive():
            return
        elapsed = time.monotonic() - self.batch_started_at if self.batch_started_at else 0.0
        completed_jobs = min(self.batch_index, self.batch_total)
        stage_fraction = min(max(self.current_stage_progress_index / max(1, len(ROOMS)), 0.0), 0.99)
        progress = min(0.99, (completed_jobs + stage_fraction) / max(1, self.batch_total))
        eta_text = "estimating..."
        if progress >= 0.08:
            remaining = max(0.0, elapsed * (1.0 - progress) / progress)
            eta_text = _format_duration(remaining)
        item_text = f"{min(self.batch_index + 1, self.batch_total)}/{self.batch_total}"
        summary = (
            f"Running {item_text} - {self.current_stage} - "
            f"elapsed {_format_duration(elapsed)} - ETA {eta_text}"
        )
        self._set_status_summary(summary, tooltip=summary)

    def _set_status_summary(self, text: str, tooltip: str | None = None) -> None:
        self.log_summary.setText(_elide_text(self.log_summary, text, reserve_px=20))
        self.log_summary.setToolTip(tooltip or text)
        self._set_header_status(text)

    def _set_header_status(self, text: str) -> None:
        if not hasattr(self, "header_status_badge"):
            return
        normalized = text.lower()
        has_failures = "failed" in normalized or ("failures" in normalized and "0 failures" not in normalized)
        has_warnings = (
            ("warning" in normalized or "warnings" in normalized)
            and "0 warning" not in normalized
            and "warnings 0" not in normalized
        )
        if normalized.startswith("running"):
            label = "Running"
            state = "running"
        elif has_failures:
            label = "Needs Review"
            state = "warning"
        elif has_warnings:
            label = "Needs Review"
            state = "warning"
        elif normalized.startswith("done"):
            label = "Done"
            state = "done"
        else:
            label = "Ready"
            state = "ready"
        self.header_status_badge.setText(label)
        self.header_status_badge.setProperty("state", state)
        self.header_status_badge.style().unpolish(self.header_status_badge)
        self.header_status_badge.style().polish(self.header_status_badge)

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

    def _append_job_status_summary(self, status_path: Path) -> int:
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self.logs.append(f"Could not read job status: {error}")
            return 0

        artifact_summary = status.get("artifact_summary") or {}
        warning_count = len(status.get("warnings") or [])
        isolated_count = int(artifact_summary.get("isolated_island_count") or 0)
        removed_count = int(artifact_summary.get("removed_island_count") or 0)
        preserved_count = int(artifact_summary.get("preserved_island_count") or 0)
        if isolated_count:
            self.logs.append(f"Small isolated islands detected: {isolated_count}")
        if removed_count:
            self.logs.append(f"Removed isolated islands: {removed_count}")
        if preserved_count:
            self.logs.append(f"Preserved isolated islands: {preserved_count}")
        filament_swap = status.get("filament_swap_summary") or {}
        if filament_swap:
            self.logs.append(
                "Filament swap colors: "
                f"{filament_swap.get('color_count_kept', 0)} kept; "
                f"final height {filament_swap.get('final_height_mm', '')} mm"
            )
            island_summary = filament_swap.get("island_summary") or {}
            if island_summary:
                self.logs.append(
                    "Filament islands: "
                    f"{island_summary.get('island_policy', '')}; "
                    f"preserved {island_summary.get('intentionally_preserved_components', 0)}, "
                    f"removed {island_summary.get('removed_components', 0)}, "
                    f"merged {island_summary.get('merged_components', 0)}, "
                    f"connected {island_summary.get('connected_components', 0)}"
                )
            for color in (filament_swap.get("detected_colors") or [])[:5]:
                if color.get("index") == 1:
                    self.logs.append(
                        f"Start with {color.get('hex', '')}: layers "
                        f"{color.get('first_layer_using_color', '')}-{color.get('last_layer_using_color', '')}"
                    )
                else:
                    self.logs.append(
                        f"Change before layer {color.get('change_before_layer', '')} "
                        f"to {color.get('hex', '')} at {color.get('aligned_start_z_mm', color.get('filament_change_at_mm', ''))} mm"
                    )
        return warning_count

    def refresh_review(self) -> None:
        if not self.current_output_dir or not self.current_output_dir.exists():
            return
        original = self._preview_output_path(f"{self.current_stem}_preview_original.png")
        stage_files = {
            "original": original,
            "cleaned": self._preview_output_path(f"{self.current_stem}_preview_cleaned.png"),
            "body": self._preview_output_path(f"{self.current_stem}_preview_body_mask.png"),
            "holes": self._preview_output_path(f"{self.current_stem}_preview_hole_mask.png"),
            "details": self._preview_output_path(f"{self.current_stem}_preview_detail_mask.png"),
            "vector": self._preview_output_path(f"{self.current_stem}_preview_svg.png"),
            "review SVG": self._preview_output_path(f"{self.current_stem}_preview_svg.png"),
            "STL": self._preview_output_path(f"{self.current_stem}_preview_stl.png"),
        }
        self._set_label_pixmap(self.review_before, original, "before")
        self._set_label_pixmap(self.review_after, stage_files.get(self.review_stage.currentText(), original), "after")
        production_paths = {
            "Input": original,
            "SVG": self._preview_output_path(f"{self.current_stem}_preview_svg.png"),
            "Review SVG": self._svg_output_path(f"{self.current_stem}_review.svg"),
            "Preview": self._preview_output_path(f"{self.current_stem}_preview.png"),
        }
        for name, label in self.production_thumbs.items():
            display_path = production_paths.get(name, original)
            fallback_preview = stage_files["vector"] if name == "Review SVG" else display_path
            self._set_label_pixmap(label, fallback_preview, name)
            if display_path:
                label.setToolTip(str(display_path))
        report_path = self._report_output_path("geometry_report.txt")
        if report_path.exists():
            report = report_path.read_text(encoding="utf-8")
            self.geometry_report_view.setPlainText(report)
            self.review_warning.setText("Warning: smoothing fallback used" if "fallback used: true" in report else "")
        else:
            self.geometry_report_view.clear()
            self.review_warning.setText("")

    def _set_label_pixmap(self, label: QLabel, path: Path | None, placeholder: str) -> None:
        label.setToolTip(str(path) if path else "")
        if path and path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                label.setPixmap(pixmap.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        label.clear()
        label.setText(placeholder)

    def _apply_preview_size(self) -> None:
        profile = PREVIEW_SIZES.get(self.ui_preferences.preview_size, PREVIEW_SIZES["medium"])
        room_width, room_height = profile["room"]
        review_width, review_height = profile["review"]
        padding = profile["padding"]
        spacing = profile["spacing"]
        max_room_height = profile["room_height"]
        if self.ui_preferences.ui_density == "compact":
            padding = max(7, padding - 2)
            spacing = max(5, spacing - 2)
            max_room_height = max(136, max_room_height - 10)

        for room in self.rooms.values():
            room.set_visual_size(room_width, room_height, max_room_height, padding, spacing)
        for label in [getattr(self, "review_before", None), getattr(self, "review_after", None)]:
            if label:
                label.setFixedSize(review_width, review_height)
        for label in getattr(self, "production_thumbs", {}).values():
            label.setFixedSize(review_width, review_height)
        if self.current_output_dir:
            self.refresh_review()

    def _theme_tokens(self) -> dict[str, str]:
        accent, accent_border, accent_hover = ACCENT_STYLES.get(
            self.ui_preferences.accent_color,
            ACCENT_STYLES["purple"],
        )
        accent_text = ACCENT_TEXT_COLORS.get(self.ui_preferences.accent_color, "#111318")
        compact = self.ui_preferences.ui_density == "compact"
        if self.ui_preferences.appearance_theme == "light":
            tokens = {
                "bg": "#f3f0f7",
                "panel": "#ffffff",
                "panel_alt": "#f8fafc",
                "field": "#f8fafc",
                "text": "#1f2937",
                "title_text": "#111827",
                "muted": "#5f6b7a",
                "muted_2": "#708093",
                "border": "#cbd5e1",
                "border_soft": "#d8dee8",
                "button": "#e8edf5",
                "button_hover": "#dde5ef",
                "button_text": "#111827",
                "disabled_bg": "#edf1f6",
                "disabled_text": "#94a3b8",
                "progress_bg": "#d8dee8",
                "active_bg": "#f4edff",
                "badge_bg": "#eef2f7",
                "done_bg": "#dcfce7",
                "done_border": "#22c55e",
                "done_text": "#166534",
                "warning_bg": "#f4eaff",
                "warning_text": "#4c1d95",
                "failed_border": "#dc2626",
            }
        else:
            tokens = {
                "bg": "#101217",
                "panel": "#171a22",
                "panel_alt": "#181c24",
                "field": "#0d0f14",
                "text": "#e8eaed",
                "title_text": "#f2f4f7",
                "muted": "#9aa4b2",
                "muted_2": "#aeb7c5",
                "border": "#2a303a",
                "border_soft": "#303744",
                "button": "#2b313d",
                "button_hover": "#343c49",
                "button_text": "#f2f4f7",
                "disabled_bg": "#20242c",
                "disabled_text": "#687386",
                "progress_bg": "#272d38",
                "active_bg": "#202331",
                "badge_bg": "#20242e",
                "done_bg": "#183326",
                "done_border": "#55b47a",
                "done_text": "#c9f7d9",
                "warning_bg": "#30243b",
                "warning_text": "#f4eaff",
                "failed_border": "#e56b6f",
            }
        tokens.update(
            {
                "accent": accent,
                "accent_border": accent_border,
                "accent_hover": accent_hover,
                "accent_text": accent_text,
                "font_size": "10pt" if compact else "10.5pt",
                "button_padding": "6px 8px" if compact else "8px 10px",
                "section_toggle_padding": "8px 10px" if compact else "10px 12px",
                "group_margin_top": "8px" if compact else "12px",
            }
        )
        return tokens

    def _apply_style(self) -> None:
        tokens = self._theme_tokens()
        style = """
            QWidget { background: __BG__; color: __TEXT__; font-family: Segoe UI; font-size: __FONT_SIZE__; }
            QLabel { background: transparent; }
            #appHeader { background: __PANEL__; border: 1px solid __BORDER__; border-radius: 10px; }
            #brandLogo { background: transparent; border: 0; }
            #appTitle { font-size: 27px; font-weight: 800; color: __ACCENT__; padding: 0; }
            #appSubtitle { color: __MUTED__; padding: 0; }
            #creatorCredit { color: __MUTED_2__; font-size: 9pt; padding: 0; }
            #workflowTagline { color: __MUTED__; font-size: 9.5pt; padding-top: 3px; }
            #dialogTitle { font-size: 20px; font-weight: 800; color: __ACCENT__; }
            #headerStatusBadge { background: __BADGE_BG__; border: 1px solid __BORDER_SOFT__; border-radius: 12px; color: __TEXT__; font-weight: 800; padding: 6px 12px; min-width: 92px; }
            #headerStatusBadge[state="running"] { background: __ACTIVE_BG__; border-color: __ACCENT__; color: __TITLE_TEXT__; }
            #headerStatusBadge[state="done"] { background: __DONE_BG__; border-color: __DONE_BORDER__; color: __DONE_TEXT__; }
            #headerStatusBadge[state="warning"] { background: __WARNING_BG__; border-color: __ACCENT_HOVER__; color: __WARNING_TEXT__; }
            #sectionTitle { color: __TITLE_TEXT__; font-size: 12pt; font-weight: 800; margin-top: 4px; }
            #supportTitle { color: __ACCENT__; font-size: 10.5pt; font-weight: 800; }
            #mutedText { color: __MUTED__; font-size: 9pt; }
            #presetDescription { color: __MUTED_2__; font-size: 9pt; line-height: 130%; padding: 2px 0 0 0; }
            #statusSummary { color: __MUTED_2__; font-size: 9.5pt; padding: 8px 10px 0 10px; }
            #sidePanel, #workflowCard { background: __PANEL__; border: 1px solid __BORDER__; border-radius: 10px; }
            #collapsibleSection { background: transparent; border: 0; }
            QDialog { background: __BG__; }
            QScrollArea { border: 0; background: __BG__; }
            QSplitter::handle { background: __BORDER__; width: 5px; height: 5px; }
            QPushButton { background: __BUTTON__; color: __BUTTON_TEXT__; border: 1px solid __BORDER_SOFT__; padding: __BUTTON_PADDING__; border-radius: 5px; font-weight: 600; }
            QPushButton:hover { background: __BUTTON_HOVER__; }
            QPushButton#primaryButton { background: __ACCENT__; color: __ACCENT_TEXT__; border: 1px solid __ACCENT_BORDER__; font-weight: 800; }
            QPushButton#primaryButton:hover { background: __ACCENT_HOVER__; }
            QPushButton#sectionToggle { text-align: left; background: __PANEL_ALT__; border: 1px solid __BORDER_SOFT__; color: __TITLE_TEXT__; font-weight: 800; padding: __SECTION_TOGGLE_PADDING__; border-radius: 8px; }
            QPushButton#sectionToggle:hover { border-color: __ACCENT__; background: __ACTIVE_BG__; }
            QPushButton:disabled { background: __DISABLED_BG__; color: __DISABLED_TEXT__; border: 1px solid __BORDER__; }
            QListWidget, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget { background: __FIELD__; border: 1px solid __BORDER_SOFT__; color: __TEXT__; border-radius: 5px; padding: 5px; }
            QHeaderView::section { background: __PANEL_ALT__; color: __MUTED__; border: 1px solid __BORDER_SOFT__; padding: 4px; }
            QComboBox, QSpinBox, QDoubleSpinBox { min-height: 28px; }
            QGroupBox#settingsGroup { background: __PANEL_ALT__; border: 1px solid __BORDER__; border-radius: 9px; margin-top: __GROUP_MARGIN_TOP__; padding-top: 10px; font-weight: 800; color: __ACCENT__; }
            QGroupBox#settingsGroup::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            #formLabel { color: __MUTED_2__; font-weight: 500; }
            QCheckBox { color: __TEXT__; spacing: 8px; }
            #roomCard { background: __PANEL_ALT__; border: 1px solid __BORDER_SOFT__; border-radius: 9px; }
            #roomCard[state="active"] { border-color: __ACCENT__; background: __ACTIVE_BG__; }
            #roomCard[state="done"] { border-color: __DONE_BORDER__; }
            #roomCard[state="warning"] { border-color: __ACCENT__; }
            #roomCard[state="failed"] { border-color: __FAILED_BORDER__; }
            #roomTitle { font-weight: 700; color: __TITLE_TEXT__; }
            #roomStatus { color: __MUTED__; font-size: 9pt; }
            #thumb { background: __FIELD__; border: 1px solid __BORDER_SOFT__; border-radius: 5px; color: __DISABLED_TEXT__; }
            #connector { color: __MUTED__; font-size: 13px; }
            QProgressBar { border: 0; height: 6px; background: __PROGRESS_BG__; border-radius: 3px; }
            QProgressBar::chunk { background: __ACCENT__; border-radius: 3px; }
        """
        replacements = {f"__{key.upper()}__": value for key, value in tokens.items()}
        for placeholder, value in replacements.items():
            style = style.replace(placeholder, value)
        self.setStyleSheet(style)


def main() -> None:
    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_ORGANIZATION_NAME)
    icon_path = app_runtime_icon_path()
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_mm_value(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _preset_label(preset: str) -> str:
    normalized = normalize_cleanup_preset(preset)
    for label, value in VISIBLE_CLEANUP_PRESETS:
        if value == normalized:
            return label
    return normalized.replace("_", " ").title()


def _product_height_multiplier(product_mode: str) -> float:
    return {
        "flat_relief": 1.0,
        "keychain": 1.15,
        "wall_art": 1.6,
    }.get(product_mode, 1.0)


if __name__ == "__main__":
    main()

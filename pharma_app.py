"""Offline desktop application for converting reports and running analytics."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from csv_converter import convert_to_csv
from stock_analytics import analyse, load_map
from top_molecule_sales import (
    load_config,
    top_sold_product_details,
    top_sold_products,
)


APP_DIRECTORY = (
    Path(os.environ["APPDATA"])
    if sys.platform == "win32" and os.environ.get("APPDATA")
    else Path.home() / "Library" / "Application Support"
    if sys.platform == "darwin"
    else Path.home() / ".local" / "share"
) / "Pharma Analytics"
CONFIG_PATH = APP_DIRECTORY / "auth.json"
JOBS_DIRECTORY = APP_DIRECTORY / "jobs"


def hash_password(password: str, salt: bytes | None = None) -> dict[str, str]:
    """Hash a password using a deliberately expensive memory-hard algorithm."""
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=2**14,
        r=8,
        p=1,
    )
    return {"salt": actual_salt.hex(), "digest": digest.hex()}


def verify_password(password: str, stored: dict[str, str]) -> bool:
    """Verify a password without exposing a timing-dependent comparison."""
    salt = bytes.fromhex(stored["salt"])
    expected = bytes.fromhex(stored["digest"])
    actual = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )
    return hmac.compare_digest(actual, expected)


def load_auth() -> dict[str, str] | None:
    if not CONFIG_PATH.is_file():
        return None
    with CONFIG_PATH.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or not {"salt", "digest"} <= data.keys():
        raise ValueError("The local password configuration is invalid.")
    return {"salt": str(data["salt"]), "digest": str(data["digest"])}


def save_auth(password: str) -> None:
    APP_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary_path = CONFIG_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(hash_password(password), file)
    temporary_path.replace(CONFIG_PATH)


def create_job_directory() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    job_directory = JOBS_DIRECTORY / f"{timestamp}-{secrets.token_hex(4)}"
    (job_directory / "uploads").mkdir(parents=True, exist_ok=False)
    (job_directory / "results").mkdir()
    return job_directory


@dataclass
class Job:
    operation: str
    files: list[Path]
    report_format: str = "stock"
    months_to_cover: float = 2.0
    limit: int = 30


class JobWorker(QThread):
    completed = Signal(list)
    failed = Signal(str)

    def __init__(self, job: Job) -> None:
        super().__init__()
        self.job = job

    def run(self) -> None:
        try:
            job_directory = create_job_directory()
            uploaded: list[Path] = []
            for source in self.job.files:
                destination = job_directory / "uploads" / source.name
                shutil.copy2(source, destination)
                uploaded.append(destination)

            if self.job.operation == "convert":
                outputs = self._convert(uploaded, job_directory / "results")
            elif self.job.operation == "stock":
                outputs = [self._stock(uploaded[0], job_directory / "results")]
            else:
                outputs = self._molecules(uploaded, job_directory / "results")
            self.completed.emit([str(path) for path in outputs])
        except (OSError, ValueError, TypeError, KeyError, pd.errors.ParserError) as error:
            self.failed.emit(str(error))

    def _convert(self, files: list[Path], output_directory: Path) -> list[Path]:
        outputs = []
        for source in files:
            output = output_directory / f"{source.stem}.csv"
            convert_to_csv(source, output, report_format=self.job.report_format)
            outputs.append(output)
        return outputs

    def _stock(self, source: Path, output_directory: Path) -> Path:
        if source.suffix.casefold() in {".xls", ".xlsx"}:
            converted = output_directory / f"{source.stem}.csv"
            convert_to_csv(source, converted, report_format="stock")
            source = converted
        result = analyse(
            pd.read_csv(source, encoding="utf-8-sig"),
            load_map(Path(__file__).parent / "column_map.json"),
            months_to_cover=self.job.months_to_cover,
        )
        output = output_directory / f"{source.stem}_stock_analysis.csv"
        result.to_csv(output, index=False, encoding="utf-8-sig")
        return output

    def _molecules(self, files: list[Path], output_directory: Path) -> list[Path]:
        input_directory = output_directory / "molecule-input"
        input_directory.mkdir()
        for source in files:
            shutil.copy2(source, input_directory / source.name)
        config = load_config(Path(__file__).parent / "top_molecule_sales_config.json")
        excluded = config.get("excluded_designation_prefixes", [])
        result = top_sold_products(input_directory, self.job.limit, excluded)
        details = top_sold_product_details(input_directory, result, excluded)
        summary_path = output_directory / "top_molecule_sales.csv"
        details_path = output_directory / "top_molecule_sales_details.csv"
        result.to_csv(summary_path, index=False, encoding="utf-8-sig")
        details.to_csv(details_path, index=False, encoding="utf-8-sig")
        return [summary_path, details_path]


class PasswordWindow(QWidget):
    authenticated = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.auth = load_auth()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.button = QPushButton()
        self.button.clicked.connect(self.submit)

        layout = QVBoxLayout(self)
        title = QLabel("Pharma Analytics")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(
            QLabel(
                "Create a local password to protect this application."
                if self.auth is None
                else "Enter your local password to continue."
            )
        )
        form = QFormLayout()
        form.addRow("Password", self.password)
        if self.auth is None:
            form.addRow("Confirm password", self.confirm)
        layout.addLayout(form)
        self.button.setText("Create password" if self.auth is None else "Unlock")
        layout.addWidget(self.button)
        layout.addWidget(self.message)
        layout.addStretch()
        self.setMinimumWidth(420)

    def submit(self) -> None:
        password = self.password.text()
        if len(password) < 8:
            self.message.setText("Use a password with at least 8 characters.")
            return
        if self.auth is None:
            if password != self.confirm.text():
                self.message.setText("The passwords do not match.")
                return
            save_auth(password)
        elif not verify_password(password, self.auth):
            self.message.setText("Incorrect password.")
            self.password.clear()
            return
        self.authenticated.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pharma Analytics")
        self.setMinimumSize(720, 560)
        self.worker: JobWorker | None = None
        self.files: list[Path] = []
        self.file_list = QListWidget()
        self.status = QLabel("Choose an operation to begin.")
        self.operation = QComboBox()
        self.operation.addItem("Convert XLS reports to CSV", "convert")
        self.operation.addItem("Analyse stock", "stock")
        self.operation.addItem("Analyse top molecule sales", "molecules")
        self.format = QComboBox()
        self.format.addItem("Stock report", "stock")
        self.format.addItem("Molecule / generic report", "generic")
        self.months = QSpinBox()
        self.months.setRange(1, 24)
        self.months.setValue(2)
        self.limit = QSpinBox()
        self.limit.setRange(1, 10000)
        self.limit.setValue(30)
        self.run_button = QPushButton("Start")
        self.run_button.clicked.connect(self.start_job)
        self.operation.currentIndexChanged.connect(self.update_form)
        self.build_ui()
        self.update_form()

    def build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        heading = QLabel("Pharma Analytics")
        heading.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("Select a task, choose your files, and press Start."))
        form = QFormLayout()
        form.addRow("Task", self.operation)
        form.addRow("Report format", self.format)
        form.addRow("Months to cover", self.months)
        form.addRow("Number of molecule groups", self.limit)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        choose = QPushButton("Choose files")
        choose.clicked.connect(self.choose_files)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_files)
        buttons.addWidget(choose)
        buttons.addWidget(clear)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addWidget(self.file_list)
        layout.addWidget(self.run_button)
        layout.addWidget(self.status)
        layout.addStretch()
        self.setCentralWidget(central)

    def update_form(self) -> None:
        operation = self.operation.currentData()
        self.format.setEnabled(operation == "convert")
        self.months.setEnabled(operation == "stock")
        self.limit.setEnabled(operation == "molecules")

    def choose_files(self) -> None:
        operation = self.operation.currentData()
        if operation == "stock":
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Choose a stock report", "", "Reports (*.csv *.xls *.xlsx)"
            )
        elif operation == "molecules":
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Choose molecule CSV files", "", "CSV files (*.csv)"
            )
        else:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Choose reports to convert", "", "Excel files (*.xls *.xlsx)"
            )
        if paths:
            self.files = [Path(path) for path in paths]
            self.file_list.clear()
            self.file_list.addItems([path.name for path in self.files])
            self.status.setText(f"{len(self.files)} file(s) selected.")

    def clear_files(self) -> None:
        self.files = []
        self.file_list.clear()
        self.status.setText("No files selected.")

    def start_job(self) -> None:
        operation = str(self.operation.currentData())
        if not self.files or (operation == "stock" and len(self.files) != 1):
            self.status.setText("Choose the required input files first.")
            return
        self.run_button.setEnabled(False)
        self.status.setText("Processing files...")
        self.worker = JobWorker(
            Job(
                operation=operation,
                files=self.files,
                report_format=str(self.format.currentData()),
                months_to_cover=float(self.months.value()),
                limit=self.limit.value(),
            )
        )
        self.worker.completed.connect(self.job_completed)
        self.worker.failed.connect(self.job_failed)
        self.worker.start()

    def job_completed(self, outputs: list[str]) -> None:
        self.run_button.setEnabled(True)
        message = "Completed files:\n" + "\n".join(outputs)
        self.status.setText("Completed successfully.")
        QMessageBox.information(self, "Completed", message)

    def job_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.status.setText("The operation failed.")
        QMessageBox.critical(self, "Could not complete operation", message)


def main() -> None:
    app = QApplication(sys.argv)
    password_window = PasswordWindow()
    main_window = MainWindow()

    def show_main() -> None:
        password_window.close()
        main_window.show()

    password_window.authenticated.connect(show_main)
    password_window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

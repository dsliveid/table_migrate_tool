from typing import Optional, Dict, Any
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QSpinBox, QMessageBox, QListWidget,
    QListWidgetItem, QGroupBox, QFormLayout, QRadioButton, QButtonGroup,
    QSplitter, QWidget
)
from PySide6.QtCore import Qt
from ..core.storage import AppStorage
from ..db.connection import test_sql_connection
from .icons import get_icon


class DataSourceDialog(QDialog):
    """数据源管理对话框（支持多数据源的增删改查与实时测试）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("数据源配置管理")
        self.resize(760, 500)
        self.storage = AppStorage()
        self.current_ds_id: Optional[int] = None

        self._init_ui()
        self._load_datasources()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)

        # ---------------- 左侧列表区域 ----------------
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("已保存的数据源列表："))
        self.ds_list = QListWidget()
        self.ds_list.currentItemChanged.connect(self._on_item_selected)
        left_layout.addWidget(self.ds_list)

        btn_box = QHBoxLayout()
        self.btn_new = QPushButton("新增")
        self.btn_new.setIcon(get_icon("plus", "#2563eb", 14))
        self.btn_new.clicked.connect(self._on_new)
        self.btn_del = QPushButton("删除")
        self.btn_del.setIcon(get_icon("trash", "#ffffff", 14))
        self.btn_del.setObjectName("DangerBtn")
        self.btn_del.clicked.connect(self._on_delete)
        btn_box.addWidget(self.btn_new)
        btn_box.addWidget(self.btn_del)
        left_layout.addLayout(btn_box)

        splitter.addWidget(left_widget)

        # ---------------- 右侧配置表单 ----------------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 0, 0, 0)

        form_group = QGroupBox("连接详细参数")
        form_layout = QFormLayout(form_group)
        form_layout.setSpacing(12)

        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("如：开发环境-主库")
        form_layout.addRow("连接名称*:", self.edit_name)

        host_port_layout = QHBoxLayout()
        self.edit_host = QLineEdit()
        self.edit_host.setPlaceholderText("IP 或 主机名 (如 127.0.0.1)")
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(1433)
        host_port_layout.addWidget(self.edit_host, 3)
        host_port_layout.addWidget(QLabel("端口:"))
        host_port_layout.addWidget(self.spin_port, 1)
        form_layout.addRow("主机/端口*:", host_port_layout)

        # 认证方式
        auth_box = QHBoxLayout()
        self.rb_sql_auth = QRadioButton("SQL Server 身份验证")
        self.rb_win_auth = QRadioButton("Windows 身份验证")
        self.rb_sql_auth.setChecked(True)
        self.auth_group = QButtonGroup()
        self.auth_group.addButton(self.rb_sql_auth)
        self.auth_group.addButton(self.rb_win_auth)
        self.auth_group.buttonToggled.connect(self._on_auth_type_changed)
        auth_box.addWidget(self.rb_sql_auth)
        auth_box.addWidget(self.rb_win_auth)
        form_layout.addRow("认证模式:", auth_box)

        self.edit_user = QLineEdit()
        self.edit_user.setText("sa")
        form_layout.addRow("登录账号:", self.edit_user)

        self.edit_pass = QLineEdit()
        self.edit_pass.setEchoMode(QLineEdit.Password)
        self.edit_pass.setPlaceholderText("密码")
        form_layout.addRow("登录密码:", self.edit_pass)

        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(3, 300)
        self.spin_timeout.setValue(10)
        self.spin_timeout.setSuffix(" 秒")
        form_layout.addRow("连接超时:", self.spin_timeout)

        right_layout.addWidget(form_group)

        # 状态与操作栏
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_layout.addWidget(self.lbl_status)

        action_box = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setIcon(get_icon("zap", "#334155", 14))
        self.btn_test.clicked.connect(self._on_test_connection)
        self.btn_save = QPushButton("保存配置")
        self.btn_save.setIcon(get_icon("save", "#ffffff", 14))
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.clicked.connect(self._on_save)

        action_box.addWidget(self.btn_test)
        action_box.addStretch()
        action_box.addWidget(self.btn_save)
        right_layout.addLayout(action_box)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter)

    def _on_auth_type_changed(self):
        is_sql = self.rb_sql_auth.isChecked()
        self.edit_user.setEnabled(is_sql)
        self.edit_pass.setEnabled(is_sql)

    def _load_datasources(self):
        self.ds_list.clear()
        datasources = self.storage.get_datasources()
        for ds in datasources:
            item = QListWidgetItem(f"{ds['name']} ({ds['host']}:{ds['port']})")
            item.setIcon(get_icon("database", "#2563eb", 15))
            item.setData(Qt.UserRole, ds["id"])
            self.ds_list.addItem(item)
        if datasources:
            self.ds_list.setCurrentRow(0)
        else:
            self._on_new()

    def _on_item_selected(self, current: QListWidgetItem, previous: QListWidgetItem = None):
        if not current:
            return
        ds_id = current.data(Qt.UserRole)
        ds = self.storage.get_datasource(ds_id)
        if not ds:
            return
        self.current_ds_id = ds["id"]
        self.edit_name.setText(ds["name"])
        self.edit_host.setText(ds["host"])
        self.spin_port.setValue(ds.get("port", 1433))
        
        auth = ds.get("auth_type", "sql")
        if auth == "windows":
            self.rb_win_auth.setChecked(True)
        else:
            self.rb_sql_auth.setChecked(True)
            
        self.edit_user.setText(ds.get("username", ""))
        self.edit_pass.setText(ds.get("password", ""))
        extra = ds.get("extra_params", {})
        self.spin_timeout.setValue(extra.get("login_timeout", 10))
        self.lbl_status.setText("")

    def _on_new(self):
        self.current_ds_id = None
        self.edit_name.clear()
        self.edit_host.setText("127.0.0.1")
        self.spin_port.setValue(1433)
        self.rb_sql_auth.setChecked(True)
        self.edit_user.setText("sa")
        self.edit_pass.clear()
        self.spin_timeout.setValue(10)
        self.lbl_status.setText("")
        self.edit_name.setFocus()

    def _collect_form_data(self) -> Optional[Dict[str, Any]]:
        name = self.edit_name.text().strip()
        host = self.edit_host.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入连接名称！")
            return None
        if not host:
            QMessageBox.warning(self, "提示", "请输入主机地址！")
            return None

        auth_type = "windows" if self.rb_win_auth.isChecked() else "sql"
        return {
            "id": self.current_ds_id,
            "name": name,
            "host": host,
            "port": self.spin_port.value(),
            "auth_type": auth_type,
            "username": self.edit_user.text().strip() if auth_type == "sql" else "",
            "password": self.edit_pass.text(),
            "extra_params": {
                "login_timeout": self.spin_timeout.value(),
                "timeout": 60
            }
        }

    def _on_test_connection(self):
        data = self._collect_form_data()
        if not data:
            return
        self.lbl_status.setText("正在测试连接，请稍候...")
        self.btn_test.setEnabled(False)
        self.repaint()

        success, msg = test_sql_connection(data)
        self.btn_test.setEnabled(True)
        if success:
            self.lbl_status.setStyleSheet("color: #16a34a; font-weight: bold; padding: 4px;")
            self.lbl_status.setText(f"[连接成功] {msg}")
        else:
            self.lbl_status.setStyleSheet("color: #dc2626; padding: 4px;")
            self.lbl_status.setText(f"[连接失败] {msg}")

    def _on_save(self):
        data = self._collect_form_data()
        if not data:
            return
        try:
            saved_id = self.storage.save_datasource(data)
            self._load_datasources()
            # 选中保存的项
            for i in range(self.ds_list.count()):
                if self.ds_list.item(i).data(Qt.UserRole) == saved_id:
                    self.ds_list.setCurrentRow(i)
                    break
            self.lbl_status.setStyleSheet("color: #16a34a; font-weight: bold; padding: 4px;")
            self.lbl_status.setText("[保存成功] 连接配置已成功保存！")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存连接配置: {e}")

    def _on_delete(self):
        if not self.current_ds_id:
            QMessageBox.information(self, "提示", "请先选择要删除的数据源！")
            return
        ans = QMessageBox.question(
            self, "删除确认",
            f"确定要删除数据源 [{self.edit_name.text()}] 吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if ans == QMessageBox.Yes:
            self.storage.delete_datasource(self.current_ds_id)
            self._load_datasources()

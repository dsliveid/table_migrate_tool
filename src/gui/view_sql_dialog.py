import re
from typing import Optional, List, Dict, Any, Callable
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QMessageBox, QFrame, QLineEdit, QRadioButton,
    QButtonGroup, QCheckBox, QGroupBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from .icons import get_icon


class ViewSqlDialog(QDialog):
    """单视图 SQL 编辑与调整对话框"""

    def __init__(
        self,
        parent=None,
        schema: str = "dbo",
        name: str = "",
        current_custom_sql: Optional[str] = None,
        fetch_raw_callback: Optional[Callable[[str, str], str]] = None
    ):
        super().__init__(parent)
        self.schema = schema
        self.name = name
        self.fetch_raw_callback = fetch_raw_callback
        self.raw_sql: Optional[str] = None
        self.final_custom_sql: Optional[str] = current_custom_sql

        self.setWindowTitle(f"调整视图定义 - [{self.schema}].[{self.name}]")
        self.resize(780, 520)

        self._init_ui(current_custom_sql)
        self._load_initial_sql(current_custom_sql)

    def _init_ui(self, current_custom_sql: Optional[str]):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 顶部提示栏
        header_card = QFrame()
        header_card.setObjectName("CardPanel")
        h_layout = QVBoxLayout(header_card)
        h_layout.setContentsMargins(10, 8, 10, 8)
        h_layout.setSpacing(4)

        title_lbl = QLabel(f"<b>目标视图：</b> <code>[{self.schema}].[{self.name}]</code>")
        title_lbl.setStyleSheet("font-size: 14px; color: #1e293b;")
        tip_lbl = QLabel(
            "<b>调整说明：</b> 您可以在此修改视图引用的跨库库名、链接服务器 IP/别名、架构名前缀或查询逻辑。\n"
            "迁移此视图时，工具将优先在目标端执行下方编辑框内的自定义 SQL 语句。"
        )
        tip_lbl.setStyleSheet("font-size: 12px; color: #475569; line-height: 1.4;")
        h_layout.addWidget(title_lbl)
        h_layout.addWidget(tip_lbl)
        layout.addWidget(header_card)

        # SQL 编辑区
        self.editor = QPlainTextEdit()
        mono_font = QFont("Consolas")
        mono_font.setStyleHint(QFont.Monospace)
        mono_font.setPointSize(10)
        self.editor.setFont(mono_font)
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setStyleSheet("""
            QPlainTextEdit {
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 8px;
                font-family: Consolas, 'Courier New', monospace;
            }
            QPlainTextEdit:focus {
                border: 1.5px solid #2563eb;
            }
        """)
        layout.addWidget(self.editor, 1)

        # 底部状态及按钮栏
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        self.btn_reset = QPushButton("还原为源库原始定义")
        self.btn_reset.setIcon(get_icon("undo", "#334155", 14))
        self.btn_reset.setToolTip("从源数据库重新拉取并重置当前编辑内容为原始定义")
        self.btn_reset.clicked.connect(self._on_reset_to_raw)

        self.btn_clear = QPushButton("清除自定义 (按源库默认迁移)")
        self.btn_clear.setIcon(get_icon("trash", "#dc2626", 14))
        self.btn_clear.setToolTip("取消自定义，迁移时按源库视图定义原样创建")
        self.btn_clear.clicked.connect(self._on_clear_custom)

        bottom_bar.addWidget(self.btn_reset)
        bottom_bar.addWidget(self.btn_clear)
        bottom_bar.addStretch()

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QPushButton("确认使用此自定义SQL")
        self.btn_save.setIcon(get_icon("save", "#ffffff", 14))
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.clicked.connect(self._on_save)

        bottom_bar.addWidget(self.btn_cancel)
        bottom_bar.addWidget(self.btn_save)
        layout.addLayout(bottom_bar)

    def _load_initial_sql(self, current_custom_sql: Optional[str]):
        """加载初始 SQL 内容"""
        if self.fetch_raw_callback:
            try:
                self.raw_sql = self.fetch_raw_callback(self.schema, self.name)
            except Exception as e:
                self.raw_sql = f"-- 无法自动拉取源库定义: {e}"

        if current_custom_sql and current_custom_sql.strip():
            self.editor.setPlainText(current_custom_sql.strip())
        elif self.raw_sql:
            self.editor.setPlainText(self.raw_sql.strip())
        else:
            self.editor.setPlainText(f"CREATE VIEW [{self.schema}].[{self.name}] AS\nSELECT 1 AS col;")

    def _on_reset_to_raw(self):
        if not self.raw_sql and self.fetch_raw_callback:
            try:
                self.raw_sql = self.fetch_raw_callback(self.schema, self.name)
            except Exception as e:
                QMessageBox.warning(self, "获取失败", f"从源库重新获取视图定义失败: {e}")
                return

        if self.raw_sql:
            self.editor.setPlainText(self.raw_sql.strip())
            QMessageBox.information(self, "已重置", "已将编辑框内容重置为源库原始定义！")

    def _on_clear_custom(self):
        self.final_custom_sql = None
        self.accept()

    def _on_save(self):
        edited_sql = self.editor.toPlainText().strip()
        if not edited_sql:
            QMessageBox.warning(self, "提示", "视图 SQL 语句不能为空！若不需要自定义，请点击【清除自定义】。")
            return

        # 若编辑内容与原始源库定义完全一致（忽略换行符差异），视为未自定义
        norm_raw = self.raw_sql.strip().replace("\r\n", "\n") if self.raw_sql else ""
        norm_edited = edited_sql.replace("\r\n", "\n")
        if self.raw_sql and norm_edited == norm_raw:
            self.final_custom_sql = None
        else:
            self.final_custom_sql = edited_sql

        self.accept()

    def get_custom_sql(self) -> Optional[str]:
        """获取最终的自定义 SQL（若为 None 则使用源库原样定义）"""
        return self.final_custom_sql


class BatchReplaceViewSqlDialog(QDialog):
    """批量替换视图 SQL 文本对话框"""

    def __init__(
        self,
        parent=None,
        views_config: Optional[List[Dict[str, Any]]] = None,
        fetch_raw_callback: Optional[Callable[[str, str], str]] = None
    ):
        super().__init__(parent)
        self.views_config = views_config or []
        self.fetch_raw_callback = fetch_raw_callback
        self.replaced_count = 0

        self.setWindowTitle("批量替换视图 SQL 文本")
        self.resize(600, 430)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 说明卡片
        tip_card = QFrame()
        tip_card.setObjectName("CardPanel")
        t_layout = QVBoxLayout(tip_card)
        t_layout.setContentsMargins(10, 8, 10, 8)
        tip_title = QLabel("<b>批量替换跨环境依赖（例如链接服务器或跨库名）</b>")
        tip_desc = QLabel(
            "• 典型场景 1：将旧链接服务器 <code>192.168.230.6</code> 批量替换为新 IP 或别名；\n"
            "• 典型场景 2：将源端跨库引用 <code>base_data_sync.dbo.</code> 替换为目标端对应库名；\n"
            "• 替换后将自动为匹配的视图生成自定义 SQL，原视图在未手动保存前不受源端修改影响。"
        )
        tip_desc.setStyleSheet("font-size: 12px; color: #475569; line-height: 1.4;")
        t_layout.addWidget(tip_title)
        t_layout.addWidget(tip_desc)
        layout.addWidget(tip_card)

        # 输入表单组
        form_group = QGroupBox("替换规则设置")
        f_layout = QVBoxLayout(form_group)
        f_layout.setSpacing(8)

        # 查找文本
        h1 = QHBoxLayout()
        lbl1 = QLabel("查找内容 (Find):")
        lbl1.setFixedWidth(110)
        self.edit_find = QLineEdit()
        self.edit_find.setPlaceholderText("例如: 192.168.230.6 或 base_data_sync")
        h1.addWidget(lbl1)
        h1.addWidget(self.edit_find)
        f_layout.addLayout(h1)

        # 替换为文本
        h2 = QHBoxLayout()
        lbl2 = QLabel("替换为 (Replace):")
        lbl2.setFixedWidth(110)
        self.edit_replace = QLineEdit()
        self.edit_replace.setPlaceholderText("例如: 192.168.200.45 或 target_sync_db")
        h2.addWidget(lbl2)
        h2.addWidget(self.edit_replace)
        f_layout.addLayout(h2)

        # 范围与选项
        opts_layout = QHBoxLayout()
        self.rb_selected = QRadioButton("仅针对已勾选的视图")
        self.rb_selected.setChecked(True)
        self.rb_all = QRadioButton("针对表格中全部视图")

        self.scope_group = QButtonGroup()
        self.scope_group.addButton(self.rb_selected)
        self.scope_group.addButton(self.rb_all)

        self.chk_case_sensitive = QCheckBox("区分大小写")
        self.chk_case_sensitive.setChecked(False)

        opts_layout.addWidget(self.rb_selected)
        opts_layout.addWidget(self.rb_all)
        opts_layout.addStretch()
        opts_layout.addWidget(self.chk_case_sensitive)
        f_layout.addLayout(opts_layout)

        layout.addWidget(form_group)

        # 结果与日志预览
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("点击【执行批量替换】后在此展示匹配与替换结果明细...")
        self.txt_log.setStyleSheet("background-color: #f8fafc; font-family: Consolas, monospace; font-size: 11px;")
        layout.addWidget(self.txt_log, 1)

        # 底部操作栏
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()
        
        self.btn_apply = QPushButton("执行批量替换")
        self.btn_apply.setObjectName("PrimaryBtn")
        self.btn_apply.setIcon(get_icon("rocket", "#ffffff", 14))
        self.btn_apply.clicked.connect(self._on_apply)

        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)

        bottom_bar.addWidget(self.btn_apply)
        bottom_bar.addWidget(self.btn_close)
        layout.addLayout(bottom_bar)

    def _on_apply(self):
        find_str = self.edit_find.text()
        replace_str = self.edit_replace.text()

        if not find_str:
            QMessageBox.warning(self, "提示", "请输入要查找的内容！")
            return

        only_selected = self.rb_selected.isChecked()
        case_sensitive = self.chk_case_sensitive.isChecked()

        # 确定目标视图
        target_views = []
        for v in self.views_config:
            if only_selected and not v.get("is_checked", True):
                continue
            target_views.append(v)

        if not target_views:
            QMessageBox.information(self, "提示", "未找到符合范围的视图！")
            return

        self.txt_log.clear()
        self.txt_log.appendPlainText(f"开始在 {len(target_views)} 个视图中查找 '{find_str}' 并替换为 '{replace_str}'...\n")

        total_views_matched = 0
        total_occurrences = 0

        flags = 0 if case_sensitive else re.IGNORECASE

        for item in target_views:
            schema = item.get("schema", "dbo")
            name = item.get("name", "")
            current_sql = item.get("custom_sql")

            # 若尚未自定义，尝试通过 callback 拉取源库原始定义
            if not current_sql:
                if self.fetch_raw_callback:
                    try:
                        current_sql = self.fetch_raw_callback(schema, name)
                    except Exception as e:
                        self.txt_log.appendPlainText(f"[-] 视图 [{schema}].[{name}] 获取定义失败: {e}")
                        continue
                else:
                    current_sql = ""

            if not current_sql:
                continue

            # 执行替换
            matches = len(re.findall(re.escape(find_str), current_sql, flags=flags))
            if matches > 0:
                new_sql = re.sub(re.escape(find_str), replace_str, current_sql, flags=flags)
                item["custom_sql"] = new_sql
                total_views_matched += 1
                total_occurrences += matches
                self.txt_log.appendPlainText(f"[OK] 视图 [{schema}].[{name}]: 替换了 {matches} 处引用")
            else:
                self.txt_log.appendPlainText(f"[--] 视图 [{schema}].[{name}]: 未包含目标文本，保持原样")

        self.replaced_count = total_views_matched
        summary_msg = f"\n=== 替换完成 ===\n共扫描 {len(target_views)} 个视图，在 {total_views_matched} 个视图中替换了 {total_occurrences} 处文本引用。"
        self.txt_log.appendPlainText(summary_msg)

        if total_views_matched > 0:
            QMessageBox.information(self, "替换成功", f"已成功在 {total_views_matched} 个视图中替换了 {total_occurrences} 处文本！")
        else:
            QMessageBox.information(self, "无匹配项", f"在所选视图定义中未找到匹配的 '{find_str}'。")

    def closeEvent(self, event):
        if self.replaced_count > 0:
            self.accept()
        super().closeEvent(event)

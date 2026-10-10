"""Native Qt node canvas; graph changes control actual pipeline execution."""
import copy
import json
from PyQt5.QtCore import Qt, QRectF, QPointF, QMimeData, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QPainterPath, QDrag
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGraphicsObject, QGraphicsScene,
                            QGraphicsView, QComboBox, QCheckBox, QListWidget, QInputDialog, QMessageBox,
                            QGraphicsPathItem, QMenu)
from desktop_theme import page, card, label, button
import workflow

COLORS = {'source': '#8bdad6', 'separation': '#b5a1ef', 'midi': '#e3c386', 'subtitles': '#91caa5'}


class Edge(QGraphicsPathItem):
    def __init__(self, path, pair, owner):
        super().__init__(path)
        self.pair, self.owner = pair, owner

    def contextMenuEvent(self, event):
        menu = QMenu()
        remove = menu.addAction('移除连接')
        if menu.exec_(event.screenPos()) == remove:
            self.owner.graph['edges'].remove(self.pair)
            self.owner.redraw_lines()
            self.owner.persist()


class Node(QGraphicsObject):
    selected = pyqtSignal(str)
    moved = pyqtSignal(str)
    released = pyqtSignal(str)
    removed = pyqtSignal(str)

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.snapping = False
        self.drag_start = None
        self.setFlags(self.ItemIsMovable | self.ItemIsSelectable | self.ItemSendsGeometryChanges)
        self.setCursor(Qt.OpenHandCursor)

    def boundingRect(self):
        return QRectF(-10, -2, 212, 106)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor('#263d49'))
        painter.setPen(QPen(QColor('#f4d89f' if self.snapping else COLORS[self.name]), 3 if self.snapping else 2 if self.isSelected() else 1))
        painter.drawRoundedRect(QRectF(0, 0, 192, 100), 14, 14)
        painter.setPen(QColor('#edf4fa'))
        painter.drawText(QRectF(16, 15, 165, 30), Qt.AlignLeft | Qt.AlignVCenter, workflow.NODES[self.name])
        painter.setPen(QColor('#b1c3d1'))
        painter.drawText(QRectF(16, 48, 160, 35), Qt.AlignLeft | Qt.AlignVCenter,
                         '松开自动连接' if self.snapping else '点击编辑 · 拖近连接')
        painter.setBrush(QColor(COLORS[self.name]))
        painter.setPen(Qt.NoPen)
        if self.name != 'source':
            painter.drawEllipse(QPointF(0, 50), 7, 7)
        if self.name != 'subtitles':
            painter.drawEllipse(QPointF(192, 50), 7, 7)

    def mousePressEvent(self, event):
        self.drag_start = self.pos()
        self.selected.emit(self.name)
        self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.OpenHandCursor)
        if self.drag_start is not None and self.pos() != self.drag_start:
            self.released.emit(self.name)
        self.drag_start = None

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == self.ItemPositionHasChanged:
            self.moved.emit(self.name)
        return result

    def contextMenuEvent(self, event):
        menu = QMenu()
        action = menu.addAction('移除节点及其连接')
        if self.name == 'source':
            action.setEnabled(False)
        if menu.exec_(event.screenPos()) == action:
            self.removed.emit(self.name)


class Palette(QListWidget):
    def __init__(self):
        super().__init__()
        self.addItems(list(workflow.NODES.values()))
        self.setDragEnabled(True)
        self.setFixedWidth(155)

    def startDrag(self, actions):
        if self.currentRow() < 0:
            return
        data = QMimeData()
        data.setData('application/x-bvwa-node', list(workflow.NODES)[self.currentRow()].encode())
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.exec_(Qt.CopyAction)


class Canvas(QGraphicsView):
    added = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setSceneRect(-50, -50, 1200, 700)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(self.RubberBandDrag)
        self.setBackgroundBrush(QColor('#162b38'))
        self.setMinimumHeight(360)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/x-bvwa-node'):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        name = bytes(event.mimeData().data('application/x-bvwa-node')).decode()
        if name in workflow.NODES:
            self.added.emit(name, self.mapToScene(event.pos()))
            event.acceptProposedAction()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1/1.15
            if .35 <= self.transform().m11()*factor <= 1.8:
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)


class WorkflowPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.loading = owner, True
        state = workflow.read_state()
        self.graph, self.presets = state['graph'], state['presets']
        self.nodes, self.lines, self.preview = {}, [], None
        panel, body = page('工作流', '拖近模块并松开即可连接；保存的预设可直接在“处理音乐”中使用。')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)
        row = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.setMinimumWidth(245)
        row.addWidget(self.preset)
        row.addWidget(button('保存为预设', self.save_preset))
        row.addWidget(button('删除预设', self.delete_preset, 'danger'))
        row.addStretch()
        row.addWidget(button('整理画布', self.arrange))
        body.addLayout(row)
        self.message = label('', 'muted')
        apply_row = QHBoxLayout()
        apply_row.addWidget(self.message, 1)
        self.use_button = button('用于处理音乐', self.use_for_processing, 'primary')
        apply_row.addWidget(self.use_button)
        body.addLayout(apply_row)
        row = QHBoxLayout()
        tools = QVBoxLayout()
        tools.addWidget(label('流程节点', 'card-title'))
        self.palette = Palette()
        self.palette.itemDoubleClicked.connect(lambda item: self.append_node(list(workflow.NODES)[self.palette.row(item)]))
        tools.addWidget(self.palette, 1)
        tools.addWidget(label('拖入或双击添加\n拖近前后模块，松开自动连接\n拉远可断开；右键可移除', 'muted'))
        row.addLayout(tools)
        self.canvas = Canvas()
        self.canvas.added.connect(self.add_node)
        row.addWidget(self.canvas, 1)
        inspector, settings = card('节点参数', '设备独立选择；已有任务继续使用建立时的设置。')
        inspector.setFixedWidth(300)
        self.sections = {}
        specs = {
            'source': [('language', '歌词语言', [('中文', 'zh'), ('日语', 'ja')])],
            'separation': [('mode', '分离方案', [('Karaoke 2 · 双轨', 'dual'), ('BS-RoFormer · 双轨', 'roformer'), ('人声单轨', 'single'), ('导入外部分轨', 'import')]),
                           ('separation_device', '基础分离设备', [('GPU · DirectML', 'dml'), ('CPU', 'cpu')]),
                           ('roformer_device', 'BS-RoFormer 设备', [('自动', 'auto'), ('CPU', 'cpu'), ('NVIDIA CUDA', 'cuda')])],
            'midi': [('midi_device', 'MIDI / ASR 设备', [('GPU · DirectML', 'dml'), ('CPU', 'cpu')]),
                     ('midi_steps', '提取精度', [('快速 · 8 步', 8), ('精细 · 16 步', 16), ('高精度 · 32 步', 32)]),
                     ('zh_lyric_mode', '中文歌词格式', [('汉字', 'hanzi'), ('拼音', 'pinyin')])],
        }
        self.fields = {}
        for node in workflow.NODES:
            section = QWidget()
            layout = QVBoxLayout(section)
            layout.setContentsMargins(0, 0, 0, 0)
            for key, title, items in specs.get(node, []):
                layout.addWidget(label(title, 'muted'))
                field = QComboBox()
                for title, data in items:
                    field.addItem(title, data)
                self.fields[key] = field
                layout.addWidget(field)
                field.currentIndexChanged.connect(self.parameters_changed)
            if node == 'midi':
                self.recognize = QCheckBox('识别并写入歌词')
                self.recognize.toggled.connect(self.parameters_changed)
                layout.addWidget(self.recognize)
            elif node == 'subtitles':
                layout.addWidget(label('从 MIDI 歌词事件导出 SRT 和 VTT。按停顿分句，完成后可在字幕工具中核对。', 'muted'))
            elif node == 'source':
                layout.addWidget(label('音乐文件、BV 号和本次参考歌词在“处理音乐”页面填写。', 'muted'))
            self.sections[node] = section
            settings.addWidget(section)
            section.hide()
        settings.addStretch()
        row.addWidget(inspector)
        body.addLayout(row, 1)
        self.reload_presets(state['preset'])
        self.apply_options(state['options'])
        self.draw_graph()
        self.loading = False
        self.preset.currentTextChanged.connect(self.choose_preset)
        self.select('midi')
        self.sync_from_owner()
        self.update_message()

    def reload_presets(self, selected='自定义'):
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItems(['自定义'] + list(workflow.builtins()) + list(self.presets))
        self.preset.setCurrentText(selected if self.preset.findText(selected) >= 0 else '自定义')
        self.preset.blockSignals(False)
        self.sync_preset_picker()

    def sync_preset_picker(self):
        picker = self.owner.workflow_preset
        picker.blockSignals(True)
        picker.clear()
        for name in ['自定义'] + list(workflow.builtins()) + list(self.presets):
            picker.addItem('当前自定义工作流' if name == '自定义' else name, name)
        picker.setCurrentIndex(max(0, picker.findData(self.preset.currentText())))
        picker.blockSignals(False)

    def matching_preset(self):
        candidates = {**workflow.builtins(), **self.presets}
        preferred = self.preset.currentText()
        names = ([preferred] if preferred in candidates else []) + [name for name in candidates if name != preferred]
        for name in names:
            preset = candidates[name]
            if preset['graph'] == self.graph and preset['options'] == self.options():
                return name
        return '自定义'

    def use_for_processing(self):
        try:
            workflow.validate_graph(self.graph)
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.persist(dirty=False)
        self.owner.navigate(0)

    def options(self):
        return {'language': self.owner.language.currentData(), 'recognize_lyrics': self.owner.recognize.isChecked(),
                'zh_lyric_mode': self.owner.zh_lyric_mode.currentData(),
                'voice_mode': self.owner.separator_model.currentData() if self.owner.voice_mode.currentData() == 'dual' else self.owner.voice_mode.currentData(),
                'midi_steps': self.fields['midi_steps'].currentData(),
                'separation_device': self.fields['separation_device'].currentData(),
                'midi_device': self.fields['midi_device'].currentData(),
                'roformer_device': self.owner.roformer_device.currentData()}

    def apply_options(self, values):
        for key, field in self.fields.items():
            value = values['voice_mode'] if key == 'mode' else values[key]
            field.setCurrentIndex(max(0, field.findData(value)))
        self.recognize.setChecked(values['recognize_lyrics'])
        self.apply_to_owner(values)

    def apply_to_owner(self, values):
        self.owner.language.setCurrentIndex(self.owner.language.findData(values['language']))
        self.owner.zh_lyric_mode.setCurrentIndex(self.owner.zh_lyric_mode.findData(values['zh_lyric_mode']))
        mode = values['voice_mode']
        self.owner.voice_mode.setCurrentIndex(self.owner.voice_mode.findData('dual' if mode == 'roformer' else mode))
        self.owner.separator_model.setCurrentIndex(self.owner.separator_model.findData('roformer' if mode == 'roformer' else 'dual'))
        self.owner.roformer_device.setCurrentIndex(self.owner.roformer_device.findData(values['roformer_device']))
        self.owner.precision.setChecked(values['midi_steps'] >= 16)
        self.owner.recognize.setChecked(values['recognize_lyrics'])

    def parameters_changed(self):
        if self.loading:
            return
        self.loading = True
        values = {key: field.currentData() for key, field in self.fields.items() if key != 'mode'}
        values.update(voice_mode=self.fields['mode'].currentData(), recognize_lyrics=self.recognize.isChecked())
        self.apply_to_owner(values)
        self.loading = False
        self.persist()

    def sync_from_owner(self):
        if self.loading:
            return
        self.loading = True
        values = self.options()
        for key in ('language', 'zh_lyric_mode', 'roformer_device'):
            self.fields[key].setCurrentIndex(self.fields[key].findData(values[key]))
        self.fields['mode'].setCurrentIndex(self.fields['mode'].findData(values['voice_mode']))
        self.recognize.setChecked(values['recognize_lyrics'])
        self.loading = False

    def choose_preset(self, name):
        if self.loading or self.owner.processing_active:
            return
        if name == '自定义':
            self.preset.blockSignals(True)
            self.preset.setCurrentText(name)
            self.preset.blockSignals(False)
            self.persist(dirty=False)
            return
        preset = {**workflow.builtins(), **self.presets}.get(name)
        if not preset:
            return
        self.loading = True
        self.preset.blockSignals(True)
        self.preset.setCurrentText(name)
        self.preset.blockSignals(False)
        self.graph = copy.deepcopy(preset['graph'])
        self.apply_options(preset['options'])
        self.draw_graph()
        self.loading = False
        self.persist(dirty=False)

    def save_preset(self):
        try:
            workflow.validate_graph(self.graph)
            name, ok = QInputDialog.getText(self, '保存处理预设', '预设名称（仅保存流程与参数，不包含音乐和参考歌词）')
            if not ok:
                return
            name = name.strip()
            if not name or len(name) > 80 or name.startswith('内置') or name == '自定义':
                raise ValueError('请填写不以“内置”开头的名称，最多 80 字。')
            if name in self.presets and QMessageBox.question(self, '覆盖预设', '已存在同名预设，是否覆盖？') != QMessageBox.Yes:
                return
            self.presets[name] = {'graph': copy.deepcopy(self.graph), 'options': self.options()}
            self.reload_presets(name)
            self.persist(dirty=False)
        except ValueError as exc:
            QMessageBox.warning(self, '无法保存预设', str(exc))

    def delete_preset(self):
        name = self.preset.currentText()
        if name in self.presets and QMessageBox.question(self, '删除预设', '删除预设“'+name+'”？') == QMessageBox.Yes:
            del self.presets[name]
            self.reload_presets()
            self.persist()

    def select(self, name):
        for key, section in self.sections.items():
            section.setVisible(key == name)

    def append_node(self, name):
        order = list(workflow.NODES)
        index = order.index(name)
        previous = order[index-1] if index else None
        if previous in self.graph['nodes']:
            x, y = self.graph['nodes'][previous]
            point = QPointF(x+workflow.NODE_WIDTH+workflow.SNAP_GAP, y)
        else:
            point = QPointF(60, 150+len(self.nodes)*140)
        self.add_node(name, point)

    def add_node(self, name, point):
        if name in self.graph['nodes']:
            self.select(name)
            self.message.setText('每种节点只需添加一次；可拖动已有节点。')
            return
        self.graph['nodes'][name] = [point.x(), point.y()]
        self.draw_graph()
        self.select(name)
        self.finish_move(name)

    def remove_node(self, name):
        if name == 'source':
            return
        self.graph['nodes'].pop(name, None)
        self.graph['edges'] = [e for e in self.graph['edges'] if name not in e]
        self.draw_graph()
        self.persist()

    def draw_graph(self):
        was_loading, self.loading = self.loading, True
        self.canvas.scene().clear()
        self.nodes, self.lines, self.preview = {}, [], None
        for name, position in self.graph['nodes'].items():
            node = Node(name)
            node.setPos(*position)
            node.selected.connect(self.select)
            node.moved.connect(self.node_moved)
            node.released.connect(self.finish_move)
            node.removed.connect(self.remove_node)
            self.canvas.scene().addItem(node)
            self.nodes[name] = node
        self.redraw_lines()
        self.canvas.centerOn(self.canvas.scene().itemsBoundingRect().center())
        self.loading = was_loading

    def showEvent(self, event):
        super().showEvent(event)
        self.canvas.fitInView(self.canvas.scene().itemsBoundingRect().adjusted(-30, -80, 30, 80), Qt.KeepAspectRatio)

    def redraw_lines(self):
        for line in self.lines:
            self.canvas.scene().removeItem(line)
        self.lines = []
        for left, right in self.graph['edges']:
            a, b = self.nodes[left].pos()+QPointF(192, 50), self.nodes[right].pos()+QPointF(0, 50)
            path = QPainterPath(a)
            middle = (a.x()+b.x())/2
            path.lineTo(QPointF(middle, a.y()))
            path.lineTo(QPointF(middle, b.y()))
            path.lineTo(b)
            line = Edge(path, [left, right], self)
            line.setPen(QPen(QColor(COLORS[left]), 3))
            line.setZValue(-1)
            line.setToolTip('连接：'+workflow.NODES[left]+' → '+workflow.NODES[right])
            self.canvas.scene().addItem(line)
            self.lines.append(line)

    def clear_preview(self):
        if self.preview:
            self.canvas.scene().removeItem(self.preview)
            self.preview = None
        for node in self.nodes.values():
            node.snapping = False
            node.update()

    def node_moved(self, name):
        if self.loading:
            return
        self.graph['nodes'] = {key: [round(node.x(), 1), round(node.y(), 1)] for key, node in self.nodes.items()}
        self.redraw_lines()
        self.clear_preview()
        candidate = workflow.nearby_connection(self.graph, name)
        if candidate:
            left, right = candidate['pair']
            a, b = self.nodes[left].pos()+QPointF(workflow.NODE_WIDTH, 50), self.nodes[right].pos()+QPointF(0, 50)
            path = QPainterPath(a)
            path.lineTo(b)
            self.preview = QGraphicsPathItem(path)
            self.preview.setPen(QPen(QColor('#f4d89f'), 4, Qt.DashLine))
            self.preview.setZValue(-.5)
            self.canvas.scene().addItem(self.preview)
            for key in candidate['pair']:
                self.nodes[key].snapping = True
                self.nodes[key].update()
            self.message.setText('松开后连接：'+workflow.NODES[left]+' → '+workflow.NODES[right])
        else:
            self.message.setText('拖近相邻模块可连接；拉远并松开可断开连接。')

    def finish_move(self, name):
        if self.loading or name not in self.nodes:
            return
        candidate = workflow.nearby_connection(self.graph, name)
        if candidate:
            self.loading = True
            self.nodes[name].setPos(*candidate['position'])
            self.graph['nodes'][name] = candidate['position']
            self.loading = False
        workflow.reconnect_nearby(self.graph, name)
        self.clear_preview()
        self.redraw_lines()
        self.persist()

    def arrange(self):
        self.graph['nodes'] = {name: [20+i*(workflow.NODE_WIDTH+workflow.SNAP_GAP), 100]
                               for i, name in enumerate(n for n in workflow.NODES if n in self.graph['nodes'])}
        for name in self.graph['nodes']:
            workflow.reconnect_nearby(self.graph, name)
        self.draw_graph()
        self.canvas.fitInView(self.canvas.scene().itemsBoundingRect().adjusted(-40, -80, 40, 80), Qt.KeepAspectRatio)
        self.persist()

    def update_message(self):
        try:
            last = workflow.validate_graph(self.graph)
            self.message.setText('流程已连接 · 结束于 '+workflow.NODES[last]+' · 所有推理在本地运行')
            self.use_button.setEnabled(True)
        except ValueError as exc:
            self.message.setText(str(exc))
            self.use_button.setEnabled(False)

    def persist(self, dirty=True):
        if self.loading or self.owner.processing_active:
            return
        if dirty:
            self.preset.blockSignals(True)
            self.preset.setCurrentText('自定义')
            self.preset.blockSignals(False)
        self.update_message()
        try:
            import os
            if os.environ.get('BVWA_TESTING') != '1' or getattr(self, '_persist_test', False):
                workflow.save_state(self.graph, self.options(), self.presets, self.preset.currentText())
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))
        if hasattr(self.owner, 'workflow_summary'):
            self.owner.refresh_workflow_summary()

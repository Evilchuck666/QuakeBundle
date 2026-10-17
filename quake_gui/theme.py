"""A compact, high-contrast Qt Widgets theme."""

STYLE = """
QWidget { background: #111318; color: #edeef1; font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 13px; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background: #111318; }
QScrollArea { border: none; }
QLabel { background: transparent; }
QLabel#eyebrow { color: #eeb376; font-size: 11px; font-weight: 700; letter-spacing: 3px; }
QLabel#heading { font-size: 34px; font-weight: 750; letter-spacing: -1px; }
QLabel#muted { color: #969ca9; }
QLabel#section { font-size: 14px; font-weight: 650; }
QLabel#status { color: #9bd0b1; font-size: 11px; }
QFrame#panel { background: #191c23; border: 1px solid #2a2e37; border-radius: 14px; }
QFrame#panel QLabel, QFrame#panel QWidget#transparent { background: transparent; }
QPushButton { border: 1px solid #343945; border-radius: 8px; background: #232731; padding: 10px 16px; font-weight: 600; }
QPushButton:hover { background: #303642; border-color: #687181; }
QPushButton:pressed { background: #16191e; }
QPushButton:focus, QToolButton:focus { border: 1px solid #eeb376; }
QPushButton:disabled { color: #636977; background: #1c1f27; border-color: #2b303a; }
QPushButton#primary { background: #eeb376; border: 1px solid #eeb376; color: #19140f; padding: 13px 24px; }
QPushButton#primary:hover { background: #ffc991; }
QPushButton#primary:disabled { background: #4d4339; border-color: #4d4339; color: #968575; }
QPushButton#edition { text-align: left; padding: 18px; background: #191c23; border: 1px solid #303540; border-radius: 12px; }
QPushButton#edition:checked { background: #30271f; border: 1px solid #eeb376; }
QPushButton#edition:hover { border-color: #eeb376; }
QLineEdit, QDoubleSpinBox, QComboBox { background: #11151b; border: 1px solid #353b46; border-radius: 7px; padding: 9px 11px; selection-background-color: #a86b34; }
QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #eeb376; }
QLineEdit:disabled, QDoubleSpinBox:disabled { color: #727987; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #232731; selection-background-color: #51402c; padding: 4px; }
QProgressBar { background: #2a2e37; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #eeb376; border-radius: 3px; }
QPlainTextEdit { background: #11151b; border: 1px solid #303540; border-radius: 8px; padding: 8px; color: #a6aebc; font-family: monospace; font-size: 11px; }
QToolButton { background: transparent; color: #afb6c4; border: 1px solid transparent; padding: 7px 0; text-align: left; }
QToolButton:hover { color: #eeb376; }
QScrollBar:vertical { background: #111318; width: 8px; }
QScrollBar::handle:vertical { background: #383e49; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #242933; color: #edeef1; border: 1px solid #596170; padding: 6px; }
"""
